"""qemu_target -- run the firmware stack under QEMU and connect to its buses.

Implements the oracle.driver.Target protocol.

The target stages the sysroot from the firmware image, configures its IPC wiring
(stack_open.xml), and launches the daemons inside bubblewrap jails sharing
loopback network. The OpenWebNet interface connects to TCP 20000 (command and
event sessions), and the bus device (/dev/ttyPIC) connects to an oracle.bus.Bus
backed by a PTY.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import select
import shutil
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import IO

from oracle import bus, run, sandbox, target

logger = logging.getLogger(__name__)

BANNER = "*#*1##"
NACK = "*#*0##"


def split_own(buf: bytearray) -> list[str]:
    """Extract complete OpenWebNet frames (ending in '##') from the buffer."""
    frames: list[str] = []
    while True:
        idx = buf.find(b"##")
        if idx == -1:
            break
        frame_bytes = buf[: idx + 2]
        del buf[: idx + 2]
        frames.append(frame_bytes.decode("ascii", "replace"))
    return frames


def _trim_stack_open_xml(cfg_path: Path, active_clients: set[str]) -> None:
    if not cfg_path.exists():
        return
    tree = ET.parse(cfg_path)  # noqa: S314 - verified sysroot file
    openserver_el = tree.find("sw/openserver")
    if openserver_el is None:
        openserver_el = tree.find("sw/bacclient")
    if openserver_el is None:
        return
    client_idx = 1
    for child in list(openserver_el):
        if child.tag.startswith("client_"):
            name_el = child.find("name")
            if name_el is None or name_el.text not in active_clients:
                openserver_el.remove(child)
            else:
                child.tag = f"client_{client_idx:02d}"
                client_idx += 1
    tree.write(cfg_path)


def _trim_openserver_ini(ops_cfg: Path, active_clients: set[str]) -> None:
    if not ops_cfg.exists():
        return
    lines = ops_cfg.read_text(encoding="latin-1").splitlines()
    new_lines: list[str] = []
    in_stackopen = False
    client_idx = 1
    for line in lines:
        stripped = line.strip()
        if stripped == "[Stackopen]":
            in_stackopen = True
            new_lines.append(line)
            continue
        if in_stackopen:
            if stripped.startswith("["):
                in_stackopen = False
                new_lines.append(line)
                continue
            if "=" in line:
                _, val = line.split("=", 1)
                client_name = val.split(";")[0]
                if client_name in active_clients:
                    new_lines.append(f"client_{client_idx:02d}={val}")
                    client_idx += 1
                continue
        new_lines.append(line)
    ops_cfg.write_text("\n".join(new_lines) + "\n", encoding="latin-1")


def prepare_stack_open(cfg_path: Path, active_clients: set[str]) -> None:
    """Trim stack_open.xml and cfg/openserver to active clients only."""
    _trim_stack_open_xml(cfg_path, active_clients)
    _trim_openserver_ini(cfg_path.parent / "openserver", active_clients)


def read_ports_from_stack_open(cfg_path: Path) -> dict[str, int]:
    """Read the listening ports for each daemon from stack_open.xml."""
    ports: dict[str, int] = {}
    if not cfg_path.exists():
        return ports
    tree = ET.parse(cfg_path)  # noqa: S314 - verified sysroot file
    root = tree.getroot()
    for p in root.findall("sw/*"):
        tag = p.tag
        if tag == "openserver":
            open_p = p.find("port_open")
            if open_p is not None and open_p.text and open_p.text.isdigit():
                ports[tag] = int(open_p.text)
                continue
        mon = p.find("port_monitor")
        port = p.find("port")
        open_p = p.find("port_open")
        if mon is not None and mon.text and mon.text.isdigit():
            ports[tag] = int(mon.text)
        elif port is not None and port.text and port.text.isdigit():
            ports[tag] = int(port.text)
        elif open_p is not None and open_p.text and open_p.text.isdigit():
            ports[tag] = int(open_p.text)
    return ports


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """Verify that a local TCP port is not already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.connect((host, port))
            return False
        except OSError:
            return True


