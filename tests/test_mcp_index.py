"""Unit tests for tools/mcp_index.py (hash-pinned MCP verdict index generator)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import mcp_index


def test_parse_output_column():
    assert mcp_index.parse_output_column("") == ([], [])
    assert mcp_index.parse_output_column("-") == ([], [])
    assert mcp_index.parse_output_column("bus:24 0d") == (["24 0d"], [])
    assert mcp_index.parse_output_column("own:*1*1*31##") == ([], ["*1*1*31##"])
    assert mcp_index.parse_output_column("bus:24 0d | own:*1*1*31##") == (
        ["24 0d"],
        ["*1*1*31##"],
    )
    assert mcp_index.parse_output_column("other:something") == ([], [])


def test_parse_header():
    lines = [
        "not a comment",
        "# product=MH200N version=010108",
        "# suite=lights-level invalidtoken",
        "#",
    ]
    hdr = mcp_index.parse_header(lines)
    assert hdr["product"] == "MH200N"
    assert hdr["version"] == "010108"
    assert hdr["suite"] == "lights-level"
    assert "invalidtoken" not in hdr


def test_read_oracle_tsv(tmp_path: Path):
    tsv_file = tmp_path / "test.tsv"
    tsv_file.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# image_sha256=img123\n"
        "# target_sha256=tgt123\n"
        "# suite=test-suite\n"
        "# suite_sha256=ste123\n"
        "\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d | own:*1*1*31##\n",
        encoding="utf-8",
    )

    items = mcp_index.read_oracle_tsv(tsv_file, root=tmp_path)
    assert len(items) == 1
    inp, entry = items[0]
    assert inp == "*1*1*31##"
    assert entry.product == "MH200N"
    assert entry.version == "010108"
    assert entry.image_sha256 == "img123"
    assert entry.target_sha256 == "tgt123"
    assert entry.suite == "test-suite"
    assert entry.suite_sha256 == "ste123"
    assert entry.reply == "ack"
    assert entry.verdict == "out"
    assert entry.bus_frames == ["24 0d"]
    assert entry.emitted_own == ["*1*1*31##"]
    assert entry.source_tsv == "test.tsv"
    assert entry.line_number == 9

    # Test file outside root (relative_to ValueError)
    outside_root = tmp_path / "other_dir"
    outside_root.mkdir()
    items_outside = mcp_index.read_oracle_tsv(tsv_file, root=outside_root)
    assert items_outside[0][1].source_tsv == str(tsv_file).replace("\\", "/")

    # Test missing columns in TSV
    incomplete_tsv = tmp_path / "incomplete.tsv"
    incomplete_tsv.write_text(
        "other_col\nval\n",
        encoding="utf-8",
    )
    incomplete_items = mcp_index.read_oracle_tsv(incomplete_tsv, root=tmp_path)
    assert len(incomplete_items) == 1
    assert incomplete_items[0][1].direction == ""
    assert incomplete_items[0][1].reply == ""


def test_build_index(tmp_path: Path):
    res_dir = tmp_path / "results"
    full_dir = res_dir / "MH200N" / "010108" / "oracle" / "full"
    full_dir.mkdir(parents=True)

    tsv1 = full_dir / "s1.tsv"
    tsv1.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# image_sha256=img1\n"
        "# target_sha256=tgt1\n"
        "# suite=s1\n"
        "# suite_sha256=s1_hash\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )

    tsv2 = full_dir / "s2.tsv"
    tsv2.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# image_sha256=img1\n"
        "# target_sha256=tgt1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tnack\tsilent\t-\n",
        encoding="utf-8",
    )

    empty_tsv = full_dir / "empty.tsv"
    empty_tsv.write_text("# empty\n", encoding="utf-8")

    index_data = mcp_index.build_index(res_dir, root=tmp_path)
    assert index_data["format_version"] == "1.0.0"
    assert index_data["total_unique_inputs"] == 1
    assert len(index_data["gateways"]) == 1
    gw = index_data["gateways"][0]
    assert gw["product"] == "MH200N"
    assert gw["suites"] == ["s1"]
    assert "*1*1*31##" in index_data["verdicts"]
    assert len(index_data["verdicts"]["*1*1*31##"]) == 2


def test_format_index_json():
    data = {"key": "value"}
    formatted = mcp_index.format_index_json(data)
    assert formatted.endswith("\n")
    assert json.loads(formatted) == data


def test_main(tmp_path: Path, monkeypatch, capsys):
    res_dir = tmp_path / "results"
    full_dir = res_dir / "MH200N" / "010108" / "oracle" / "full"
    full_dir.mkdir(parents=True)

    tsv = full_dir / "s1.tsv"
    tsv.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# image_sha256=img1\n"
        "# target_sha256=tgt1\n"
        "# suite=s1\n"
        "# suite_sha256=s1_hash\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )

    out_file = res_dir / "mcp_index.json"

    with patch("mcp_index.ROOT", tmp_path):
        # 1. Normal run with --summary
        monkeypatch.setattr(
            "sys.argv",
            [
                "mcp_index.py",
                "--results-dir",
                str(res_dir),
                "--out",
                str(out_file),
                "--summary",
            ],
        )
        assert mcp_index.main() == 0
        captured = capsys.readouterr()
        assert "wrote results/mcp_index.json" in captured.out
        assert "Gateways Indexed: 1" in captured.out

        # 2. Check run passes
        monkeypatch.setattr(
            "sys.argv",
            [
                "mcp_index.py",
                "--results-dir",
                str(res_dir),
                "--out",
                str(out_file),
                "--check",
            ],
        )
        assert mcp_index.main() == 0
        captured = capsys.readouterr()
        assert "mcp_index: ok" in captured.out

        # 3. Check run fails when out does not exist
        missing_out = tmp_path / "missing.json"
        monkeypatch.setattr(
            "sys.argv",
            [
                "mcp_index.py",
                "--results-dir",
                str(res_dir),
                "--out",
                str(missing_out),
                "--check",
            ],
        )
        assert mcp_index.main() == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

        # 4. Check run fails when out differs
        out_file.write_text('{"out": "of date"}\n', encoding="utf-8")
        monkeypatch.setattr(
            "sys.argv",
            [
                "mcp_index.py",
                "--results-dir",
                str(res_dir),
                "--out",
                str(out_file),
                "--check",
            ],
        )
        assert mcp_index.main() == 1
        captured = capsys.readouterr()
        assert "is out of date" in captured.err

        # 5. Normal run outside ROOT (ValueError on relative_to)
        outside_out = tmp_path.parent / "outside_index.json"
        monkeypatch.setattr(
            "sys.argv",
            [
                "mcp_index.py",
                "--results-dir",
                str(res_dir),
                "--out",
                str(outside_out),
            ],
        )
        assert mcp_index.main() == 0
        captured = capsys.readouterr()
        assert f"wrote {str(outside_out).replace('\\', '/')}" in captured.out
