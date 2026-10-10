"""Unit tests for oracle.qemu_target, bus extensions, and run.py suite command.

All tests run without QEMU, root, or firmware binaries using mocks and fakes.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import io
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    import tty
except ImportError:
    tty = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle import bus, cases, driver, qemu_target, record, run, sandbox, target


@pytest.fixture(autouse=True)
def mock_sandbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORACLE_IN_SANDBOX", "1")


# --- bus extensions: PtyPort and PicResponder --------------------------------


def test_pty_port_read_and_write():
    assert tty is not None
    master, slave = os.openpty()
    tty.setraw(master)
    tty.setraw(slave)
    try:
        port = bus.PtyPort(master)
        # Non-blocking read when empty
        assert port.read() == b""

        # Write to master and read from slave
        port.write(b"$24\r")
        assert os.read(slave, 1024) == b"$24\r"

        # Write empty data is a no-op
        port.write(b"")

        # Write to slave and read from port
        os.write(slave, b"$25010108\r")
        assert port.read() == b"$25010108\r"

        # When master is closed, read returns b"" and write suppresses OSError
        os.close(master)
        assert port.read() == b""
        port.write(b"fail")
    finally:
        with contextlib.suppress(OSError):
            os.close(slave)


def test_pic_responder():
    resp = bus.PicResponder(version="010108")
    assert resp.name == "pic"
    assert resp.respond(b"$24\r") == [b"$25010108\r"]
    assert resp.respond(b"$2603\r") == [b"$26000\r"]
    assert resp.respond(b"$270200\r") == [b"$270200\r"]
    assert resp.respond(b"$020000\r") == [b"$020000\r"]
    assert resp.respond(b"$2802\r") == [b"$2802\r"]
    assert resp.respond(b"$150500FF\r") == [b"$00\r"]
    assert resp.respond(b"$1600\r") == [b"$00\r"]
    assert resp.respond(b"$0331001200\r") == [b"$19\r"]
    assert resp.respond(b"$04B7011300\r") == [b"$00\r"]
    assert resp.respond(b"$05010203\r") == [b"$00\r"]
    assert resp.respond(b"$06D13101420D0D0100\r") == [b"$00\r"]
    assert resp.respond(b"$5101\r") == [b"$00\r"]
    assert resp.respond(b"$520205\r") == [b"$00\r"]
    assert resp.respond(b"$530102\r") == [b"$00\r"]
    assert resp.respond(b"$99\r") == []

    # With inner scripted responder
    inner = bus.Scripted(
        "test",
        {
            b"$0331001200\r": [b"echo"],
            b"$04B7011300\r": [b"echo4"],
            b"$06D13101420D0D0100\r": [b"echo6"],
            b"$99\r": [b"inner"],
        },
    )
    resp_inner = bus.PicResponder(version="010108", inner=inner)
    assert resp_inner.name == "pic:script:test"
    assert resp_inner.respond(b"$0331001200\r") == [b"$19\r", b"echo"]
    assert resp_inner.respond(b"$04B7011300\r") == [b"$00\r", b"echo4"]
    assert resp_inner.respond(b"$06D13101420D0D0100\r") == [b"$00\r", b"echo6"]
    assert resp_inner.respond(b"$99\r") == [b"inner"]


# --- qemu_target helpers: split_own, prepare_stack_open, read_ports ----------


def test_split_own():
    buf = bytearray(b"*#*1##*1*1*31##*1*0")
    frames = qemu_target.split_own(buf)
    assert frames == ["*#*1##", "*1*1*31##"]
    assert buf == bytearray(b"*1*0")
    assert qemu_target.split_own(buf) == []


def test_prepare_stack_open(tmp_path):
    cfg = tmp_path / "stack_open.xml"
    # Non-existent file does not crash
    qemu_target.prepare_stack_open(tmp_path / "nope.xml", {"bt_luci"})

    # Missing openserver section does not crash
    cfg.write_text("<root><sw></sw></root>", encoding="utf-8")
    qemu_target.prepare_stack_open(cfg, {"bt_luci"})

    # Full xml trims non-active clients
    cfg.write_text(
        """<root><sw><openserver>
        <client_01><name>bt_luci</name></client_01>
        <client_02><name>bt_difson</name></client_02>
        <other>keep</other>
        </openserver></sw></root>""",
        encoding="utf-8",
    )
    # INI openserver config trimming
    ops_cfg = tmp_path / "openserver"
    ops_cfg.write_text(
        "[General]\n"
        "OwnPort=20000\n"
        "[Stackopen]\n"
        "# Comment line\n"
        "client_01=bt_luci;30001\n"
        "client_02=bt_difson;30002\n"
        "client_03=bt_device;30003\n"
        "[OtherSection]\n"
        "Key=Value\n",
        encoding="latin-1",
    )
    qemu_target.prepare_stack_open(cfg, {"bt_luci", "bt_device"})
    tree = qemu_target.ET.parse(cfg)
    openserver_el = tree.find("sw/openserver")
    assert openserver_el is not None
    tags = [c.tag for c in openserver_el]
    assert "client_01" in tags
    assert "client_02" not in tags
    assert "other" in tags

    ini_content = ops_cfg.read_text(encoding="latin-1")
    assert "client_01=bt_luci;30001" in ini_content
    assert "client_02=bt_device;30003" in ini_content
    assert "bt_difson" not in ini_content
    assert "[OtherSection]" in ini_content
    assert "Key=Value" in ini_content


def test_read_ports_from_stack_open(tmp_path):
    assert qemu_target.read_ports_from_stack_open(tmp_path / "nope.xml") == {}

    cfg = tmp_path / "stack_open.xml"
    cfg.write_text(
        """<root><sw>
        <openserver><port_open>20000</port_open></openserver>
        <scsserver><port>20001</port></scsserver>
        <bt_luci><port_monitor>40001</port_monitor></bt_luci>
        <no_port></no_port>
        </sw></root>""",
        encoding="utf-8",
    )
    ports = qemu_target.read_ports_from_stack_open(cfg)
    assert ports == {"openserver": 20000, "scsserver": 20001, "bt_luci": 40001}


def test_wait_tcp_port():
    # Test failure timeout with on_poll callback
    polled = False

    def poll_cb():
        nonlocal polled
        polled = True

    assert not qemu_target.wait_tcp_port(
        59999, timeout=0.05, poll_interval=0.02, on_poll=poll_cb
    )
    assert polled is True

    # Test success
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert qemu_target.wait_tcp_port(port, timeout=1.0)
    finally:
        srv.close()


def test_recv_own_frame():
    # Socket EOF / timeout
    s1, s2 = socket.socketpair()
    try:
        s2.close()
        assert qemu_target.recv_own_frame(s1, timeout=0.1) == ""
    finally:
        s1.close()

    # Success frame
    s1, s2 = socket.socketpair()
    try:
        s2.sendall(b"*#*1##")
        assert qemu_target.recv_own_frame(s1, timeout=0.5) == "*#*1##"
    finally:
        s1.close()
        s2.close()


# --- QemuTarget lifecycle and methods ----------------------------------------


def _dummy_spec():
    prog_luci = target.Program(
        "bt_luci", "home/bticino/bin/bt_luci", "1" * 64, "app.zip!"
    )
    prog_scs = target.Program(
        "scsserver", "home/bticino/bin/scsserver", "2" * 64, "fs:"
    )
    prog_open = target.Program(
        "openserver", "home/bticino/bin/openserver", "3" * 64, "app.zip!"
    )
    return target.TargetSpec(
        product="MH200N",
        version="010108",
        sysroot=("fs:", "app.zip!"),
        programs={
            "bt_luci": prog_luci,
            "scsserver": prog_scs,
            "openserver": prog_open,
        },
        boundary={"status": "discovered", "bus": "pty", "own": "full"},
        image_sha256="e" * 64,
        runtime=target.Runtime(devices={"/dev/ttyPIC": "pty"}),
    )


def test_qemu_target_init_and_copy_sysroot(tmp_path):
    spec = _dummy_spec()
    work = tmp_path / "work"
    work.mkdir()
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "test.txt").write_text("ok")

    tgt = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        work,
        staged_sysroot=staged,
    )
    assert tgt.settle_ms == 300.0
    assert tgt.alive() is False

    tgt._copy_sysroot()
    assert (tgt.sysroot / "test.txt").read_text() == "ok"

    # Copy when sysroot already exists
    tgt._copy_sysroot()
    assert (tgt.sysroot / "test.txt").read_text() == "ok"

    # Copy when sysroot_base is identical to sysroot
    tgt.sysroot_base = tgt.sysroot
    tgt._copy_sysroot()
    assert (tgt.sysroot / "test.txt").read_text() == "ok"

    # Runtime files are written into sysroot
    tgt.sysroot_base = staged
    tgt.spec = dataclasses.replace(
        tgt.spec,
        runtime=dataclasses.replace(
            tgt.spec.runtime, files={"/sub/dir/custom.txt": "created"}
        ),
    )
    tgt._copy_sysroot()
    assert (tgt.sysroot / "sub/dir/custom.txt").read_text() == "created"

    # Copy when sysroot_base does not exist
    tgt.sysroot_base = tmp_path / "nonexistent"
    tgt._copy_sysroot()
    assert tgt.sysroot.exists()


def test_qemu_target_copy_sysroot_rmtree_retry(tmp_path, monkeypatch):
    spec = _dummy_spec()
    work = tmp_path / "work"
    work.mkdir()
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "test.txt").write_text("ok")

    tgt = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        work,
        staged_sysroot=staged,
    )
    tgt._copy_sysroot()
    assert tgt.sysroot.exists()

    calls = 0
    orig_rmtree = qemu_target.shutil.rmtree

    def fail_twice_then_succeed(path):
        nonlocal calls
        calls += 1
        if calls < 3:
            err = OSError("directory not empty")
            err.errno = errno.ENOTEMPTY
            raise err
        orig_rmtree(path)

    monkeypatch.setattr(qemu_target.shutil, "rmtree", fail_twice_then_succeed)
    monkeypatch.setattr(qemu_target.time, "sleep", lambda _s: None)

    tgt._copy_sysroot()
    assert calls == 3
    assert (tgt.sysroot / "test.txt").read_text() == "ok"

    err_perm = OSError("permission denied")
    err_perm.errno = errno.EPERM
    monkeypatch.setattr(
        qemu_target.shutil,
        "rmtree",
        MagicMock(side_effect=err_perm),
    )
    with pytest.raises(OSError, match="permission denied"):
        tgt._copy_sysroot()

    err_busy = OSError("persistent busy")
    err_busy.errno = errno.EBUSY
    monkeypatch.setattr(
        qemu_target.shutil,
        "rmtree",
        MagicMock(side_effect=err_busy),
    )
    with pytest.raises(OSError, match="persistent busy"):
        tgt._copy_sysroot()


def test_qemu_target_active_clients(tmp_path):
    spec = _dummy_spec()
    tgt_full = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w1",
        harness="full",
        staged_sysroot=tmp_path,
    )
    assert tgt_full._active_clients() == {"bt_luci"}

    tgt_unit = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w2",
        harness="unit:bt_luci",
        staged_sysroot=tmp_path,
    )
    assert tgt_unit._active_clients() == {"bt_luci"}


def test_qemu_target_connect_sessions_failures(monkeypatch, tmp_path):
    spec = _dummy_spec()
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )

    # 1. Event session bad banner
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: MagicMock())
    monkeypatch.setattr(qemu_target, "recv_own_frame", lambda s, timeout: "*#*0##")
    with pytest.raises(RuntimeError, match="event session unexpected banner"):
        tgt._connect_sessions(20000)

    # 2. Event session bad ack
    calls = ["*#*1##", "*#*0##"]
    monkeypatch.setattr(qemu_target, "recv_own_frame", lambda s, timeout: calls.pop(0))
    with pytest.raises(RuntimeError, match="event session handshake failed"):
        tgt._connect_sessions(20000)

    # 3. Command session bad banner
    calls = ["*#*1##", "*#*1##", "*#*0##"]
    monkeypatch.setattr(qemu_target, "recv_own_frame", lambda s, timeout: calls.pop(0))
    with pytest.raises(RuntimeError, match="command session unexpected banner"):
        tgt._connect_sessions(20000)

    # 4. Command session bad ack
    calls = ["*#*1##", "*#*1##", "*#*1##", "*#*0##"]
    monkeypatch.setattr(qemu_target, "recv_own_frame", lambda s, timeout: calls.pop(0))
    with pytest.raises(RuntimeError, match="command session handshake failed"):
        tgt._connect_sessions(20000)


def test_qemu_target_restart_errors(monkeypatch, tmp_path):
    spec = _dummy_spec()
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    monkeypatch.setattr(tgt, "_copy_sysroot", lambda: None)
    monkeypatch.setattr(run, "open_devices", lambda rt: [])
    proc_mock = MagicMock()
    proc_mock.poll.return_value = None
    monkeypatch.setattr(tgt, "_start_program", lambda name, dev, trace: proc_mock)

    # scsserver timeout
    monkeypatch.setattr(
        qemu_target, "wait_tcp_port", lambda port, timeout, **kwargs: False
    )
    with pytest.raises(RuntimeError, match="scsserver did not listen"):
        tgt.restart()

    # translator timeout
    def fake_wait(port, timeout, **kwargs):
        return port == 20001  # scsserver ok, translator fails

    monkeypatch.setattr(
        qemu_target, "read_ports_from_stack_open", lambda p: {"bt_luci": 40001}
    )
    monkeypatch.setattr(qemu_target, "wait_tcp_port", fake_wait)
    with pytest.raises(RuntimeError, match="bt_luci did not listen"):
        tgt.restart()

    # openserver timeout
    def fake_wait_open(port, timeout, **kwargs):
        return port in (20001, 40001)  # openserver fails

    monkeypatch.setattr(
        qemu_target,
        "read_ports_from_stack_open",
        lambda p: {"bt_luci": 40001, "openserver": 20000},
    )
    monkeypatch.setattr(qemu_target, "wait_tcp_port", fake_wait_open)
    with pytest.raises(RuntimeError, match="openserver did not listen"):
        tgt.restart()


def test_qemu_target_session_interaction(monkeypatch, tmp_path):
    spec = _dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(),
        framer=bus.LineFramer(),
        responder=bus.Silent(),
    )
    tgt = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w",
        staged_sysroot=tmp_path,
        bus_instance=fake_bus,
    )
    # Target not running: send_own is a no-op
    tgt.send_own("*1*1*31##")
    assert tgt.take_reply() == "-"

    s_cmd1, s_cmd2 = socket.socketpair()
    s_ev1, s_ev2 = socket.socketpair()
    tgt._cmd_sock = s_cmd1
    tgt._ev_sock = s_ev1
    tgt._running = True

    try:
        # 1. Successful ACK
        s_cmd2.sendall(b"*#*1##")
        tgt.send_own("*1*1*31##")
        assert tgt.take_reply() == "ack"
        assert tgt.take_reply() == "-"  # resets to "-"

        # 2. Successful NACK
        s_cmd2.sendall(b"*#*0##")
        tgt.send_own("*1*0*31##")
        assert tgt.take_reply() == "nack"

        # 3. Intermediate dimension frame before ACK
        s_cmd2.sendall(b"*#1*31*0##*#*1##")
        tgt.send_own("*#1*31##")
        assert tgt.take_reply() == "ack"
        assert tgt.take_own() == ["*#1*31*0##"]

        # 4. Command socket timeout
        tgt.cmd_timeout = 0.01
        s_cmd1.settimeout(0.01)
        tgt.send_own("*1*1*31##")
        assert tgt.take_reply() == "-"

        # 5. Event pumping
        s_ev2.sendall(b"*1*1*31##")
        assert tgt.take_own() == ["*1*1*31##"]
        assert tgt.take_own() == []

        # 6. Event pumping with closed socket (OSError)
        s_ev2.close()
        s_ev1.close()
        tgt._pump_own()
    finally:
        with contextlib.suppress(OSError):
            s_cmd1.close()
        with contextlib.suppress(OSError):
            s_cmd2.close()


def test_qemu_target_bus_delegation_and_lifecycle(tmp_path):
    spec = _dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(),
        framer=bus.LineFramer(),
        responder=bus.Silent(),
    )
    tgt = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w",
        staged_sysroot=tmp_path,
        bus_instance=fake_bus,
    )

    # Bus delegation
    fake_bus.inject = MagicMock()
    tgt.inject_bus(b"test")
    fake_bus.inject.assert_called_once_with(b"test")

    fake_bus.gateway_frames = MagicMock(return_value=[b"frame1"])
    fake_bus.mark = MagicMock(return_value=1)
    assert tgt.take_bus() == [b"frame1"]

    # Settle
    fake_bus.settle = MagicMock(return_value=True)
    assert tgt.settle() is True
    fake_bus.settle.assert_called_once()

    # Alive
    tgt._running = True
    proc = MagicMock()
    proc.poll.return_value = None
    tgt._procs = {"test": proc}
    assert tgt.alive() is True
    proc.poll.return_value = 1
    assert tgt.alive() is False

    # Context manager and close
    tgt._running = True
    tgt._cmd_sock = MagicMock()
    tgt._ev_sock = MagicMock()
    with tgt:
        pass
    assert tgt._running is False
    assert tgt._cmd_sock is None
    assert tgt._ev_sock is None


# --- run.py suite command tests ----------------------------------------------


def test_parse_reset():
    assert run.parse_reset("each") == 1
    assert run.parse_reset("batch-5") == 5
    assert run.parse_reset("0") == 0
    assert run.parse_reset("10") == 10
    with pytest.raises(SystemExit, match="invalid reset"):
        run.parse_reset("batch-0")
    with pytest.raises(SystemExit, match="invalid reset"):
        run.parse_reset("invalid")


def test_suite_path():
    spec = _dummy_spec()
    assert run.suite_path(spec, "full", "lights") == (
        ROOT / "results/MH200N/010108/oracle/full/lights.tsv"
    )
    assert run.suite_path(spec, "unit:bt_luci", "lights") == (
        ROOT / "results/MH200N/010108/oracle/unit-bt_luci/lights.tsv"
    )


def test_cmd_suite_execution(monkeypatch, tmp_path):
    spec = _dummy_spec()
    cases_file = tmp_path / "lights.cases"
    cases_file.write_text("down *1*1*31##\n", encoding="ascii")
    out_file = tmp_path / "out.tsv"

    monkeypatch.setattr(target, "load", lambda p, r: spec)
    monkeypatch.setattr(
        run,
        "stage_target",
        lambda sp, img, work, sandboxed: run.stage.Staged(work / "sysroot", 1, 0, 0),
    )

    class DummyTarget:
        def __init__(self, *args, **kwargs):
            self.bus = MagicMock()
            self.bus.framer.name = "line"
            self.bus.responder.name = "pic"
            self.settle_ms = 300.0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(qemu_target, "QemuTarget", DummyTarget)
    monkeypatch.setattr(
        driver,
        "run_suite",
        lambda tgt, s, restart_every: [
            record.Row("down", "*1*1*31##", "ack", "out", ("bus:03",))
        ],
    )

    args = [
        "suite",
        "oracle/targets/MH200N/010108.yaml",
        str(cases_file),
        "--image",
        "img.zip",
        "-o",
        str(out_file),
        "--reset",
        "each",
    ]
    assert run.main(args) == 0
    assert out_file.exists()
    header = record.read_header(out_file)
    assert header["product"] == "MH200N"
    assert header["suite"] == "lights"
    assert header["framer"] == "line"
    assert header["responder"] == "pic"
    assert header["harness"] == "full"

    # Test with --keep and unit harness
    keep_dir = tmp_path / "keep_dir"
    args_keep = [
        "suite",
        "oracle/targets/MH200N/010108.yaml",
        str(cases_file),
        "--image",
        "img.zip",
        "--keep",
        str(keep_dir),
        "--harness",
        "unit:bt_luci",
        "-o",
        str(out_file),
    ]
    assert run.main(args_keep) == 0
    assert keep_dir.exists()


def test_recv_own_frame_branches():
    # 1. Timeout / OSError
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = TimeoutError()
    assert qemu_target.recv_own_frame(mock_sock) == ""

    mock_sock.recv.side_effect = OSError()
    assert qemu_target.recv_own_frame(mock_sock) == ""

    # 2. Multi-chunk frame assembly where first chunk has no complete frame
    mock_sock2 = MagicMock()
    mock_sock2.recv.side_effect = [b"*1*", b"1*31##"]
    assert qemu_target.recv_own_frame(mock_sock2) == "*1*1*31##"


def test_qemu_target_init_stages_when_missing(monkeypatch, tmp_path):
    spec = _dummy_spec()
    img = tmp_path / "img.zip"
    img.touch()
    base = tmp_path / "staged_base"
    work = tmp_path / "work"

    staged_called = False

    def fake_stage(sp, image, work_parent, sandboxed=True):
        nonlocal staged_called
        staged_called = True

    monkeypatch.setattr(run, "stage_target", fake_stage)
    _ = qemu_target.QemuTarget(spec, img, work, staged_sysroot=base)
    assert staged_called is True


def test_qemu_target_start_program_and_sandbox(monkeypatch, tmp_path):
    spec = _dummy_spec()
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    # Test real sandbox.jail with share_net=True
    jail_cmd = sandbox.jail(
        tmp_path,
        ["/bin/sh"],
        cwd="/",
        share_net=True,
    )
    assert "--share-net" in jail_cmd

    # Test _start_program with console None and not None
    mock_popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    tgt._start_program("bt_luci", {"/dev/ttyPIC": "/dev/null"})
    assert mock_popen.call_args[1]["stdout"] is not None
    assert mock_popen.call_args[1]["stdout"] != subprocess.DEVNULL
    assert (tgt.work_dir / "console.bt_luci.log").exists()

    buf = io.BytesIO()
    tgt._start_program("bt_luci", {"/dev/ttyPIC": "/dev/null"}, console=buf)
    assert mock_popen.call_args[1]["stdout"] is buf

    # Test _start_program with trace_dir creates daemon trace directory
    trace_dir = tmp_path / "custom_traces"
    tgt._start_program(
        "bt_luci", {"/dev/ttyPIC": "/dev/null"}, trace_dir=trace_dir, console=buf
    )
    assert (trace_dir / "bt_luci").is_dir()


def test_qemu_target_connect_sessions_success(monkeypatch, tmp_path):
    spec = _dummy_spec()
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    s_mock = MagicMock()
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: s_mock)
    monkeypatch.setattr(qemu_target, "recv_own_frame", lambda s, timeout: "*#*1##")

    tgt._connect_sessions(20000)
    assert tgt._cmd_sock is s_mock
    assert tgt._ev_sock is s_mock


def test_qemu_target_connect_sessions_open_v2_success(monkeypatch, tmp_path):
    spec = dataclasses.replace(_dummy_spec(), product="H4684")
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    s_mock = MagicMock()
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: s_mock)
    monkeypatch.setattr(qemu_target, "recv_own_frame", lambda s, timeout: "*#*1##")

    tgt._connect_sessions(20000)
    assert tgt._cmd_sock is s_mock
    assert tgt._ev_sock is s_mock
    s_mock.sendall.assert_called_with(b"*99***1##")


def test_qemu_target_connect_sessions_open_v2_failure(monkeypatch, tmp_path):
    spec = dataclasses.replace(_dummy_spec(), product="H4684")
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    s_mock = MagicMock()
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: s_mock)
    monkeypatch.setattr(qemu_target, "recv_own_frame", lambda s, timeout: "*#*0##")

    with pytest.raises(RuntimeError, match="OPEN v2 event session handshake failed"):
        tgt._connect_sessions(20000)
    s_mock.close.assert_called_once()


def test_qemu_target_stop_timeout_and_devices(tmp_path):
    spec = _dummy_spec()
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=1.0), None]
    tgt._procs = {"test": proc}

    m1, s1 = os.pipe()
    tgt._devices = [run.Device("/dev/ttyPIC", m1, s1, "/dev/null")]
    tgt._stop()
    proc.kill.assert_called_once()
    assert tgt._devices == []


def test_qemu_target_restart_branches(monkeypatch, tmp_path):
    spec = _dummy_spec()
    # 1. Custom bus is preserved, trace dir created when keep_trace=True
    fake_bus = bus.Bus(
        port=MagicMock(), framer=bus.LineFramer(), responder=bus.Silent()
    )
    tgt1 = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w1",
        staged_sysroot=tmp_path,
        bus_instance=fake_bus,
        keep_trace=True,
    )
    monkeypatch.setattr(tgt1, "_copy_sysroot", lambda: None)
    monkeypatch.setattr(qemu_target, "wait_tcp_port", lambda *args, **kwargs: True)
    proc_mock = MagicMock()
    proc_mock.poll.return_value = None
    monkeypatch.setattr(tgt1, "_start_program", lambda name, dev, trace: proc_mock)
    monkeypatch.setattr(tgt1, "_connect_sessions", lambda port: None)
    monkeypatch.setattr(tgt1, "settle", lambda: True)

    m1, s1 = os.pipe()
    monkeypatch.setattr(
        run, "open_devices", lambda rt: [run.Device("/dev/ttyPIC", m1, s1, "/dev/null")]
    )
    tgt1.restart()
    assert tgt1.bus is fake_bus
    assert (tmp_path / "w1" / "trace").is_dir()

    # 2. No custom bus -> creates PtyPort
    tgt2 = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w2",
        staged_sysroot=tmp_path,
    )
    monkeypatch.setattr(tgt2, "_copy_sysroot", lambda: None)
    monkeypatch.setattr(tgt2, "_start_program", lambda name, dev, trace: proc_mock)
    monkeypatch.setattr(tgt2, "_connect_sessions", lambda port: None)
    monkeypatch.setattr(tgt2, "settle", lambda: True)
    m2, s2 = os.pipe()
    monkeypatch.setattr(
        run, "open_devices", lambda rt: [run.Device("/dev/ttyPIC", m2, s2, "/dev/null")]
    )
    tgt2.restart()
    assert isinstance(tgt2.bus.port, bus.PtyPort)
    tgt2.close()

    # 3. Programs omitted from spec (e.g. no scsserver, unit harness)
    spec_sparse = target.TargetSpec(
        product="MH200N",
        version="010108",
        sysroot=("fs:",),
        programs={"other": target.Program("other", "bin/other", "0" * 64, "fs:")},
        boundary={"status": "discovered"},
        runtime=spec.runtime,
    )
    tgt3 = qemu_target.QemuTarget(
        spec_sparse,
        tmp_path / "img.zip",
        tmp_path / "w3",
        harness="unit:not_in_progs",
        staged_sysroot=tmp_path,
        bus_instance=fake_bus,
    )
    monkeypatch.setattr(tgt3, "_copy_sysroot", lambda: None)
    tgt3.restart()
    assert tgt3._procs == {}


def test_qemu_target_send_own_and_pump_branches():
    spec = _dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(), framer=bus.LineFramer(), responder=bus.Silent()
    )
    tgt = qemu_target.QemuTarget(
        spec,
        Path("/tmp/img.zip"),
        Path("/tmp/w"),
        staged_sysroot=Path("/tmp"),
        bus_instance=fake_bus,
    )
    tgt._running = True

    # send_own: cmd sock receives intermediate frame then EOF
    mock_cmd = MagicMock()
    mock_cmd.fileno.return_value = 42
    mock_cmd.recv.side_effect = [b"*1*", b""]
    tgt._cmd_sock = mock_cmd
    with patch("select.select", return_value=([mock_cmd], [], [])):
        tgt.send_own("*1*1*31##")
    assert tgt.take_reply() == "-"

    # send_own: cmd sock raises OSError
    mock_cmd.sendall.side_effect = OSError("broken pipe")
    tgt.send_own("*1*1*31##")
    assert tgt.take_reply() == "-"

    # send_own: fileno < 0
    mock_cmd.fileno.return_value = -1
    tgt.send_own("*1*1*31##")
    assert tgt.take_reply() == "-"

    # pump_own: ev sock closes unexpectedly (chunk == b"")
    s_ev3, s_ev4 = socket.socketpair()
    tgt._ev_sock = s_ev3
    s_ev4.close()
    tgt._pump_own()
    assert tgt.take_own() == []
    s_ev3.close()

    # pump_own: fileno < 0
    mock_ev = MagicMock()
    mock_ev.fileno.return_value = -1
    tgt._ev_sock = mock_ev
    tgt._pump_own()

    # pump_own: OSError / ValueError handled
    tgt._ev_sock = MagicMock()
    tgt._ev_sock.fileno.return_value = 42
    with patch("select.select", side_effect=ValueError("bad fd")):
        tgt._pump_own()


def test_qemu_target_send_own_reconnect():
    spec = _dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(), framer=bus.LineFramer(), responder=bus.Silent()
    )
    tgt = qemu_target.QemuTarget(
        spec,
        Path("/tmp/img.zip"),
        Path("/tmp/w"),
        staged_sysroot=Path("/tmp"),
        bus_instance=fake_bus,
    )
    tgt._running = True
    tgt._open_port = 20000
    tgt.cmd_timeout = 0.01

    mock_cmd = MagicMock()
    mock_cmd.fileno.return_value = 43
    mock_cmd.recv.side_effect = [b""]
    tgt._cmd_sock = mock_cmd
    reconnected: list[int] = []
    tgt._connect_cmd_session = reconnected.append
    with patch("select.select", return_value=([mock_cmd], [], [])):
        tgt.send_own("*1*1*31##")
    assert tgt.take_reply() == "-"
    assert reconnected == [20000]

    mock_cmd2 = MagicMock()
    mock_cmd2.fileno.return_value = 44
    mock_cmd2.recv.side_effect = [b""]
    tgt._connect_cmd_session = MagicMock(side_effect=OSError("reconnect fail"))
    tgt._cmd_sock = mock_cmd2
    with patch("select.select", return_value=([mock_cmd2], [], [])):
        tgt.send_own("*1*1*31##")
    assert tgt.take_reply() == "-"
    assert tgt._cmd_sock is None

    old_cmd = MagicMock()
    tgt._cmd_sock = old_cmd
    with (
        patch("socket.create_connection") as mock_conn,
        patch("oracle.qemu_target.recv_own_frame", return_value="*#*1##"),
    ):
        mock_conn.return_value = MagicMock()
        qemu_target.QemuTarget._connect_cmd_session(tgt, 20000)
    old_cmd.close.assert_called_once()


def test_pty_port_negative_fd():
    port = bus.PtyPort(-1)
    assert port.read() == b""
    port.write(b"something")


def test_bus_settle_with_on_poll():
    fake_port = MagicMock()
    fake_port.read.return_value = b""
    clock = bus.MonotonicClock()
    b = bus.Bus(fake_port, bus.LineFramer(), bus.Silent(), clock=clock)
    called = []

    def on_poll() -> bool:
        called.append(True)
        return False

    assert b.settle(quiet_ms=10.0, max_ms=50.0, on_poll=on_poll) is True
    assert called


def test_port_is_free():
    # Free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert qemu_target.port_is_free(port) is True

    # Busy port
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", 0))
    s2.listen(1)
    busy_port = s2.getsockname()[1]
    try:
        assert qemu_target.port_is_free(busy_port) is False
    finally:
        s2.close()


def test_wait_tcp_port_with_on_poll():
    poll_calls = []

    def on_poll() -> None:
        poll_calls.append(1)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert qemu_target.wait_tcp_port(port, timeout=1.0, on_poll=on_poll) is True
        assert poll_calls == [1]
    finally:
        s.close()


def test_read_ports_openserver_fallback(tmp_path):
    cfg = tmp_path / "stack.xml"
    cfg.write_text(
        "<cfg_stack><sw>"
        "<openserver><port>20005</port></openserver>"
        "<other><port_open>20010</port_open></other>"
        "</sw></cfg_stack>",
        encoding="ascii",
    )
    assert qemu_target.read_ports_from_stack_open(cfg) == {
        "openserver": 20005,
        "other": 20010,
    }


def test_qemu_target_requires_sandbox_isolation(monkeypatch, tmp_path):
    spec = _dummy_spec()
    monkeypatch.delenv("ORACLE_IN_SANDBOX", raising=False)
    monkeypatch.setattr(sandbox, "is_net_isolated", lambda: False)

    with pytest.raises(sandbox.SandboxError, match="requires network isolation"):
        qemu_target.QemuTarget(spec, tmp_path / "img.zip", tmp_path / "w")

    # sandboxed=False bypasses check
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", sandboxed=False
    )
    assert tgt.sandboxed is False


def test_qemu_target_restart_port_conflicts(monkeypatch, tmp_path):
    spec = _dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(), framer=bus.LineFramer(), responder=bus.Silent()
    )
    tgt = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w",
        staged_sysroot=tmp_path,
        bus_instance=fake_bus,
    )
    monkeypatch.setattr(tgt, "_copy_sysroot", lambda: None)
    monkeypatch.setattr(tgt, "_active_clients", lambda: {"bt_luci"})
    monkeypatch.setattr(
        qemu_target,
        "read_ports_from_stack_open",
        lambda p: {"scsserver": 20001, "bt_luci": 30001, "openserver": 20000},
    )

    # 1. scsserver port conflict
    monkeypatch.setattr(
        qemu_target,
        "port_is_free",
        lambda p: p != 20001,
    )
    with pytest.raises(RuntimeError, match="port 20001 is already in use"):
        tgt.restart()

    # 2. client port conflict
    monkeypatch.setattr(
        qemu_target,
        "port_is_free",
        lambda p: p != 30001,
    )
    monkeypatch.setattr(
        tgt,
        "_start_program",
        lambda name, dev, trace: MagicMock(poll=lambda: None),
    )
    monkeypatch.setattr(qemu_target, "wait_tcp_port", lambda p, **kw: True)
    with pytest.raises(RuntimeError, match="port 30001 is already in use"):
        tgt.restart()

    # 3. openserver port conflict
    monkeypatch.setattr(
        qemu_target,
        "port_is_free",
        lambda p: p != 20000,
    )
    with pytest.raises(RuntimeError, match="port 20000 is already in use"):
        tgt.restart()


def test_qemu_target_stop_closes_console_files(tmp_path):
    spec = _dummy_spec()
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    f_mock = MagicMock()
    f_mock.close.side_effect = OSError("disk error")
    tgt._console_files["prog"] = f_mock
    tgt._stop()
    assert tgt._console_files == {}
    f_mock.close.assert_called_once()


def test_qemu_target_send_own_unit_harness_and_early_exit():
    spec = _dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(), framer=bus.LineFramer(), responder=bus.Silent()
    )
    tgt = qemu_target.QemuTarget(
        spec,
        Path("/tmp/img.zip"),
        Path("/tmp/w"),
        harness="unit:bt_luci",
        staged_sysroot=Path("/tmp"),
        bus_instance=fake_bus,
    )
    tgt._running = True
    tgt._cmd_sock = None
    with pytest.raises(RuntimeError, match="has no OpenWebNet command session"):
        tgt.send_own("*1*1*31##")

    # Early exit when ACK received before loop ends
    mock_cmd = MagicMock()
    mock_cmd.fileno.return_value = 42
    mock_cmd.recv.return_value = b"*#*1##"
    tgt._cmd_sock = mock_cmd
    with patch("select.select", return_value=([mock_cmd], [], [])):
        tgt.send_own("*1*1*31##")
    assert tgt.take_reply() == "ack"


def test_is_net_isolated_branches(tmp_path, monkeypatch):
    # 1. /proc/net/route does not exist
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert sandbox.is_net_isolated() is False

    # 2. OSError reading route
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "read_text", MagicMock(side_effect=OSError("denied")))
    assert sandbox.is_net_isolated() is False

    # 3. Empty or only header -> True
    monkeypatch.setattr(
        Path, "read_text", MagicMock(return_value="Iface Destination Gateway\n")
    )
    assert sandbox.is_net_isolated() is True

    # 4. Multiple lines -> False
    monkeypatch.setattr(
        Path,
        "read_text",
        MagicMock(return_value="Iface Destination Gateway\neth0 0000 0000\n"),
    )
    assert sandbox.is_net_isolated() is False


def test_sandbox_wrap_with_binds_and_env(tmp_path):
    w = tmp_path / "work"
    cmd = sandbox.wrap(
        ["/bin/ls"],
        w.resolve(),
        ro_binds=(Path("/extra/ro"),),
        rw_binds=(Path("/extra/rw"),),
        env={"FOO": "BAR"},
    )
    assert "--ro-bind-try" in cmd
    assert "/extra/ro" in cmd
    assert "--bind" in cmd
    assert "/extra/rw" in cmd
    assert "--setenv" in cmd
    idx = cmd.index("FOO")
    assert cmd[idx + 1] == "BAR"


def test_validate_harness():
    spec = _dummy_spec()
    assert run.validate_harness(spec, "full") == "openserver"
    assert run.validate_harness(spec, "unit:bt_luci") == "bt_luci"

    with pytest.raises(SystemExit, match="invalid harness"):
        run.validate_harness(spec, "bad-harness")

    spec_no_open = target.TargetSpec(
        product="MH200N",
        version="010108",
        sysroot=("fs:", "app.zip!"),
        programs={"bt_luci": spec.programs["bt_luci"]},
        boundary={"status": "discovered"},
        runtime=spec.runtime,
    )
    with pytest.raises(SystemExit, match="requires 'openserver'"):
        run.validate_harness(spec_no_open, "full")

    with pytest.raises(SystemExit, match="not in target programs"):
        run.validate_harness(spec, "unit:missing_daemon")


def test_reexec_suite_in_sandbox(monkeypatch, tmp_path):
    spec = _dummy_spec()
    suite = cases.Suite(name="test", sha256="0" * 64, ordered=False, steps=())
    args = run.argparse.Namespace(
        target=str(tmp_path / "target.yaml"),
        suite=str(tmp_path / "test.cases"),
        image=str(tmp_path / "img.zip"),
        out=str(tmp_path / "out.tsv"),
        harness="full",
        reset="each",
        keep=str(tmp_path / "keep"),
        no_sandbox=False,
    )

    # Missing bwrap raises SystemExit
    monkeypatch.setattr(run.shutil, "which", lambda cmd: None)
    with pytest.raises(SystemExit, match="bwrap not found"):
        run.reexec_suite_in_sandbox(args, spec, suite)

    # Present bwrap calls subprocess.run
    monkeypatch.setattr(run.shutil, "which", lambda cmd: "/usr/bin/bwrap")
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(run.subprocess, "run", mock_run)
    code = run.reexec_suite_in_sandbox(args, spec, suite)
    assert code == 0
    assert mock_run.called

    # When keep is None, tempdir is used
    args.keep = None
    args.out = None
    code2 = run.reexec_suite_in_sandbox(args, spec, suite)
    assert code2 == 0


def test_qemu_target_connect_sessions_failure(monkeypatch, tmp_path):
    spec = _dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(), framer=bus.LineFramer(), responder=bus.Silent()
    )
    tgt = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w",
        staged_sysroot=tmp_path,
        bus_instance=fake_bus,
    )
    monkeypatch.setattr(tgt, "_copy_sysroot", lambda: None)
    monkeypatch.setattr(tgt, "_active_clients", lambda: {"bt_luci"})
    monkeypatch.setattr(
        qemu_target,
        "read_ports_from_stack_open",
        lambda p: {"scsserver": 20001, "bt_luci": 30001, "openserver": 20000},
    )
    monkeypatch.setattr(qemu_target, "port_is_free", lambda port: True)
    monkeypatch.setattr(
        tgt,
        "_start_program",
        lambda name, dev_map, trace_dir: MagicMock(poll=lambda: None),
    )
    monkeypatch.setattr(
        qemu_target, "wait_tcp_port", lambda port, timeout, on_poll: True
    )
    monkeypatch.setattr(
        tgt,
        "_connect_sessions",
        MagicMock(side_effect=ConnectionRefusedError("fail")),
    )
    stop_called = []
    orig_stop = tgt._stop
    monkeypatch.setattr(tgt, "_stop", lambda: stop_called.append(True) or orig_stop())

    with pytest.raises(ConnectionRefusedError, match="fail"):
        tgt.restart()
    assert len(stop_called) == 2


def test_cmd_suite_reexecs_when_not_sandboxed(monkeypatch, tmp_path):
    spec = _dummy_spec()
    cases_file = tmp_path / "lights.cases"
    cases_file.write_text("down *1*1*31##\n", encoding="ascii")
    args = run.argparse.Namespace(
        target=str(tmp_path / "target.yaml"),
        suite=str(cases_file),
        image=str(tmp_path / "img.zip"),
        out=str(tmp_path / "out.tsv"),
        harness="full",
        reset="each",
        keep=None,
        no_sandbox=False,
    )
    monkeypatch.delenv("ORACLE_IN_SANDBOX", raising=False)
    monkeypatch.setattr(sandbox, "is_net_isolated", lambda: False)
    monkeypatch.setattr(target, "load", lambda p, r: spec)
    reexec_called = []
    monkeypatch.setattr(
        run,
        "reexec_suite_in_sandbox",
        lambda a, s, su: reexec_called.append(True) or 42,
    )

    code = run.cmd_suite(args)
    assert code == 42
    assert reexec_called == [True]


def test_qemu_target_connect_sessions_auth_not_implemented(tmp_path):
    spec = _dummy_spec()
    spec = target.TargetSpec(
        product=spec.product,
        version=spec.version,
        sysroot=spec.sysroot,
        programs=spec.programs,
        boundary=spec.boundary,
        image_sha256=spec.image_sha256,
        runtime=spec.runtime,
        own_auth="openwebnet",
    )
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    with pytest.raises(
        NotImplementedError, match="authentication scheme 'openwebnet' not implemented"
    ):
        tgt._connect_sessions(20000)


def _f450_dummy_spec() -> target.TargetSpec:
    prog_bac = target.Program(
        "bacclient",
        "home/bticino/bin/bacclient",
        "4" * 64,
        "fs:",
        role="own_server",
        port=20000,
    )
    prog_scs = target.Program(
        "scsserver",
        "home/bticino/bin/scsserver",
        "2" * 64,
        "fs:",
        role="bus_server",
        port=20001,
    )
    return target.TargetSpec(
        product="F450",
        version="020010",
        sysroot=("fs:",),
        programs={
            "bacclient": prog_bac,
            "scsserver": prog_scs,
        },
        boundary={"status": "discovered", "bus": "pty", "own": "full"},
        image_sha256="e" * 64,
        runtime=target.Runtime(devices={"/dev/ttyS1": "pty"}),
    )


def test_soap_worker_success():
    stop_evt = threading.Event()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    t = threading.Thread(
        target=qemu_target._soap_worker, args=(srv, stop_evt), daemon=True
    )
    t.start()

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
            # getValue
            s.sendall(
                b"<SOAP-ENV:Envelope><SOAP-ENV:Body><ns:getValue/></SOAP-ENV:Body></SOAP-ENV:Envelope>"
            )
            resp = s.recv(4096).decode("latin-1")
            assert "getValueResponse" in resp
            assert '<result xsi:type="xsd:string">1</result>' in resp

            # setValue
            s.sendall(
                b"<SOAP-ENV:Envelope><SOAP-ENV:Body><ns:setValue/></SOAP-ENV:Body></SOAP-ENV:Envelope>"
            )
            resp = s.recv(4096).decode("latin-1")
            assert "setValueResponse" in resp
    finally:
        stop_evt.set()
        srv.close()
        t.join(timeout=2.0)


def test_soap_worker_branches():
    stop_evt = threading.Event()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    t = threading.Thread(
        target=qemu_target._soap_worker, args=(srv, stop_evt), daemon=True
    )
    t.start()

    try:
        # Client connects and immediately closes without sending data
        with socket.create_connection(("127.0.0.1", port), timeout=2.0):
            pass

        # Client connects and sends incomplete envelope then stops
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
            s.sendall(b"<SOAP-ENV:Envelope>")
            time.sleep(1.2)  # trigger conn timeout
    finally:
        stop_evt.set()
        srv.close()
        t.join(timeout=2.0)


def test_soap_worker_accept_timeout_and_error():
    stop_evt = threading.Event()
    srv = MagicMock()

    # 1. TimeoutError then stop_evt set
    def fake_accept():
        stop_evt.set()
        raise TimeoutError

    srv.accept.side_effect = fake_accept
    qemu_target._soap_worker(srv, stop_evt)

    # 2. OSError breaks immediately
    stop_evt.clear()
    srv.accept.side_effect = OSError("closed")
    qemu_target._soap_worker(srv, stop_evt)


def test_soap_worker_send_error():
    stop_evt = threading.Event()
    srv = MagicMock()
    fake_conn = MagicMock()
    fake_conn.recv.return_value = (
        b"<SOAP-ENV:Envelope><SOAP-ENV:Body><ns:getValue/></SOAP-ENV:Body>"
        b"</SOAP-ENV:Envelope>"
    )

    def fake_send(data):
        stop_evt.set()
        raise OSError("send failed")

    fake_conn.sendall.side_effect = fake_send
    srv.accept.return_value = (fake_conn, ("127.0.0.1", 12345))
    qemu_target._soap_worker(srv, stop_evt)
    assert fake_conn.close.called


def test_soap_worker_inner_stop():
    stop_evt = threading.Event()
    srv = MagicMock()
    fake_conn = MagicMock()

    def fake_recv(bufsize):
        stop_evt.set()
        return b"incomplete"

    fake_conn.recv.side_effect = fake_recv
    srv.accept.return_value = (fake_conn, ("127.0.0.1", 12345))
    qemu_target._soap_worker(srv, stop_evt)
    assert fake_conn.close.called


def test_qemu_target_f450_soap_lifecycle(monkeypatch, tmp_path):
    spec = _f450_dummy_spec()
    fake_bus = bus.Bus(
        port=MagicMock(), framer=bus.LineFramer(), responder=bus.Silent()
    )
    tgt = qemu_target.QemuTarget(
        spec,
        tmp_path / "img.zip",
        tmp_path / "w",
        staged_sysroot=tmp_path,
        bus_instance=fake_bus,
    )
    monkeypatch.setattr(tgt, "_copy_sysroot", lambda: None)
    monkeypatch.setattr(tgt, "_active_clients", set)
    monkeypatch.setattr(
        qemu_target,
        "read_ports_from_stack_open",
        lambda p: {"scsserver": 20001, "bacclient": 20000},
    )
    monkeypatch.setattr(qemu_target, "port_is_free", lambda port: True)
    monkeypatch.setattr(
        tgt,
        "_start_program",
        lambda name, dev_map, trace_dir: MagicMock(poll=lambda: None),
    )
    monkeypatch.setattr(
        qemu_target, "wait_tcp_port", lambda port, timeout, on_poll: True
    )
    monkeypatch.setattr(tgt, "_connect_sessions", lambda port: None)

    fake_sock = MagicMock()
    fake_sock.accept.side_effect = TimeoutError
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: fake_sock)

    tgt.restart()
    assert tgt._soap_server is fake_sock
    assert tgt._soap_thread is not None
    assert tgt._soap_stop is not None

    tgt._stop()
    assert tgt._soap_server is None
    assert tgt._soap_thread is None
    assert tgt._soap_stop is None
    assert fake_sock.close.called

    # Calling _stop again when everything is None
    tgt._stop()


def test_qemu_target_f450_uses_its_own_bacnet_service(tmp_path):
    """With the firmware's ebacgw on port 1234 there is no mock to start."""
    spec = _f450_dummy_spec()
    ebacgw = target.Program(
        "ebacgw",
        "home/bticino/bin/ebacgw",
        "3" * 64,
        "fs:",
        role="translator",
        port=qemu_target.SOAP_PORT,
    )
    spec = dataclasses.replace(spec, programs={**spec.programs, "ebacgw": ebacgw})
    tgt = qemu_target.QemuTarget(
        spec, tmp_path / "img.zip", tmp_path / "w", staged_sysroot=tmp_path
    )
    tgt._start_soap_server()
    assert tgt._soap_server is None
    assert tgt._soap_thread is None
