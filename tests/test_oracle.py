"""Unit tests for the phase 2 oracle scaffold -- no firmware, no qemu, no bwrap.

The driver runs against a fake Target, the bus against a queue Port and a
fake clock, the target spec against the committed MH200N manifest.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle import bus, cases, driver, record, target

# --- cases -------------------------------------------------------------------


def _suite(tmp_path, name, text):
    p = tmp_path / name
    p.write_bytes(text.encode("ascii"))
    return cases.load(p)


def test_cases_sort_independent_steps_and_skip_comments(tmp_path):
    s = _suite(
        tmp_path, "s.cases", "; c\ndown *1*1*31##\n\nup A8 31  a3\ndown *1*0*31##\n"
    )
    assert not s.ordered
    assert [(st.direction, st.input) for st in s.steps] == [
        ("down", "*1*0*31##"),
        ("down", "*1*1*31##"),
        ("up", "a8 31 a3"),
    ]


def test_cases_hash_ignores_crlf(tmp_path):
    a = _suite(tmp_path, "a.cases", "down *1*1*31##\n")
    b = _suite(tmp_path, "b.cases", "down *1*1*31##\r\n")
    assert a.sha256 == b.sha256


def test_cases_keep_sequence_order_and_allow_repeats(tmp_path):
    s = _suite(tmp_path, "s.seq", "down *1*1*31##\ndown *#1*31##\ndown *1*1*31##\n")
    assert s.ordered
    assert [st.input for st in s.steps] == ["*1*1*31##", "*#1*31##", "*1*1*31##"]


@pytest.mark.parametrize(
    "text",
    [
        "down *1*1*31##\ndown *1*1*31##\n",  # duplicate in an unordered suite
        "sideways *1*1*31##\n",  # unknown direction
        "up a8 3\n",  # not hex bytes
        "down *1*1 *31##\n",  # space inside OpenWebNet text
        "; only a comment\n",  # no steps
    ],
)
def test_cases_reject_bad_suites(tmp_path, text):
    with pytest.raises(cases.CaseError):
        _suite(tmp_path, "s.cases", text)


def test_cases_reject_bad_names(tmp_path):
    with pytest.raises(cases.CaseError):
        _suite(tmp_path, "Bad Name.cases", "down *1*1*31##\n")
    with pytest.raises(cases.CaseError):
        _suite(tmp_path, "s.txt", "down *1*1*31##\n")


def test_committed_suites_load():
    suites = sorted((ROOT / "oracle" / "cases").glob("*.*"))
    assert suites
    for p in suites:
        cases.load(p)


# --- record ------------------------------------------------------------------

HEADER = {
    "product": "MH200N",
    "version": "010108",
    "image_sha256": "e" * 64,
    "harness": "unit:bt_luci",
    "target_sha256": "1" * 64,
    "adapter": "pty-1",
    "reset": "each",
    "bus": "pty",
    "framer": "idle:20",
    "responder": "silent",
    "settle_ms": "300",
    "suite": "lights",
    "suite_sha256": "5" * 64,
    "oracle_version": "1",
}


def test_record_sorts_rows_and_is_stable():
    rows = [
        record.Row("up", "a8 a3", "-", "out", ("own:*1*0*31##",)),
        record.Row("down", "*1*1*31##", "ack", "out", ("bus:a8 31 a3", "bus:a5")),
        record.Row("down", "*1*0*31##", "nack", "silent"),
    ]
    text = record.render(HEADER, rows, ordered=False)
    assert text == record.render(HEADER, list(reversed(rows)), ordered=False)
    body = text.splitlines()[len(record.HEADER_KEYS) :]
    assert body == [
        "direction\tinput\treply\tverdict\toutput",
        "down\t*1*0*31##\tnack\tsilent\t-",
        "down\t*1*1*31##\tack\tout\tbus:a8 31 a3 | bus:a5",
        "up\ta8 a3\t-\tout\town:*1*0*31##",
    ]


def test_record_escapes_hostile_firmware_output():
    row = record.Row("up", "a8", "-", "out", ("own:*1\t9\n|é##",))
    cell = row.cells()[-1]
    assert "\t" not in cell
    assert "\n" not in cell
    assert "|" not in cell
    assert cell.isascii()


def test_record_rejects_incomplete_headers_and_bad_rows():
    with pytest.raises(ValueError, match="missing"):
        record.render(
            {k: v for k, v in HEADER.items() if k != "suite_sha256"}, [], ordered=False
        )
    with pytest.raises(ValueError, match="harness"):
        record.render({**HEADER, "harness": "unit:../x"}, [], ordered=False)
    with pytest.raises(ValueError, match="plain token"):
        record.render({**HEADER, "suite": "two words"}, [], ordered=False)
    row = record.Row("down", "x", "ack", "silent")
    with pytest.raises(ValueError, match="duplicate"):
        record.render(HEADER, [row, row], ordered=False)
    with pytest.raises(ValueError, match="needs at least one output"):
        record.Row("down", "x", "ack", "out")
    with pytest.raises(ValueError, match="cannot have outputs"):
        record.Row("down", "x", "ack", "silent", ("bus:a8",))
    for bad in (("sideways", "-", "out"), ("down", "maybe", "out"), ("up", "-", "?")):
        with pytest.raises(ValueError, match=r"direction|reply|verdict"):
            record.Row(bad[0], "x", bad[1], bad[2], ("bus:a8",))
    record.Row("down", "x", "-", "crash", ("bus:a8",))  # crash keeps partial output
    # a sequence keeps its order and may repeat a step
    seq = [
        record.Row("down", "b", "ack", "silent"),
        record.Row("down", "a", "-", "silent"),
    ]
    assert record.render(HEADER, [*seq, seq[0]], ordered=True).splitlines()[-3:] == [
        "down\tb\tack\tsilent\t-",
        "down\ta\t-\tsilent\t-",
        "down\tb\tack\tsilent\t-",
    ]


def test_record_roundtrips_header_and_result_path(tmp_path):
    p = tmp_path / record.result_path("MH200N", "010108", "unit:bt_luci", "lights")
    record.write(p, HEADER, [], ordered=False)
    assert p.as_posix().endswith("MH200N/010108/oracle/unit-bt_luci/lights.tsv")
    assert record.read_header(p) == HEADER


# --- bus ---------------------------------------------------------------------


class QueuePort:
    def __init__(self, script=None):
        self.script = list(script or [])  # successive read() results
        self.written = []

    def read(self):
        return self.script.pop(0) if self.script else b""

    def write(self, data):
        self.written.append(data)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now_ms(self):
        return self.t

    def sleep_ms(self, ms):
        self.t += ms


def test_delimited_framer_splits_and_keeps_strays():
    f = bus.DelimitedFramer()
    assert f.feed(bytes.fromhex("00 a8 31"), 0) == [b"\x00"]
    assert f.feed(bytes.fromhex("12 a3 a8 01 a3"), 0) == [
        bytes.fromhex("a8 31 12 a3"),
        bytes.fromhex("a8 01 a3"),
    ]
    assert f.feed(bytes.fromhex("a8 99"), 0) == []
    assert f.flush(0) == [bytes.fromhex("a8 99")]


def test_idle_gap_framer_cuts_on_silence():
    f = bus.IdleGapFramer(gap_ms=20)
    assert f.feed(b"\x01\x02", 0) == []
    assert f.feed(b"\x03", 10) == []
    assert f.flush(25) == []
    assert f.flush(30) == [b"\x01\x02\x03"]
    assert f.feed(b"\x04", 31) == []
    assert f.feed(b"\x05", 60) == [b"\x04"]


def test_bus_settles_logs_and_answers_with_responder():
    port = QueuePort([bytes.fromhex("a8 31 a3"), b"", b"", bytes.fromhex("a8 32 a3")])
    b = bus.Bus(port, bus.DelimitedFramer(), bus.AckAll(), FakeClock(), poll_ms=5)
    b.inject(b"\xa8\x00\xa3")
    mark = 1
    assert b.settle(quiet_ms=50, max_ms=1000) is True
    assert b.gateway_frames(mark) == [
        bytes.fromhex("a8 31 a3"),
        bytes.fromhex("a8 32 a3"),
    ]
    assert [f.source for f in b.log] == [
        bus.DRIVER,
        bus.GATEWAY,
        bus.DEVICE,
        bus.GATEWAY,
        bus.DEVICE,
    ]
    assert port.written == [b"\xa8\x00\xa3", b"\xa5", b"\xa5"]


def test_bus_times_out_on_a_chatty_firmware():
    port = QueuePort([b"\x01"] * 1000)
    b = bus.Bus(port, bus.IdleGapFramer(gap_ms=1), bus.Silent(), FakeClock(), poll_ms=5)
    assert b.settle(quiet_ms=50, max_ms=100) is False


# --- driver ------------------------------------------------------------------


class FakeTarget:
    """A pretend translator: switches reach the bus, '*9*...' crashes it."""

    def __init__(self):
        self.restarts = 0
        self._alive = True
        self._bus: list[bytes] = []
        self._own: list[str] = []
        self._reply = "-"

    def send_own(self, frame):
        if frame.startswith("*9*"):
            self._alive = False
            return
        if frame.startswith("*1*"):
            self._bus.append(bytes.fromhex("a8 31 a3"))
            self._reply = "ack"
        else:
            self._reply = "nack"

    def inject_bus(self, data):
        self._own.append("*1*1*31##")

    def settle(self):
        return True

    def take_reply(self):
        r, self._reply = self._reply, "-"
        return r

    def take_bus(self):
        out, self._bus = self._bus, []
        return out

    def take_own(self):
        out, self._own = self._own, []
        return out

    def alive(self):
        return self._alive

    def restart(self):
        self.restarts += 1
        self._alive = True
        self._own.append("*#*boot##")  # boot noise must not leak into a row


def test_driver_classifies_and_restarts_after_a_crash(tmp_path):
    s = _suite(
        tmp_path,
        "s.cases",
        "down *1*1*31##\ndown *2*1*31##\ndown *9*1##\nup a8 31 a3\n",
    )
    t = FakeTarget()
    rows = {(r.direction, r.input): r for r in driver.run_suite(t, s, restart_every=0)}
    assert rows[("down", "*1*1*31##")] == record.Row(
        "down", "*1*1*31##", "ack", "out", ("bus:a8 31 a3",)
    )
    assert rows[("down", "*2*1*31##")].verdict == "silent"
    assert rows[("down", "*2*1*31##")].reply == "nack"
    assert rows[("down", "*9*1##")].verdict == "crash"
    assert rows[("up", "a8 31 a3")] == record.Row(
        "up", "a8 31 a3", "-", "out", ("own:*1*1*31##",)
    )
    assert t.restarts == 2  # initial + after the crash


def test_driver_resets_before_every_step_by_default(tmp_path):
    s = _suite(tmp_path, "s.cases", "down *1*1*31##\ndown *2*1*31##\ndown *1*0*31##\n")
    t = FakeTarget()
    rows = driver.run_suite(t, s)
    assert t.restarts == 3
    assert all("own:*#*boot##" not in r.outputs for r in rows)


def test_driver_skips_the_rest_of_a_sequence_after_a_crash(tmp_path):
    s = _suite(tmp_path, "s.seq", "down *1*1*31##\ndown *9*1##\ndown *1*0*31##\n")
    rows = driver.run_suite(FakeTarget(), s)
    assert [r.verdict for r in rows] == ["out", "crash", "skipped"]


# --- target ------------------------------------------------------------------


def test_committed_mh200n_target_matches_the_manifest():
    spec = target.load(ROOT / "oracle/targets/MH200N/010108.yaml", ROOT / "results")
    assert set(spec.programs) == {
        "bt_processi",
        "openserver",
        "scsserver",
        "bt_luci",
        "bt_device",
    }
    assert spec.programs["bt_luci"].layer.endswith("btweb_app.zip!")
    assert spec.arch == "arm"
    assert spec.runtime.devices == {"/dev/ttyPIC": "pty"}
    assert spec.ready
    spec.require_ready()
    # every program has its discovery record, pinned to the same binary
    for name, prog in spec.programs.items():
        tsv = ROOT / f"results/MH200N/010108/oracle/boundary/{name}.tsv"
        header = dict(
            line[2:].split("=", 1)
            for line in tsv.read_text().splitlines()
            if line.startswith("# ")
        )
        assert header["target_sha256"] == prog.sha256
        assert header["image_sha256"] == spec.image_sha256


def _write_target(tmp_path, programs, boundary="status: pending", extra=""):
    results = tmp_path / "results"
    (results / "P" / "1").mkdir(parents=True)
    (results / "P" / "1" / "manifest.tsv").write_text(
        f"# product=P version=1\n# image_sha256={'e' * 64}\n"
        "path\ttype\tsize\tsha256\n"
        f"w.zip!fs:/bin/a\tELF/ARM/exec/32le/oabi\t1\t{'a' * 64}\n"
        f"w.zip!fs:/bin/f\tELF/ARM/exec/32le/oabi\t1\t{'f' * 64}\n"
        f"w.zip!app.zip!bin/a\tELF/ARM/exec/32le/oabi\t1\t{'b' * 64}\n"
        f"w.zip!app.zip!bin/m\tELF/MIPS/exec/32be\t1\t{'c' * 64}\n"
        f"w.zip!app.zip!bin/z\tELF/SPARC/exec/32be\t1\t{'d' * 64}\n"
    )
    spec = tmp_path / "targets" / "P" / "1.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        'product: P\nversion: "1"\nsysroot: ["w.zip!fs:", "w.zip!app.zip!"]\n'
        f"programs:\n{programs}\nboundary:\n  {boundary}\n{extra}"
    )
    return spec, results


def test_target_overlay_takes_the_last_layer(tmp_path):
    spec, results = _write_target(
        tmp_path, f'  a: {{path: bin/a, sha256: "{"b" * 64}"}}'
    )
    assert target.load(spec, results).programs["a"].layer == "w.zip!app.zip!"


@pytest.mark.parametrize(
    "programs",
    [
        f'  a: {{path: bin/a, sha256: "{"a" * 64}"}}',  # hash of the overridden layer
        f'  a: {{path: ../bin/a, sha256: "{"b" * 64}"}}',  # escapes the sysroot
        f'  a: {{path: bin/missing, sha256: "{"b" * 64}"}}',
        '  a: {path: bin/a, sha256: "nothex"}',
    ],
)
def test_target_rejects_unpinned_programs(tmp_path, programs):
    spec, results = _write_target(tmp_path, programs)
    with pytest.raises(target.TargetError):
        target.load(spec, results)


def test_target_validates_the_boundary_block(tmp_path):
    ok = f'  a: {{path: bin/a, sha256: "{"b" * 64}"}}'
    spec, results = _write_target(
        tmp_path, ok, "status: discovered\n  bus: pty\n  own: unit"
    )
    assert target.load(spec, results).ready
    spec, results = _write_target(
        tmp_path / "x", ok, "status: discovered\n  bus: preload\n  own: unit"
    )
    with pytest.raises(target.TargetError):
        target.load(spec, results)


def test_bus_get_framer():
    assert isinstance(bus.get_framer("line"), bus.LineFramer)
    f_idle = bus.get_framer("idle:25")
    assert isinstance(f_idle, bus.IdleGapFramer)
    assert f_idle.gap_ms == 25
    f_delim = bus.get_framer("delimited:a8:a3")
    assert isinstance(f_delim, bus.DelimitedFramer)
    assert f_delim.start == 0xA8
    assert f_delim.end == 0xA3

    # Error cases exercising all branch conditions
    for bad in (
        "unknown",
        "idle:xyz",  # ValueError
        "idle:1:2",  # len != 2
        "delimited:a8",  # len != 3
        "delimited:zz:a3",  # ValueError
        "delimited:a8:a3:extra",  # len != 3
    ):
        with pytest.raises(ValueError, match="unknown framer"):
            bus.get_framer(bad)


def test_bus_get_responder():
    assert isinstance(bus.get_responder("silent"), bus.Silent)
    r_ack = bus.get_responder("ack:a5")
    assert isinstance(r_ack, bus.AckAll)
    assert r_ack.ack == b"\xa5"
    r_pic = bus.get_responder("pic", version="010108")
    assert isinstance(r_pic, bus.PicResponder)
    assert r_pic.version == "010108"
    assert isinstance(bus.get_responder("pic-mh200n"), bus.PicResponder)

    # Error cases exercising all branch conditions
    for bad in (
        "unknown",
        "ack:xyz",  # ValueError
        "ack:a5",  # valid, not bad (just checking bad list)
        "ack:a5:extra",  # len != 2
    ):
        if bad == "ack:a5":
            continue
        with pytest.raises(ValueError, match="unknown responder"):
            bus.get_responder(bad)


def test_target_roles_and_ports():
    assert target.default_role("openserver") == "own_server"
    assert target.default_role("bin/openserver") == "own_server"
    assert target.default_role("scsserver") == "bus_server"
    assert target.default_role("bin/scsserver") == "bus_server"
    assert target.default_role("bt_processi") == "supervisor"
    assert target.default_role("bt_luci") == "translator"

    p = target.Program("bt_luci", "bin/bt_luci", "a" * 64, "fs:")
    assert p.role == "translator"

    p_sup = target.Program("bt_processi", "bin/bt_processi", "a" * 64, "fs:")
    assert p_sup.role == "supervisor"

    with pytest.raises(target.TargetError, match="invalid role 'bogus'"):
        target.Program("p", "bin/p", "a" * 64, "fs:", role="bogus")

    with pytest.raises(
        target.TargetError, match="port must be an integer between 1 and 65535"
    ):
        target.Program("p", "bin/p", "a" * 64, "fs:", port=0)

    with pytest.raises(
        target.TargetError, match="port must be an integer between 1 and 65535"
    ):
        target.Program("p", "bin/p", "a" * 64, "fs:", port=True)

    with pytest.raises(
        target.TargetError, match="port must be an integer between 1 and 65535"
    ):
        target.Program("p", "bin/p", "a" * 64, "fs:", port="80")  # type: ignore[arg-type]


def test_target_spec_bus_and_auth_validation(tmp_path):
    ok_prog = (
        f'  a: {{path: bin/a, sha256: "{"b" * 64}", role: translator, port: 8080}}'
    )

    # Valid extra fields
    spec, results = _write_target(
        tmp_path,
        ok_prog,
        extra=(
            "bus_device: /dev/ttyS0\n"
            "bus_framer: 'idle:30'\n"
            "bus_responder: 'ack:a5'\n"
            "own_auth: none\n"
            "runtime:\n"
            "  stack_config: cfg/stack.xml\n"
            "  kernel_release: '5.10.35'\n"
        ),
    )
    loaded = target.load(spec, results)
    assert loaded.bus_device == "/dev/ttyS0"
    assert loaded.bus_framer == "idle:30"
    assert loaded.bus_responder == "ack:a5"
    assert loaded.own_auth == "none"
    assert loaded.runtime.stack_config == "cfg/stack.xml"
    assert loaded.runtime.kernel_release == "5.10.35"
    assert loaded.programs["a"].role == "translator"
    assert loaded.programs["a"].port == 8080

    # Invalid program role in yaml
    bad_role = f'  a: {{path: bin/a, sha256: "{"b" * 64}", role: badrole}}'
    spec, results = _write_target(tmp_path / "t1", bad_role)
    with pytest.raises(target.TargetError, match="invalid role"):
        target.load(spec, results)

    # Invalid bus_device
    spec, results = _write_target(
        tmp_path / "t2", ok_prog, extra="bus_device: rel/path\n"
    )
    with pytest.raises(target.TargetError, match="bus_device"):
        target.load(spec, results)

    # Invalid bus_framer
    spec, results = _write_target(
        tmp_path / "t3", ok_prog, extra="bus_framer: unknown\n"
    )
    with pytest.raises(target.TargetError, match="bus_framer"):
        target.load(spec, results)

    # Invalid bus_responder
    spec, results = _write_target(
        tmp_path / "t4", ok_prog, extra="bus_responder: unknown\n"
    )
    with pytest.raises(target.TargetError, match="bus_responder"):
        target.load(spec, results)

    # Invalid own_auth
    spec, results = _write_target(
        tmp_path / "t5", ok_prog, extra="own_auth: unsupported\n"
    )
    with pytest.raises(target.TargetError, match="own_auth"):
        target.load(spec, results)

    # Invalid runtime stack_config
    spec, results = _write_target(
        tmp_path / "t6", ok_prog, extra="runtime:\n  stack_config: 123\n"
    )
    with pytest.raises(
        target.TargetError, match=r"runtime\.stack_config must be a string"
    ):
        target.load(spec, results)

    # Invalid runtime kernel_release
    spec, results = _write_target(
        tmp_path / "t7", ok_prog, extra="runtime:\n  kernel_release: 123\n"
    )
    with pytest.raises(
        target.TargetError, match=r"runtime\.kernel_release must be a string"
    ):
        target.load(spec, results)


# Sandbox, discovery, staging and the command line: test_oracle_discovery.py
