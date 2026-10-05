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
        check.parse_ownd_frame("*#1001*74*11*111110111111111111110111##")
        == "OWNEvent"
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
    assert (
        check.parse_ownd_column("bus:24 30 0d | own:*1*1*31##")
        == "OWNLightingEvent"
    )


def test_extract_own_frames():
    assert check.extract_own_frames("-") == []
    assert check.extract_own_frames("") == []
    assert check.extract_own_frames("bus:24 30 33 0d") == []
    assert check.extract_own_frames("own:*1*1*31##") == ["*1*1*31##"]
    assert check.extract_own_frames(
        "bus:24 | own:*1*1*31## | own:*1*19*74##"
    ) == ["*1*1*31##", "*1*19*74##"]


def test_models_for_gateway():
    assert check._models_for_gateway(None) == ()
    assert check._models_for_gateway("MH200") == ("MH200", "MH200N")
    assert check._models_for_gateway("MH200N") == ("MH200N", "MH200")
    assert check._models_for_gateway("MyHomeServer1") == ("MyHomeServer1",)


def test_load_evidence(tmp_path: Path):
    assert check.load_evidence(tmp_path / "nonexistent") == []

    # Valid evidence folder
    cap_dir = tmp_path / "cap-1"
    cap_dir.mkdir()
    manifest = {
        "evidence_id": "EVID-TEST-1",
        "environment": {"gateway_model": "MH200"},
    }
    (cap_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    frames = [
        {"dir": "tx", "raw": "*#1*74##"},
        {"dir": "rx", "raw": "*1*19*74##"},
        {"dir": "rx", "raw": "*#1001*74*11*111110111111111111110111##"},
        {"dir": "tx", "raw": "*#1*99*1##"},
        {"dir": "rx", "raw": "*#1*99*1*200*2##"},
        {"dir": "other", "raw": "ignored"},
        {"dir": "tx", "raw": ""},  # empty raw ignored
    ]
    (cap_dir / "frames.jsonl").write_text(
        "\n".join(json.dumps(f) for f in frames) + "\n\n", encoding="utf-8"
    )

    # Incomplete folder
    (tmp_path / "cap-incomplete").mkdir()

    # Plain file in evidence dir
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    items = check.load_evidence(tmp_path)
    assert len(items) == 2
    assert items[0].evidence_id == "EVID-TEST-1"
    assert items[0].tx_input == "*#1*74##"
    assert items[0].rx_outputs == (
        "*1*19*74##",
        "*#1001*74*11*111110111111111111110111##",
    )
    assert items[1].tx_input == "*#1*99*1##"
    assert items[1].rx_outputs == ("*#1*99*1*200*2##",)

    # Folder with only rx frames (tx_input remains None)
    cap_rx = tmp_path / "cap-rx-only"
    cap_rx.mkdir()
    (cap_rx / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cap_rx / "frames.jsonl").write_text(
        json.dumps({"dir": "rx", "raw": "*1*1*31##"}), encoding="utf-8"
    )
    assert check.load_evidence(tmp_path) == items


def test_find_default_evidence_dir(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    assert check.find_default_evidence_dir(root) is None

    enc_ev = tmp_path / "OpenWebNet-Encyclopedia" / "evidence"
    enc_ev.mkdir(parents=True)
    assert check.find_default_evidence_dir(root) == enc_ev


def test_match_live():
    item = EvidenceItem(
        evidence_id="EVID-1",
        gateway_models=("MH200", "MH200N"),
        tx_input="*#1*74##",
        rx_outputs=("*1*19*74##",),
    )

    # Non-down direction is unchecked
    assert check.match_live("up", "*#1*74##", "own:*1*19*74##", [item]) == "unchecked"

    # Incompatible product is unchecked
    assert (
        check.match_live("down", "*#1*74##", "own:*1*19*74##", [item], product="F454")
        == "unchecked"
    )

    # Matching input and output -> agree
    assert (
        check.match_live("down", "*#1*74##", "own:*1*19*74##", [item], product="MH200N")
        == "agree EVID-1"
    )

    # Divergent output -> diverge
    assert (
        check.match_live("down", "*#1*74##", "own:*1*0*74##", [item], product="MH200N")
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
        "down\t*#1*74##\tack\tout\town:*1*19*74##\t"
        "OWNLightingEvent\tagree EVID-WHAT19"
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
    b = tmp_path / "results" / "MH200N" / "010108" / "oracle" / "boundary"
    b.mkdir(parents=True)
    (b / "bound.tsv").write_text("boundary")
    m = tmp_path / "results" / "MH200N" / "010108" / "manifest.tsv"
    m.write_text("manifest")

    nonexistent = tmp_path / "nonexistent"

    with patch("check.ROOT", tmp_path):
        assert check._collect_targets([], all_flag=True) == [f1]
        assert check._collect_targets(
            [tmp_path / "results", nonexistent], all_flag=False
        ) == [f1]
        assert check._collect_targets([f1], all_flag=False) == [f1]


def test_main(tmp_path: Path, monkeypatch, capsys):
    in_file = (
        tmp_path / "results" / "MH200N" / "010108" / "oracle" / "full" / "lights.tsv"
    )
    in_file.parent.mkdir(parents=True)
    in_file.write_text(
        "# product=MH200N\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="ascii",
    )

    with patch("check.ROOT", tmp_path):
        monkeypatch.setattr("sys.argv", ["check.py", str(in_file)])
        check.main()
        captured = capsys.readouterr()
        assert "wrote" in captured.out

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

