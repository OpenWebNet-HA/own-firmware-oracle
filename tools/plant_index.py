#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""plant_index -- build a deterministic index of closed-loop virtual plant verdicts.

Scans `results/<product>/<version>/oracle/plant-*/<suite>.tsv` and writes
`results/plant_index.json` without touching `results/mcp_index.json`.
Each entry records `responder`, `plant_sha256`, `bus_frames`, `bus_rx`,
`cmd_replies`, and `evt_events`.
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
class PlantVerdictEntry:
    direction: str
    product: str
    version: str
    harness: str
    responder: str
    plant_sha256: str
    image_sha256: str
    target_sha256: str
    suite: str
    suite_sha256: str
    reply: str
    verdict: str
    bus_frames: list[str]
    bus_rx: list[str]
    cmd_replies: list[str]
    evt_events: list[str]
    source_tsv: str
    line_number: int


def parse_header(lines: list[str]) -> dict[str, str]:
    """Parse # key=value comments into a header dictionary."""
    hdr: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        for token in stripped[1:].strip().split():
            if "=" in token:
                k, v = token.split("=", 1)
                hdr[k] = v
    return hdr


def parse_plant_output_column(
    output_str: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Parse plant TSV output into (bus_frames, bus_rx, cmd_replies, evt_events)."""
    bus_frames: list[str] = []
    bus_rx: list[str] = []
    cmd_replies: list[str] = []
    evt_events: list[str] = []
    if not output_str or output_str == "-":
        return bus_frames, bus_rx, cmd_replies, evt_events

    for raw_part in output_str.split(" | "):
        part = raw_part.strip()
        if part.startswith("bus:"):
            bus_frames.append(part[4:].strip())
        elif part.startswith("bus_rx:"):
            bus_rx.append(part[7:].strip())
        elif part.startswith("cmd:"):
            cmd_replies.append(part[4:].strip().replace("\\x7c", "|"))
        elif part.startswith(("evt:", "own:")):
            evt_events.append(part[4:].strip().replace("\\x7c", "|"))
    return bus_frames, bus_rx, cmd_replies, evt_events


def fixture_sha256(fixture_name: str, fixtures_dir: Path | None = None) -> str:
    """Compute SHA-256 of oracle/plant/fixtures/<fixture_name>.yaml if present."""
    fdir = fixtures_dir or (ROOT / "oracle" / "plant" / "fixtures")
    fname = fixture_name if fixture_name.endswith(".yaml") else f"{fixture_name}.yaml"
    fpath = fdir / fname
    if not fpath.is_file():
        return ""
    return hashlib.sha256(fpath.read_bytes()).hexdigest()


def read_plant_tsv(
    tsv_path: Path,
    root: Path = ROOT,
    fixtures_dir: Path | None = None,
) -> list[tuple[str, PlantVerdictEntry]]:
    """Read a plant TSV file and return (input_frame, PlantVerdictEntry) pairs."""
    try:
        rel_tsv = str(tsv_path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        rel_tsv = str(tsv_path).replace("\\", "/")

    lines = tsv_path.read_text(encoding="utf-8").splitlines()
    header_lines = [ln for ln in lines if ln.startswith("#")]
    hdr = parse_header(header_lines)

    product = hdr.get("product", "")
    version = hdr.get("version", "")
    harness = hdr.get("harness", "")
    fixture_name = harness.split(":", 1)[1] if ":" in harness else harness
    responder = f"pic:{harness}" if harness.startswith("plant:") else harness
    plant_sha = fixture_sha256(fixture_name, fixtures_dir=fixtures_dir)
    image_sha256 = hdr.get("image_sha256", "")
    target_sha256 = hdr.get("target_sha256", "")
    suite = hdr.get("suite", "")
    suite_sha256 = hdr.get("suite_sha256", "")

    entries: list[tuple[str, PlantVerdictEntry]] = []
    col_idx: dict[str, int] = {}

    for line_idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if not col_idx:
            col_idx = {name: idx for idx, name in enumerate(parts)}
            continue

        direction = parts[col_idx["direction"]] if "direction" in col_idx else ""
        inp = parts[col_idx["input"]] if "input" in col_idx else ""
        reply = parts[col_idx["reply"]] if "reply" in col_idx else ""
        verdict = parts[col_idx["verdict"]] if "verdict" in col_idx else ""
        output_str = parts[col_idx["output"]] if "output" in col_idx else ""

        bus_tx, bus_rx, cmd_rep, evt_ev = parse_plant_output_column(output_str)
        entry = PlantVerdictEntry(
            direction=direction,
            product=product,
            version=version,
            harness=harness,
            responder=responder,
            plant_sha256=plant_sha,
            image_sha256=image_sha256,
            target_sha256=target_sha256,
            suite=suite,
            suite_sha256=suite_sha256,
            reply=reply,
            verdict=verdict,
            bus_frames=bus_tx,
            bus_rx=bus_rx,
            cmd_replies=cmd_rep,
            evt_events=evt_ev,
            source_tsv=rel_tsv,
            line_number=line_idx,
        )
        entries.append((inp, entry))

    return entries


def build_plant_index(
    results_dir: Path,
    root: Path = ROOT,
    fixtures_dir: Path | None = None,
) -> dict[str, object]:
    """Build deterministic plant verdict index from results/*/*/oracle/plant-*/*.tsv."""
    tsv_files = sorted(results_dir.glob("*/[0-9]*/oracle/plant-*/*.tsv"))
    verdicts_map: dict[str, list[dict[str, object]]] = {}
    suites_seen: set[str] = set()

    for tsv_path in tsv_files:
        items = read_plant_tsv(tsv_path, root=root, fixtures_dir=fixtures_dir)
        for inp, entry in items:
            if entry.suite:
                suites_seen.add(entry.suite)
            verdicts_map.setdefault(inp, []).append(asdict(entry))

    sorted_verdicts: dict[str, list[dict[str, object]]] = {}
    for frame in sorted(verdicts_map.keys()):
        sorted_verdicts[frame] = sorted(
            verdicts_map[frame],
            key=lambda e: (
                str(e["product"]),
                str(e["version"]),
                str(e["harness"]),
                str(e["suite"]),
                int(str(e["line_number"])),
            ),
        )

    canonical = json.dumps(sorted_verdicts, sort_keys=True, separators=(",", ":"))
    verdicts_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return {
        "format_version": "1.0.0",
        "generator": "own-firmware-oracle/plant_index",
        "schema_version": "1.0.0",
        "suites": sorted(suites_seen),
        "total_unique_inputs": len(sorted_verdicts),
        "verdicts": sorted_verdicts,
        "verdicts_sha256": verdicts_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "results",
        help="Root directory containing results (default: ROOT/results)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "results" / "plant_index.json",
        help="Output JSON index path (default: results/plant_index.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify existing plant_index.json matches newly built index",
    )
    args = parser.parse_args(argv)

    index_data = build_plant_index(args.results_dir, root=ROOT)
    rendered = json.dumps(index_data, indent=2, sort_keys=True) + "\n"
    total_inputs = int(str(index_data["total_unique_inputs"]))

    if args.check:
        if not args.out.is_file():
            print(
                f"plant_index: ERROR - {args.out} does not exist",
                file=sys.stderr,
            )
            return 1
        current = args.out.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"plant_index: ERROR - {args.out} is out of date",
                file=sys.stderr,
            )
            return 1
        print(f"plant_index: ok ({total_inputs} inputs)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.out} ({total_inputs} inputs)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
