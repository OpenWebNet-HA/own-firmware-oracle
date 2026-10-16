#!/usr/bin/env python3
"""check -- compare oracle results with OWNd's parser and live bus evidence.

Reads an oracle TSV (results/<product>/<version>/oracle/<harness>/<suite>.tsv)
and writes results/<product>/<version>/checks/<suite>.tsv with two extra columns:
  * ownd: OWNd's parse of each own: output (type name or unparsed / error), or
          EMPTY ('-') if no own: output was emitted;
  * live: agree <evidence_id> / agree_bus <evidence_id> / diverge <evidence_id> /
          unchecked (with [cross:<model>@<ver>] when compared across versions).

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
SIGNAL_FRAMES = ("*#*1##", "*#*0##")

HEADER_RE = re.compile(r"^#\s*([A-Za-z0-9_]+)=(.*)$")


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    gateway_models: tuple[str, ...]
    tx_input: str
    rx_outputs: tuple[str, ...]
    exact_model: str | None = None
    gateway_version: str | None = None
    rx_reply: str | None = None


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


def normalize_version(v: str | None) -> tuple[int, ...] | None:
    """Normalize a 6-digit ('020102') or dotted ('2.1.2') version string to ints."""
    if not v:
        return None
    s = v.strip()
    if len(s) == 6 and s.isdigit():
        return (int(s[0:2]), int(s[2:4]), int(s[4:6]))
    parts = s.split(".")
    if all(p.isdigit() for p in parts):
        return tuple(int(p) for p in parts)
    return None


def _models_for_gateway(gw_model: str | None) -> tuple[str, ...]:
    if not gw_model:
        return ()
    models = [gw_model]
    if gw_model == "MH200":
        models.append("MH200N")
    elif gw_model == "MH200N":
        models.append("MH200")
    elif gw_model == "H4890":
        models.append("H4684")
    elif gw_model == "H4684":
        models.append("H4890")
    return tuple(models)


def _pairs_to_items(
    evidence_id: str,
    models: tuple[str, ...],
    records: list[tuple[str, str]],
    *,
    exact_model: str | None = None,
    gateway_version: str | None = None,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    tx_input: str | None = None
    rx_outputs: list[str] = []
    rx_reply: str | None = None

    for direction, raw in records:
        if direction == "tx":
            if tx_input is not None:
                items.append(
                    EvidenceItem(
                        evidence_id=evidence_id,
                        gateway_models=models,
                        tx_input=tx_input,
                        rx_outputs=tuple(rx_outputs),
                        exact_model=exact_model,
                        gateway_version=gateway_version,
                        rx_reply=rx_reply,
                    )
                )
            tx_input = raw
            rx_outputs = []
            rx_reply = None
        elif direction == "rx" and tx_input is not None:
            if raw == "*#*1##":
                rx_reply = "ack"
            elif raw == "*#*0##":
                rx_reply = "nack"
            else:
                rx_outputs.append(raw)

    if tx_input is not None:
        items.append(
            EvidenceItem(
                evidence_id=evidence_id,
                gateway_models=models,
                tx_input=tx_input,
                rx_outputs=tuple(rx_outputs),
                exact_model=exact_model,
                gateway_version=gateway_version,
                rx_reply=rx_reply,
            )
        )
    return items


def _load_installations_map(
    evidence_dir: Path,
) -> dict[str, tuple[str | None, str | None]]:
    inst_path = evidence_dir / "installations.json"
    if not inst_path.is_file():
        return {}
    try:
        data = json.loads(inst_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, tuple[str | None, str | None]] = {}
    gateways = data.get("gateways")
    if isinstance(gateways, list):
        for gw in gateways:
            if isinstance(gw, dict) and gw.get("gateway_id"):
                gid = str(gw["gateway_id"])
                model = str(gw["model"]) if gw.get("model") else None
                fw = str(gw["firmware_version"]) if gw.get("firmware_version") else None
                out[gid] = (model, fw)
    return out


def _load_evidence_item(
    sub: Path,
    installations: dict[str, tuple[str | None, str | None]] | None = None,
) -> list[EvidenceItem]:
    manifest_path = sub / "manifest.json"
    frames_path = sub / "frames.jsonl"
    if not manifest_path.is_file() or not frames_path.is_file():
        return []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_id = str(manifest.get("evidence_id", sub.name))
    env = manifest.get("environment") or {}
    gw_model = env.get("gateway_model")
    gw_fw = env.get("gateway_firmware")

    if not gw_model and installations:
        gid = manifest.get("gateway_id")
        if isinstance(gid, str) and gid in installations:
            gw_model, inst_fw = installations[gid]
            if not gw_fw:
                gw_fw = inst_fw

    exact_model = str(gw_model) if gw_model else None
    gateway_version = str(gw_fw) if gw_fw else None
    models = _models_for_gateway(exact_model)

    records: list[tuple[str, str]] = []
    for raw_line in frames_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        record = json.loads(line)
        direction = record.get("dir")
        raw = record.get("raw")
        if raw and direction in ("tx", "rx"):
            records.append((str(direction), str(raw)))

    return _pairs_to_items(
        evidence_id,
        models,
        records,
        exact_model=exact_model,
        gateway_version=gateway_version,
    )


def load_evidence(evidence_dir: Path) -> list[EvidenceItem]:
    """Load evidence items from an Encyclopedia evidence directory."""
    items: list[EvidenceItem] = []
    if not evidence_dir.is_dir():
        return items

    installations = _load_installations_map(evidence_dir)
    for sub in sorted(evidence_dir.iterdir()):
        if sub.is_dir():
            items.extend(_load_evidence_item(sub, installations=installations))
    return items


def _load_trace_file(trace_path: Path) -> list[EvidenceItem]:
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    gw_obj = payload.get("gateway")
    gw_model = gw_obj.get("model") if isinstance(gw_obj, dict) else None
    gw_fw = gw_obj.get("firmware") if isinstance(gw_obj, dict) else None
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list):
        data_obj = payload.get("data")
        if isinstance(data_obj, dict):
            prof = data_obj.get("gateway_profile")
            if isinstance(prof, dict) and not gw_model:
                gw_model = prof.get("model_name")
                gw_fw = prof.get("firmware_version")
            bm = data_obj.get("bus_monitor")
            if isinstance(bm, dict):
                raw_frames = bm.get("recent_frames")
    if not isinstance(raw_frames, list):
        return []

    exact_model = str(gw_model) if gw_model else None
    gateway_version = str(gw_fw) if gw_fw else None
    models = _models_for_gateway(exact_model)
    records: list[tuple[str, str]] = []
    for entry in raw_frames:
        if isinstance(entry, dict):
            direction = entry.get("direction")
            raw = entry.get("raw")
            if raw and direction in ("tx", "rx"):
                records.append((str(direction), str(raw)))

    return _pairs_to_items(
        trace_path.stem,
        models,
        records,
        exact_model=exact_model,
        gateway_version=gateway_version,
    )


def load_trace_evidence(traces_dir: Path) -> list[EvidenceItem]:
    """Load EvidenceItems from a directory of MyHOME JSON trace/diagnostic files."""
    if not traces_dir.is_dir():
        return []
    items: list[EvidenceItem] = []
    for json_file in sorted(traces_dir.rglob("*.json")):
        items.extend(_load_trace_file(json_file))
    return items


def load_live_tsv(tsv_path: Path) -> list[EvidenceItem]:
    """Load EvidenceItems from an oracle-shaped TSV emitted by tools/live_probe.py."""
    if not tsv_path.is_file():
        return []
    lines = tsv_path.read_text(encoding="utf-8").splitlines()
    header: dict[str, str] = {}
    items: list[EvidenceItem] = []
    cols_seen = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = HEADER_RE.match(line)
            if m:
                header[m.group(1)] = m.group(2)
            continue
        cells = line.split("\t")
        if not cols_seen and cells[0] == "direction":
            cols_seen = True
            continue
        if len(cells) < 5:
            continue
        direction, inp, reply, _verdict, output = cells[:5]
        if direction != "down":
            continue
        exact_model = header.get("product")
        gw_ver = header.get("version")
        suite = header.get("suite", tsv_path.stem)
        ev_id = f"LIVE-{exact_model or 'GW'}-{suite}"
        items.append(
            EvidenceItem(
                evidence_id=ev_id,
                gateway_models=_models_for_gateway(exact_model),
                tx_input=inp,
                rx_outputs=tuple(extract_own_frames(output)),
                exact_model=exact_model,
                gateway_version=gw_ver,
                rx_reply=reply if reply in ("ack", "nack") else None,
            )
        )
    return items


def find_default_evidence_dir(root: Path) -> Path | None:
    candidate = root.parent / "OpenWebNet-Encyclopedia" / "evidence"
    if candidate.is_dir():
        return candidate
    return None


def _cross_suffix(
    item: EvidenceItem,
    product: str | None,
    version: str | None,
) -> str:
    if not product or not item.exact_model:
        return ""
    same_model = item.exact_model == product
    norm_item_v = normalize_version(item.gateway_version)
    norm_oracle_v = normalize_version(version)
    same_ver = (
        norm_item_v == norm_oracle_v
        if (norm_item_v is not None and norm_oracle_v is not None)
        else (not item.gateway_version or not version)
    )
    if same_model and same_ver:
        return ""
    ver_label = f"@{item.gateway_version}" if item.gateway_version else ""
    return f" [cross:{item.exact_model}{ver_label}]"


@dataclass
class CheckStats:
    total_rows: int = 0
    agree: int = 0
    unparsed: int = 0
    diverge: int = 0
    crash: int = 0


def match_live(
    direction: str,
    input_val: str,
    output_val: str,
    evidence_items: list[EvidenceItem],
    product: str | None = None,
    version: str | None = None,
    verdict: str | None = None,
    reply: str | None = None,
) -> str:
    """Compare an oracle row against loaded live evidence."""
    if direction != "down":
        return "unchecked"

    oracle_own = extract_own_frames(output_val)

    for item in evidence_items:
        if item.gateway_models and product and product not in item.gateway_models:
            continue
        if item.tx_input == input_val:
            suffix = _cross_suffix(item, product, version)
            if verdict == "crash":
                return f"diverge {item.evidence_id}{suffix}"
            reply_mismatch = (
                item.rx_reply is not None
                and reply is not None
                and item.rx_reply != reply
            )
            if list(item.rx_outputs) == oracle_own and not reply_mismatch:
                return f"agree {item.evidence_id}{suffix}"
            if verdict == "out" and not oracle_own and not reply_mismatch:
                return f"agree_bus {item.evidence_id}{suffix}"
            return f"diverge {item.evidence_id}{suffix}"

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
    version = header.get("version")
    out_rows: list[str] = []
    for line in data_lines:
        cells = line.split("\t")
        if len(cells) < len(COLUMNS_IN):
            continue
        direction, in_val, reply, verdict, output = cells[: len(COLUMNS_IN)]
        ownd_col = parse_ownd_column(output)
        live_col = match_live(
            direction,
            in_val,
            output,
            evidence,
            product=product,
            version=version,
            verdict=verdict,
            reply=reply,
        )
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


def collect_check_stats(
    results: list[tuple[Path, str]],
) -> tuple[
    CheckStats,
    list[tuple[str, str, str, str]],
    list[tuple[str, str, str, str]],
    list[tuple[str, str, str, str]],
]:
    """Return (stats, unparsed_rows, diverge_rows, crash_rows)."""
    stats = CheckStats()
    unparsed_rows: list[tuple[str, str, str, str]] = []
    diverge_rows: list[tuple[str, str, str, str]] = []
    crash_rows: list[tuple[str, str, str, str]] = []

    for path, checked_text in results:
        hdr: dict[str, str] = {}
        cols_seen = False
        for line in checked_text.splitlines():
            if line.startswith("#"):
                m = HEADER_RE.match(line)
                if m:
                    hdr[m.group(1)] = m.group(2)
                continue
            cells = line.split("\t")
            if not cols_seen:
                cols_seen = True
                continue
            if len(cells) < 7:
                continue
            stats.total_rows += 1
            _dir, inp, reply, verdict, output, ownd_col, live_col = cells[:7]
            gw_suite = (
                f"{hdr.get('product', '?')} {hdr.get('version', '?')} / "
                f"{hdr.get('suite', path.stem)}"
            )
            if verdict == "crash":
                stats.crash += 1
                crash_rows.append((gw_suite, inp, reply, output))
            if "unparsed" in ownd_col or "error:" in ownd_col:
                stats.unparsed += 1
                unparsed_rows.append((gw_suite, inp, output, ownd_col))
            if live_col.startswith("agree"):
                stats.agree += 1
            elif live_col.startswith("diverge"):
                stats.diverge += 1
                diverge_rows.append(
                    (gw_suite, inp, f"{reply}/{verdict} ({output})", live_col)
                )

    return stats, unparsed_rows, diverge_rows, crash_rows


def analyze_checked_results(
    results: list[tuple[Path, str]],
) -> tuple[int, int, list[tuple[str, str, str, str]], list[tuple[str, str, str, str]]]:
    """Return (total_rows, agree_count, unparsed_rows, diverge_rows)."""
    stats, unparsed_rows, diverge_rows, _crash_rows = collect_check_stats(results)
    return stats.total_rows, stats.agree, unparsed_rows, diverge_rows


def format_summary(stats: CheckStats) -> str:
    """Format a compact one-line summary of CheckStats."""
    return (
        f"total={stats.total_rows} agree={stats.agree} "
        f"diverge={stats.diverge} unparsed={stats.unparsed} crash={stats.crash}"
    )


def format_divergence_report(results: list[tuple[Path, str]]) -> str:
    """Format a Markdown conformance and live-divergence report."""
    stats, unparsed_rows, diverge_rows, crash_rows = collect_check_stats(results)
    lines = [
        "## Oracle Conformance & Live-Evidence Report",
        "",
    ]
    if stats.crash > 0:
        lines.extend(
            [
                f"> [!WARNING]\n> **{stats.crash} oracle step(s) recorded "
                "`verdict == crash`!** Crashing rows are never counted as "
                "live-evidence agreements.",
                "",
            ]
        )
    lines.extend(
        [
            f"- **Checked suite TSVs**: {len(results)}",
            f"- **Total steps evaluated**: {stats.total_rows}",
            f"- **OWNd parser failures (`unparsed` / `error`)**: {stats.unparsed}",
            f"- **Live evidence agreements (`agree` / `agree_bus`)**: {stats.agree}",
            f"- **Live evidence divergences (`diverge`)**: {stats.diverge}",
            f"- **Oracle daemon crashes (`crash`)**: {stats.crash}",
        ]
    )

    if crash_rows:
        lines.extend(
            [
                "",
                "### Oracle Daemon Crashes (`verdict == crash`)",
                "",
                "| Gateway / Suite | Input | Reply | Emitted Output |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for gw_suite, inp, reply, output in crash_rows[:50]:
            lines.append(f"| {gw_suite} | `{inp}` | `{reply}` | `{output}` |")

    if diverge_rows:
        lines.extend(
            [
                "",
                "### Live Evidence Divergences",
                "",
                "| Gateway / Suite | Input | Oracle Outcome | Live Verdict |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for gw_suite, inp, outcome, live_col in diverge_rows:
            lines.append(f"| {gw_suite} | `{inp}` | `{outcome}` | `{live_col}` |")

    if unparsed_rows:
        lines.extend(
            [
                "",
                "### OWNd Parser Failures",
                "",
                "| Gateway / Suite | Input | Output | OWNd Result |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for gw_suite, inp, output, ownd_col in unparsed_rows:
            lines.append(f"| {gw_suite} | `{inp}` | `{output}` | `{ownd_col}` |")

    return "\n".join(lines) + "\n"


def _collect_targets(
    paths: list[Path],
    *,
    all_flag: bool,
    product: str | None = None,
    version: str | None = None,
    suite: str | None = None,
) -> list[Path]:
    targets: list[Path] = []
    if all_flag or (not paths and (product or version or suite)):
        prod_glob = product or "*"
        ver_glob = version or "*"
        suite_glob = f"{suite}.tsv" if suite else "*.tsv"
        pattern = f"{prod_glob}/{ver_glob}/oracle/full/{suite_glob}"
        for tsv in sorted((ROOT / "results").glob(pattern)):
            targets.append(tsv)
        if all_flag and not (product or version or suite):
            for tsv in sorted((ROOT / "results").rglob("*.tsv")):
                parts = tsv.parts
                if "oracle" in parts and "boundary" not in parts and tsv not in targets:
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


def build_parser() -> argparse.ArgumentParser:
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
        "--traces-dir",
        type=Path,
        default=None,
        help="path to MyHOME JSON traces directory",
    )
    ap.add_argument(
        "--live-tsv",
        type=Path,
        action="append",
        default=[],
        help="path to live_probe TSV file (repeatable)",
    )
    ap.add_argument("--product", help="filter results by gateway product")
    ap.add_argument("--version", help="filter results by gateway firmware version")
    ap.add_argument("--suite", help="filter results by suite name")
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
    ap.add_argument(
        "--report",
        action="store_true",
        help="print Markdown divergence and OWNd conformance report",
    )
    ap.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="write Markdown report to file",
    )
    ap.add_argument(
        "--fail-on-unparsed",
        action="store_true",
        help="exit 1 if any emitted OWN frame is unparsed or raises an error in OWNd",
    )
    ap.add_argument(
        "--fail-on-diverge",
        action="store_true",
        help="exit 1 if any row diverges from live evidence",
    )
    ap.add_argument(
        "--fail-on-crash",
        action="store_true",
        help="exit 1 if any oracle row has verdict == 'crash'",
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)

    ev_dir = args.evidence_dir or find_default_evidence_dir(ROOT)
    evidence_items = load_evidence(ev_dir) if ev_dir else []
    if args.traces_dir:
        evidence_items.extend(load_trace_evidence(args.traces_dir))
    for ltsv in args.live_tsv:
        evidence_items.extend(load_live_tsv(ltsv))

    targets = _collect_targets(
        args.paths,
        all_flag=args.all,
        product=args.product,
        version=args.version,
        suite=args.suite,
    )
    if not targets:
        ap.error(
            "no input oracle TSVs specified (use paths, --all, or --product/--suite)"
        )

    if args.out and len(targets) > 1:
        ap.error("--out cannot be used with multiple inputs")

    checked_results: list[tuple[Path, str]] = []
    for t in targets:
        checked_str = check_content(
            t.read_text(encoding="ascii"), evidence_items=evidence_items
        )
        checked_results.append((t, checked_str))
        dest = process_oracle_tsv(
            t,
            out_path=args.out,
            evidence_items=evidence_items,
            to_stdout=args.stdout,
        )
        if dest and not args.stdout and not args.report:
            try:
                rel = dest.resolve().relative_to(ROOT.resolve())
            except ValueError:
                rel = dest
            print(f"wrote {rel}")

    if args.report or args.report_out:
        report_md = format_divergence_report(checked_results)
        if args.report_out:
            args.report_out.parent.mkdir(parents=True, exist_ok=True)
            args.report_out.write_text(report_md, encoding="utf-8", newline="\n")
        if args.report:
            sys.stdout.write(report_md)

    stats, _unparsed, _diverge, _crash = collect_check_stats(checked_results)
    if (
        (args.fail_on_unparsed and stats.unparsed > 0)
        or (args.fail_on_diverge and stats.diverge > 0)
        or (args.fail_on_crash and stats.crash > 0)
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
