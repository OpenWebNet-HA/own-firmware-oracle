"""discover -- reduce a `qemu-arm -strace` trace to boundary facts.

Which device nodes a program opens, which ioctl requests it sends to them,
which sockets it binds or connects: observed behaviour, not code. The facts
decide the bus cut (pty vs socket) and the OpenWebNet cut (full vs unit) per
target (docs/oracle-architecture.md section 5).

The output is deterministic: fd numbers, PIDs and pointer values are dropped,
results become 'ok' or an errno name, and facts are de-duplicated and sorted,
so a polling loop that ran 50 or 500 times gives the same file. The parser is
tolerant on purpose: lines it does not recognise are skipped.

qemu-user prints `<pid> name(args)` when a call starts and ` = result` when it
returns, so with several processes the text of one call can be glued to the
next (`4 execve(...)4 uname(...) = 0`). Glued calls are split first; a call
whose result never arrives is dropped -- except execve, which does not return
when it succeeds.
"""

from __future__ import annotations

import errno
import re
import signal
from dataclasses import dataclass, field
from pathlib import Path

from oracle.record import tsv_field

# "1234 open("/dev/ttyS1",O_RDWR|O_NOCTTY) = 3". Sub-calls of the OABI
# socketcall multiplexer print in upper case: "1234 BIND(3,{...}, 16,) = 0".
CALL = re.compile(r"^(?:\d+ )?(?P<name>[A-Za-z_0-9]+)\((?P<args>.*)\) = (?P<ret>.+)$")
EXEC_STARTED = re.compile(r"^(?:\d+ )?execve\((?P<args>.*)\)$")
GLUED = re.compile(r"\)(?=\d+ [A-Za-z_0-9]+\()")
TERMIOS_FIELD = re.compile(r"(c_[a-z]flag) = ([^,]*(?:,[A-Z][A-Z0-9|]*)*)")
TCSETS = {"TCSETS", "TCSETSW", "TCSETSF"}
ERRNO = re.compile(r"-1 errno=(\d+)")
FIRST_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
SUN_PATH = re.compile(r'sun_path="?([^}",]*)')
SIN_PORT = re.compile(r"sin_port=htons\((\d+)\)")
SIN_ADDR = re.compile(r'sin_addr=inet_addr\("([0-9.]+)"\)')
OPEN_FLAGS = re.compile(r"O_[A-Z]+(?:\|O_[A-Z]+)*")
# The loader probing its search path (libfoo.so.0 under v8l/fast-mult/...) says
# nothing about the boundary: shared objects are never facts here.
SHARED_OBJECT = re.compile(r"\.so(?:\.\d+)*$")

# Guest paths worth a fact: the places a gateway keeps devices, sockets,
# state and configuration. Library loading (/lib, /usr/lib) is noise here.
INTERESTING_PREFIXES = (
    "/dev/",
    "/proc/",
    "/sys/",
    "/var/",
    "/tmp/",  # noqa: S108 - a guest path in a trace, never opened here
    "/home/",
    "/etc/",
)


@dataclass(frozen=True, order=True)
class Fact:
    kind: str
    detail: str
    result: str


def _result(ret: str) -> tuple[str, int | None]:
    """('ok', value) or (errno name, None)."""
    m = ERRNO.match(ret.strip())
    if m:
        code = int(m.group(1))
        return errno.errorcode.get(code, f"errno{code}"), None
    try:
        return "ok", int(ret.split(maxsplit=1)[0], 0)
    except ValueError:
        return "ok", None


def _sockaddr(args: str) -> str | None:
    if m := SUN_PATH.search(args):
        return f"unix:{m.group(1)}"
    if port := SIN_PORT.search(args):
        addr = SIN_ADDR.search(args)
        return f"inet:{addr.group(1) if addr else '?'}:{port.group(1)}"
    return None


def _first_int(args: str) -> int | None:
    try:
        return int(args.split(",", 1)[0], 0)
    except ValueError:
        return None


