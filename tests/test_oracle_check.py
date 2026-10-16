"""Unit tests for tools/check.py (OWND parser validation and live evidence checks)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

if sys.modules.get("OWNd") is None:
    try:
        import OWNd  # noqa: F401
    except ModuleNotFoundError:
        import types

        class OWNLightingEvent:
            pass

        class OWNEvent:
            pass

        class MockOWNMessage:
            @staticmethod
            def parse(frame: str) -> object | None:
                if frame in ("*1*1*31##", "*1*19*74##"):
                    return OWNLightingEvent()
                if frame == "*#1001*74*11*111110111111111111110111##":
                    return OWNEvent()
                return None

        ownd_mod = types.ModuleType("OWNd")
        ownd_mod.__version__ = "test-mock"
        ownd_msg_mod = types.ModuleType("OWNd.message")
        ownd_msg_mod.OWNMessage = MockOWNMessage  # type: ignore[attr-defined]
        ownd_mod.message = ownd_msg_mod  # type: ignore[attr-defined]
        sys.modules["OWNd"] = ownd_mod
        sys.modules["OWNd.message"] = ownd_msg_mod

import check
from check import EvidenceItem


def test_parse_ownd_frame():
    assert check.parse_ownd_frame("*1*1*31##") == "OWNLightingEvent"
    assert (
        check.parse_ownd_frame("*#1001*74*11*111110111111111111110111##") == "OWNEvent"
    )
    assert check.parse_ownd_frame("*999*foo##") == "unparsed"

    with patch("check.OWNMessage.parse", side_effect=RuntimeError("fail")):
        assert check.parse_ownd_frame("*1*1*31##") == "error:RuntimeError"


def test_parse_ownd_column():
    assert check.parse_ownd_column("-") == "-"
    assert check.parse_ownd_column("") == "-"
    assert check.parse_ownd_column("bus:24 30 33 0d") == "-"
    assert check.parse_ownd_column("own:*1*1*31##") == "OWNLightingEvent"
    assert (
        check.parse_ownd_column(
            "own:*1*19*74## | own:*#1001*74*11*111110111111111111110111##"
        )
        == "OWNLightingEvent | OWNEvent"
    )
    assert check.parse_ownd_column("bus:24 30 0d | own:*1*1*31##") == "OWNLightingEvent"


def test_extract_own_frames():
    assert check.extract_own_frames("-") == []
    assert check.extract_own_frames("") == []
    assert check.extract_own_frames("bus:24 30 33 0d") == []
    assert check.extract_own_frames("own:*1*1*31##") == ["*1*1*31##"]
    assert check.extract_own_frames("bus:24 | own:*1*1*31## | own:*1*19*74##") == [
        "*1*1*31##",
        "*1*19*74##",
    ]


def test_normalize_version_and_models():
    assert check.normalize_version(None) is None
    assert check.normalize_version("") is None
    assert check.normalize_version("020102") == (2, 1, 2)
    assert check.normalize_version("2.1.0") == (2, 1, 0)
    assert check.normalize_version("v2-beta") is None

    assert check._models_for_gateway(None) == ()
    assert check._models_for_gateway("MH200") == ("MH200", "MH200N")
    assert check._models_for_gateway("MH200N") == ("MH200N", "MH200")
    assert check._models_for_gateway("H4890") == ("H4890", "H4684")
    assert check._models_for_gateway("H4684") == ("H4684", "H4890")
    assert check._models_for_gateway("MyHomeServer1") == ("MyHomeServer1",)


def test_load_evidence_and_installations(tmp_path: Path):
    assert check.load_evidence(tmp_path / "nonexistent") == []

    # Create installations.json
    inst_data = {
        "gateways": [
            {"gateway_id": "GW-1", "model": "MH200", "firmware_version": "2.1.0"},
            {"gateway_id": "GW-NO-FW", "model": "MH200N"},
            "not-a-dict",
        ]
    }
    (tmp_path / "installations.json").write_text(
        json.dumps(inst_data), encoding="utf-8"
    )

    # Valid evidence folder using gateway_id from installations.json
    cap_dir = tmp_path / "cap-1"
    cap_dir.mkdir()
    manifest = {
        "evidence_id": "EVID-TEST-1",
        "gateway_id": "GW-1",
    }
    (cap_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    frames = [
        {"dir": "tx", "raw": "*#1*74##"},
        {"dir": "rx", "raw": "*1*19*74##"},
        {"dir": "rx", "raw": "*#1001*74*11*111110111111111111110111##"},
        {"dir": "rx", "raw": "*#*1##"},
        {"dir": "tx", "raw": "*#1*99*1##"},
        {"dir": "rx", "raw": "*#*0##"},
        {"dir": "other", "raw": "ignored"},
        {"dir": "tx", "raw": ""},  # empty raw ignored
    ]
    (cap_dir / "frames.jsonl").write_text(
        "\n".join(json.dumps(f) for f in frames) + "\n\n", encoding="utf-8"
    )

    # Second folder with explicit environment gateway_firmware + GW-NO-FW
    cap_dir2 = tmp_path / "cap-2"
    cap_dir2.mkdir()
    (cap_dir2 / "manifest.json").write_text(
        json.dumps(
            {
                "evidence_id": "EVID-2",
                "gateway_id": "GW-NO-FW",
                "environment": {"gateway_firmware": "1.1.8"},
            }
        ),
        encoding="utf-8",
    )
    (cap_dir2 / "frames.jsonl").write_text(
        json.dumps({"dir": "tx", "raw": "*#1*21##"}) + "\n", encoding="utf-8"
    )

    # Incomplete folder
    (tmp_path / "cap-incomplete").mkdir()

    # Plain file in evidence dir
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    items = check.load_evidence(tmp_path)
    assert len(items) == 3
    assert items[0].evidence_id == "EVID-TEST-1"
    assert items[0].exact_model == "MH200"
    assert items[0].gateway_version == "2.1.0"
    assert items[0].rx_reply == "ack"
    assert items[0].tx_input == "*#1*74##"
    assert items[0].rx_outputs == (
        "*1*19*74##",
        "*#1001*74*11*111110111111111111110111##",
    )
    assert items[1].tx_input == "*#1*99*1##"
    assert items[1].rx_reply == "nack"
    assert items[2].exact_model == "MH200N"
    assert items[2].gateway_version == "1.1.8"

    # Folder with only rx frames (tx_input remains None)
    cap_rx = tmp_path / "cap-rx-only"
    cap_rx.mkdir()
    (cap_rx / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cap_rx / "frames.jsonl").write_text(
        json.dumps({"dir": "rx", "raw": "*1*1*31##"}), encoding="utf-8"
    )
    assert len(check.load_evidence(tmp_path)) == 3

    # Corrupt, missing, and non-dict installations.json
    (tmp_path / "installations.json").unlink()
    assert check._load_installations_map(tmp_path) == {}
    # Also test _load_evidence_item when gw_model is missing and installations
    # is empty or gateway_id is not in installations
    assert len(check._load_evidence_item(cap_dir2, {})) == 1
    other_inst = {"OTHER-GW": ("F454", "020051")}
    assert len(check._load_evidence_item(cap_dir2, other_inst)) == 1
    (tmp_path / "installations.json").write_text("{corrupt", encoding="utf-8")
    assert check._load_installations_map(tmp_path) == {}
    (tmp_path / "installations.json").write_text("[]", encoding="utf-8")
    assert check._load_installations_map(tmp_path) == {}
    (tmp_path / "installations.json").write_text(
        '{"gateways": "bad"}', encoding="utf-8"
    )
    assert check._load_installations_map(tmp_path) == {}


def test_load_trace_evidence_and_live_tsv(tmp_path: Path):
    assert check.load_trace_evidence(tmp_path / "missing") == []
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()

    # 1. Direct frames format
    (traces_dir / "t1.json").write_text(
        json.dumps(
            {
                "gateway": {"model": "MH200N", "firmware": "010108"},
                "frames": [
                    {"direction": "tx", "raw": "*1*1*21##"},
                    {"direction": "rx", "raw": "*#*1##"},
                    {"direction": "other", "raw": "*#*1##"},
                    "not-a-dict",
                ],
            }
        ),
        encoding="utf-8",
    )
    # 2. Home Assistant diagnostic nested format
    (traces_dir / "t2.json").write_text(
        json.dumps(
            {
                "data": {
                    "gateway_profile": {
                        "model_name": "MyHomeServer1",
                        "firmware_version": "028206",
                    },
                    "bus_monitor": {
                        "recent_frames": [
                            {"direction": "tx", "raw": "*#1*21##"},
                            {"direction": "rx", "raw": "*1*1*21##"},
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    # 3. Invalid / non-matching JSON files
    (traces_dir / "corrupt.json").write_text("{bad", encoding="utf-8")
    (traces_dir / "list.json").write_text("[]", encoding="utf-8")
    (traces_dir / "no_frames.json").write_text('{"data": "none"}', encoding="utf-8")
    (traces_dir / "no_bm.json").write_text(
        '{"gateway": {"model": "X"}, '
        '"data": {"gateway_profile": "bad", "bus_monitor": "bad"}}',
        encoding="utf-8",
    )

    trace_items = check.load_trace_evidence(traces_dir)
    assert len(trace_items) == 2

    # Test load_live_tsv
    assert check.load_live_tsv(tmp_path / "missing.tsv") == []
    ltsv = tmp_path / "live.tsv"
    ltsv.write_text(
        "# comment without equals\n"
        "# product=MH200N\n"
        "# version=010108\n"
        "# suite=canary\n"
        "\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*#1*74##\tack\tout\town:*1*19*74##\n"
        "up\ta8 21\t-\tout\t-\n"
        "short\tline\n",
        encoding="utf-8",
    )
    live_items = check.load_live_tsv(ltsv)
    assert len(live_items) == 1
    assert live_items[0].evidence_id == "LIVE-MH200N-canary"
    assert live_items[0].rx_reply == "ack"


def test_find_default_evidence_dir(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    assert check.find_default_evidence_dir(root) is None

    enc_ev = tmp_path / "OpenWebNet-Encyclopedia" / "evidence"
    enc_ev.mkdir(parents=True)
    assert check.find_default_evidence_dir(root) == enc_ev


def test_match_live_and_cross_labels():
    item = EvidenceItem(
        evidence_id="EVID-1",
        gateway_models=("MH200", "MH200N"),
        tx_input="*#1*74##",
        rx_outputs=("*1*19*74##",),
        exact_model="MH200",
        gateway_version="2.1.0",
        rx_reply="ack",
    )

    # Non-down direction is unchecked
    assert check.match_live("up", "*#1*74##", "own:*1*19*74##", [item]) == "unchecked"

    # Incompatible product is unchecked
    assert (
        check.match_live("down", "*#1*74##", "own:*1*19*74##", [item], product="F454")
        == "unchecked"
    )

    # Cross-product match (MH200@2.1.0 vs MH200N@010108) -> [cross:MH200@2.1.0]
    assert (
        check.match_live(
            "down",
            "*#1*74##",
            "own:*1*19*74##",
            [item],
            product="MH200N",
            version="010108",
            reply="ack",
        )
        == "agree EVID-1 [cross:MH200@2.1.0]"
    )

    # Exact product+version match (MH200@2.1.0 vs MH200@020100) -> no [cross:...]
    assert (
        check.match_live(
            "down",
            "*#1*74##",
            "own:*1*19*74##",
            [item],
            product="MH200",
            version="020100",
            reply="ack",
        )
        == "agree EVID-1"
    )

    # agree_bus when oracle emitted bus frames only (verdict == "out")
    item_no_rx = EvidenceItem(
        evidence_id="EVID-BUS",
        gateway_models=("MH200N",),
        tx_input="*1*1*21##",
        rx_outputs=("*1*1*21##",),
        exact_model="MH200N",
        gateway_version=None,
        rx_reply="ack",
    )
    assert (
        check.match_live(
            "down",
            "*1*1*21##",
            "bus:a8 21",
            [item_no_rx],
            product="MH200N",
            version="010108",
            verdict="out",
            reply="ack",
        )
        == "agree_bus EVID-BUS"
    )

    # Reply mismatch (oracle nack vs live ack) -> diverge
    assert (
        check.match_live(
            "down",
            "*1*1*21##",
            "-",
            [item_no_rx],
            product="MH200N",
            version="010108",
            verdict="silent",
            reply="nack",
        )
        == "diverge EVID-BUS"
    )

    # Divergent output -> diverge
    assert (
        check.match_live("down", "*#1*74##", "own:*1*0*74##", [item], product="MH200N")
        == "diverge EVID-1 [cross:MH200@2.1.0]"
    )

    # verdict == "crash" -> never agree or agree_bus, always diverge
    assert (
        check.match_live(
            "down",
            "*#1*74##",
            "own:*1*19*74##",
            [item],
            product="MH200",
            version="020100",
            verdict="crash",
            reply="ack",
        )
        == "diverge EVID-1"
    )

    # Unmatched input -> unchecked
    assert (
        check.match_live("down", "*#1*99##", "own:*1*99##", [item], product="MH200N")
        == "unchecked"
    )


def test_check_path_for_oracle_tsv():
    p1 = Path("results/MH200N/010108/oracle/full/lights-level.tsv")
    assert check.check_path_for_oracle_tsv(p1) == Path(
        "results/MH200N/010108/checks/lights-level.tsv"
    )

    p2 = Path("results/MH200N/010108/oracle/unit-bt_luci/diag.tsv")
    assert check.check_path_for_oracle_tsv(p2) == Path(
        "results/MH200N/010108/checks/unit-bt_luci/diag.tsv"
    )

    p3 = Path("somewhere/else/test.tsv")
    assert check.check_path_for_oracle_tsv(p3) == Path(
        "somewhere/else/checked_test.tsv"
    )


def test_check_content_validation():
    with pytest.raises(ValueError, match="no column header"):
        check.check_content("# product=MH200N\n")

    with pytest.raises(ValueError, match="unexpected columns"):
        check.check_content("foo\tbar\tbaz\n")


def test_check_content_and_process_tsv(tmp_path: Path):
    tsv_content = (
        "# plain comment without equals\n"
        "# product=MH200N\n"
        "# version=010108\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*#1*74##\tack\tout\town:*1*19*74##\n"
        "\n"
        "down\t*1*1*31##\tack\tout\tbus:24 30 33 0d\n"
        "down\t*1*0*31##\tnack\tsilent\t-\n"
        "short_line\n"
    )

    item = EvidenceItem(
        evidence_id="EVID-WHAT19",
        gateway_models=("MH200", "MH200N"),
        tx_input="*#1*74##",
        rx_outputs=("*1*19*74##",),
    )

    out_text = check.check_content(tsv_content, evidence_items=[item])
    lines = out_text.splitlines()

    assert "# ownd_version=" in out_text
    assert lines[4] == "direction\tinput\treply\tverdict\toutput\townd\tlive"
    assert lines[5] == (
        "down\t*#1*74##\tack\tout\town:*1*19*74##\tOWNLightingEvent\tagree EVID-WHAT19"
    )

    assert lines[6] == "down\t*1*1*31##\tack\tout\tbus:24 30 33 0d\t-\tunchecked"
    assert lines[7] == "down\t*1*0*31##\tnack\tsilent\t-\t-\tunchecked"

    # Test process_oracle_tsv file writing
    in_file = (
        tmp_path / "results" / "MH200N" / "010108" / "oracle" / "full" / "test.tsv"
    )
    in_file.parent.mkdir(parents=True)
    in_file.write_text(tsv_content, encoding="ascii")

    dest = check.process_oracle_tsv(in_file, evidence_items=[item])
    assert dest == tmp_path / "results" / "MH200N" / "010108" / "checks" / "test.tsv"
    assert dest.exists()
    assert "agree EVID-WHAT19" in dest.read_text(encoding="ascii")

    # Test to_stdout
    captured = check.process_oracle_tsv(in_file, evidence_items=[item], to_stdout=True)
    assert captured is None


def test_collect_targets(tmp_path: Path):
    d = tmp_path / "results" / "MH200N" / "010108" / "oracle" / "full"
    d.mkdir(parents=True)
    f1 = d / "test.tsv"
    f1.write_text("hello")
    u = tmp_path / "results" / "MH200N" / "010108" / "oracle" / "unit-bt_luci"
    u.mkdir(parents=True)
    f2 = u / "unit.tsv"
    f2.write_text("unit")
    b = tmp_path / "results" / "MH200N" / "010108" / "oracle" / "boundary"
    b.mkdir(parents=True)
    (b / "bound.tsv").write_text("boundary")
    m = tmp_path / "results" / "MH200N" / "010108" / "manifest.tsv"
    m.write_text("manifest")

    nonexistent = tmp_path / "nonexistent"

    with patch("check.ROOT", tmp_path):
        assert check._collect_targets([], all_flag=True) == [f1, f2]
        assert check._collect_targets(
            [], all_flag=False, product="MH200N", version="010108", suite="test"
        ) == [f1]
        assert check._collect_targets(
            [tmp_path / "results", nonexistent], all_flag=False
        ) == [f1, f2]
        assert check._collect_targets([f1], all_flag=False) == [f1]


def test_report_and_gates(tmp_path: Path, capsys):
    checked_sample = (
        "# comment\n"
        "# product=MH200N\n"
        "# version=010108\n"
        "# suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\townd\tlive\n"
        "down\t*#1*74##\tack\tcrash\town:*999*bad##\tunparsed\tdiverge EVID-1\n"
        "down\t*1*1*31##\tack\tout\town:*1*1*31##\tOWNLightingEvent\tagree EVID-2\n"
        "short\n"
    )
    stats, unparsed_rows, diverge_rows, crash_rows = check.collect_check_stats(
        [(Path("s1.tsv"), checked_sample)]
    )
    assert stats == check.CheckStats(
        total_rows=2, agree=1, unparsed=1, diverge=1, crash=1
    )
    assert len(unparsed_rows) == 1
    assert len(diverge_rows) == 1
    assert len(crash_rows) == 1
    assert check.format_summary(stats) == (
        "total=2 agree=1 diverge=1 unparsed=1 crash=1"
    )
    total_r, agree_r, unp_r, div_r = check.analyze_checked_results(
        [(Path("s1.tsv"), checked_sample)]
    )
    assert (total_r, agree_r, len(unp_r), len(div_r)) == (2, 1, 1, 1)

    report = check.format_divergence_report([(Path("s1.tsv"), checked_sample)])
    assert "> [!WARNING]" in report
    assert "### Oracle Daemon Crashes (`verdict == crash`)" in report
    assert "### Live Evidence Divergences" in report
    assert "### OWNd Parser Failures" in report

    clean_sample = (
        "# product=MH200N\n"
        "# version=010108\n"
        "# suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\townd\tlive\n"
        "down\t*1*1*31##\tack\tout\town:*1*1*31##\tOWNLightingEvent\tagree EVID-2\n"
    )
    clean_report = check.format_divergence_report([(Path("s1.tsv"), clean_sample)])
    assert "> [!WARNING]" not in clean_report
    assert "### Oracle Daemon Crashes (`verdict == crash`)" not in clean_report
    assert "### Live Evidence Divergences" not in clean_report
    assert "### OWNd Parser Failures" not in clean_report


def test_main(tmp_path: Path, monkeypatch, capsys):
    in_file = (
        tmp_path / "results" / "MH200N" / "010108" / "oracle" / "full" / "lights.tsv"
    )
    in_file.parent.mkdir(parents=True)
    in_file.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# suite=lights\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\town:*999*foo##\n",
        encoding="ascii",
    )

    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    ltsv = tmp_path / "live.tsv"
    ltsv.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# suite=lights\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tnack\tsilent\t-\n",
        encoding="utf-8",
    )
    rep_out = tmp_path / "report.md"

    with patch("check.ROOT", tmp_path):
        monkeypatch.setattr("sys.argv", ["check.py", str(in_file)])
        check.main()
        captured = capsys.readouterr()
        assert "wrote" in captured.out

        # Test with --report only (no --report-out) and --report-out only (no --report)
        check.main([str(in_file), "--report"])
        assert "Oracle Conformance & Live-Evidence Report" in capsys.readouterr().out
        check.main([str(in_file), "--report-out", str(rep_out)])
        assert rep_out.is_file()

        # Test with --traces-dir, --live-tsv, --report, and --fail-on-diverge
        with pytest.raises(SystemExit) as exc_info:
            check.main(
                [
                    "--product",
                    "MH200N",
                    "--version",
                    "010108",
                    "--suite",
                    "lights",
                    "--traces-dir",
                    str(traces_dir),
                    "--live-tsv",
                    str(ltsv),
                    "--report",
                    "--report-out",
                    str(rep_out),
                    "--fail-on-diverge",
                ]
            )
        assert exc_info.value.code == 1
        assert rep_out.is_file()
        assert "Live Evidence Divergences" in capsys.readouterr().out

        # Test --fail-on-unparsed
        with pytest.raises(SystemExit) as exc_info2:
            check.main([str(in_file), "--fail-on-unparsed"])
        assert exc_info2.value.code == 1

        # Test --fail-on-crash when no crashes exist vs when crash exists
        check.main([str(in_file), "--fail-on-crash"])
        crash_file = in_file.parent / "crash.tsv"
        crash_file.write_text(
            "# product=MH200\n"
            "# version=010024\n"
            "# suite=crash\n"
            "direction\tinput\treply\tverdict\toutput\n"
            "down\t*1*1*31##\tack\tcrash\town:*1*1*31##\n",
            encoding="ascii",
        )
        with pytest.raises(SystemExit) as exc_crash:
            check.main([str(crash_file), "--fail-on-crash"])
        assert exc_crash.value.code == 1

        # Test stdout
        monkeypatch.setattr("sys.argv", ["check.py", str(in_file), "--stdout"])
        check.main()
        captured = capsys.readouterr()
        assert "ownd_version" in captured.out

        # Test error when no targets
        monkeypatch.setattr("sys.argv", ["check.py"])
        with pytest.raises(SystemExit):
            check.main()

        # Test error when --out used with multiple inputs
        in_file2 = in_file.parent / "lights2.tsv"
        in_file2.write_text(in_file.read_text())
        monkeypatch.setattr(
            "sys.argv",
            ["check.py", str(in_file), str(in_file2), "--out", "out.tsv"],
        )
        with pytest.raises(SystemExit):
            check.main()

        # Test writing outside ROOT (dest.relative_to raises ValueError)
        out_outside = tmp_path.parent / "outside_out.tsv"
        monkeypatch.setattr(
            "sys.argv",
            ["check.py", str(in_file), "--out", str(out_outside)],
        )
        check.main()
        captured = capsys.readouterr()
        assert f"wrote {out_outside}" in captured.out
