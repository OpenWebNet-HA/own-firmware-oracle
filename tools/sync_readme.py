#!/usr/bin/env python3
"""sync_readme -- automated README synchronization & anti-drift sentinel.

Synchronizes dynamic statistics, tables, and metrics in README.md against
the codebase, catalog entries, manifest files, targets, and mcp_index.json:
1. MCP Index Metrics (<!-- MCP_METRICS_START --> / <!-- MCP_METRICS_END -->)
2. Fleet Table (<!-- FLEET_TABLE_START --> / <!-- FLEET_TABLE_END -->)
3. Fleet Census (<!-- FLEET_CENSUS_START --> / <!-- FLEET_CENSUS_END -->)
4. Excluded Hardware (<!-- EXCLUDED_HARDWARE_START --> / <!-- EXCLUDED_HARDWARE_END -->)
5. Status Phase 1 (<!-- STATUS_PHASE1_START --> / <!-- STATUS_PHASE1_END -->)
6. Status Phase 2f (<!-- STATUS_PHASE2F_START --> / <!-- STATUS_PHASE2F_END -->)
7. Status Phase 3 (<!-- STATUS_PHASE3_START --> / <!-- STATUS_PHASE3_END -->)

CLI Modes:
- python tools/sync_readme.py (or --update): updates README.md in place
- python tools/sync_readme.py --check: exits 0 if in sync, exits 1 on drift
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Marker definitions
MCP_METRICS_START = "<!-- MCP_METRICS_START -->"
MCP_METRICS_END = "<!-- MCP_METRICS_END -->"
FLEET_TABLE_START = "<!-- FLEET_TABLE_START -->"
FLEET_TABLE_END = "<!-- FLEET_TABLE_END -->"
FLEET_CENSUS_START = "<!-- FLEET_CENSUS_START -->"
FLEET_CENSUS_END = "<!-- FLEET_CENSUS_END -->"
EXCLUDED_HARDWARE_START = "<!-- EXCLUDED_HARDWARE_START -->"
EXCLUDED_HARDWARE_END = "<!-- EXCLUDED_HARDWARE_END -->"
STATUS_PHASE1_START = "<!-- STATUS_PHASE1_START -->"
STATUS_PHASE1_END = "<!-- STATUS_PHASE1_END -->"
STATUS_PHASE2F_START = "<!-- STATUS_PHASE2F_START -->"
STATUS_PHASE2F_END = "<!-- STATUS_PHASE2F_END -->"
STATUS_PHASE3_START = "<!-- STATUS_PHASE3_START -->"
STATUS_PHASE3_END = "<!-- STATUS_PHASE3_END -->"

MARKERS = [
    (MCP_METRICS_START, MCP_METRICS_END),
    (FLEET_TABLE_START, FLEET_TABLE_END),
    (FLEET_CENSUS_START, FLEET_CENSUS_END),
    (EXCLUDED_HARDWARE_START, EXCLUDED_HARDWARE_END),
    (STATUS_PHASE1_START, STATUS_PHASE1_END),
    (STATUS_PHASE2F_START, STATUS_PHASE2F_END),
    (STATUS_PHASE3_START, STATUS_PHASE3_END),
]

# Canonical preferred ordering for existing hardware
# (new catalogued items are appended in sorted order)
FLEET_ORDER = [
    "MH200N",
    "MyHomeServer1",
    "F454",
    "MH202",
    "F453AV",
    "F455",
    "MH201",
    "F461",
    "F450",
    "F459",
    "F460",
    "H4684",
    "L4561N",
]

STANDALONE_GATEWAYS = [
    "MH200N",
    "MyHomeServer1",
    "F454",
    "MH202",
    "F459",
    "F453AV",
    "F460",
    "F461",
    "F450",
    "F455",
    "MH201",
]

PHASE1_ORDER = [
    "MH200N",
    "MyHomeServer1",
    "F454",
    "MH202",
    "F453AV",
    "F455",
    "MH201",
    "F461",
    "F450",
    "F459",
    "F460",
]

NO_LINUX_ORDER = [
    "F455",
    "MH201",
    "L4561N",
]

EMULATED_ORDER = [
    "MH200N",
    "MyHomeServer1",
    "F454",
    "MH202",
    "F459",
    "F453AV",
    "F460",
    "F461",
    "F450",
]

# Known hardware categorization sets
KNOWN_AUXILIARY_PRODUCTS = {"H4684", "L4561N"}
KNOWN_NO_LINUX_PRODUCTS = {"F455", "MH201", "L4561N"}

# Curated metadata dictionary for historical / published models
GATEWAY_METADATA: dict[str, dict[str, str]] = {
    "MH200N": {
        "arch": "Linux ARMv5 `eabi5`",
        "layers": "U-Boot, Ext2, Zip",
        "daemons": "`openserver`, `scsserver`",
        "census_desc": "DIN scenario programmer & OpenWebNet gateway",
        "phase2f_desc": (
            "DIN scenario programmer (`openserver`, `scsserver` — {suites} suites)"
        ),
    },
    "MyHomeServer1": {
        "arch": "Linux ARMv7 `eabi5`",
        "layers": "U-Boot, Ext4, Zip",
        "daemons": (
            "`openserver`, `bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, "
            "`bt_supervisione`, `coso`"
        ),
        "census_desc": "Modern Linux gateway & IoT bridge",
        "phase2f_desc": (
            "Multi-daemon Linux gateway (`openserver`, `bt_luci`, `bt_device`, "
            "`bt_termo`, `bt_multi`, `bt_supervisione` on 30018/31018 — "
            "{suites} suites)"
        ),
    },
    "F454": {
        "arch": "Linux ARMv5 `eabi5`",
        "layers": "JFFS2, CramFS, Zip",
        "daemons": (
            "`bt_daemon`, `stackopen` (serial `/dev/ttyS1`), `bt_vct`, "
            "`openserver`, `scsserver`"
        ),
        "census_desc": "Web server audio/video DIN gateway",
        "phase2f_desc": (
            "Audio/Video web server DIN gateway (`bt_daemon`, `stackopen` serial "
            "PTY `/dev/ttyS1`, `bt_vct`, `openserver`, `scsserver` — {suites} suites)"
        ),
    },
    "MH202": {
        "arch": "Linux ARMv5 `eabi5`",
        "layers": "SquashFS, Zip",
        "daemons": (
            "`bt_daemon`, `stackopen`, `bt_device`, `bt_energia`, "
            "`bt_supervisione`, `openserver`, `scsserver`"
        ),
        "census_desc": "Advanced scenario programmer & BACnet gateway",
        "phase2f_desc": (
            "Advanced scenario programmer & BACnet gateway (`bt_daemon`, `stackopen`, "
            "`bt_device`, `bt_energia`, `bt_supervisione`, `openserver`, "
            "`scsserver` — {suites} suites)"
        ),
    },
    "F453AV": {
        "arch": "Linux ARMv4 `oabi`",
        "layers": "CramFS, Zip",
        "daemons": (
            "`bt_processi`, `openserver`, `bt_vct` (serial `/dev/ttyPIC`, "
            "DSP `/dev/dsp1`)"
        ),
        "census_desc": "DIN audio/video web server (ARMv4 OABI)",
        "phase2f_desc": (
            "Legacy DIN audio/video gateway (`openserver`, `bt_vct`, `bt_processi` "
            "with `/dev/dsp1` audio DSP and `/dev/ttyPIC` PTY under ARMv4 OABI — "
            "{suites} suites)"
        ),
    },
    "F455": {
        "arch": "Bare-metal ARM Cortex-M",
        "layers": "Monolithic `.bin`",
        "daemons": "Flash image `F455_1_1_2.bin` (301 KB, no OS)",
        "pending_status": "Pending Emulation (Bare-metal MCU — zero matrix value)",
        "census_desc": "Basic OpenWebNet IP interface (bare-metal ARM Cortex-M)",
        "census_emulation_note": (
            "bare-metal microcontroller flash image without OS/userland; "
            "basic lighting/shutter subset already 100% covered by Linux gateways "
            "with zero added value to the matrix"
        ),
    },
    "MH201": {
        "arch": "Bare-metal ARM Cortex-M3 (STM32F217)",
        "layers": "Monolithic `.bin`",
        "daemons": "Flash image `MH201_3_6_44_signed.bin` (502 KB, CMX-RTX, no OS)",
        "pending_status": "Pending Emulation (Bare-metal MCU — zero matrix value)",
        "census_desc": (
            "Hotel guest room scenario module (bare-metal ARM Cortex-M3 STM32F217)"
        ),
        "census_emulation_note": (
            "bare-metal microcontroller flash image without OS/userland; "
            "basic lighting/shutter/scenario subset already covered by Linux gateways "
            "with zero added value to the matrix"
        ),
    },
    "F461": {
        "arch": "Linux AArch64 (ARM64)",
        "layers": "Ext4, SquashFS, Zip",
        "daemons": (
            "Server gateway stack (`openserver`, `scsserver`, `bt_luci`, `bt_device`, "
            "`bt_termo`, `bt_multi`, `bt_energia`, `coso`)"
        ),
        "census_desc": "Server gateway stack (AArch64 / ARM64)",
        "phase2f_desc": (
            "Eliot AArch64 server gateway stack (`openserver`, `scsserver`, `bt_luci`, "
            "`bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `coso` on "
            "`/dev/ttyRPMSG30` PTY — {suites} suites)"
        ),
    },
    "F450": {
        "arch": "Linux ARMv5 `eabi5`",
        "layers": "JFFS2, Zip",
        "daemons": (
            "Basic IP interface gateway stack (`bacclient`, `scsserver`, `bt_device`, "
            "`bt_termo`)"
        ),
        "census_desc": "IP interface gateway (OPEN-BACnet)",
        "census_emulation_note": (
            "Emulated — {suites} suites, full matrix parity via built-in SOAP mock"
        ),
        "phase2f_desc": (
            "IP interface gateway (`bacclient`, `scsserver`, `bt_device`, "
            "`bt_termo` via built-in SOAP mock on port 1234 — {suites} suites)"
        ),
    },
    "F459": {
        "arch": "Linux ARMv5 `eabi5`",
        "layers": "SquashFS, Zip",
        "daemons": (
            "Hospitality / hotel room gateway stack (`openserver`, `scsserver`, "
            "`bt_luci`, `bt_termo`, `bt_multi`, `bt_energia`, `bt_supervisione`)"
        ),
        "census_desc": "Hotel / hospitality driver manager gateway",
        "phase2f_desc": (
            "Hospitality / hotel room gateway (`openserver`, `scsserver`, `bt_luci`, "
            "`bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `bt_supervisione`, "
            "`coso` — {suites} suites)"
        ),
    },
    "F460": {
        "arch": "Linux AArch64 (ARM64)",
        "layers": "Ext4, SquashFS, Zip",
        "daemons": (
            "Hotel scenario programmer gateway stack (`openserver`, `scsserver`, "
            "`bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `coso`)"
        ),
        "census_desc": "Hotel scenario programmer gateway (AArch64 / ARM64)",
        "phase2f_desc": (
            "Eliot AArch64 hotel scenario programmer stack (`openserver`, `scsserver`, "
            "`bt_luci`, `bt_device`, `bt_termo`, `bt_multi`, `bt_energia`, `coso` on "
            "`/dev/ttyRPMSG30` PTY — {suites} suites)"
        ),
    },
    "H4684": {
        "title": "Colour Touch Screen",
        "arch": "Linux ARMv4 `oabi`",
        "layers": "Ext2, Gzip, Zip",
        "daemons": (
            "Colour touch screen console (`bt_processi`, `openserver`, `scsserver`, "
            "`bt_luci`, `bt_device`)"
        ),
        "pending_status": "Pending Emulation (Phase 2 target defined)",
    },
    "L4561N": {
        "title": "Stereo Control Interface",
        "arch": "Bare-metal Microcontroller",
        "layers": "Zip, Intel HEX",
        "daemons": "Stereo control interface firmware (`rca_ir.HEX`)",
        "pending_status": (
            "Pending Emulation (Specialized bus interface — zero matrix value)"
        ),
    },
}

KNOWN_EXCLUDED_HARDWARE = [
    (
        "F452 / F452V",
        ["F452", "F452V"],
        "First-generation Web Server DIN",
        (
            "Discontinued early 2000s hardware. Firmware was stored in masked "
            "ROM / EEPROM; no firmware update packages were ever "
            "published for download."
        ),
    ),
    (
        "F453",
        ["F453"],
        "Enhanced Web Server DIN",
        (
            "Pre-Audio/Video version, replaced by F453AV. No separate public "
            "firmware download package exists."
        ),
    ),
    (
        "F458 / 003599",
        ["F458", "003599"],
        "IP Server",
        (
            "Specialized telecom/IP server module; no public firmware archive "
            "distributed."
        ),
    ),
    (
        "MH200 / 003535",
        ["MH200", "003535"],
        "Legacy Scenes Programmer",
        (
            "Physical RS232 serial hardware predecessor to MH200N (no Ethernet "
            "OpenWebNet server daemon)."
        ),
    ),
    (
        'HOMETOUCH 7" (3488 / 067259)',
        ["HOMETOUCH", "3488", "067259"],
        "Connected Touchscreen",
        (
            "Embedded Android touch display; firmware updates are distributed "
            "exclusively as full-device Android OTA updates, not OpenWebNet gateway "
            "images."
        ),
    ),
    (
        "Classe 300X (`344642`, `344742`)",
        ["Classe 300X", "344642", "344742"],
        "Video Internal Unit with Wi-Fi",
        (
            "2-wire video internal unit with Netatmo cloud bridging; firmware updates "
            "are distributed exclusively as encrypted OTA cloud synchronization."
        ),
    ),
    (
        "Classe 300 EOS (`344842`, `344845`)",
        ["Classe 300 EOS", "344842", "344845"],
        "Smart Video Internal Unit",
        (
            "Connected video internal unit with Alexa; firmware updated "
            "exclusively via Netatmo / Legrand cloud OTA."
        ),
    ),
    (
        "H4684 / L4684 / LN4684A (`067283`, `078474`)",
        ["H4684", "L4684", "LN4684A", "067283", "078474"],
        'Colour Touch Screen 3.5" & 10"',
        (
            "Embedded display consoles; firmware flashed via MyHOME_Suite or USB, "
            "not released as standalone gateway images."
        ),
    ),
    (
        "MH201",
        ["MH201"],
        "IP Scenario Module",
        "Early DIN scenario module; no standalone public download archive.",
    ),
    (
        "HC4690 / HD4690 / HS4690 (`067285`)",
        ["HC4690", "HD4690", "HS4690", "067285"],
        "Multimedia Touch Screen",
        "10-inch multimedia display console; specialized display firmware.",
    ),
    (
        "F422 / 003562",
        ["F422", "003562"],
        "SCS-to-SCS Interface Router",
        (
            "Pure galvanic bus-to-bus bridge microcontroller; no IP interface or "
            "OpenWebNet parser."
        ),
    ),
    (
        "F429 / 002631",
        ["F429", "002631"],
        "SCS/DALI Gateway",
        (
            "Specialized DALI lighting interface controller; no OpenWebNet TCP "
            "server daemon."
        ),
    ),
    (
        "BMNE4000 / 048832",
        ["BMNE4000", "048832"],
        "SCS/ZigBee Gateway",
        (
            "Hardware radio bridge; firmware is embedded radio stack without "
            "standalone OpenWebNet daemon."
        ),
    ),
]


def rel_path(path: Path, root: Path = ROOT) -> str:
    """Return path relative to root, or absolute string if outside."""
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def format_version(version_str: str) -> str:
    """Format 6-digit version into dotted form (e.g. 010108 -> 1.1.8)."""
    v = str(version_str).strip()
    if len(v) == 6 and v.isdigit():
        return f"{int(v[0:2])}.{int(v[2:4])}.{int(v[4:6])}"
    return v


def parse_manifest_file(manifest_path: Path) -> list[tuple[str, str, int, str]]:
    """Parse manifest.tsv into a list of (path, type, size, sha256) tuples."""
    if not manifest_path.is_file():
        return []
    rows: list[tuple[str, str, int, str]] = []
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].lower() != "path":
            size = 0
            if len(parts) >= 3:
                try:
                    size = int(parts[2])
                except ValueError:
                    size = 0
            sha = parts[3] if len(parts) >= 4 else ""
            rows.append((parts[0], parts[1], size, sha))
    return rows


def count_manifest_rows(manifest_path: Path) -> int:
    """Count data rows in manifest.tsv (ignoring comments and column header)."""
    if not manifest_path.is_file():
        return 0
    lines = [
        ln.strip()
        for ln in manifest_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return max(0, len(lines) - 1)


def load_mcp_index(index_path: Path) -> dict[str, Any]:
    """Load results/mcp_index.json."""
    if not index_path.is_file():
        raise FileNotFoundError(f"mcp_index file not found: {index_path}")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"mcp_index is not a mapping: {index_path}")
    return data


def get_catalog_entries(catalog_dir: Path) -> dict[str, dict[str, Any]]:
    """Scan catalog directory and return map of product -> catalog metadata dict."""
    entries: dict[str, dict[str, Any]] = {}
    if not catalog_dir.is_dir():
        return entries

    for yaml_path in sorted(catalog_dir.glob("*/*.yaml")):
        try:
            doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(
                f"sync_readme: WARNING - failed to parse {yaml_path}: {exc}",
                file=sys.stderr,
            )
            continue

        if not isinstance(doc, dict):
            print(
                f"sync_readme: WARNING - skipping {yaml_path}: not a mapping",
                file=sys.stderr,
            )
            continue

        product = str(doc.get("product", "")).strip()
        version = str(doc.get("version", "")).strip()
        if product and version:
            entries[product] = doc
        else:
            print(
                f"sync_readme: WARNING - skipping {yaml_path}: "
                "missing product or version",
                file=sys.stderr,
            )

    return entries


def get_oracle_targets(targets_dir: Path) -> dict[str, dict[str, Any]]:
    """Scan targets directory and return product -> target metadata dict."""
    targets: dict[str, dict[str, Any]] = {}
    if not targets_dir.is_dir():
        return targets

    for yaml_path in sorted(targets_dir.glob("*/*.yaml")):
        try:
            doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(
                f"sync_readme: WARNING - failed to parse target {yaml_path}: {exc}",
                file=sys.stderr,
            )
            continue

        if not isinstance(doc, dict):
            print(
                f"sync_readme: WARNING - skipping target {yaml_path}: not a mapping",
                file=sys.stderr,
            )
            continue

        product = str(doc.get("product", "")).strip()
        if product:
            targets[product] = doc

    return targets


def order_products(
    candidates: Iterable[str],
    preferred_order: list[str],
) -> list[str]:
    """Order candidate products by preferred_order first, then append extras sorted."""
    c_set = set(candidates)
    ordered = [p for p in preferred_order if p in c_set]
    extras = sorted(c_set - set(ordered))
    return ordered + extras


def is_auxiliary_product(product: str, catalog_doc: dict[str, Any]) -> bool:
    """Determine whether a product is auxiliary console/interface hardware."""
    if product in KNOWN_AUXILIARY_PRODUCTS:
        return True
    cat_type = str(catalog_doc.get("category", "")).lower().strip()
    if cat_type in ("auxiliary", "console", "interface", "display"):
        return True
    name = str(catalog_doc.get("name", "")).lower().strip()
    return any(
        k in name for k in ("touch screen", "display console", "control interface")
    )


def is_no_linux_product(product: str, results_dir: Path | None = None) -> bool:
    """Check if product is a non-Linux device (bare-metal MCU / interface)."""
    if results_dir is not None and results_dir.is_dir():
        manifests = list(results_dir.glob(f"{product}/*/manifest.tsv"))
        if manifests:
            return not any(
                row[1].startswith("ELF/")
                for m in manifests
                for row in parse_manifest_file(m)
            )
    return product in KNOWN_NO_LINUX_PRODUCTS


def infer_architecture_from_manifest(manifest_path: Path) -> str:
    """Infer system architecture from unpacked manifest entries."""
    rows = parse_manifest_file(manifest_path)
    types = {r[1] for r in rows}
    if any(t.startswith("ELF/AArch64") for t in types):
        return "Linux AArch64 (ARM64)"
    if any(t.startswith("ELF/ARM") and "eabi5-hf" in t for t in types):
        return "Linux ARMv7 `eabi5`"
    if any(t.startswith("ELF/ARM") and "eabi5" in t for t in types):
        return "Linux ARMv5 `eabi5`"
    if any(t.startswith("ELF/ARM") and "oabi" in t for t in types):
        return "Linux ARMv4 `oabi`"
    if any(t.startswith("ELF/x86-64") for t in types):
        return "Linux x86-64"

    paths = [r[0].lower() for r in rows]
    if any(p.endswith(".hex") for p in paths):
        return "Bare-metal Microcontroller"
    if any(p.endswith(".bin") for p in paths):
        return "Bare-metal ARM Cortex-M"

    return "Embedded Architecture"


def infer_layers_from_manifest(manifest_path: Path) -> str:
    """Infer container and archive layer types from manifest."""
    rows = parse_manifest_file(manifest_path)
    types = {r[1] for r in rows}
    paths_str = " ".join(r[0] for r in rows).lower()

    layers: list[str] = []
    if "uImage" in types:
        layers.append("U-Boot")
    if "jffs2" in paths_str:
        layers.append("JFFS2")
    if "ext-fs" in types:
        layers.append("Ext4" if "ext4" in paths_str else "Ext2")
    if "squashfs" in paths_str or "squashfs" in types:
        layers.append("SquashFS")
    if "cramfs" in paths_str or "cramfs" in types:
        layers.append("CramFS")
    if "gzip" in types:
        layers.append("Gzip")
    if "tar" in types:
        layers.append("Tar")
    if "zip" in types:
        layers.append("Zip")
    if any(r[0].lower().endswith(".hex") for r in rows):
        layers.append("Intel HEX")

    if not layers:
        if any(r[0].lower().endswith(".bin") for r in rows):
            return "Monolithic `.bin`"
        return "Standard Container"

    return ", ".join(layers)


def infer_daemons(
    product: str,
    targets: dict[str, dict[str, Any]],
    manifest_path: Path,
) -> str:
    """Infer daemon list or firmware artifact description."""
    if product in targets:
        programs = targets[product].get("programs", {})
        if isinstance(programs, dict) and programs:
            return ", ".join(f"`{p}`" for p in programs)

    rows = parse_manifest_file(manifest_path)
    for path, _, size, _ in rows:
        filename = path.split("!")[-1].split("/")[-1]
        if filename.lower().endswith((".bin", ".hex")):
            size_kb = max(1, round(size / 1024))
            return f"Flash image `{filename}` ({size_kb} KB, no OS)"

    return "Gateway Server Stack"


def build_mcp_metrics_block(index_data: dict[str, Any]) -> str:
    """Build section 2 item 1 MCP metrics block."""
    total_inputs = int(
        index_data.get("total_unique_inputs", len(index_data.get("verdicts", {})))
    )
    verdicts = index_data.get("verdicts", {})
    total_verdicts = (
        sum(len(v) for v in verdicts.values()) if isinstance(verdicts, dict) else 0
    )
    gateways = index_data.get("gateways", [])
    active_emulators = (
        sum(
            1 for g in gateways if isinstance(g, dict) and g.get("status") == "emulated"
        )
        if isinstance(gateways, list)
        else 0
    )

    lines = [
        "1. **Ship results as data.** (**Shipped**) Published a deterministic, "
        "hash-pinned",
        f"   index at `results/mcp_index.json` ({total_inputs:,} unique frames, "
        f"{total_verdicts:,} verdicts across {active_emulators}",
        "   active gateway emulators). The MCP stays offline and read-only; "
        "it consumes",
        "   this generated corpus like it does for the Machine KB.",
    ]
    return "\n".join(lines)


def build_fleet_table_block(
    catalog_dir: Path,
    results_dir: Path,
    targets_dir: Path,
    index_data: dict[str, Any],
) -> str:
    """Build Supported Gateways & Hardware markdown table for all catalogued models."""
    catalog_entries = get_catalog_entries(catalog_dir)
    targets = get_oracle_targets(targets_dir)

    gw_index_map: dict[str, dict[str, Any]] = {}
    for g in index_data.get("gateways", []):
        if isinstance(g, dict) and "product" in g:
            gw_index_map[str(g["product"])] = g

    ordered_products = order_products(catalog_entries.keys(), FLEET_ORDER)

    lines = [
        "| Gateway | Firmware Version | System Architecture | Manifest Rows | "
        "Layer Types | Core Daemons / Firmware Artifact | Emulation Status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for product in ordered_products:
        cat = catalog_entries[product]
        ver = str(cat.get("version", "")).strip()
        ver_display = f"`{ver}` ({format_version(ver)})"

        manifest_path = results_dir / product / ver / "manifest.tsv"
        row_count = count_manifest_rows(manifest_path)
        row_count_str = f"{row_count:,}"

        meta = GATEWAY_METADATA.get(product, {})
        arch = meta.get("arch") or infer_architecture_from_manifest(manifest_path)
        layer_types = meta.get("layers") or infer_layers_from_manifest(manifest_path)
        daemons = meta.get("daemons") or infer_daemons(product, targets, manifest_path)

        idx_record = gw_index_map.get(product, {})
        status = idx_record.get("status", "")
        if status == "emulated":
            suites_count = len(idx_record.get("suites", []))
            emulation_status = (
                f"**Emulated** (Phase 2 — {suites_count} suites, full parity)"
            )
        elif "pending_status" in meta:
            emulation_status = meta["pending_status"]
        elif product in targets:
            emulation_status = "Pending Emulation (Phase 2 target defined)"
        elif is_no_linux_product(product, results_dir):
            emulation_status = "Pending Emulation (Bare-metal MCU — zero matrix value)"
        else:
            emulation_status = "Pending Emulation"

        row = (
            f"| **{product}** | {ver_display} | {arch} | {row_count_str} | "
            f"{layer_types} | {daemons} | {emulation_status} |"
        )
        lines.append(row)

    return "\n".join(lines)


def _format_census_status(
    product: str,
    meta: dict[str, str],
    idx_rec: dict[str, Any],
    results_dir: Path | None,
    targets: dict[str, dict[str, Any]],
) -> str:
    """Format census status parenthetical note for a standalone gateway."""
    if idx_rec.get("status") == "emulated":
        suites = len(idx_rec.get("suites", []))
        note_tpl = meta.get(
            "census_emulation_note",
            "Emulated — {suites} suites, full matrix parity",
        )
        note = note_tpl.format(suites=suites)
        return f"*({note})*"

    custom_note = meta.get("census_emulation_note")
    if not custom_note:
        if is_no_linux_product(product, results_dir):
            custom_note = (
                "bare-metal microcontroller flash image without OS/userland; "
                "basic lighting/shutter subset already covered by Linux "
                "gateways with zero added value to the matrix"
            )
        elif product in targets:
            custom_note = "Phase 2 target defined"
        else:
            custom_note = "bare-metal flash image"
    return f"*(Pending Emulation — {custom_note})*"


def _format_auxiliary_note(
    aux_entries: dict[str, dict[str, Any]],
    total_packages: int,
) -> str:
    """Format auxiliary hardware note at end of census block."""
    ordered_aux = order_products(aux_entries.keys(), ["H4684", "L4561N"])
    if not ordered_aux:
        return (
            "*(Note: Auxiliary touch screen and specialized bus interface hardware "
            "are also catalogued with full cryptographic provenance, bringing total "
            f"catalogued firmware packages to {total_packages}).*"
        )

    aux_items: list[str] = []
    for p in ordered_aux:
        cat = aux_entries[p]
        ver = str(cat.get("version", "")).strip()
        meta = GATEWAY_METADATA.get(p, {})
        title = meta.get("title", meta.get("census_desc", cat.get("name", p)))
        item_prefix = "the " if p == "H4684" else ""
        aux_items.append(f"{item_prefix}**{p}** {title} (`catalog/{p}/{ver}.yaml`)")

    if len(aux_items) == 1:
        aux_str = aux_items[0]
    elif len(aux_items) == 2:
        aux_str = f"{aux_items[0]} and {aux_items[1]}"
    else:
        aux_str = f"{', '.join(aux_items[:-1])}, and {aux_items[-1]}"

    return (
        "*(Note: Auxiliary touch screen and specialized bus interface hardware "
        f"such as {aux_str} are also catalogued with full cryptographic "
        f"provenance, bringing total catalogued firmware packages to "
        f"{total_packages}).*"
    )


def build_fleet_census_block(
    catalog_dir: Path,
    index_data: dict[str, Any],
    results_dir: Path | None = None,
    targets_dir: Path | None = None,
) -> str:
    """Build Ingested Fleet census list and auxiliary hardware note."""
    catalog_entries = get_catalog_entries(catalog_dir)
    targets = get_oracle_targets(targets_dir) if targets_dir else {}
    gw_index_map: dict[str, dict[str, Any]] = {}
    for g in index_data.get("gateways", []):
        if isinstance(g, dict) and "product" in g:
            gw_index_map[str(g["product"])] = g

    standalone_candidates = [
        p for p, doc in catalog_entries.items() if not is_auxiliary_product(p, doc)
    ]
    ordered_standalone = order_products(standalone_candidates, STANDALONE_GATEWAYS)

    lines: list[str] = []
    for product in ordered_standalone:
        cat = catalog_entries[product]
        ver = str(cat.get("version", "")).strip()
        dot_ver = format_version(ver)
        meta = GATEWAY_METADATA.get(product, {})
        desc = meta.get("census_desc") or cat.get("name") or "OpenWebNet gateway"
        idx_rec = gw_index_map.get(product, {})
        status_str = _format_census_status(product, meta, idx_rec, results_dir, targets)
        lines.append(f"- **{product}** (`{ver}` / {dot_ver}): {desc}. {status_str}")

    aux_entries = {
        p: doc for p, doc in catalog_entries.items() if is_auxiliary_product(p, doc)
    }
    total_packages = len(catalog_entries)
    lines.append("")
    lines.append(_format_auxiliary_note(aux_entries, total_packages))
    return "\n".join(lines)


def build_excluded_hardware_block(catalog_dir: Path) -> str:
    """Build Excluded Hardware table, ensuring catalogued products are excluded."""
    catalog_entries = get_catalog_entries(catalog_dir)
    catalog_products_lower = {p.lower() for p in catalog_entries}

    lines = [
        "| Product SKU | Description | Exclusion Reason |",
        "| :--- | :--- | :--- |",
    ]

    for sku, aliases, desc, reason in KNOWN_EXCLUDED_HARDWARE:
        if any(alias.lower() in catalog_products_lower for alias in aliases):
            continue
        lines.append(f"| **{sku}** | {desc} | {reason} |")

    return "\n".join(lines)


def build_status_phase1_block(catalog_dir: Path) -> str:
    """Build Status Phase 1 list item."""
    catalog_entries = get_catalog_entries(catalog_dir)
    standalone_candidates = [
        p for p, doc in catalog_entries.items() if not is_auxiliary_product(p, doc)
    ]
    ordered_standalone = order_products(standalone_candidates, PHASE1_ORDER)
    count = len(ordered_standalone)
    names_str = ", ".join(ordered_standalone)
    return (
        f"- **Phase 1: Complete Fleet Ingestion & Unpack.** Done for all {count} "
        f"standalone OpenWebNet gateways ({names_str}). Every manifest is verified "
        "byte-for-byte and covered by weekly CI reproducibility runs."
    )


def _format_phase2f_desc(
    prod: str,
    g: dict[str, Any],
    targets: dict[str, dict[str, Any]] | None,
) -> str:
    """Format description string for an emulated gateway in Phase 2f."""
    ver = str(g.get("version", ""))
    suites = len(g.get("suites", []))
    meta = GATEWAY_METADATA.get(prod, {})
    if "phase2f_desc" in meta:
        return meta["phase2f_desc"].format(suites=suites, version=ver)

    prog_names: list[str] = []
    if targets and prod in targets:
        prog_names = list(targets[prod].get("programs", {}).keys())

    if prog_names:
        prog_str = ", ".join(f"`{p}`" for p in prog_names)
        return f"Gateway stack ({prog_str} — {suites} suites)"
    return f"Gateway stack — {suites} suites"


def build_status_phase2f_block(
    index_data: dict[str, Any],
    results_dir: Path,
    targets: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Build Status Phase 2f list item and sub-bullets."""
    gw_index_map: dict[str, dict[str, Any]] = {}
    emulated_list: list[dict[str, Any]] = []
    for g in index_data.get("gateways", []):
        if isinstance(g, dict) and g.get("status") == "emulated":
            prod = str(g.get("product", ""))
            if prod:
                gw_index_map[prod] = g
                emulated_list.append(g)

    active_count = len(emulated_list)
    suite_counts = [len(g.get("suites", [])) for g in emulated_list if "suites" in g]
    std_suites = min(suite_counts) if suite_counts else 18

    all_tsvs = list(results_dir.glob("*/[0-9]*/oracle/full/*.tsv"))
    total_tsvs = len(all_tsvs)

    extra_clauses: list[str] = []
    for g in emulated_list:
        prod = str(g.get("product", ""))
        g_suites = g.get("suites", [])
        if len(g_suites) > std_suites:
            if prod == "F454":
                extra_clauses.append("including sound source suite on F454")
            else:
                extra_diff = len(g_suites) - std_suites
                extra_clauses.append(f"{extra_diff} extra suite on {prod}")

    extra_summary = f", {'; '.join(extra_clauses)}" if extra_clauses else ""

    top_line = (
        "- **Phase 2f: Gateway Fleet Target Emulation.** Expanded execution harness "
        "in `oracle/qemu_target.py` and target specifications in `oracle/targets/` "
        f"supporting {active_count} active gateways under QEMU user emulation, "
        f"achieving **full matrix parity across all {std_suites} standard test suites "
        f"({total_tsvs} complete suite TSVs{extra_summary})**:"
    )

    lines = [top_line]
    ordered_emulated = order_products(gw_index_map.keys(), EMULATED_ORDER)
    for prod in ordered_emulated:
        g = gw_index_map[prod]
        ver = str(g.get("version", ""))
        desc = _format_phase2f_desc(prod, g, targets)
        lines.append(f"  - **{prod}** (`{ver}`): {desc}.")

    return "\n".join(lines)