def wait_tcp_port(
    port: int,
    timeout: float = 5.0,
    poll_interval: float = 0.05,
    on_poll: Callable[[], object] | None = None,
) -> bool:
    """Poll until a local TCP port is open or timeout expires."""
    start_t = time.monotonic()
    while time.monotonic() - start_t < timeout:
        if on_poll is not None:
            on_poll()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(poll_interval)
                s.connect(("127.0.0.1", port))
                return True
        except OSError:
            time.sleep(poll_interval)
    return False


def recv_own_frame(sock: socket.socket, timeout: float = 2.0) -> str:
    """Read bytes from socket until the first complete OpenWebNet frame arrives."""
    sock.settimeout(timeout)
    buf = bytearray()
    while True:
        try:
            chunk = sock.recv(1024)
        except (TimeoutError, OSError):
            break
        if not chunk:
            break
        buf.extend(chunk)
        frames = split_own(buf)
        if frames:
            return frames[0]
    return ""


class QemuTarget:
    """A Target running real or simulated firmware binaries under QEMU."""

    def __init__(
        self,
        spec: target.TargetSpec,
        image: Path,
        work_dir: Path,
        *,
        harness: str = "full",
        framer: bus.Framer | None = None,
        responder: bus.Responder | None = None,
        clock: bus.Clock | None = None,
        quiet_ms: float = 50.0,
        max_ms: float = 300.0,
        sandboxed: bool = True,
        keep_trace: bool = False,
        staged_sysroot: Path | None = None,
        bus_instance: bus.Bus | None = None,
    ) -> None:
        self.spec = spec
        self.image = image
        self.work_dir = work_dir
        self.harness = harness
        self.quiet_ms = quiet_ms
        self.max_ms = max_ms
        self.sandboxed = sandboxed
        self.keep_trace = keep_trace
        self.clock = clock or bus.MonotonicClock()

        if self.sandboxed and not (
            sandbox.is_net_isolated() or os.environ.get("ORACLE_IN_SANDBOX")
        ):
            raise sandbox.SandboxError(
                "QemuTarget requires network isolation (run via oracle.run suite "
                "or under sandbox.wrap); pass sandboxed=False to run unconfined"
            )

        self._console_files: dict[str, IO[bytes]] = {}
        self._custom_bus = bus_instance
        self._framer = framer or bus.get_framer(spec.bus_framer)
        self._responder = responder or bus.get_responder(
            spec.bus_responder, version=spec.version
        )

        self.sysroot_base = staged_sysroot or (work_dir / "stage" / "sysroot")
        self.sysroot = work_dir / "sysroot"

        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._devices: list[run.Device] = []
        self.bus: bus.Bus = (
            bus_instance
            if bus_instance is not None
            else bus.Bus(bus.PtyPort(-1), self._framer, self._responder, self.clock)
        )
        self._bus_mark: int = 0
        self._cmd_sock: socket.socket | None = None
        self._ev_sock: socket.socket | None = None
        self._ev_buf: bytearray = bytearray()
        self._own_events: list[str] = []
        self._reply: str = "-"
        self._running: bool = False

        if not self.sysroot_base.exists() and image.exists():
            run.stage_target(spec, image, self.sysroot_base.parent, sandboxed=sandboxed)

    @property
    def settle_ms(self) -> float:
        return self.max_ms

    def _copy_sysroot(self) -> None:
        """Populate work sysroot from the staged baseline."""
        if not self.sysroot_base.exists():
            self.sysroot.mkdir(parents=True, exist_ok=True)
            return
        if self.sysroot_base.resolve() == self.sysroot.resolve():
            return
        if self.sysroot.exists():
            attempt = 0
            while True:
                try:
                    shutil.rmtree(self.sysroot)
                    break
                except OSError as err:
                    if err.errno not in (errno.ENOTEMPTY, errno.EBUSY) or attempt >= 4:
                        raise
                    logger.debug(
                        "rmtree(%s) failed (%s), retrying (attempt %d)...",
                        self.sysroot,
                        err,
                        attempt + 1,
                    )
                    time.sleep(0.05 * (2**attempt))
                    attempt += 1
        shutil.copytree(self.sysroot_base, self.sysroot, symlinks=True)
        for f_path, content in self.spec.runtime.files.items():
            dest = self.sysroot / f_path.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="latin-1")

    def _active_clients(self) -> set[str]:
        if self.harness.startswith("unit:"):
            return {self.harness[len("unit:") :]}
        return {
            name
            for name, prog in self.spec.programs.items()
            if prog.role == "translator"
        }

    def _start_program(
        self,
        name: str,
        devices: dict[str, str],
        trace_dir: Path | None = None,
        console: IO[bytes] | None = None,
    ) -> subprocess.Popen[bytes]:
        prog = self.spec.programs[name]
        release = self.spec.runtime.kernel_release or sandbox.KERNEL_RELEASE
        prog_trace = trace_dir / name if trace_dir is not None else None
        if prog_trace is not None:
            prog_trace.mkdir(parents=True, exist_ok=True)
        argv = sandbox.jail(
            self.sysroot,
            ["/" + prog.path, *prog.args],
            cwd=self.spec.runtime.cwd,
            env=self.spec.runtime.env,
            devices=devices,
            tmpfs=self.spec.runtime.tmpfs,
            dirs=self.spec.runtime.dirs,
            links=self.spec.runtime.links,
            trace_dir=prog_trace,
            release=release,
            share_net=True,
        )
        if console is None:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.work_dir / f"console.{name}.log"
            f = log_path.open("wb")
            self._console_files[name] = f
            console = f

        return subprocess.Popen(  # noqa: S603 - argv built by sandbox.jail
            argv,
            stdin=subprocess.DEVNULL,
            stdout=console,
            stderr=console,
        )

    def _connect_sessions(self, port: int) -> None:
        if self.spec.own_auth != "none":
            raise NotImplementedError(
                f"authentication scheme {self.spec.own_auth!r} not implemented yet"
            )
        s_ev = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        banner = recv_own_frame(s_ev, timeout=2.0)
        if banner != BANNER:
            s_ev.close()
            raise RuntimeError(f"event session unexpected banner: {banner!r}")
        s_ev.sendall(b"*99*1##")
        ack = recv_own_frame(s_ev, timeout=2.0)
        if ack != BANNER:
            s_ev.close()
            raise RuntimeError(f"event session handshake failed: {ack!r}")
        s_ev.setblocking(False)
        self._ev_sock = s_ev

        s_cmd = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        banner = recv_own_frame(s_cmd, timeout=2.0)
        if banner != BANNER:
            s_cmd.close()
            raise RuntimeError(f"command session unexpected banner: {banner!r}")
        s_cmd.sendall(b"*99*0##")
        ack = recv_own_frame(s_cmd, timeout=2.0)
        if ack != BANNER:
            s_cmd.close()
            raise RuntimeError(f"command session handshake failed: {ack!r}")
        s_cmd.settimeout(4.0)
        self._cmd_sock = s_cmd

    def _stop(self) -> None:
        self._running = False
        if self._cmd_sock is not None:
            with contextlib.suppress(OSError):
                self._cmd_sock.close()
            self._cmd_sock = None
        if self._ev_sock is not None:
            with contextlib.suppress(OSError):
                self._ev_sock.close()
            self._ev_sock = None

        for p in self._procs.values():
            if p.poll() is None:
                p.terminate()
        for p in self._procs.values():
            try:
                p.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self._procs.clear()

        for d in self._devices:
            with contextlib.suppress(OSError):
                os.close(d.master)
            with contextlib.suppress(OSError):
                os.close(d.slave)
        self._devices.clear()

        for f in self._console_files.values():
            with contextlib.suppress(OSError):
                f.close()
        self._console_files.clear()

    def _launch_daemon(
        self,
        name: str,
        port: int | None,
        dev_map: dict[str, str],
        trace_dir: Path | None,
    ) -> None:
        if port is not None and not port_is_free(port):
            self._stop()
            raise RuntimeError(f"port {port} is already in use before starting {name}")
        self._procs[name] = self._start_program(name, dev_map, trace_dir)
        if port is not None and not wait_tcp_port(
            port, timeout=10.0, on_poll=self.bus.pump
        ):
            self._stop()
            raise RuntimeError(
                f"{name} did not listen on port {port} "
                f"(see console log at {self.work_dir / f'console.{name}.log'})"
            )

    def restart(self) -> None:
        self._stop()
        self._copy_sysroot()
        cfg_path = self.sysroot / self.spec.runtime.stack_config
        active = self._active_clients()
        prepare_stack_open(cfg_path, active)
        ports = read_ports_from_stack_open(cfg_path)

        self._devices = run.open_devices(self.spec.runtime)
        dev_map = {d.guest: d.slave_path for d in self._devices}
        pty_dev = next(
            (d for d in self._devices if d.guest == self.spec.bus_device),
            self._devices[0] if self._devices else None,
        )

        if self._custom_bus is not None:
            self.bus = self._custom_bus
        elif pty_dev is not None:
            pty_port = bus.PtyPort(pty_dev.master)
            self.bus = bus.Bus(pty_port, self._framer, self._responder, self.clock)
        self._bus_mark = self.bus.mark()

        trace_dir = self.work_dir / "trace" if self.keep_trace else None
        if trace_dir is not None:
            trace_dir.mkdir(parents=True, exist_ok=True)

        bus_servers = [
            name
            for name, prog in self.spec.programs.items()
            if prog.role == "bus_server"
        ]
        for name in sorted(bus_servers):
            prog = self.spec.programs[name]
            server_port = prog.port or ports.get(name, 20001)
            self._launch_daemon(name, server_port, dev_map, trace_dir)

        for name in sorted(active):
            if name in self.spec.programs:
                prog = self.spec.programs[name]
                client_port = prog.port or ports.get(name)
                self._launch_daemon(name, client_port, dev_map, trace_dir)

        if self.harness == "full":
            own_servers = [
                name
                for name, prog in self.spec.programs.items()
                if prog.role == "own_server"
            ]
            for name in sorted(own_servers):
                prog = self.spec.programs[name]
                open_port = prog.port or ports.get(name, 20000)
                self._launch_daemon(name, open_port, dev_map, trace_dir)
                try:
                    self._connect_sessions(open_port)
                except Exception:
                    self._stop()
                    raise

        self._running = True
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            self.bus.pump()
            time.sleep(0.02)
        self.settle()
        self.take_bus()
        self.take_own()

    def send_own(self, frame: str) -> None:
        self._reply = "-"
        if not self._running:
            return
        if self._cmd_sock is None or self._cmd_sock.fileno() < 0:
            if self.harness.startswith("unit:"):
                raise RuntimeError(
                    f"harness {self.harness!r} has no OpenWebNet command session; "
                    "cannot execute 'down' step"
                )
            return
        try:
            self._cmd_sock.sendall(frame.encode("ascii"))
            buf = bytearray()
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                self.bus.pump()
                self._pump_own()
                r, _, _ = select.select([self._cmd_sock], [], [], 0.01)
                if not r:
                    continue
                chunk = self._cmd_sock.recv(1024)
                if not chunk:
                    break
                buf.extend(chunk)
                frames = split_own(buf)
                for f in frames:
                    if f == BANNER:
                        self._reply = "ack"
                    elif f == NACK:
                        self._reply = "nack"
                    else:
                        self._own_events.append(f)
                if self._reply != "-":
                    return
        except (TimeoutError, OSError):
            self._reply = "-"

    def take_reply(self) -> str:
        r, self._reply = self._reply, "-"
        return r

    def _pump_own(self) -> bool:
        if self._ev_sock is None or self._ev_sock.fileno() < 0:
            return False
        had_data = False
        try:
            while True:
                r, _, _ = select.select([self._ev_sock], [], [], 0)
                if not r:
                    break
                chunk = self._ev_sock.recv(4096)
                if not chunk:
                    break
                had_data = True
                self._ev_buf.extend(chunk)
                self._own_events.extend(split_own(self._ev_buf))
        except (OSError, ValueError):
            pass
        return had_data

    def take_own(self) -> list[str]:
        self._pump_own()
        out, self._own_events = self._own_events, []
        return out

    def take_bus(self) -> list[bytes]:
        frames = self.bus.gateway_frames(self._bus_mark)
        self._bus_mark = self.bus.mark()
        return frames

    def inject_bus(self, data: bytes) -> None:
        self.bus.inject(data)

    def settle(self) -> bool:
        return self.bus.settle(
            quiet_ms=self.quiet_ms, max_ms=self.max_ms, on_poll=self._pump_own
        )

    def alive(self) -> bool:
        if not self._running or not self._procs:
            return False
        return all(p.poll() is None for p in self._procs.values())

    def close(self) -> None:
        self._stop()

    def __enter__(self) -> QemuTarget:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()
