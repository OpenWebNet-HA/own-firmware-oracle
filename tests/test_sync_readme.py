"""Unit tests for tools/sync_readme.py (README anti-drift sentinel)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import sync_readme


def test_rel_path(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    child = root / "subdir" / "file.txt"
    child.parent.mkdir()
    child.write_text("hello", encoding="utf-8")

    assert sync_readme.rel_path(child, root) == "subdir/file.txt"

    # Outside root
    outside = tmp_path / "outside" / "file.txt"
    assert sync_readme.rel_path(outside, root) == str(outside).replace("\\", "/")


def test_format_version():
    assert sync_readme.format_version("010108") == "1.1.8"
    assert sync_readme.format_version("020051") == "2.0.51"
    assert sync_readme.format_version("1.1.2") == "1.1.2"
    assert sync_readme.format_version("01010a") == "01010a"
    assert sync_readme.format_version("") == ""


def test_parse_manifest_file(tmp_path: Path):
    # Missing file
    missing = tmp_path / "missing.tsv"
    assert sync_readme.parse_manifest_file(missing) == []

    # Valid manifest with various column formats and invalid size
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "# sha256: 1234\n"
        "\n"
        "path\ttype\tsize\tsha256\n"
        "bin/busybox\tELF/ARM/exec/32le/eabi5\t1024\tabc123\n"
        "etc/rcS\tdata\tnot_a_number\tdef456\n"
        "short/entry\text-fs\n",
        encoding="utf-8",
    )
    rows = sync_readme.parse_manifest_file(manifest)
    assert len(rows) == 3
    assert rows[0] == ("bin/busybox", "ELF/ARM/exec/32le/eabi5", 1024, "abc123")
    assert rows[1] == ("etc/rcS", "data", 0, "def456")
    assert rows[2] == ("short/entry", "ext-fs", 0, "")


def test_count_manifest_rows(tmp_path: Path):
    # Missing file
    missing = tmp_path / "missing.tsv"
    assert sync_readme.count_manifest_rows(missing) == 0

    # Empty file
    empty = tmp_path / "empty.tsv"
    empty.write_text("", encoding="utf-8")
    assert sync_readme.count_manifest_rows(empty) == 0

    # Manifest with comments, headers, and rows
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "# sha256: 1234\n"
        "\n"
        "# another comment\n"
        "path\tsha256\tsize\n"
        "bin/busybox\tabc123\t1024\n"
        "etc/init.d/rcS\tdef456\t512\n",
        encoding="utf-8",
    )
    assert sync_readme.count_manifest_rows(manifest) == 2


def test_load_mcp_index(tmp_path: Path):
    # Missing file
    missing = tmp_path / "mcp_index.json"
    with (
        patch("pathlib.Path.is_file", return_value=False),
        pytest.raises(FileNotFoundError, match="mcp_index file not found"),
    ):
        sync_readme.load_mcp_index(missing)

    # Non-dict JSON
    non_dict = tmp_path / "non_dict.json"
    non_dict.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="mcp_index is not a mapping"):
        sync_readme.load_mcp_index(non_dict)

    # Valid dict
    valid = tmp_path / "valid.json"
    valid.write_text('{"gateways": []}', encoding="utf-8")
    assert sync_readme.load_mcp_index(valid) == {"gateways": []}


def test_get_catalog_entries(tmp_path: Path, capsys):
    # Non-existent directory
    missing_dir = tmp_path / "non_existent"
    assert sync_readme.get_catalog_entries(missing_dir) == {}

    cat_dir = tmp_path / "catalog"
    (cat_dir / "MH200N").mkdir(parents=True)
    (cat_dir / "Broken").mkdir(parents=True)
    (cat_dir / "NonDict").mkdir(parents=True)
    (cat_dir / "NoProd").mkdir(parents=True)

    # Valid
    (cat_dir / "MH200N" / "010108.yaml").write_text(
        "product: MH200N\nversion: '010108'\n", encoding="utf-8"
    )
    # Malformed YAML
    (cat_dir / "Broken" / "bad.yaml").write_text(":\n", encoding="utf-8")
    # Non-dict
    (cat_dir / "NonDict" / "list.yaml").write_text("- item\n", encoding="utf-8")
    # Missing product or version
    (cat_dir / "NoProd" / "noprod.yaml").write_text(
        "product: ''\nversion: '1.0'\n", encoding="utf-8"
    )

    entries = sync_readme.get_catalog_entries(cat_dir)
    assert "MH200N" in entries
    assert entries["MH200N"]["version"] == "010108"

    captured = capsys.readouterr()
    assert "failed to parse" in captured.err
    assert "not a mapping" in captured.err
    assert "missing product or version" in captured.err


def test_get_oracle_targets(tmp_path: Path, capsys):
    # Non-existent directory
    missing_dir = tmp_path / "non_existent"
    assert sync_readme.get_oracle_targets(missing_dir) == {}

    tgt_dir = tmp_path / "targets"
    (tgt_dir / "H4684").mkdir(parents=True)
    (tgt_dir / "Broken").mkdir(parents=True)
    (tgt_dir / "NonDict").mkdir(parents=True)
    (tgt_dir / "NoProd").mkdir(parents=True)

    # Valid target
    (tgt_dir / "H4684" / "020054.yaml").write_text(
        "product: H4684\narch: arm\n", encoding="utf-8"
    )
    # Broken YAML
    (tgt_dir / "Broken" / "bad.yaml").write_text(":\n", encoding="utf-8")
    # Non-dict
    (tgt_dir / "NonDict" / "list.yaml").write_text("- item\n", encoding="utf-8")
    # Missing product
    (tgt_dir / "NoProd" / "noprod.yaml").write_text("arch: arm\n", encoding="utf-8")

    targets = sync_readme.get_oracle_targets(tgt_dir)
    assert "H4684" in targets
    assert targets["H4684"]["arch"] == "arm"

    captured = capsys.readouterr()
    assert "failed to parse target" in captured.err
    assert "skipping target" in captured.err


def test_order_products():
    preferred = ["P1", "P2", "P3"]
    candidates = ["P3", "P1", "ExtraB", "ExtraA"]
    ordered = sync_readme.order_products(candidates, preferred)
    assert ordered == ["P1", "P3", "ExtraA", "ExtraB"]


def test_is_auxiliary_product():
    # Known auxiliary
    assert sync_readme.is_auxiliary_product("H4684", {}) is True
    assert sync_readme.is_auxiliary_product("L4561N", {}) is True

    # Category indicator
    assert (
        sync_readme.is_auxiliary_product("CustomConsole", {"category": "console"})
        is True
    )

    # Name indicator
    assert (
        sync_readme.is_auxiliary_product(
            "CustomTouch", {"name": "Multimedia Touch Screen"}
        )
        is True
    )

    # Standard gateway
    assert (
        sync_readme.is_auxiliary_product("MH200N", {"name": "scenarios scheduler"})
        is False
    )


def test_is_no_linux_product(tmp_path: Path):
    # Fallback without results_dir
    assert sync_readme.is_no_linux_product("F455", None) is True
    assert sync_readme.is_no_linux_product("MH200N", None) is False

    # With results_dir containing ELF
    res_dir = tmp_path / "results"
    linux_prod = res_dir / "LinuxGW" / "010000"
    linux_prod.mkdir(parents=True)
    (linux_prod / "manifest.tsv").write_text(
        "path\ttype\tsize\tsha256\nbin/elf\tELF/ARM/exec/32le/eabi5\t100\thash\n",
        encoding="utf-8",
    )
    assert sync_readme.is_no_linux_product("LinuxGW", res_dir) is False

    # With results_dir containing bare-metal (0 ELF)
    mcu_prod = res_dir / "MCUGW" / "010000"
    mcu_prod.mkdir(parents=True)
    (mcu_prod / "manifest.tsv").write_text(
        "path\ttype\tsize\tsha256\nfw.bin\tdata\t100\thash\n",
        encoding="utf-8",
    )
    assert sync_readme.is_no_linux_product("MCUGW", res_dir) is True


def test_infer_architecture_from_manifest(tmp_path: Path):
    missing = tmp_path / "missing.tsv"
    assert (
        sync_readme.infer_architecture_from_manifest(missing) == "Embedded Architecture"
    )

    def make_manifest(types_and_paths: list[tuple[str, str]]) -> Path:
        p = tmp_path / "test_m.tsv"
        lines = ["path\ttype\tsize\tsha256"]
        for path, t in types_and_paths:
            lines.append(f"{path}\t{t}\t100\thash")
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    # AArch64
    m = make_manifest([("app", "ELF/AArch64/exec/64le")])
    assert sync_readme.infer_architecture_from_manifest(m) == "Linux AArch64 (ARM64)"

    # ARMv7
    m = make_manifest([("app", "ELF/ARM/exec/32le/eabi5-hf")])
    assert sync_readme.infer_architecture_from_manifest(m) == "Linux ARMv7 `eabi5`"

    # ARMv5
    m = make_manifest([("app", "ELF/ARM/exec/32le/eabi5")])
    assert sync_readme.infer_architecture_from_manifest(m) == "Linux ARMv5 `eabi5`"

    # ARMv4
    m = make_manifest([("app", "ELF/ARM/exec/32le/oabi")])
    assert sync_readme.infer_architecture_from_manifest(m) == "Linux ARMv4 `oabi`"

    # x86-64
    m = make_manifest([("app", "ELF/x86-64/exec/64le")])
    assert sync_readme.infer_architecture_from_manifest(m) == "Linux x86-64"

    # Hex
    m = make_manifest([("firmware.hex", "data")])
    assert (
        sync_readme.infer_architecture_from_manifest(m) == "Bare-metal Microcontroller"
    )

    # Bin
    m = make_manifest([("firmware.bin", "data")])
    assert sync_readme.infer_architecture_from_manifest(m) == "Bare-metal ARM Cortex-M"

    # Fallback
    m = make_manifest([("firmware.dat", "data")])
    assert sync_readme.infer_architecture_from_manifest(m) == "Embedded Architecture"


def test_infer_layers_from_manifest(tmp_path: Path):
    missing = tmp_path / "missing.tsv"
    assert sync_readme.infer_layers_from_manifest(missing) == "Standard Container"

    def make_manifest(types_and_paths: list[tuple[str, str]]) -> Path:
        p = tmp_path / "layers_m.tsv"
        lines = ["path\ttype\tsize\tsha256"]
        for path, t in types_and_paths:
            lines.append(f"{path}\t{t}\t100\thash")
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    # Comprehensive filesystem layers
    m = make_manifest(
        [
            ("uImage", "uImage"),
            ("jffs2_img", "data"),
            ("rootfs.ext4", "ext-fs"),
            ("squashfs_img", "squashfs"),
            ("cramfs_img", "cramfs"),
            ("archive.tar.gz", "gzip"),
            ("archive.tar", "tar"),
            ("bundle.zip", "zip"),
        ]
    )
    assert (
        sync_readme.infer_layers_from_manifest(m)
        == "U-Boot, JFFS2, Ext4, SquashFS, CramFS, Gzip, Tar, Zip"
    )

    # Ext2
    m = make_manifest([("rootfs.ext2", "ext-fs")])
    assert sync_readme.infer_layers_from_manifest(m) == "Ext2"

    # Hex with zip
    m = make_manifest([("firmware.hex", "data"), ("firmware.zip", "zip")])
    assert sync_readme.infer_layers_from_manifest(m) == "Zip, Intel HEX"

    # Hex only
    m = make_manifest([("firmware.hex", "data")])
    assert sync_readme.infer_layers_from_manifest(m) == "Intel HEX"

    # Bin only
    m = make_manifest([("firmware.bin", "data")])
    assert sync_readme.infer_layers_from_manifest(m) == "Monolithic `.bin`"

    # Fallback
    m = make_manifest([("firmware.dat", "data")])
    assert sync_readme.infer_layers_from_manifest(m) == "Standard Container"


def test_infer_daemons(tmp_path: Path):
    # From targets programs
    targets = {"GW": {"programs": {"p1": {}, "p2": {}}}}
    dummy_m = tmp_path / "dummy.tsv"
    assert sync_readme.infer_daemons("GW", targets, dummy_m) == "`p1`, `p2`"

    # From manifest bin
    bin_m = tmp_path / "bin.tsv"
    bin_m.write_text(
        "path\ttype\tsize\tsha256\nwrap!fw_image.bin\tdata\t204800\thash\n",
        encoding="utf-8",
    )
    assert (
        sync_readme.infer_daemons("Unknown", {}, bin_m)
        == "Flash image `fw_image.bin` (200 KB, no OS)"
    )

    # Fallback
    dat_m = tmp_path / "dat.tsv"
    dat_m.write_text(
        "path\ttype\tsize\tsha256\nwrap!data.rom\tdata\t100\thash\n",
        encoding="utf-8",
    )
    assert sync_readme.infer_daemons("Unknown", {}, dat_m) == "Gateway Server Stack"


def test_build_mcp_metrics_block():
    index_data = {
        "total_unique_inputs": 389,
        "verdicts": {
            "*1*1*31##": [{"suite": "s1"}, {"suite": "s2"}],
            "*#1*0##": [{"suite": "s1"}],
        },
        "gateways": [
            {"product": "MH200N", "status": "emulated"},
            {"product": "F454", "status": "emulated"},
            {"product": "F455", "status": "catalogued"},
            "not-a-dict",
        ],
    }
    block = sync_readme.build_mcp_metrics_block(index_data)
    assert "389 unique frames" in block
    assert "3 verdicts across 2" in block

    # Fallback when total_unique_inputs missing, verdicts not dict, gateways not list
    fallback_data = {
        "verdicts": {},
        "gateways": None,
    }
    fb_block = sync_readme.build_mcp_metrics_block(fallback_data)
    assert "0 unique frames" in fb_block
    assert "0 verdicts across 0" in fb_block

    # Non-dict verdicts
    fb_block2 = sync_readme.build_mcp_metrics_block(
        {"verdicts": "invalid", "gateways": None}
    )
    assert "0 verdicts across 0" in fb_block2


def test_build_fleet_table_block(tmp_path: Path):
    cat_dir = tmp_path / "catalog"
    res_dir = tmp_path / "results"
    tgt_dir = tmp_path / "targets"

    (cat_dir / "MH200N").mkdir(parents=True)
    (cat_dir / "F455").mkdir(parents=True)
    (cat_dir / "H4684").mkdir(parents=True)
    (cat_dir / "UnknownGW").mkdir(parents=True)
    (cat_dir / "McuGW").mkdir(parents=True)

    (cat_dir / "MH200N" / "010108.yaml").write_text(
        "product: MH200N\nversion: '010108'\n", encoding="utf-8"
    )
    (cat_dir / "F455" / "010102.yaml").write_text(
        "product: F455\nversion: '010102'\n", encoding="utf-8"
    )
    (cat_dir / "H4684" / "020054.yaml").write_text(
        "product: H4684\nversion: '020054'\n", encoding="utf-8"
    )
    (cat_dir / "TargetGW").mkdir(parents=True)
    (cat_dir / "TargetGW" / "010000.yaml").write_text(
        "product: TargetGW\nversion: '010000'\n", encoding="utf-8"
    )
    (cat_dir / "UnknownGW" / "010000.yaml").write_text(
        "product: UnknownGW\nversion: '010000'\n", encoding="utf-8"
    )
    (cat_dir / "McuGW" / "010000.yaml").write_text(
        "product: McuGW\nversion: '010000'\n", encoding="utf-8"
    )

    # Manifest for MH200N
    mh_res = res_dir / "MH200N" / "010108"
    mh_res.mkdir(parents=True)
    (mh_res / "manifest.tsv").write_text("header\nrow1\nrow2\n", encoding="utf-8")

    # Manifest for McuGW (no ELF)
    mcu_res = res_dir / "McuGW" / "010000"
    mcu_res.mkdir(parents=True)
    (mcu_res / "manifest.tsv").write_text(
        "path\ttype\tsize\tsha256\nfw.bin\tdata\t100\thash\n", encoding="utf-8"
    )

    # Target for TargetGW (subfolder with YAML)
    tgt_dir.mkdir(parents=True)
    (tgt_dir / "TargetGW").mkdir(parents=True)
    (tgt_dir / "TargetGW" / "010000.yaml").write_text(
        "product: TargetGW\n", encoding="utf-8"
    )

    index_data = {
        "gateways": [
            {"product": "MH200N", "status": "emulated", "suites": ["s1", "s2"]},
            {"status": "emulated"},  # missing product
            "not-a-dict",
        ]
    }

    tbl = sync_readme.build_fleet_table_block(cat_dir, res_dir, tgt_dir, index_data)
    assert "**MH200N**" in tbl
    assert "`010108` (1.1.8)" in tbl
    assert "2 suites, full parity" in tbl
    assert "**F455**" in tbl
    assert "Pending Emulation (Bare-metal MCU — zero matrix value)" in tbl
    assert "**TargetGW**" in tbl
    assert "Pending Emulation (Phase 2 target defined)" in tbl
    assert "**UnknownGW**" in tbl
    assert "Embedded Architecture" in tbl
    assert "Pending Emulation" in tbl
    assert "**McuGW**" in tbl


def test_build_fleet_census_block(tmp_path: Path):
    cat_dir = tmp_path / "catalog"
    (cat_dir / "MH200N").mkdir(parents=True)
    (cat_dir / "F450").mkdir(parents=True)
    (cat_dir / "F455").mkdir(parents=True)
    (cat_dir / "MH202").mkdir(parents=True)
    (cat_dir / "TargetPending").mkdir(parents=True)
    (cat_dir / "AuxOne").mkdir(parents=True)

    (cat_dir / "MH200N" / "010108.yaml").write_text(
        "product: MH200N\nversion: '010108'\n", encoding="utf-8"
    )
    (cat_dir / "F450" / "010010.yaml").write_text(
        "product: F450\nversion: '010010'\n", encoding="utf-8"
    )
    (cat_dir / "F455" / "010102.yaml").write_text(
        "product: F455\nversion: '010102'\n", encoding="utf-8"
    )
    (cat_dir / "MH202" / "010001.yaml").write_text(
        "product: MH202\nversion: '010001'\n", encoding="utf-8"
    )
    (cat_dir / "TargetPending" / "010000.yaml").write_text(
        "product: TargetPending\nversion: '010000'\n", encoding="utf-8"
    )
    (cat_dir / "AuxOne" / "010000.yaml").write_text(
        "product: AuxOne\nversion: '010000'\ncategory: auxiliary\n", encoding="utf-8"
    )

    tgt_dir = tmp_path / "targets"
    (tgt_dir / "TargetPending").mkdir(parents=True)
    (tgt_dir / "TargetPending" / "010000.yaml").write_text(
        "product: TargetPending\n", encoding="utf-8"
    )

    (cat_dir / "McuCensus").mkdir(parents=True)
    (cat_dir / "McuCensus" / "010000.yaml").write_text(
        "product: McuCensus\nversion: '010000'\n", encoding="utf-8"
    )

    res_dir = tmp_path / "results"
    mcu_res = res_dir / "McuCensus" / "010000"
    mcu_res.mkdir(parents=True)
    (mcu_res / "manifest.tsv").write_text(
        "path\ttype\tsize\tsha256\nfw.bin\tdata\t100\thash\n", encoding="utf-8"
    )

    index_data = {
        "gateways": [
            {"product": "MH200N", "status": "emulated", "suites": ["s1"]},
            {"product": "F450", "status": "emulated", "suites": ["s1", "s2"]},
            {"product": "F455", "status": "catalogued"},
            {"product": "MH202", "status": "pending"},
            {"product": "TargetPending", "status": "pending"},
            {"product": "McuCensus", "status": "pending"},
            {"no_product": 1},
            "not-a-dict",
        ]
    }

    census = sync_readme.build_fleet_census_block(
        cat_dir, index_data, results_dir=res_dir, targets_dir=tgt_dir
    )
    assert "**MH200N**" in census
    assert "Emulated — 1 suites, full matrix parity" in census
    assert "**F450**" in census
    assert "full matrix parity via built-in SOAP mock" in census
    assert "**F455**" in census
    assert "Pending Emulation — bare-metal microcontroller" in census
    assert "**MH202**" in census
    assert "Pending Emulation — bare-metal flash image" in census
    assert "**TargetPending**" in census
    assert "Pending Emulation — Phase 2 target defined" in census
    assert "**McuCensus**" in census
    assert "bare-metal microcontroller flash image without OS/userland" in census
    assert "total catalogued firmware packages to 7" in census
    assert "such as **AuxOne** AuxOne" in census

    # Multi-auxiliary test (>= 3 items)
    (cat_dir / "AuxTwo").mkdir(parents=True)
    (cat_dir / "AuxTwo" / "010000.yaml").write_text(
        "product: AuxTwo\nversion: '010000'\ncategory: auxiliary\n", encoding="utf-8"
    )
    (cat_dir / "AuxThree").mkdir(parents=True)
    (cat_dir / "AuxThree" / "010000.yaml").write_text(
        "product: AuxThree\nversion: '010000'\ncategory: auxiliary\n", encoding="utf-8"
    )
    census_multi = sync_readme.build_fleet_census_block(cat_dir, index_data)
    assert ", and **AuxTwo**" in census_multi or ", and **AuxThree**" in census_multi


def test_build_excluded_hardware_block(tmp_path: Path):
    cat_dir = tmp_path / "catalog"
    (cat_dir / "H4684").mkdir(parents=True)
    (cat_dir / "H4684" / "020054.yaml").write_text(
        "product: H4684\nversion: '020054'\n", encoding="utf-8"
    )

    excluded = sync_readme.build_excluded_hardware_block(cat_dir)
    assert "H4684" not in excluded
    assert "F452" in excluded
    assert "MH200 / 003535" in excluded


def test_build_status_phase1_block(tmp_path: Path):
    cat_dir = tmp_path / "catalog"
    (cat_dir / "MH200N").mkdir(parents=True)
    (cat_dir / "MH200N" / "010108.yaml").write_text(
        "product: MH200N\nversion: '010108'\n", encoding="utf-8"
    )
    p1 = sync_readme.build_status_phase1_block(cat_dir)
    assert "Done for all 1 standalone OpenWebNet gateways (MH200N)" in p1


def test_build_status_phase2f_block(tmp_path: Path):
    res_dir = tmp_path / "results"
    full_dir = res_dir / "MH200N" / "010108" / "oracle" / "full"
    full_dir.mkdir(parents=True)
    (full_dir / "s1.tsv").write_text("tsv", encoding="utf-8")

    index_data = {
        "gateways": [
            {"product": "MH200N", "status": "emulated", "suites": ["s1", "s2"]},
            {
                "product": "CustomEmulated",
                "status": "emulated",
                "suites": ["s1", "s2", "s3"],
            },
            {
                "product": "NoTargetEmulated",
                "status": "emulated",
                "suites": ["s1", "s2"],
            },
            {"product": "F454", "status": "emulated", "suites": ["s1", "s2", "s3"]},
            {"product": "", "status": "emulated"},
            {"product": "F455", "status": "catalogued"},
            "not-a-dict",
        ]
    }

    mock_targets = {"CustomEmulated": {"programs": {"p_alpha": {}, "p_beta": {}}}}

    p2f = sync_readme.build_status_phase2f_block(
        index_data, res_dir, targets=mock_targets
    )
    assert "supporting 4 active gateways" in p2f
    assert "1 complete suite TSVs" in p2f
    assert "including sound source suite on F454" in p2f
    assert "1 extra suite on CustomEmulated" in p2f
    assert "Gateway stack (`p_alpha`, `p_beta` — 3 suites)" in p2f
    assert "**NoTargetEmulated** (``): Gateway stack — 2 suites." in p2f

    # Empty suite counts fallback to 18
    p2f_empty = sync_readme.build_status_phase2f_block({"gateways": []}, res_dir)
    assert "across all 18 standard test suites" in p2f_empty


def test_build_status_phase3_block(tmp_path: Path):
    res_dir = tmp_path / "results"
    mcu_dir = res_dir / "NewMcu" / "010000"
    mcu_dir.mkdir(parents=True)
    (mcu_dir / "manifest.tsv").write_text(
        "path\ttype\tsize\tsha256\nfw.bin\tdata\t100\thash\n", encoding="utf-8"
    )

    index_data = {
        "total_unique_inputs": 389,
        "verdicts": {
            "*1*1*31##": [
                {"suite": "lights-level"},
                {"suite": "lights-on-off"},
                "not-a-dict",
                {},
            ],
            "*#1*0##": "not-a-list",
        },
        "gateways": [
            {"product": "MH200N", "status": "emulated"},
            {"product": "ExtraEmulated", "status": "emulated"},
            {"product": "F455", "status": "catalogued"},
            {"product": "MH201", "status": "catalogued"},
            {"product": "L4561N", "status": "catalogued"},
            {"product": "NewMcu", "status": "catalogued"},
            {"product": "F454", "status": "catalogued"},
            {"product": "OtherStatus", "status": "unknown"},
            "not-a-dict",
        ],
    }

    p3 = sync_readme.build_status_phase3_block(index_data, results_dir=res_dir)
    assert "389 unique OpenWebNet frames" in p3
    assert "2 test suites" in p3
    assert "**2 active gateways** (MH200N, ExtraEmulated)" in p3
    assert "F455, MH201, L4561N, NewMcu" in p3

    # Fallback when total_unique_inputs missing and verdicts empty
    p3_fb = sync_readme.build_status_phase3_block({"verdicts": {}, "gateways": []})
    assert "**0 unique OpenWebNet frames**" in p3_fb
    assert "**0 deterministic verdict entries**" in p3_fb

    # Non-dict verdicts
    p3_fb2 = sync_readme.build_status_phase3_block(
        {"total_unique_inputs": 0, "verdicts": "not-dict", "gateways": []}
    )
    assert "**0 deterministic verdict entries**" in p3_fb2


def test_replace_marker_block():
    content = "prefix\n<!-- START -->old<!-- END -->\nsuffix"
    replaced = sync_readme.replace_marker_block(
        content, "<!-- START -->", "<!-- END -->", "new body"
    )
    assert replaced == "prefix\n<!-- START -->\nnew body\n<!-- END -->\nsuffix"


def test_sync_readme(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    readme = root / "README.md"

    # 1. Missing README file
    in_sync, diff, msgs = sync_readme.sync_readme(readme, root)
    assert not in_sync
    assert "README file not found" in msgs[0]

    # 2. Missing marker pairs
    readme.write_text("Hello world without markers", encoding="utf-8")
    in_sync, diff, msgs = sync_readme.sync_readme(readme, root)
    assert not in_sync
    assert any("Missing marker pair" in m for m in msgs)

    # 3. Failing to load mcp_index
    all_markers_text = "\n".join(
        f"{start}\n{end}" for start, end in sync_readme.MARKERS
    )
    readme.write_text(all_markers_text, encoding="utf-8")
    in_sync, diff, msgs = sync_readme.sync_readme(readme, root)
    assert not in_sync
    assert any("Failed to load mcp_index" in m for m in msgs)

    # Setup valid catalog and mcp_index
    cat_dir = root / "catalog"
    cat_dir.mkdir()
    res_dir = root / "results"
    res_dir.mkdir()
    (root / "oracle" / "targets").mkdir(parents=True)

    index_data = {
        "format_version": "1.0.0",
        "schema_version": "1.1.0",
        "total_unique_inputs": 0,
        "gateways": [],
        "verdicts": {},
    }
    (res_dir / "mcp_index.json").write_text(json.dumps(index_data), encoding="utf-8")

    # 4. Out of sync, update=False
    in_sync, diff, msgs = sync_readme.sync_readme(readme, root, update=False)
    assert not in_sync
    assert "README out of sync" in msgs[0]
    assert diff != ""

    # 5. Out of sync, update=True
    in_sync, diff, msgs = sync_readme.sync_readme(readme, root, update=True)
    assert in_sync
    assert "Updated README in place" in msgs[0]

    # 6. Already in sync
    in_sync, diff, msgs = sync_readme.sync_readme(readme, root, update=False)
    assert in_sync
    assert "README in sync" in msgs[0]
    assert diff == ""


def test_main(tmp_path: Path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    readme = root / "README.md"
    all_markers_text = "\n".join(
        f"{start}\n{end}" for start, end in sync_readme.MARKERS
    )
    readme.write_text(all_markers_text, encoding="utf-8")

    (root / "catalog").mkdir()
    res_dir = root / "results"
    res_dir.mkdir()
    (root / "oracle" / "targets").mkdir(parents=True)
    index_data = {
        "format_version": "1.0.0",
        "schema_version": "1.1.0",
        "total_unique_inputs": 0,
        "gateways": [],
        "verdicts": {},
    }
    (res_dir / "mcp_index.json").write_text(json.dumps(index_data), encoding="utf-8")

    # 1. Main --check on out-of-sync readme -> exits 1
    ret = sync_readme.main(["--readme", str(readme), "--root", str(root), "--check"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "README.md is out of sync" in captured.err

    # 2. Main in update mode -> exits 0 and updates
    ret = sync_readme.main(["--readme", str(readme), "--root", str(root), "--update"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Updated README in place" in captured.out

    # 3. Main --check on up-to-date readme -> exits 0
    ret = sync_readme.main(["--readme", str(readme), "--root", str(root), "--check"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "README.md is up to date" in captured.out

    # 4. Main in update mode on already up-to-date readme -> exits 0
    ret = sync_readme.main(["--readme", str(readme), "--root", str(root)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "README in sync" in captured.out

    # 5. Main in update mode with error (missing file) -> exits 1
    missing_readme = root / "missing.md"
    ret = sync_readme.main(["--readme", str(missing_readme), "--root", str(root)])
    assert ret == 1
    captured = capsys.readouterr()
    assert "README file not found" in captured.out


def test_real_repo_sync():
    """Verify that current repository README is 100% in sync with code."""
    ret = sync_readme.main(["--check"])
    assert ret == 0
