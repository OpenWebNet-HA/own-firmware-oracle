"""Unit tests for tools/compare.py (cross-gateway comparison & matrix tool)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import compare


@pytest.fixture
def sample_index(tmp_path: Path) -> Path:
    data = {
        "format_version": "1.0.0",
        "generator": "own-firmware-oracle",
        "schema_version": "1.1.0",
        "total_unique_inputs": 5,
        "verdicts_sha256": "a" * 64,
        "gateways": [
            {
                "product": "MH200N",
                "version": "010108",
                "status": "emulated",
                "suites": ["s1", "s2"],
                "image_sha256": "1" * 64,
                "target_sha256": "2" * 64,
            },
            {
                "product": "F454",
                "version": "020051",
                "status": "emulated",
                "suites": ["s1", "s2"],
                "image_sha256": "3" * 64,
                "target_sha256": "4" * 64,
            },
            {
                "product": "F455",
                "version": "010102",
                "status": "catalogued",
                "suites": [],
                "image_sha256": "5" * 64,
                "target_sha256": "",
            },
        ],
        "verdicts": {
            "*1*1*21##": [
                {
                    "product": "MH200N",
                    "version": "010108",
                    "suite": "s1",
                    "reply": "ack",
                    "verdict": "out",
                    "bus_frames": ["$0312\\x0d"],
                    "emitted_own": [],
                },
                {
                    "product": "MH200N",
                    "version": "010108",
                    "suite": "s2",
                    "reply": "ack",
                    "verdict": "out",
                    "bus_frames": ["$0312\\x0d"],
                    "emitted_own": [],
                },
                {
                    "product": "F454",
                    "version": "020051",
                    "suite": "s1",
                    "reply": "ack",
                    "verdict": "out",
                    "bus_frames": ["$24\\x0d", "$00312\\x0d"],
                    "emitted_own": [],
                },
            ],
            "*22*0*1##": [
                {
                    "product": "MH200N",
                    "version": "010108",
                    "suite": "s1",
                    "reply": "nack",
                    "verdict": "silent",
                    "bus_frames": [],
                    "emitted_own": [],
                },
                {
                    "product": "F454",
                    "version": "020051",
                    "suite": "s1",
                    "reply": "ack",
                    "verdict": "out",
                    "bus_frames": ["$0490\\x0d"],
                    "emitted_own": ["*22*0*1##"],
                },
            ],
            "*99*0##": [
                {
                    "product": "MH200N",
                    "version": "010108",
                    "suite": "s1",
                    "reply": "ack",
                    "verdict": "silent",
                    "bus_frames": [],
                    "emitted_own": [],
                },
            ],
            "a8 21 a3": [
                {
                    "product": "MH200N",
                    "version": "010108",
                    "suite": "s1",
                    "reply": "-",
                    "verdict": "out",
                    "bus_frames": [],
                    "emitted_own": ["*1*1*21##"],
                },
                {
                    "product": "F454",
                    "version": "020051",
                    "suite": "s1",
                    "reply": "nack",
                    "verdict": "out",
                    "bus_frames": ["$24\\x0d"],
                    "emitted_own": [],
                },
            ],
            "*1*99*21##": [
                {
                    "product": "MH200N",
                    "version": "010108",
                    "suite": "s1",
                    "reply": "-",
                    "verdict": "silent",
                    "bus_frames": [],
                    "emitted_own": [],
                },
                {
                    "product": "F454",
                    "version": "020051",
                    "suite": "s1",
                    "reply": "-",
                    "verdict": "silent",
                    "bus_frames": [],
                    "emitted_own": [],
                },
            ],
        },
    }
    p = tmp_path / "mcp_index.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_helpers(tmp_path: Path) -> None:
    assert compare.normalize_input_frame("1*1*21") == "*1*1*21##"
    assert compare.normalize_input_frame("*1*1*21##") == "*1*1*21##"
    assert compare.normalize_input_frame("A8 21 A3") == "a8 21 a3"
    assert compare.normalize_input_frame("") == ""

    assert compare.extract_who("*1*1*21##") == 1
    assert compare.extract_who("*#22*3#1#1*1##") == 22
    assert compare.extract_who("a8 21 a3") is None

    assert compare.normalize_bus_frames(
        ["$24\\x0d", "$00312\\x0d"], normalize_prefix=True
    ) == ("$0312\\r",)
    assert compare.normalize_bus_frames(
        ["$24\\x0d", "$24\\x0d"], normalize_prefix=False
    ) == ("$24\\r", "$24\\r")
    assert compare.normalize_bus_frames(["$24\\x0d"], normalize_prefix=False) == (
        "$24\\r",
    )

    assert compare._badge("ack", "crash") == "CRASH"
    assert compare._badge("nack", "crash") == "CRASH"
    assert compare._badge("-", "timeout") == "TIMEOUT"

    bad_idx = tmp_path / "bad.json"
    bad_idx.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid index JSON"):
        compare.load_index(bad_idx)


def test_compare_frame_formats(sample_index: Path) -> None:
    idx = compare.load_index(sample_index)

    md = compare.compare_frame(idx, "22*0*1", suite="s1", fmt="markdown")
    assert "## Cross-Gateway Comparison: `*22*0*1##`" in md
    assert "### Equivalence Classes" in md

    # Untested emulated gateway shows UNTESTED class
    md_single = compare.compare_frame(idx, "*99*0##", fmt="markdown")
    assert "UNTESTED" in md_single

    tsv = compare.compare_frame(idx, "*1*1*21##", fmt="tsv")
    assert "MH200N\t010108\temulated\tack\tout" in tsv

    js = json.loads(compare.compare_frame(idx, "*1*1*21##", fmt="json"))
    assert js["frame"] == "*1*1*21##"


def test_scan_divergences_and_matrix(
    sample_index: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    idx = compare.load_index(sample_index)

    # Divergences in markdown, tsv, json
    md_div = compare.scan_divergences(
        idx, include_bus=True, normalize_bus=True, fmt="markdown"
    )
    assert "Divergent across fleet" in md_div

    tsv_div = compare.scan_divergences(idx, suite="s1", fmt="tsv")
    assert "*22*0*1##" in tsv_div

    js_div = json.loads(compare.scan_divergences(idx, who=22, fmt="json"))
    assert js_div["divergent_frames"] == 1

    # Unanimous only filter
    md_unan = compare.scan_divergences(idx, who=1, include_bus=True, normalize_bus=True)
    assert "_All analyzed frames are unanimous across the fleet._" in md_unan

    # Suite filter with no matching entries
    md_none = compare.scan_divergences(idx, suite="nonexistent")
    assert "_All analyzed frames are unanimous across the fleet._" in md_none

    # Matrix in markdown, tsv, json
    assert "## Cross-Gateway Applicability Matrix" in compare.generate_matrix(
        idx, suite="s1", who=1, fmt="markdown"
    )
    tsv_mat = compare.generate_matrix(idx, suite="s1", fmt="tsv")
    assert "frame\tMH200N\tF454" in tsv_mat
    assert "*1*1*21##\tACK+OUT\tACK+OUT" in tsv_mat
    assert "frame\tMH200N\tF454" in compare.generate_matrix(
        idx, suite="nonexistent", fmt="tsv"
    )
    js_mat = json.loads(compare.generate_matrix(idx, fmt="json"))
    assert js_mat["gateways"] == ["MH200N", "F454"]

    # CLI main() subcommands
    assert compare.main(["--index", str(sample_index), "frame", "*1*1*21##"]) == 0
    assert (
        compare.main(["--index", str(sample_index), "divergences", "--suite", "s1"])
        == 0
    )
    assert compare.main(["--index", str(sample_index), "matrix", "--who", "1"]) == 0
    assert "Cross-Gateway" in capsys.readouterr().out
