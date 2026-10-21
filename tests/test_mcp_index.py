"""Unit tests for tools/mcp_index.py (hash-pinned MCP verdict index generator)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
    assert index_data["schema_version"] == "1.2.0"
    assert index_data["total_unique_inputs"] == 1
    assert len(index_data["gateways"]) == 1
    gw = index_data["gateways"][0]
    assert gw["product"] == "MH200N"
    assert gw["status"] == "emulated"
    assert gw["suites"] == ["s1"]
    # No responder= header and no target spec: nothing to classify the replies by.
    assert gw["reply_semantics"] == "unknown"
    assert "*1*1*31##" in index_data["verdicts"]
    assert len(index_data["verdicts"]["*1*1*31##"]) == 2


def test_format_index_json():
    data = {"key": "value"}
    formatted = mcp_index.format_index_json(data)
    assert formatted.endswith("\n")
    assert json.loads(formatted) == data


def test_build_index_with_catalog(tmp_path: Path, capsys):
    cat_dir = tmp_path / "catalog"
    (cat_dir / "F454").mkdir(parents=True)
    (cat_dir / "MH200N").mkdir(parents=True)
    (cat_dir / "Broken").mkdir(parents=True)
    (cat_dir / "NonDict").mkdir(parents=True)
    (cat_dir / "NoProd").mkdir(parents=True)
    (cat_dir / "NoImgDict").mkdir(parents=True)

    # 1. Valid catalogued entry
    (cat_dir / "F454" / "020051.yaml").write_text(
        "product: F454\nversion: '020051'\nimage:\n  sha256: f454_img\n",
        encoding="utf-8",
    )

    # 2. Valid emulated entry in catalog
    (cat_dir / "MH200N" / "010108.yaml").write_text(
        "product: MH200N\nversion: '010108'\nimage:\n  sha256: mh_img\n",
        encoding="utf-8",
    )

    # 3. Malformed YAML (raises exception)
    (cat_dir / "Broken" / "broken.yaml").write_text(":\n", encoding="utf-8")

    # 4. Non-dict YAML (e.g. list)
    (cat_dir / "NonDict" / "list.yaml").write_text("- item1\n", encoding="utf-8")

    # 5. Missing product/version
    (cat_dir / "NoProd" / "noprod.yaml").write_text(
        "image:\n  sha256: abc\n", encoding="utf-8"
    )

    # 6. image is not a dict
    (cat_dir / "NoImgDict" / "noimg.yaml").write_text(
        "product: NoImg\nversion: '1'\nimage: null\n", encoding="utf-8"
    )

    # TSV with TSV results for MH200N
    res_dir = tmp_path / "results"
    full_dir = res_dir / "MH200N" / "010108" / "oracle" / "full"
    full_dir.mkdir(parents=True)

    tsv1 = full_dir / "s1.tsv"
    tsv1.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# image_sha256=mh_img\n"
        "# target_sha256=tgt_updated\n"
        "# suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )

    # Second TSV without target_sha256 or image_sha256 in header
    tsv2 = full_dir / "s2.tsv"
    tsv2.write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# suite=s2\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )

    index_data = mcp_index.build_index(res_dir, catalog_dir=cat_dir, root=tmp_path)
    gateways = index_data["gateways"]
    gw_by_prod = {g["product"]: g for g in gateways}

    assert "F454" in gw_by_prod
    assert gw_by_prod["F454"]["status"] == "catalogued"
    assert gw_by_prod["F454"]["suites"] == []
    assert gw_by_prod["F454"]["image_sha256"] == "f454_img"
    assert gw_by_prod["F454"]["target_sha256"] == ""
    assert gw_by_prod["F454"]["reply_semantics"] == ""

    assert "MH200N" in gw_by_prod
    assert gw_by_prod["MH200N"]["status"] == "emulated"
    assert gw_by_prod["MH200N"]["suites"] == ["s1", "s2"]
    assert gw_by_prod["MH200N"]["target_sha256"] == "tgt_updated"
    assert gw_by_prod["MH200N"]["image_sha256"] == "mh_img"

    assert "NoImg" in gw_by_prod
    assert gw_by_prod["NoImg"]["image_sha256"] == ""

    captured = capsys.readouterr()
    assert "WARNING - failed to parse" in captured.err
    assert "WARNING - skipping" in captured.err
    assert "not a mapping" in captured.err
    assert "missing product or version" in captured.err


def test_build_index_conflicting_image_sha256(tmp_path: Path):
    cat_dir = tmp_path / "catalog"
    (cat_dir / "MH200N").mkdir(parents=True)
    (cat_dir / "MH200N" / "010108.yaml").write_text(
        "product: MH200N\nversion: '010108'\nimage:\n  sha256: mh_img_orig\n",
        encoding="utf-8",
    )
    res_dir = tmp_path / "results"
    full_dir = res_dir / "MH200N" / "010108" / "oracle" / "full"
    full_dir.mkdir(parents=True)
    (full_dir / "s1.tsv").write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# image_sha256=mh_img_conflict\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Conflicting image_sha256"):
        mcp_index.build_index(res_dir, catalog_dir=cat_dir, root=tmp_path)


def test_build_index_conflicting_target_sha256(tmp_path: Path):
    cat_dir = tmp_path / "catalog"
    (cat_dir / "MH200N").mkdir(parents=True)
    (cat_dir / "MH200N" / "010108.yaml").write_text(
        "product: MH200N\nversion: '010108'\nimage:\n  sha256: mh_img\n",
        encoding="utf-8",
    )
    res_dir = tmp_path / "results"
    full_dir = res_dir / "MH200N" / "010108" / "oracle" / "full"
    full_dir.mkdir(parents=True)
    (full_dir / "s1.tsv").write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# target_sha256=tgt_1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )
    (full_dir / "s2.tsv").write_text(
        "# product=MH200N\n"
        "# version=010108\n"
        "# target_sha256=tgt_2\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*31##\tack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Conflicting target_sha256"):
        mcp_index.build_index(res_dir, catalog_dir=cat_dir, root=tmp_path)


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

    cat_dir = tmp_path / "catalog"
    (cat_dir / "F454").mkdir(parents=True)
    (cat_dir / "F454" / "020051.yaml").write_text(
        "product: F454\nversion: '020051'\nimage:\n  sha256: img_f454\n",
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
        assert "Gateways Indexed: 2 (1 emulated, 1 catalogued)" in captured.out

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

        # 4. Check run fails when out differs (valid schema, outdated content)
        outdated = mcp_index.build_index(res_dir, catalog_dir=cat_dir, root=tmp_path)
        outdated["gateways"].append(  # type: ignore[union-attr]
            {
                "image_sha256": "x",
                "product": "EXTRA",
                "reply_semantics": "",
                "status": "catalogued",
                "suites": [],
                "target_sha256": "",
                "version": "010000",
            }
        )
        out_file.write_text(mcp_index.format_index_json(outdated), encoding="utf-8")
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

        # 4a. Check run fails on invalid JSON or schema errors
        out_file.write_text("{corrupt", encoding="utf-8")
        assert mcp_index.main() == 1
        assert "invalid JSON" in capsys.readouterr().err

        out_file.write_text('{"format_version": "9.9.9"}\n', encoding="utf-8")
        assert mcp_index.main() == 1
        assert "format_version" in capsys.readouterr().err

        # 4b. Check run fails when build_index raises ValueError
        with patch("mcp_index.build_index", side_effect=ValueError("conflicting hash")):
            monkeypatch.setattr(
                "sys.argv",
                [
                    "mcp_index.py",
                    "--results-dir",
                    str(res_dir),
                    "--out",
                    str(out_file),
                ],
            )
            assert mcp_index.main() == 1
            captured = capsys.readouterr()
            assert "mcp_index: ERROR - conflicting hash" in captured.err

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

        # 6. Run with --catalog-dir and --summary
        monkeypatch.setattr(
            "sys.argv",
            [
                "mcp_index.py",
                "--results-dir",
                str(res_dir),
                "--catalog-dir",
                str(cat_dir),
                "--out",
                str(out_file),
                "--summary",
            ],
        )
        assert mcp_index.main() == 0
        captured = capsys.readouterr()
        assert "catalogued" in captured.out
        assert "[emulated]" in captured.out
        assert "[catalogued]" in captured.out


def test_validate_index_data() -> None:
    # Non-dict verdicts returns early
    errs1 = mcp_index.validate_index_data(
        {
            "format_version": "2.0",
            "schema_version": "2.0",
            "generator": "other",
            "gateways": "not-a-list",
            "verdicts": "not-a-dict",
        }
    )
    assert any("format_version" in e for e in errs1)
    assert any("schema_version" in e for e in errs1)
    assert any("generator" in e for e in errs1)
    assert any("gateways must be a list" in e for e in errs1)
    assert any("verdicts must be a dict" in e for e in errs1)

    # Bad gateway items and bad verdict items + hash mismatch
    errs2 = mcp_index.validate_index_data(
        {
            "format_version": "1.0.0",
            "schema_version": "1.2.0",
            "generator": "own-firmware-oracle",
            "total_unique_inputs": 99,
            "verdicts_sha256": "bad_sha",
            "gateways": [
                {"product": "incomplete"},
                {
                    "image_sha256": "",
                    "product": "X",
                    "reply_semantics": "",
                    "status": "invalid_status",
                    "suites": [],
                    "target_sha256": "",
                    "version": "1",
                },
                {
                    "image_sha256": "",
                    "product": "Y",
                    "reply_semantics": "",
                    "status": "emulated",
                    "suites": [],
                    "target_sha256": "",
                    "version": "1",
                },
                {
                    "image_sha256": "",
                    "product": "Z",
                    "reply_semantics": "pic_ack",
                    "status": "catalogued",
                    "suites": [],
                    "target_sha256": "",
                    "version": "1",
                },
            ],
            "verdicts": {
                "*1*0*21##": [],
                "*1*1*21##": [{"bad": "entry"}],
            },
        }
    )
    assert any("gateways[0] missing required keys" in e for e in errs2)
    assert any("gateways[1] invalid status" in e for e in errs2)
    assert any("gateways[2] invalid reply_semantics ''" in e for e in errs2)
    assert any("gateways[3] invalid reply_semantics 'pic_ack'" in e for e in errs2)
    assert any("total_unique_inputs" in e for e in errs2)
    assert any("must be a non-empty list" in e for e in errs2)
    assert any("entry missing required keys" in e for e in errs2)
    assert any("verdicts_sha256 mismatch" in e for e in errs2)


def _target(root: Path, product: str, version: str, body: str) -> Path:
    path = root / "oracle" / "targets" / product / f"{version}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_reply_semantics(tmp_path: Path) -> None:
    rpmsg = _target(
        tmp_path, "F461", "020011", "version: '020011'\nbus_device: /dev/ttyRPMSG30\n"
    )
    v8 = _target(
        tmp_path,
        "MyHomeServer1",
        "028206",
        "version: '028206'\nbus_device: /dev/ttymxc2\n",
    )
    legacy = _target(tmp_path, "MH200N", "010108", "version: '010108'\n")
    not_a_mapping = _target(tmp_path, "Odd", "1", "- item\n")

    assert mcp_index.reply_semantics({"pic"}, rpmsg) == "empty_bus_txack"
    assert mcp_index.reply_semantics({"pic"}, v8) == "empty_bus_txack"
    # Default /dev/ttyPIC, legacy 2-digit PIC: $19 / $00 is the success outcome.
    assert mcp_index.reply_semantics({"pic"}, legacy) == "pic_ack"
    assert mcp_index.reply_semantics({"pic"}, not_a_mapping) == "unknown"
    assert mcp_index.reply_semantics({"pic"}, tmp_path / "missing.yaml") == "unknown"
    assert mcp_index.reply_semantics(set(), rpmsg) == "unknown"
    assert mcp_index.reply_semantics({"pic:plant:f461-bench"}, rpmsg) == "unknown"
    assert mcp_index.reply_semantics({"pic", "silent"}, rpmsg) == "unknown"


def test_build_index_reply_semantics(tmp_path: Path) -> None:
    _target(
        tmp_path, "F461", "020011", "version: '020011'\nbus_device: /dev/ttyRPMSG30\n"
    )
    full_dir = tmp_path / "results" / "F461" / "020011" / "oracle" / "full"
    full_dir.mkdir(parents=True)
    (full_dir / "s1.tsv").write_text(
        "# product=F461\n"
        "# version=020011\n"
        "# responder=pic\n"
        "# suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*0##\tnack\tout\tbus:24 0d\n",
        encoding="utf-8",
    )

    index_data = mcp_index.build_index(tmp_path / "results", root=tmp_path)
    assert index_data["gateways"] == [
        {
            "image_sha256": "",
            "product": "F461",
            "reply_semantics": "empty_bus_txack",
            "status": "emulated",
            "suites": ["s1"],
            "target_sha256": "",
            "version": "020011",
        }
    ]
    # The classification lives on the gateway; verdict entries stay as recorded.
    assert "reply_semantics" not in index_data["verdicts"]["*1*1*0##"][0]


def test_committed_index_reply_semantics() -> None:
    """Pin which gateways' full-suite NACKs are empty-bus artefacts, with evidence.

    findings/empty-bus-reply-semantics.md: the plain `pic` responder never gives
    F460/F461 (3-digit RPMsg) a `$00800` or MyHomeServer1 028206 (2-digit v8) a `$08`,
    so their full suites record `nack` for frames that went out. With the f461-bench
    plant answering `$00800` / `$08`, the same kind of frame is ACKed.
    """
    index = json.loads((ROOT / "results" / "mcp_index.json").read_text("utf-8"))
    by_gw = {g["product"]: g["reply_semantics"] for g in index["gateways"]}
    assert sorted(p for p, sem in by_gw.items() if sem == "empty_bus_txack") == [
        "F460",
        "F461",
        "MyHomeServer1",
    ]

    def replies(entries: list[dict], product: str) -> set[tuple[str, str]]:
        return {(e["reply"], e["verdict"]) for e in entries if e["product"] == product}

    full = index["verdicts"]
    for product in ("F460", "F461", "MyHomeServer1"):
        assert replies(full["*1*1*0##"], product) == {("nack", "out")}
    assert replies(full["*1*1*0##"], "MH200N") == {("ack", "out")}

    plant_index = ROOT / "results" / "plant_index.json"
    plant = json.loads(plant_index.read_text("utf-8"))["verdicts"]
    for product in ("F461", "MyHomeServer1"):
        assert replies(plant["*1*0*0##"], product) == {("ack", "out")}
        # An address the plant leaves unpopulated NACKs exactly like the empty bus.
        assert replies(plant["*#1*08##"], product) == {("nack", "out")}
