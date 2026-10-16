"""Unit tests for tools/live_probe.py (read-only physical gateway probe)."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import live_probe


class FakeSocket:
    """In-memory mock socket for testing LiveProbeClient without network I/O."""

    def __init__(self, rx_chunks: list[bytes | Exception]) -> None:
        self.rx_chunks = list(rx_chunks)
        self.tx_data = b""
        self.closed = False

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def sendall(self, data: bytes) -> None:
        self.tx_data += data

    def recv(self, _bufsize: int) -> bytes:
        if not self.rx_chunks:
            return b""
        item = self.rx_chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_is_readonly_frame() -> None:
    # Allowed status & dimension reads
    assert live_probe.is_readonly_frame("*#1*0##")
    assert live_probe.is_readonly_frame("*#1*21##")
    assert live_probe.is_readonly_frame("*#4*1*0##")
    assert live_probe.is_readonly_frame("*#4*#1#1*14##")
    assert live_probe.is_readonly_frame("*#13**15##")
    assert live_probe.is_readonly_frame("*#22*3#1#1*1##")
    assert live_probe.is_readonly_frame("*#18*51*511#1##")

    # Rejected writes, commands, and session frames
    assert not live_probe.is_readonly_frame("*1*1*21##")
    assert not live_probe.is_readonly_frame("*#1*31*#1*100*0##")
    assert not live_probe.is_readonly_frame("*#13**#0*12*00*00*00##")
    assert not live_probe.is_readonly_frame("*#*1##")
    assert not live_probe.is_readonly_frame("*99*0##")
    assert not live_probe.is_readonly_frame("a8 21 00 12 01 22 a3")


def test_load_probe_steps(tmp_path: Path) -> None:
    canary = ROOT / "oracle" / "canary" / "canary-live.cases"
    suite, steps = live_probe.load_probe_steps(canary)
    assert suite.name == "canary-live"
    assert len(steps) == 11

    mixed = tmp_path / "mixed.cases"
    mixed.write_text(
        "down *#1*21##\ndown *1*1*21##\nup a8 21 a3\n",
        encoding="ascii",
    )
    with pytest.raises(ValueError, match="refused non-read-only"):
        live_probe.load_probe_steps(mixed, skip_writes=False)

    _s2, safe = live_probe.load_probe_steps(mixed, skip_writes=True)
    assert len(safe) == 1
    assert safe[0].input == "*#1*21##"

    writes_only = tmp_path / "writes.cases"
    writes_only.write_text("down *1*1*21##\n", encoding="ascii")
    with pytest.raises(ValueError, match="no read-only steps"):
        live_probe.load_probe_steps(writes_only, skip_writes=True)


def test_compute_open_hash() -> None:
    # Exercise all digits 0..9 in nonce
    h = live_probe.compute_open_hash("12345", "01234567890")
    assert h.isdigit()
    # Nonce starting with non-zero and unknown char fallback
    with pytest.raises(ValueError, match="numeric"):
        live_probe.compute_open_hash("abc", "123")
    with pytest.raises(ValueError, match="numeric"):
        live_probe.compute_open_hash("123", "abc")


def test_parse_model_and_version_replies() -> None:
    assert live_probe._parse_model_reply(["*#13**15*11##"]) == "MH200N"
    assert live_probe._parse_model_reply(["*#13**15*999##"]) == "MODEL_999"
    assert live_probe._parse_model_reply(["*1*1*21##"]) is None

    assert live_probe._parse_version_reply(["*#13**16*01*01*08##"]) == "010108"
    assert live_probe._parse_version_reply(["*1*1*21##"]) is None


def test_default_socket_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    def fake_create(addr: tuple[str, int], timeout: float) -> str:
        called.append((addr, timeout))
        return "sock"

    monkeypatch.setattr(socket, "create_connection", fake_create)
    assert live_probe._default_socket_factory(("127.0.0.1", 20000), 1.5) == "sock"
    assert called == [(("127.0.0.1", 20000), 1.5)]


def test_live_probe_client_sessions_and_errors() -> None:
    # 1. Refuses non-readonly frame in query_frame directly
    client_bad = live_probe.LiveProbeClient("127.0.0.1")
    with pytest.raises(ValueError, match="refused non-read-only"):
        client_bad.query_frame("*1*1*21##")

    # 2. Bad greeting
    client_greet = live_probe.LiveProbeClient(
        "127.0.0.1",
        socket_factory=lambda _a, _t: FakeSocket([b"*#*0##"]),  # type: ignore[return-value]
    )
    with pytest.raises(ConnectionError, match="unexpected gateway greeting"):
        client_greet.query_frame("*#1*21##")

    # 3. Password challenge without password configured
    client_nopw = live_probe.LiveProbeClient(
        "127.0.0.1",
        socket_factory=lambda _a, _t: FakeSocket([b"*#*1##*#12345##"]),  # type: ignore[return-value]
    )
    with pytest.raises(PermissionError, match="--password was not provided"):
        client_nopw.query_frame("*#1*21##")

    # 4. Password challenge rejected by gateway
    client_badpw = live_probe.LiveProbeClient(
        "127.0.0.1",
        password="12345",
        socket_factory=lambda _a, _t: FakeSocket([b"*#*1##", b"*#12345##", b"*#*0##"]),  # type: ignore[return-value]
    )
    with pytest.raises(PermissionError, match="gateway rejected password"):
        client_badpw.query_frame("*#1*21##")

    # 5. Password challenge accepted + NACK response
    client_okpw = live_probe.LiveProbeClient(
        "127.0.0.1",
        password="12345",
        socket_factory=lambda _a, _t: FakeSocket(
            [b"*#*1##", b"*#12345##", b"*#*1##", b"*#*0##"]
        ),  # type: ignore[return-value]
    )
    reply, verdict, emitted = client_okpw.query_frame("*#1*21##")
    assert (reply, verdict, emitted) == ("nack", "silent", [])

    # 6. Unexpected handshake response
    client_weird = live_probe.LiveProbeClient(
        "127.0.0.1",
        socket_factory=lambda _a, _t: FakeSocket([b"*#*1##", b"*#*0##"]),  # type: ignore[return-value]
    )
    with pytest.raises(ConnectionError, match="handshake failed"):
        client_weird.query_frame("*#1*21##")

    # 7. Timeout / EOF during query_frame
    client_timeout = live_probe.LiveProbeClient(
        "127.0.0.1",
        socket_factory=lambda _a, _t: FakeSocket(
            [b"*#*1##", b"*#*1##", b"*1*1*21##", TimeoutError("timed out")]
        ),  # type: ignore[return-value]
    )
    r_to, v_to, em_to = client_timeout.query_frame("*#1*21##")
    assert (r_to, v_to, em_to) == ("-", "out", ["*1*1*21##"])


def test_detect_identity_and_run_probe(tmp_path: Path) -> None:
    suite_file = tmp_path / "probe.cases"
    suite_file.write_text("down *#1*74##\ndown *#1*21##\n", encoding="ascii")

    sessions = [
        # 1. detect model (*#13**15##)
        [b"*#*1##*#*1##*#13**15*11##*#*1##"],
        # 2. detect version (*#13**16##)
        [b"*#*1##*#*1##*#13**16*01*01*08##*#*1##"],
        # 3. *#1*21## (sorted first)
        [b"*#*1##*#*1##*#*0##"],
        # 4. *#1*74## (sorted second)
        [b"*#*1##*#*1##*1*19*74##*#*1##"],
    ]

    def factory(_addr: tuple[str, int], _timeout: float) -> FakeSocket:
        return FakeSocket(sessions.pop(0))

    client = live_probe.LiveProbeClient("192.0.2.1", socket_factory=factory)  # type: ignore[arg-type]
    tsv = live_probe.run_probe_tsv(client, suite_file)
    assert "# product=MH200N" in tsv
    assert "# version=010108" in tsv
    assert "down\t*#1*21##\tnack\tsilent\t-" in tsv
    assert "down\t*#1*74##\tack\tout\town:*1*19*74##" in tsv

    # Test detect_identity when queries return no model/version or raise OSError
    client_empty_id = live_probe.LiveProbeClient(
        "192.0.2.1",
        socket_factory=lambda _a, _t: FakeSocket([b"*#*1##*#*1##*#*0##"]),  # type: ignore[return-value]
    )
    assert client_empty_id.detect_identity() == ("UNKNOWN", "000000")

    def err_factory(_addr: tuple[str, int], _timeout: float) -> FakeSocket:
        raise OSError("network unreachable")

    client_err = live_probe.LiveProbeClient("192.0.2.1", socket_factory=err_factory)  # type: ignore[arg-type]
    assert client_err.detect_identity() == ("UNKNOWN", "000000")


def test_main_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 1. --validate-only on default canary suite
    assert live_probe.main(["--validate-only"]) == 0
    assert "read-only steps verified" in capsys.readouterr().out

    # 2. Invalid suite file fails cleanly with exit code 1
    bad_suite = tmp_path / "bad.cases"
    bad_suite.write_text("down *1*1*21##\n", encoding="ascii")
    assert live_probe.main(["--suite", str(bad_suite), "--validate-only"]) == 1
    assert "ERROR" in capsys.readouterr().err

    # 3. Missing --host when not --validate-only
    with pytest.raises(SystemExit):
        live_probe.main(["--suite", str(ROOT / "oracle/canary/canary-live.cases")])

    # 4. Full execution writing to -o file and stdout with --product/--version
    one_step = tmp_path / "one.cases"
    one_step.write_text("down *#1*21##\n", encoding="ascii")
    out_tsv = tmp_path / "live_out.tsv"

    def ok_factory(_addr: tuple[str, int], _timeout: float) -> FakeSocket:
        return FakeSocket([b"*#*1##*#*1##*1*1*21##*#*1##"])

    rc = live_probe.main(
        [
            "--host",
            "192.0.2.1",
            "--suite",
            str(one_step),
            "--product",
            "MH200N",
            "--version",
            "010108",
            "-o",
            str(out_tsv),
        ],
        socket_factory=ok_factory,  # type: ignore[arg-type]
    )
    assert rc == 0
    assert out_tsv.is_file()
    assert "own:*1*1*21##" in out_tsv.read_text(encoding="ascii")

    rc_stdout = live_probe.main(
        [
            "--host",
            "192.0.2.1",
            "--suite",
            str(one_step),
            "--product",
            "MH200N",
            "--version",
            "010108",
        ],
        socket_factory=ok_factory,  # type: ignore[arg-type]
    )
    assert rc_stdout == 0
    assert "own:*1*1*21##" in capsys.readouterr().out


def test_nonzero_nonce_and_eof() -> None:
    # Ensure compute_open_hash covers nonce starting with non-'0' digit followed by '0'
    res = live_probe.compute_open_hash("12345", "10")
    assert res.isdigit()

    # Ensure EOF (b"") in _recv_until_delimiter returns None cleanly
    def eof_factory(_addr: tuple[str, int], _timeout: float) -> FakeSocket:
        return FakeSocket([b""])

    client = live_probe.LiveProbeClient("192.0.2.1", socket_factory=eof_factory)  # type: ignore[arg-type]
    with pytest.raises(ConnectionError, match="unexpected gateway greeting: None"):
        client.query_frame("*#13**15##")