@dataclass
class _Reducer:
    facts: set[Fact] = field(default_factory=set)
    fd_path: dict[int, str] = field(default_factory=dict)

    def open(self, args: str, result: str, value: int | None) -> None:
        # openat's first argument is the dirfd; the path is the first string
        path_m = FIRST_STRING.search(args)
        if not path_m or not path_m.group(1).startswith(INTERESTING_PREFIXES):
            return
        if SHARED_OBJECT.search(path_m.group(1)):
            return
        path = path_m.group(1)
        flags = OPEN_FLAGS.search(args)
        self.facts.add(
            Fact("open", f"{path} {flags.group(0) if flags else '-'}", result)
        )
        if value is not None and result == "ok":
            self.fd_path[value] = path

    def ioctl(self, args: str, result: str) -> None:
        """`ioctl(5,TCSETS,{...})` (qemu names what it knows) or `ioctl(5,0x5401,..)`.
        A termios write keeps its iflag / cflag: the line settings are part of
        the boundary (baud rate, framing, flow control)."""
        parts = args.split(",", 2)
        fd = _first_int(args)
        if fd is None or len(parts) < 2:
            return
        try:
            req = f"0x{int(parts[1], 0):04x}"
        except ValueError:
            req = parts[1]
        detail = f"{self.fd_path.get(fd, 'fd')} {req}"
        if req in TCSETS:
            fields = dict(TERMIOS_FIELD.findall(args))
            detail += "".join(
                f" {k[2:]}={fields[k]}" for k in ("c_iflag", "c_cflag") if k in fields
            )
        self.facts.add(Fact("ioctl", detail, result))

    def sockaddr(self, name: str, args: str, result: str) -> None:
        if addr := _sockaddr(args):
            self.facts.add(Fact(name, addr, result))
            fd = _first_int(args)
            if name == "bind" and result == "ok" and fd is not None:
                self.fd_path[fd] = addr  # so listen() can name what it serves

    def listen(self, args: str, result: str) -> None:
        fd = _first_int(args)
        addr = self.fd_path.get(fd) if fd is not None else None
        if addr and addr.startswith(("inet:", "unix:")):
            self.facts.add(Fact("listen", addr, result))

    def close(self, args: str) -> None:
        fd = _first_int(args)
        if fd is not None:
            self.fd_path.pop(fd, None)  # fds get reused

    def execve(self, args: str, result: str) -> None:
        if path_m := FIRST_STRING.search(args):
            self.facts.add(Fact("execve", path_m.group(1), result))

    def line(self, raw: str) -> None:
        m = CALL.match(raw.strip())
        if not m:
            if started := EXEC_STARTED.match(raw.strip()):
                self.execve(started.group("args"), "ok")  # it never returned
            return
        name, args = m.group("name").lower(), m.group("args")
        result, value = _result(m.group("ret"))
        if name in {"open", "openat"}:
            self.open(args, result, value)
        elif name == "ioctl":
            self.ioctl(args, result)
        elif name in {"bind", "connect"}:
            self.sockaddr(name, args, result)
        elif name == "listen":
            self.listen(args, result)
        elif name == "close":
            self.close(args)
        elif name == "execve":
            self.execve(args, result)


def parse(trace: str) -> list[Fact]:
    reducer = _Reducer()
    for raw in GLUED.sub(")\n", trace).splitlines():
        reducer.line(raw)
    return sorted(reducer.facts)


def parse_files(paths: list[Path]) -> list[Fact]:
    """One trace file per process (sandbox.jail's trace_dir): each is parsed
    on its own, so fd numbers never leak between processes."""
    facts: set[Fact] = set()
    for path in paths:
        facts.update(parse(path.read_text(encoding="utf-8", errors="replace")))
    return sorted(facts)


def exit_fact(returncode: int | None) -> Fact:
    """How the run ended. None = still running when the window closed, which
    for a daemon is the expected outcome."""
    if returncode is None:
        return Fact("exit", "running", "timeout")
    if returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"SIG{-returncode}"
        return Fact("exit", "signal", name)
    return Fact("exit", "status", str(returncode))


def render(header: dict[str, str], facts: list[Fact]) -> str:
    lines = [f"# {k}={v}" for k, v in header.items()]
    lines.append("kind\tdetail\tresult")
    lines += [f"{f.kind}\t{tsv_field(f.detail)}\t{tsv_field(f.result)}" for f in facts]
    return "\n".join(lines) + "\n"