def build_status_phase3_block(
    index_data: dict[str, Any],
    results_dir: Path | None = None,
) -> str:
    """Build Status Phase 3 list item."""
    total_inputs = int(
        index_data.get("total_unique_inputs", len(index_data.get("verdicts", {})))
    )
    verdicts = index_data.get("verdicts", {})
    total_verdicts = (
        sum(len(v) for v in verdicts.values()) if isinstance(verdicts, dict) else 0
    )

    all_suites: set[str] = set()
    if isinstance(verdicts, dict):
        for entries in verdicts.values():
            if isinstance(entries, list):
                for e in entries:
                    if isinstance(e, dict) and "suite" in e:
                        all_suites.add(str(e["suite"]))
    total_suites = len(all_suites)

    emulated_products: list[str] = []
    catalogued_no_linux: list[str] = []
    for g in index_data.get("gateways", []):
        if isinstance(g, dict):
            prod = str(g.get("product", ""))
            status = g.get("status", "")
            if status == "emulated":
                emulated_products.append(prod)
            elif status == "catalogued" and is_no_linux_product(prod, results_dir):
                catalogued_no_linux.append(prod)

    ordered_emulated = order_products(emulated_products, EMULATED_ORDER)
    active_count = len(ordered_emulated)
    active_str = ", ".join(ordered_emulated)

    ordered_no_linux = order_products(catalogued_no_linux, NO_LINUX_ORDER)
    non_linux_str = ", ".join(ordered_no_linux)

    return (
        "- **Phase 3: Hash-Pinned MCP Verdict Index.** Completed schema 1.1.0 "
        "index covering the full catalogued fleet. `tools/mcp_index.py` aggregates "
        "verdicts across suites and gateways into `results/mcp_index.json`, protected "
        "by a canonical SHA-256 fingerprint (`verdicts_sha256`) for direct consumption "
        f"by `openwebnet-mcp`. The index tracks **{total_inputs:,} unique OpenWebNet "
        f"frames** across **{total_suites} test suites** and **{active_count} active "
        f"gateways** ({active_str}), delivering **{total_verdicts:,} deterministic "
        "verdict entries** with a zero-diff PR consistency gate in CI "
        f"(`tools/mcp_index.py --check`). Catalogued devices without Linux userland "
        f'({non_linux_str}) are indexed with `status: "catalogued"` and empty suite '
        "arrays (bare-metal microcontroller flash firmware or interfaces without an "
        "OS; basic lighting/shutter/scenario OpenWebNet subsets already 100% covered)."
    )


