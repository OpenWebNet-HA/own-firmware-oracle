#!/usr/bin/env python3
"""mcp_index -- build a deterministic, hash-pinned verdict index for openwebnet-mcp.

Scans `results/<product>/<version>/oracle/**/*.tsv` and aggregates all input
frames (down/up) into a deterministic JSON index mapping each frame to its
observed gateway replies, verdicts, bus outputs, and emitted events with
complete cryptographic and row-level provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class VerdictEntry:
    direction: str
    product: str
    version: str
    image_sha256: str
    target_sha256: str
    suite: str
    suite_sha256: str
    reply: str
    verdict: str
    bus_frames: list[str]
    emitted_own: list[str]
    source_tsv: str
    line_number: int


@dataclass
class GatewayRecord:
    product: str
    version: str
    image_sha256: str
    target_sha256: str
    suites: set[str]


def parse_output_column(output_str: str) -> tuple[list[str], list[str]]:
    """Parse output column into bus hex frames and emitted OpenWebNet frames."""
    bus_frames: list[str] = []
    emitted_own: list[str] = []
    if not output_str or output_str == "-":
        return bus_frames, emitted_own

    for part in output_str.split(" | "):
        stripped_part = part.strip()
        if stripped_part.startswith("bus:"):
            bus_frames.append(stripped_part[4:].strip())
        elif stripped_part.startswith("own:"):
            emitted_own.append(stripped_part[4:].strip())
    return bus_frames, emitted_own


def parse_header(lines: list[str]) -> dict[str, str]:
    """Parse # key=value comments into a header dictionary."""
    hdr: dict[str, str] = {}
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line.startswith("#"):
            continue
        rest = stripped_line[1:].strip()
        for token in rest.split():
            if "=" in token:
                k, v = token.split("=", 1)
                hdr[k] = v
    return hdr


