#!/usr/bin/env python3
"""check -- compare oracle results with OWNd's parser and live bus evidence.

Reads an oracle TSV (results/<product>/<version>/oracle/<harness>/<suite>.tsv)
and writes results/<product>/<version>/checks/<suite>.tsv with two extra columns:
  * ownd: OWNd's parse of each own: output (type name or unparsed / error), or
          EMPTY ('-') if no own: output was emitted;
  * live: agree <evidence_id> / diverge <evidence_id> / unchecked.

It is a separate file so an OWNd bump or a new live capture never invalidates
the oracle TSV itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import OWNd
from OWNd.message import OWNMessage

ROOT = Path(__file__).resolve().parent.parent

COLUMNS_IN = ("direction", "input", "reply", "verdict", "output")
COLUMNS_EXTRA = ("ownd", "live")
COLUMNS_OUT = COLUMNS_IN + COLUMNS_EXTRA

OUTPUT_SEP = " | "
EMPTY = "-"

HEADER_RE = re.compile(r"^#\s*([A-Za-z0-9_]+)=(.*)$")


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    gateway_models: tuple[str, ...]
    tx_input: str
    rx_outputs: tuple[str, ...]


def parse_ownd_frame(frame: str) -> str:
    """Parse a single OpenWebNet frame with OWNd and return class name or failure."""
    try:
        msg = OWNMessage.parse(frame)
        if msg is not None:
            return type(msg).__name__
        return "unparsed"
    except Exception as exc:  # noqa: BLE001
        return f"error:{type(exc).__name__}"


def parse_ownd_column(output_cell: str) -> str:
    """Parse all own: items in the output cell, joined by OUTPUT_SEP."""
    if output_cell == EMPTY or not output_cell:
        return EMPTY
    parts = output_cell.split(OUTPUT_SEP)
    ownd_results: list[str] = []
    for raw_part in parts:
        part = raw_part.strip()
        if part.startswith("own:"):
            frame = part[4:].replace("\\x7c", "|")
            ownd_results.append(parse_ownd_frame(frame))
    if not ownd_results:
        return EMPTY
    return OUTPUT_SEP.join(ownd_results)


def extract_own_frames(output_cell: str) -> list[str]:
    """Extract raw OpenWebNet frames from the output cell."""
    if output_cell == EMPTY or not output_cell:
        return []
    parts = output_cell.split(OUTPUT_SEP)
    frames: list[str] = []
    for raw_part in parts:
        part = raw_part.strip()
        if part.startswith("own:"):
            frames.append(part[4:].replace("\\x7c", "|"))
    return frames


def _models_for_gateway(gw_model: str | None) -> tuple[str, ...]:
    if not gw_model:
        return ()
    models = [gw_model]
    if gw_model == "MH200":
        models.append("MH200N")
    elif gw_model == "MH200N":
        models.append("MH200")
    return tuple(models)


def _load_evidence_item(sub: Path) -> list[EvidenceItem]:
    manifest_path = sub / "manifest.json"
    frames_path = sub / "frames.jsonl"
    if not manifest_path.is_file() or not frames_path.is_file():
        return []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_id = str(manifest.get("evidence_id", sub.name))
    models = _models_for_gateway(manifest.get("environment", {}).get("gateway_model"))

    items: list[EvidenceItem] = []
    tx_input: str | None = None
    rx_outputs: list[str] = []

    for raw_line in frames_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        record = json.loads(line)
        direction = record.get("dir")
        raw = record.get("raw")
        if not raw:
            continue

        if direction == "tx":
            if tx_input is not None:
                items.append(
                    EvidenceItem(
                        evidence_id=evidence_id,
                        gateway_models=models,
                        tx_input=tx_input,
                        rx_outputs=tuple(rx_outputs),
                    )
                )
            tx_input = str(raw)
            rx_outputs = []
        elif direction == "rx" and tx_input is not None:
            rx_outputs.append(str(raw))

    if tx_input is not None:
        items.append(
            EvidenceItem(
                evidence_id=evidence_id,
                gateway_models=models,
                tx_input=tx_input,
                rx_outputs=tuple(rx_outputs),
            )
        )
    return items


def load_evidence(evidence_dir: Path) -> list[EvidenceItem]:
    """Load evidence items from an Encyclopedia evidence directory."""
    items: list[EvidenceItem] = []
    if not evidence_dir.is_dir():
        return items

    for sub in sorted(evidence_dir.iterdir()):
        if sub.is_dir():
            items.extend(_load_evidence_item(sub))
    return items


def find_default_evidence_dir(root: Path) -> Path | None:
    candidate = root.parent / "OpenWebNet-Encyclopedia" / "evidence"
    if candidate.is_dir():
        return candidate
    return None


def match_live(
    direction: str,
    input_val: str,
    output_val: str,
    evidence_items: list[EvidenceItem],
    product: str | None = None,
) -> str:
    """Compare an oracle row against loaded live evidence."""
    if direction != "down":
        return "unchecked"

    oracle_own = extract_own_frames(output_val)

    for item in evidence_items:
        if item.gateway_models and product and product not in item.gateway_models:
            continue
        if item.tx_input == input_val:
            if list(item.rx_outputs) == oracle_own:
                return f"agree {item.evidence_id}"
            return f"diverge {item.evidence_id}"

    return "unchecked"


def check_path_for_oracle_tsv(oracle_path: Path) -> Path:
    """Derive results/<product>/<version>/checks/<suite>.tsv from an oracle TSV."""
    parts = list(oracle_path.parts)
    try:
        oracle_idx = parts.index("oracle")
    except ValueError:
        return oracle_path.parent / f"checked_{oracle_path.name}"

    prefix = Path(*parts[:oracle_idx])
    remainder = parts[oracle_idx + 1 :]
    if len(remainder) >= 2 and remainder[0] == "full":
        return prefix / "checks" / remainder[-1]
    return prefix / "checks" / Path(*remainder)


def check_content(
    text: str,
    evidence_items: list[EvidenceItem] | None = None,
) -> str:
    """Process an oracle TSV string and return the checked TSV string."""
    evidence = evidence_items or []
    lines = text.splitlines()
    header_lines: list[str] = []
    header: dict[str, str] = {}
    data_lines: list[str] = []
    columns_found = False

    for line in lines:
        if line.startswith("#"):
            header_lines.append(line)
            m = HEADER_RE.match(line)
            if m:
                header[m.group(1)] = m.group(2)
        elif not columns_found:
            cols = line.split("\t")
            if cols[: len(COLUMNS_IN)] != list(COLUMNS_IN):
                raise ValueError(f"unexpected columns: {line!r}")
            columns_found = True
        elif line.strip():
            data_lines.append(line)

    if not columns_found:
        raise ValueError("no column header line found in oracle TSV")

    ownd_ver = getattr(OWNd, "__version__", "unknown")
    out_headers = list(header_lines)
    out_headers.append(f"# ownd_version={ownd_ver}")

    product = header.get("product")
    out_rows: list[str] = []
    for line in data_lines:
        cells = line.split("\t")
        if len(cells) < len(COLUMNS_IN):
            continue
        direction, in_val, reply, verdict, output = cells[: len(COLUMNS_IN)]
        ownd_col = parse_ownd_column(output)
        live_col = match_live(direction, in_val, output, evidence, product=product)
        out_rows.append(
            "\t".join([direction, in_val, reply, verdict, output, ownd_col, live_col])
        )

    out_lines = [*out_headers, "\t".join(COLUMNS_OUT), *out_rows]
    return "\n".join(out_lines) + "\n"


def process_oracle_tsv(
    oracle_path: Path,
    out_path: Path | None = None,
    evidence_items: list[EvidenceItem] | None = None,
    *,
    to_stdout: bool = False,
) -> Path | None:
    text = oracle_path.read_text(encoding="ascii")
    checked = check_content(text, evidence_items=evidence_items)

    if to_stdout:
        sys.stdout.write(checked)
        return None

    target = out_path or check_path_for_oracle_tsv(oracle_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(checked, encoding="ascii", newline="\n")
    return target


def _collect_targets(paths: list[Path], *, all_flag: bool) -> list[Path]:
    targets: list[Path] = []
    if all_flag:
        for tsv in sorted((ROOT / "results").rglob("*.tsv")):
            parts = tsv.parts
            if "oracle" in parts and "boundary" not in parts:
                targets.append(tsv)
        return targets

    for p in paths:
        if p.is_dir():
            for tsv in sorted(p.rglob("*.tsv")):
                if "oracle" in tsv.parts and "boundary" not in tsv.parts:
                    targets.append(tsv)
        elif p.is_file():
            targets.append(p)
    return targets


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="one or more oracle TSV files to check",
    )
    ap.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="path to OpenWebNet-Encyclopedia evidence directory",
    )
    ap.add_argument(
        "--out",
        "-o",
        type=Path,
        default=None,
        help="explicit output path (only valid with single input file)",
    )
    ap.add_argument(
        "--stdout",
        action="store_true",
        help="write checked TSV to stdout",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="process all oracle suite results in results/",
    )
    args = ap.parse_args()

    ev_dir = args.evidence_dir or find_default_evidence_dir(ROOT)
    evidence_items = load_evidence(ev_dir) if ev_dir else []

    targets = _collect_targets(args.paths, all_flag=args.all)
    if not targets:
        ap.error("no input oracle TSVs specified (use paths or --all)")

    if args.out and len(targets) > 1:
        ap.error("--out cannot be used with multiple inputs")

    for t in targets:
        dest = process_oracle_tsv(
            t,
            out_path=args.out,
            evidence_items=evidence_items,
            to_stdout=args.stdout,
        )
        if dest and not args.stdout:
            try:
                rel = dest.resolve().relative_to(ROOT.resolve())
            except ValueError:
                rel = dest
            print(f"wrote {rel}")


if __name__ == "__main__":
    main()
