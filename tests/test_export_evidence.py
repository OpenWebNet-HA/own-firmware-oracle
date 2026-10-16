"""Tests for tools/export_evidence.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import export_evidence


def _sample_tsv_text() -> str:
    return (
        "# product=MH200N\n"
        "# version=010108\n"
        f"# image_sha256={'a' * 64}\n"
        "# harness=full\n"
        f"# target_sha256={'b' * 64}\n"
        "# adapter=pty-1\n"
        "# reset=each\n"
        "# bus=pty\n"
        "# framer=line\n"
        "# responder=pic\n"
        "# settle_ms=300\n"
        "# suite=what19\n"
        f"# suite_sha256={'c' * 64}\n"
        "# oracle_version=1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:$030312012200\\x0d\n"
        "down\t*#1*21*#1*100##\tnack\tsilent\t-\n"
        "down\t*#4*1*0##\tack\tout\town:*#4*1*0*0210##\n"
        "up\ta8 21 00 19 01 00 a3\t-\tout\town:*1*19*21## | own:*#1001*21*11*1##\n"
    )


def test_parse_own_frame_variants() -> None:
    assert export_evidence.parse_own_frame("*#*1##") == {
        "who": None,
        "where": None,
        "what": None,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*1*1*21##") == {
        "who": 1,
        "where": "21",
        "what": 1,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*4*110#200*#1##") == {
        "who": 4,
        "where": "#1",
        "what": 110,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*#4*1*0*0210##") == {
        "who": 4,
        "where": "1",
        "what": None,
        "dimension": 0,
        "dimension_values": ["0210"],
    }
    assert export_evidence.parse_own_frame("*#1*21*#1*100*2##") == {
        "who": 1,
        "where": "21",
        "what": None,
        "dimension": 1,
        "dimension_values": ["100", "2"],
    }
    assert export_evidence.parse_own_frame("*#1*21##") == {
        "who": 1,
        "where": "21",
        "what": None,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*#13**22##") == {
        "who": 13,
        "where": None,
        "what": None,
        "dimension": 22,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*#13*1*#abc##") == {
        "who": 13,
        "where": "1",
        "what": None,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*#*21##") == {
        "who": None,
        "where": None,
        "what": None,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*1*#x*21##") == {
        "who": 1,
        "where": "21",
        "what": None,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("*1*21##") == {
        "who": None,
        "where": None,
        "what": None,
        "dimension": None,
        "dimension_values": None,
    }
    assert export_evidence.parse_own_frame("invalid") == {
        "who": None,
        "where": None,
        "what": None,
        "dimension": None,
        "dimension_values": None,
    }


def test_parse_oracle_tsv_edge_cases(tmp_path: Path) -> None:
    tsv = tmp_path / "edge.tsv"
    tsv.write_text(
        "\n# comment without space\n# key=val\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "short\tline\n"
        "down\t*1*1*21##\tack\tsilent\t-\n",
        encoding="utf-8",
    )
    header, rows = export_evidence.parse_oracle_tsv(tsv)
    assert header == {"key": "val"}
    assert len(rows) == 1
    assert rows[0]["input"] == "*1*1*21##"


def test_clean_tokens_and_defaults() -> None:
    assert export_evidence._clean_upper_token("---") == "SUITE"
    assert export_evidence._clean_lower_token("---") == "suite"
    assert (
        export_evidence.default_evidence_id("MH200N", "lights-level")
        == "EVID-MH200N-LIGHTS-LEVEL-001"
    )
    assert (
        export_evidence.default_capture_id("MH200N", "lights-level", "2026-10-15")
        == "cap-mh200n-lights-level-20261015"
    )


def test_detect_oracle_commit_and_target_config_sha(tmp_path: Path) -> None:
    sha = export_evidence.detect_oracle_commit(ROOT)
    assert len(sha) == 40

    # Non-git directory returns 40 zeroes
    assert export_evidence.detect_oracle_commit(tmp_path) == "0" * 40

    # OSError fallback
    with patch.object(subprocess, "run", side_effect=OSError("no git")):
        assert export_evidence.detect_oracle_commit(ROOT) == "0" * 40

    # Existing vs missing target YAML
    real_sha = export_evidence.compute_target_config_sha256(ROOT, "MH200N", "010108")
    assert len(real_sha) == 64
    assert real_sha != "0" * 64
    assert (
        export_evidence.compute_target_config_sha256(tmp_path, "NoProd", "000")
        == "0" * 64
    )


def test_build_frames_and_summary_branches() -> None:
    rows = [
        {
            "direction": "down",
            "input": "*1*1*21##",
            "reply": "-",
            "verdict": "timeout",
            "output": "own:not_a_frame",
        },
        {
            "direction": "up",
            "input": "a8 00 a3",
            "reply": "-",
            "verdict": "out",
            "output": "own:not_a_frame | own:*#13*1*0## | own:*#*1##",
        },
        {
            "direction": "sideways",
            "input": "x",
            "reply": "-",
            "verdict": "silent",
            "output": "-",
        },
    ]
    frames, stats = export_evidence.build_frames_and_summary(rows)
    assert stats["tx_frames"] == 1
    assert stats["rx_frames"] == 2
    assert stats["dimensions"] == [0]
    assert len(frames) == 3


def test_export_bundle_full_and_seq_and_no_who(tmp_path: Path) -> None:
    tsv = tmp_path / "what19.tsv"
    tsv.write_text(_sample_tsv_text(), encoding="utf-8")
    out_dir = tmp_path / "bundle"

    manifest = export_evidence.export_bundle(
        tsv,
        out_dir,
        root=ROOT,
        date_str="2026-10-15",
        oracle_commit="1" * 40,
    )
    assert manifest["evidence_id"] == "EVID-MH200N-WHAT19-001"
    assert manifest["capture_id"] == "cap-mh200n-what19-20261015"
    assert manifest["frame_count"] == 9
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "frames.jsonl").is_file()
    assert (out_dir / "privacy.json").is_file()

    frames_lines = (out_dir / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(frames_lines) == 9
    first_frame = json.loads(frames_lines[0])
    assert first_frame["dir"] == "tx"
    assert first_frame["raw"] == "*1*1*21##"

    # Test with .seq suite and no WHO (only ACK/NACK frame)
    fake_root = tmp_path / "fake_root"
    (fake_root / "oracle" / "cases").mkdir(parents=True)
    (fake_root / "oracle" / "cases" / "seqsuite.seq").write_text(
        "down *#*1##\n", encoding="utf-8"
    )
    tsv_seq = tmp_path / "seqsuite.tsv"
    tsv_seq.write_text(
        _sample_tsv_text()
        .replace("# suite=what19", "# suite=seqsuite")
        .replace("*1*1*21##", "*#*1##"),
        encoding="utf-8",
    )
    out_seq = tmp_path / "bundle_seq"
    manifest_seq = export_evidence.export_bundle(
        tsv_seq,
        out_seq,
        root=fake_root,
        inputs=["*#*1##"],
        oracle_commit="2" * 40,
    )
    assert (
        manifest_seq["firmware_target"]["source_cases"] == "oracle/cases/seqsuite.seq"
    )
    assert (
        "Firmware oracle suite 'seqsuite' verified"
        in manifest_seq["claims_supported"][0]
    )


def test_export_bundle_errors_and_cli_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_tsv = tmp_path / "bad.tsv"
    bad_tsv.write_text("direction\tinput\treply\tverdict\toutput\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required oracle TSV header keys"):
        export_evidence.export_bundle(bad_tsv, tmp_path / "out")

    tsv = tmp_path / "what19.tsv"
    tsv.write_text(_sample_tsv_text(), encoding="utf-8")
    with pytest.raises(ValueError, match="no exportable OpenWebNet frames matched"):
        export_evidence.export_bundle(tsv, tmp_path / "out", inputs=["*99*99*99##"])

    crash_tsv = tmp_path / "crash.tsv"
    crash_tsv.write_text(
        _sample_tsv_text().replace("\tack\tout\t", "\tack\tcrash\t"),
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError, match="cannot export evidence bundle containing 2 crash"
    ):
        export_evidence.export_bundle(crash_tsv, tmp_path / "out_crash")

    # CLI success
    out_cli = tmp_path / "out_cli"
    rc = export_evidence.main(
        [
            str(tsv),
            "--out-dir",
            str(out_cli),
            "--date",
            "2026-10-15",
            "--input",
            "*1*1*21##",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Exported EVID-MH200N-WHAT19-001 (2 frames)" in captured.out

    # CLI error
    rc_err = export_evidence.main(
        [
            str(bad_tsv),
            "--out-dir",
            str(tmp_path / "out_err"),
        ]
    )
    assert rc_err == 1
    assert "export_evidence: ERROR" in capsys.readouterr().err
