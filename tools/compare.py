#!/usr/bin/env python3
"""compare -- cross-gateway comparison, divergence scanner, and applicability matrix.

Reads `results/mcp_index.json` and answers three high-value questions from the CLI:
  1. `frame <frame>`       Compare a single OpenWebNet or bus frame across all gateways.
  2. `divergences`         Find all frames where emulated gateways disagree on verdict
                           or bus/OWN output.
  3. `matrix`              Generate an Encyclopedia-ready applicability matrix across
                           all emulated gateways.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

KEEPALIVE_POLLS = {"$24\\x0d", "$24\\r", "$24", "24 0d"}
WHO_RE = re.compile(r"^\*#?(?P<who>\d+)\*")


def normalize_input_frame(frame: str) -> str:
    """Normalize an OpenWebNet input frame (leaving hex `up` frames intact)."""
    s = frame.strip()
    if not s or re.fullmatch(r"[0-9a-fA-F]{2}(?:\s+[0-9a-fA-F]{2})*", s):
        return s.lower()
    if not s.startswith("*"):
        s = "*" + s
    if not s.endswith("##"):
        s = s + "##"
    return s


def extract_who(frame: str) -> int | None:
    """Extract numeric WHO code from an OpenWebNet frame, or None."""
    m = WHO_RE.match(frame.strip())
    return int(m.group("who")) if m else None


def decode_bus_frame(raw_bus: str) -> str:
    """Format a raw bus frame cleanly for human display."""
    s = raw_bus.strip()
    return s.replace("\\x0d", "\\r")


def normalize_bus_frames(
    bus_frames: list[str], *, normalize_prefix: bool = False
) -> tuple[str, ...]:
    """Strip background keepalive polls ($24\\r) when accompanied by command frames,
    and optionally normalize 3-digit Eliot RPMsg prefix $003... to $03..."""
    cleaned = [decode_bus_frame(b) for b in bus_frames if b.strip()]
    if len(cleaned) > 1:
        non_poll = [b for b in cleaned if b not in KEEPALIVE_POLLS]
        if non_poll:
            cleaned = non_poll
    if normalize_prefix:
        cleaned = [("$03" + b[4:]) if b.startswith("$003") else b for b in cleaned]
    return tuple(cleaned)


def load_index(index_path: Path) -> dict[str, Any]:
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid index JSON in {index_path}")
    return data


def _emulated_gateways(index_data: dict[str, Any]) -> list[tuple[str, str]]:
    gws: list[tuple[str, str]] = []
    for g in index_data.get("gateways", []):
        if isinstance(g, dict) and g.get("status") == "emulated":
            gws.append((str(g["product"]), str(g["version"])))
    return gws


def _badge(reply: str, verdict: str) -> str:
    r = reply.lower()
    v = verdict.lower()
    if v == "crash":
        return "CRASH"
    if v == "timeout":
        return "TIMEOUT"
    if r == "ack" and v == "out":
        return "ACK+OUT"
    if r == "ack":
        return "ACK"
    if r == "nack" and v == "out":
        return "NACK+OUT"
    if r == "nack":
        return "NACK"
    if v == "out":
        return "OUT"
    return "SILENT"


def compare_frame(
    index_data: dict[str, Any],
    frame: str,
    *,
    suite: str | None = None,
    fmt: str = "markdown",
) -> str:
    """Compare a single frame across all gateways in mcp_index.json."""
    verdicts_map: dict[str, list[dict[str, Any]]] = index_data.get("verdicts", {})
    norm = normalize_input_frame(frame)
    entries = verdicts_map.get(frame, verdicts_map.get(norm, []))
    if suite:
        entries = [e for e in entries if e.get("suite") == suite]

    by_gw: dict[tuple[str, str], dict[str, Any]] = {}
    for e in entries:
        key = (str(e["product"]), str(e["version"]))
        if key not in by_gw:
            by_gw[key] = e

    rows_data: list[dict[str, Any]] = []
    classes: dict[str, list[str]] = defaultdict(list)

    for g in index_data.get("gateways", []):
        prod = str(g["product"])
        ver = str(g["version"])
        status = str(g.get("status", "catalogued"))
        entry = by_gw.get((prod, ver))
        if entry is not None:
            reply = str(entry.get("reply", "-"))
            verdict = str(entry.get("verdict", "-"))
            bus_list = [decode_bus_frame(b) for b in entry.get("bus_frames", [])]
            own_list = list(entry.get("emitted_own", []))
            st_name = str(entry.get("suite", "-"))
            badge = _badge(reply, verdict)
            classes[badge].append(f"{prod} ({ver})")
            rows_data.append(
                {
                    "product": prod,
                    "version": ver,
                    "status": status,
                    "reply": reply,
                    "verdict": verdict,
                    "bus_frames": bus_list,
                    "emitted_own": own_list,
                    "suite": st_name,
                }
            )
        else:
            if status == "emulated":
                classes["UNTESTED"].append(f"{prod} ({ver})")
            rows_data.append(
                {
                    "product": prod,
                    "version": ver,
                    "status": status,
                    "reply": "-",
                    "verdict": "-",
                    "bus_frames": [],
                    "emitted_own": [],
                    "suite": "-",
                }
            )

    if fmt == "json":
        return (
            json.dumps(
                {
                    "frame": norm,
                    "equivalence_classes": dict(classes),
                    "gateways": rows_data,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    if fmt == "tsv":
        lines = [
            "product\tversion\tstatus\treply\tverdict\tbus_frames\temitted_own\tsuite"
        ]
        for r in rows_data:
            bus_s = " | ".join(r["bus_frames"]) or "-"
            own_s = " | ".join(r["emitted_own"]) or "-"
            lines.append(
                "\t".join(
                    [
                        r["product"],
                        r["version"],
                        r["status"],
                        r["reply"],
                        r["verdict"],
                        bus_s,
                        own_s,
                        r["suite"],
                    ]
                )
            )
        return "\n".join(lines) + "\n"

    lines = [
        f"## Cross-Gateway Comparison: `{norm}`",
        "",
        (
            "| Gateway | Version | Status | Reply | Verdict | "
            "Bus Frames | Emitted OWN | Suite |"
        ),
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for r in rows_data:
        bus_s = f"`{', '.join(r['bus_frames'])}`" if r["bus_frames"] else "-"
        own_s = f"`{', '.join(r['emitted_own'])}`" if r["emitted_own"] else "-"
        lines.append(
            f"| **{r['product']}** | `{r['version']}` | {r['status']} | "
            f"`{r['reply']}` | `{r['verdict']}` | {bus_s} | {own_s} | `{r['suite']}` |"
        )

    lines.extend(["", "### Equivalence Classes"])
    for cls_name, gws in sorted(classes.items()):
        lines.append(f"- **{cls_name} ({len(gws)})**: {', '.join(gws)}")
    return "\n".join(lines) + "\n"


def scan_divergences(
    index_data: dict[str, Any],
    *,
    suite: str | None = None,
    who: int | None = None,
    include_bus: bool = False,
    normalize_bus: bool = False,
    fmt: str = "markdown",
) -> str:
    """Scan mcp_index.json for frames where emulated gateways disagree."""
    verdicts_map: dict[str, list[dict[str, Any]]] = index_data.get("verdicts", {})
    total_analyzed = 0
    unanimous_count = 0
    divergent_items: list[dict[str, Any]] = []

    for frame, raw_entries in sorted(verdicts_map.items()):
        if who is not None and extract_who(frame) != who:
            continue
        entries = (
            [e for e in raw_entries if e.get("suite") == suite]
            if suite
            else list(raw_entries)
        )
        if not entries:
            continue

        # Deduplicate per gateway (first suite entry wins when not filtered by suite)
        by_gw: dict[str, dict[str, Any]] = {}
        for e in entries:
            prod = str(e["product"])
            if prod not in by_gw:
                by_gw[prod] = e

        if len(by_gw) < 2:
            continue

        total_analyzed += 1
        groups: dict[str, list[str]] = defaultdict(list)
        for prod, e in sorted(by_gw.items()):
            badge = _badge(str(e.get("reply", "-")), str(e.get("verdict", "-")))
            if include_bus:
                b_norm = normalize_bus_frames(
                    list(e.get("bus_frames", [])), normalize_prefix=normalize_bus
                )
                own_tup = tuple(e.get("emitted_own", []))
                detail_parts: list[str] = []
                if b_norm:
                    detail_parts.append("bus:" + ",".join(b_norm))
                if own_tup:
                    detail_parts.append("own:" + ",".join(own_tup))
                sig = f"{badge} [{' '.join(detail_parts)}]" if detail_parts else badge
            else:
                sig = badge
            groups[sig].append(prod)

        if len(groups) == 1:
            unanimous_count += 1
        else:
            suites_in_frame = sorted({str(e.get("suite", "")) for e in entries})
            divergent_items.append(
                {
                    "frame": frame,
                    "suites": suites_in_frame,
                    "outcomes": dict(groups),
                }
            )

    if fmt == "json":
        return (
            json.dumps(
                {
                    "total_analyzed": total_analyzed,
                    "unanimous_frames": unanimous_count,
                    "divergent_frames": len(divergent_items),
                    "divergences": divergent_items,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    if fmt == "tsv":
        lines = ["frame\tsuites\toutcomes"]
        for item in divergent_items:
            suites_s = ",".join(item["suites"])
            out_s = " ; ".join(
                f"{sig}: {','.join(gws)}"
                for sig, gws in sorted(item["outcomes"].items())
            )
            lines.append(f"{item['frame']}\t{suites_s}\t{out_s}")
        return "\n".join(lines) + "\n"

    lines = [
        "## Cross-Gateway Divergence Report",
        "",
        f"- **Total multi-gateway frames analyzed**: {total_analyzed}",
        f"- **Unanimous across fleet**: {unanimous_count}",
        f"- **Divergent across fleet**: {len(divergent_items)}",
    ]
    if not divergent_items:
        lines.extend(["", "_All analyzed frames are unanimous across the fleet._"])
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "",
            "| Frame | Suite(s) | Gateway Outcomes |",
            "| :--- | :--- | :--- |",
        ]
    )
    for item in divergent_items:
        suites_s = ", ".join(f"`{s}`" for s in item["suites"])
        out_parts = [
            f"**{sig}**: {', '.join(gws)}"
            for sig, gws in sorted(item["outcomes"].items())
        ]
        lines.append(f"| `{item['frame']}` | {suites_s} | {'<br>'.join(out_parts)} |")
    return "\n".join(lines) + "\n"


def generate_matrix(
    index_data: dict[str, Any],
    *,
    suite: str | None = None,
    who: int | None = None,
    fmt: str = "markdown",
) -> str:
    """Generate an Encyclopedia-ready applicability matrix across emulated gateways."""
    emulated = _emulated_gateways(index_data)
    gw_names = [prod for prod, _ver in emulated]
    verdicts_map: dict[str, list[dict[str, Any]]] = index_data.get("verdicts", {})

    matrix_rows: list[dict[str, Any]] = []
    for frame, raw_entries in sorted(verdicts_map.items()):
        if who is not None and extract_who(frame) != who:
            continue
        entries = (
            [e for e in raw_entries if e.get("suite") == suite]
            if suite
            else list(raw_entries)
        )
        if not entries:
            continue

        by_prod: dict[str, str] = {}
        for e in entries:
            prod = str(e["product"])
            if prod not in by_prod:
                by_prod[prod] = _badge(
                    str(e.get("reply", "-")), str(e.get("verdict", "-"))
                )

        row_cells = {prod: by_prod.get(prod, "-") for prod in gw_names}
        matrix_rows.append({"frame": frame, "gateways": row_cells})

    if fmt == "json":
        return (
            json.dumps(
                {"gateways": gw_names, "rows": matrix_rows},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    if fmt == "tsv":
        lines = ["frame\t" + "\t".join(gw_names)]
        for r in matrix_rows:
            cells = [r["gateways"][g] for g in gw_names]
            lines.append(r["frame"] + "\t" + "\t".join(cells))
        return "\n".join(lines) + "\n"

    header_cols = " | ".join(["Frame", *gw_names])
    sep_cols = " | ".join([":---"] + [":---:" for _ in gw_names])
    lines = [
        "## Cross-Gateway Applicability Matrix",
        "",
        f"| {header_cols} |",
        f"| {sep_cols} |",
    ]
    for r in matrix_rows:
        cells = [f"`{r['gateways'][g]}`" for g in gw_names]
        lines.append(f"| `{r['frame']}` | {' | '.join(cells)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--index",
        type=Path,
        default=ROOT / "results" / "mcp_index.json",
        help="path to mcp_index.json (default: results/mcp_index.json)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_frame = sub.add_parser("frame", help="compare a single frame across gateways")
    p_frame.add_argument("frame", help="OpenWebNet frame or hex bus frame")
    p_frame.add_argument("--suite", help="optional suite filter")
    p_frame.add_argument(
        "--format",
        choices=("markdown", "tsv", "json"),
        default="markdown",
    )

    p_div = sub.add_parser("divergences", help="list all cross-gateway divergences")
    p_div.add_argument("--suite", help="filter by suite name")
    p_div.add_argument("--who", type=int, help="filter by OpenWebNet WHO number")
    p_div.add_argument(
        "--include-bus",
        action="store_true",
        help="also flag differences in emitted bus/OWN frames when verdict agrees",
    )
    p_div.add_argument(
        "--normalize-bus",
        action="store_true",
        help="normalize 3-digit $003... RPMsg bus prefix to $03...",
    )
    p_div.add_argument(
        "--format",
        choices=("markdown", "tsv", "json"),
        default="markdown",
    )

    p_mat = sub.add_parser("matrix", help="generate applicability matrix")
    p_mat.add_argument("--suite", help="filter by suite name")
    p_mat.add_argument("--who", type=int, help="filter by OpenWebNet WHO number")
    p_mat.add_argument(
        "--format",
        choices=("markdown", "tsv", "json"),
        default="markdown",
    )

    args = ap.parse_args(argv)
    index_data = load_index(args.index)

    if args.cmd == "frame":
        out = compare_frame(index_data, args.frame, suite=args.suite, fmt=args.format)
    elif args.cmd == "divergences":
        out = scan_divergences(
            index_data,
            suite=args.suite,
            who=args.who,
            include_bus=args.include_bus,
            normalize_bus=args.normalize_bus,
            fmt=args.format,
        )
    else:
        out = generate_matrix(
            index_data, suite=args.suite, who=args.who, fmt=args.format
        )

    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