def read_oracle_tsv(
    tsv_path: Path, root: Path = ROOT
) -> list[tuple[str, VerdictEntry]]:
    """Read an oracle TSV file and return (input_frame, VerdictEntry) pairs."""
    try:
        rel_tsv = str(tsv_path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        rel_tsv = str(tsv_path).replace("\\", "/")

    lines = tsv_path.read_text(encoding="utf-8").splitlines()
    header_lines = [ln for ln in lines if ln.startswith("#")]
    hdr = parse_header(header_lines)

    product = hdr.get("product", "")
    version = hdr.get("version", "")
    image_sha256 = hdr.get("image_sha256", "")
    target_sha256 = hdr.get("target_sha256", "")
    suite = hdr.get("suite", "")
    suite_sha256 = hdr.get("suite_sha256", "")

    entries: list[tuple[str, VerdictEntry]] = []
    col_idx: dict[str, int] = {}

    for line_idx, line in enumerate(lines, start=1):
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue

        parts = stripped_line.split("\t")
        if not col_idx:
            # Header line: direction, input, reply, verdict, output
            col_idx = {name: idx for idx, name in enumerate(parts)}
            continue

        direction = parts[col_idx["direction"]] if "direction" in col_idx else ""
        inp = parts[col_idx["input"]] if "input" in col_idx else ""
        reply = parts[col_idx["reply"]] if "reply" in col_idx else ""
        verdict = parts[col_idx["verdict"]] if "verdict" in col_idx else ""
        output_str = parts[col_idx["output"]] if "output" in col_idx else ""

        bus_frames, emitted_own = parse_output_column(output_str)

        entry = VerdictEntry(
            direction=direction,
            product=product,
            version=version,
            image_sha256=image_sha256,
            target_sha256=target_sha256,
            suite=suite,
            suite_sha256=suite_sha256,
            reply=reply,
            verdict=verdict,
            bus_frames=bus_frames,
            emitted_own=emitted_own,
            source_tsv=rel_tsv,
            line_number=line_idx,
        )
        entries.append((inp, entry))

    return entries


def build_index(results_dir: Path, root: Path = ROOT) -> dict[str, object]:
    """Build the index dictionary from all oracle TSVs under results_dir."""
    tsv_files = sorted(results_dir.glob("*/[0-9]*/oracle/full/*.tsv"))

    gateways_map: dict[tuple[str, str], GatewayRecord] = {}
    verdicts_map: dict[str, list[dict[str, object]]] = {}

    for tsv_path in tsv_files:
        items = read_oracle_tsv(tsv_path, root=root)
        if not items:
            continue

        first_entry = items[0][1]
        gw_key = (first_entry.product, first_entry.version)
        if gw_key not in gateways_map:
            gateways_map[gw_key] = GatewayRecord(
                product=first_entry.product,
                version=first_entry.version,
                image_sha256=first_entry.image_sha256,
                target_sha256=first_entry.target_sha256,
                suites=set(),
            )
        if first_entry.suite:
            gateways_map[gw_key].suites.add(first_entry.suite)

        for inp, entry in items:
            if inp not in verdicts_map:
                verdicts_map[inp] = []
            verdicts_map[inp].append(asdict(entry))

    gateways_list = [
        {
            "image_sha256": gw.image_sha256,
            "product": gw.product,
            "suites": sorted(gw.suites),
            "target_sha256": gw.target_sha256,
            "version": gw.version,
        }
        for (_prod, _vers), gw in sorted(gateways_map.items())
    ]

    # Sort verdicts list by input frame
    sorted_verdicts: dict[str, list[dict[str, object]]] = {}
    for frame in sorted(verdicts_map.keys()):
        # Sort entries per frame deterministically
        entries = sorted(
            verdicts_map[frame],
            key=lambda e: (
                str(e["product"]),
                str(e["version"]),
                str(e["suite"]),
                int(str(e["line_number"])),
            ),
        )
        sorted_verdicts[frame] = entries

    canonical_verdicts_json = json.dumps(
        sorted_verdicts, sort_keys=True, separators=(",", ":")
    )
    content_sha256 = hashlib.sha256(canonical_verdicts_json.encode("utf-8")).hexdigest()

    return {
        "format_version": "1.0.0",
        "generator": "own-firmware-oracle",
        "schema_version": "1.0.0",
        "verdicts_sha256": content_sha256,
        "total_unique_inputs": len(sorted_verdicts),
        "gateways": gateways_list,
        "verdicts": sorted_verdicts,
    }


def format_index_json(index_data: dict[str, object]) -> str:
    """Format index data as indented JSON string ending with a newline."""
    return json.dumps(index_data, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic, hash-pinned verdict index for openwebnet-mcp."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "results",
        help="Root directory containing results (default: ROOT/results)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results" / "mcp_index.json",
        help="Output JSON index path (default: results/mcp_index.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check that existing output file matches newly generated index",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary of indexed frames and gateways",
    )

    args = parser.parse_args(argv)

    index_data = build_index(args.results_dir)
    rendered = format_index_json(index_data)

    gateways_obj = index_data.get("gateways", [])
    gateways_list: list[dict[str, object]] = (
        gateways_obj if isinstance(gateways_obj, list) else []
    )

    total_inputs = int(str(index_data["total_unique_inputs"]))
    gateways_count = len(gateways_list)

    if args.check:
        if not args.out.exists():
            print(f"mcp_index: ERROR - {args.out} does not exist", file=sys.stderr)
            return 1
        current = args.out.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"mcp_index: ERROR - {args.out} is out of date; re-run without --check",
                file=sys.stderr,
            )
            return 1
        print(f"mcp_index: ok ({total_inputs} inputs, {gateways_count} gateways)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    try:
        rel_out = str(args.out.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        rel_out = str(args.out).replace("\\", "/")

    print(f"wrote {rel_out} ({total_inputs} inputs, {gateways_count} gateways)")

    if args.summary:
        print(f"Total Unique Inputs: {total_inputs}")
        print(f"Gateways Indexed: {gateways_count}")
        for gw in gateways_list:
            suites_val = gw["suites"]
            print(
                f"  - {gw['product']} {gw['version']}: {len(suites_val)} suites"  # type: ignore[arg-type]
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