def replace_marker_block(
    content: str,
    start_marker: str,
    end_marker: str,
    body: str,
) -> str:
    """Replace content between start_marker and end_marker with given body."""
    pattern = re.compile(
        rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}",
        re.DOTALL,
    )
    expected_block = f"{start_marker}\n{body.strip()}\n{end_marker}"
    return pattern.sub(lambda _: expected_block, content, count=1)


def sync_readme(
    readme_path: Path,
    root: Path = ROOT,
    update: bool = False,
) -> tuple[bool, str, list[str]]:
    """Check or update README.md against codebase data.

    Returns (is_in_sync, unified_diff_str, report_messages).
    """
    messages: list[str] = []
    if not readme_path.is_file():
        return False, "", [f"README file not found: {readme_path}"]

    content = readme_path.read_text(encoding="utf-8")

    # Validate marker presence
    for start_m, end_m in MARKERS:
        if start_m not in content or end_m not in content:
            messages.append(f"Missing marker pair: {start_m} / {end_m}")

    if messages:
        return False, "", messages

    catalog_dir = root / "catalog"
    results_dir = root / "results"
    targets_dir = root / "oracle" / "targets"
    index_path = results_dir / "mcp_index.json"

    try:
        index_data = load_mcp_index(index_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, "", [f"Failed to load mcp_index: {exc}"]

    targets = get_oracle_targets(targets_dir)

    # Build generated blocks
    mcp_metrics = build_mcp_metrics_block(index_data)
    fleet_tbl = build_fleet_table_block(
        catalog_dir, results_dir, targets_dir, index_data
    )
    fleet_cen = build_fleet_census_block(
        catalog_dir, index_data, results_dir, targets_dir
    )
    excluded_hw = build_excluded_hardware_block(catalog_dir)
    status_p1 = build_status_phase1_block(catalog_dir)
    status_p2f = build_status_phase2f_block(index_data, results_dir, targets)
    status_p3 = build_status_phase3_block(index_data, results_dir)

    new_content = content
    new_content = replace_marker_block(
        new_content, MCP_METRICS_START, MCP_METRICS_END, mcp_metrics
    )
    new_content = replace_marker_block(
        new_content, FLEET_TABLE_START, FLEET_TABLE_END, fleet_tbl
    )
    new_content = replace_marker_block(
        new_content, FLEET_CENSUS_START, FLEET_CENSUS_END, fleet_cen
    )
    new_content = replace_marker_block(
        new_content, EXCLUDED_HARDWARE_START, EXCLUDED_HARDWARE_END, excluded_hw
    )
    new_content = replace_marker_block(
        new_content, STATUS_PHASE1_START, STATUS_PHASE1_END, status_p1
    )
    new_content = replace_marker_block(
        new_content, STATUS_PHASE2F_START, STATUS_PHASE2F_END, status_p2f
    )
    new_content = replace_marker_block(
        new_content, STATUS_PHASE3_START, STATUS_PHASE3_END, status_p3
    )

    if new_content == content:
        messages.append(f"README in sync: {rel_path(readme_path, root)}")
        return True, "", messages

    diff_lines = list(
        difflib.unified_diff(
            content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{rel_path(readme_path, root)} (current)",
            tofile=f"{rel_path(readme_path, root)} (expected)",
        )
    )
    diff_str = "".join(diff_lines)

    if update:
        readme_path.write_text(new_content, encoding="utf-8")
        messages.append(f"Updated README in place: {rel_path(readme_path, root)}")
        return True, diff_str, messages

    messages.append(f"README out of sync: {rel_path(readme_path, root)}")
    return False, diff_str, messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize README.md metrics and tables with oracle codebase."
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=ROOT / "README.md",
        help="Path to README.md (default: ROOT/README.md)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root directory (default: ROOT)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether README.md is in sync, exit 1 with diff if drifted",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update README.md in place",
    )

    args = parser.parse_args(argv)
    do_update = not args.check

    in_sync, diff, messages = sync_readme(
        readme_path=args.readme,
        root=args.root,
        update=do_update,
    )

    if args.check:
        if not in_sync:
            print(
                "sync_readme: ERROR - README.md is out of sync. Diff:",
                file=sys.stderr,
            )
            sys.stderr.write(diff)
            for msg in messages:
                print(f"  {msg}", file=sys.stderr)
            return 1
        print("sync_readme: ok (README.md is up to date)")
        return 0

    for msg in messages:
        print(f"sync_readme: {msg}")
    return 0 if in_sync else 1


if __name__ == "__main__":
    sys.exit(main())
