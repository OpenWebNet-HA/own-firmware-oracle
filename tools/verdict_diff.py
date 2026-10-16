#!/usr/bin/env python3
"""verdict_diff -- semantic diff generator for oracle TSV results across git or dirs.

Parses oracle TSV headers and rows across git revisions or directories,
collapsing header-only updates (where target_sha256 or oracle_version changed
but 0 frame verdicts changed) into a compact summary and grouping actual
behavioural changes (verdict/reply flips, bus/OWN output changes, new suites)
into Markdown tables or JSON.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, order=True)
class RowKey:
    product: str
    version: str
    suite: str
    step_index: int
    direction: str
    input: str


@dataclass(frozen=True, order=True)
class RowState:
    reply: str
    verdict: str
    output: str


@dataclass(frozen=True)
class ParsedTsv:
    rel_path: str
    product: str
    version: str
    suite: str
    header: dict[str, str]
    rows: dict[RowKey, RowState]


@dataclass(frozen=True)
class RowTransition:
    key: RowKey
    old: RowState
    new: RowState


@dataclass(frozen=True)
class SuiteDelta:
    rel_path: str
    product: str
    version: str
    suite: str
    status: str  # "added" | "removed" | "header-only" | "modified" | "unchanged"
    header_changes: dict[str, tuple[str, str]]
    total_rows: int
    ack_out: int
    nack_out: int
    nack_silent: int
    other_rows: int
    row_changes_count: int
    crash_rows: int = 0


@dataclass(frozen=True)
class VerdictDiffReport:
    suite_deltas: list[SuiteDelta]
    verdict_flips: list[RowTransition]
    output_deltas: list[RowTransition]
    added_rows: list[tuple[RowKey, RowState]]
    removed_rows: list[tuple[RowKey, RowState]]


def _infer_meta_from_path(rel_path: str) -> tuple[str, str, str]:
    norm = rel_path.replace("\\", "/").strip("/")
    parts = norm.split("/")
    if len(parts) >= 6 and parts[0] == "results":
        return parts[1], parts[2], Path(parts[-1]).stem
    if len(parts) >= 3:
        return parts[0], parts[1], Path(parts[-1]).stem
    return "", "", Path(norm).stem if norm else ""


def parse_tsv_text(text: str, rel_path: str = "") -> ParsedTsv:
    """Parse an oracle TSV string into header metadata and keyed row states."""
    header: dict[str, str] = {}
    raw_rows: list[tuple[str, str, RowState]] = []
    columns_seen = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            rest = line[1:].strip()
            for token in rest.split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    header[k] = v
            continue
        cols = line.split("\t")
        if not columns_seen and cols[0] == "direction":
            columns_seen = True
            continue
        if len(cols) >= 5:
            direction, inp, reply, verdict, output = cols[:5]
            raw_rows.append((direction, inp, RowState(reply, verdict, output)))

    path_prod, path_ver, path_suite = _infer_meta_from_path(rel_path)
    product = header.get("product", path_prod)
    version = header.get("version", path_ver)
    suite = header.get("suite", path_suite)

    counts: dict[tuple[str, str], int] = defaultdict(int)
    for direction, inp, _state in raw_rows:
        counts[(direction, inp)] += 1

    seen_idx: dict[tuple[str, str], int] = defaultdict(int)
    rows: dict[RowKey, RowState] = {}
    for direction, inp, state in raw_rows:
        pair = (direction, inp)
        if counts[pair] > 1:
            seen_idx[pair] += 1
            step_index = seen_idx[pair]
        else:
            step_index = 0
        key = RowKey(product, version, suite, step_index, direction, inp)
        rows[key] = state

    return ParsedTsv(
        rel_path=rel_path.replace("\\", "/"),
        product=product,
        version=version,
        suite=suite,
        header=header,
        rows=rows,
    )


def _count_outcomes(rows: dict[RowKey, RowState]) -> tuple[int, int, int, int, int]:
    ack_out = 0
    nack_out = 0
    nack_silent = 0
    other = 0
    crashes = 0
    for st in rows.values():
        if st.verdict == "crash":
            crashes += 1
        if st.reply == "ack" and st.verdict == "out":
            ack_out += 1
        elif st.reply == "nack" and st.verdict == "out":
            nack_out += 1
        elif st.reply == "nack" and st.verdict == "silent":
            nack_silent += 1
        else:
            other += 1
    return ack_out, nack_out, nack_silent, other, crashes


def _header_diff(
    old_hdr: dict[str, str], new_hdr: dict[str, str]
) -> dict[str, tuple[str, str]]:
    changes: dict[str, tuple[str, str]] = {}
    for k in sorted(set(old_hdr) | set(new_hdr)):
        old_v = old_hdr.get(k, "")
        new_v = new_hdr.get(k, "")
        if old_v != new_v:
            changes[k] = (old_v, new_v)
    return changes


def diff_parsed_tsvs(
    old_files: dict[str, ParsedTsv],
    new_files: dict[str, ParsedTsv],
) -> VerdictDiffReport:
    """Compare two mappings of rel_path -> ParsedTsv and build VerdictDiffReport."""
    suite_deltas: list[SuiteDelta] = []
    verdict_flips: list[RowTransition] = []
    output_deltas: list[RowTransition] = []
    added_rows: list[tuple[RowKey, RowState]] = []
    removed_rows: list[tuple[RowKey, RowState]] = []

    all_paths = sorted(set(old_files) | set(new_files))
    for path in all_paths:
        old_tsv = old_files.get(path)
        new_tsv = new_files.get(path)

        if old_tsv is None:
            if new_tsv is not None:  # pragma: no branch
                ack_o, nack_o, nack_s, other, crashes = _count_outcomes(new_tsv.rows)
                suite_deltas.append(
                    SuiteDelta(
                        rel_path=path,
                        product=new_tsv.product,
                        version=new_tsv.version,
                        suite=new_tsv.suite,
                        status="added",
                        header_changes={},
                        total_rows=len(new_tsv.rows),
                        ack_out=ack_o,
                        nack_out=nack_o,
                        nack_silent=nack_s,
                        other_rows=other,
                        row_changes_count=len(new_tsv.rows),
                        crash_rows=crashes,
                    )
                )
            continue

        if new_tsv is None:
            ack_o, nack_o, nack_s, other, crashes = _count_outcomes(old_tsv.rows)
            suite_deltas.append(
                SuiteDelta(
                    rel_path=path,
                    product=old_tsv.product,
                    version=old_tsv.version,
                    suite=old_tsv.suite,
                    status="removed",
                    header_changes={},
                    total_rows=len(old_tsv.rows),
                    ack_out=ack_o,
                    nack_out=nack_o,
                    nack_silent=nack_s,
                    other_rows=other,
                    row_changes_count=len(old_tsv.rows),
                    crash_rows=crashes,
                )
            )
            continue

        hdr_changes = _header_diff(old_tsv.header, new_tsv.header)
        ack_o, nack_o, nack_s, other, crashes = _count_outcomes(new_tsv.rows)

        if old_tsv.rows == new_tsv.rows:
            status = "header-only" if hdr_changes else "unchanged"
            suite_deltas.append(
                SuiteDelta(
                    rel_path=path,
                    product=new_tsv.product,
                    version=new_tsv.version,
                    suite=new_tsv.suite,
                    status=status,
                    header_changes=hdr_changes,
                    total_rows=len(new_tsv.rows),
                    ack_out=ack_o,
                    nack_out=nack_o,
                    nack_silent=nack_s,
                    other_rows=other,
                    row_changes_count=0,
                    crash_rows=crashes,
                )
            )
            continue

        row_changes = 0
        all_keys = sorted(set(old_tsv.rows) | set(new_tsv.rows))
        for key in all_keys:
            if key not in old_tsv.rows:
                added_rows.append((key, new_tsv.rows[key]))
                row_changes += 1
            elif key not in new_tsv.rows:
                removed_rows.append((key, old_tsv.rows[key]))
                row_changes += 1
            else:
                old_st = old_tsv.rows[key]
                new_st = new_tsv.rows[key]
                if old_st != new_st:
                    row_changes += 1
                    if old_st.reply != new_st.reply or old_st.verdict != new_st.verdict:
                        verdict_flips.append(RowTransition(key, old_st, new_st))
                    else:
                        output_deltas.append(RowTransition(key, old_st, new_st))

        suite_deltas.append(
            SuiteDelta(
                rel_path=path,
                product=new_tsv.product,
                version=new_tsv.version,
                suite=new_tsv.suite,
                status="modified",
                header_changes=hdr_changes,
                total_rows=len(new_tsv.rows),
                ack_out=ack_o,
                nack_out=nack_o,
                nack_silent=nack_s,
                other_rows=other,
                row_changes_count=row_changes,
                crash_rows=crashes,
            )
        )

    return VerdictDiffReport(
        suite_deltas=suite_deltas,
        verdict_flips=verdict_flips,
        output_deltas=output_deltas,
        added_rows=added_rows,
        removed_rows=removed_rows,
    )


def count_new_crashes(report: VerdictDiffReport) -> tuple[int, list[str]]:
    """Return (total_new_crashes, sorted_affected_gateway_suites)."""
    affected: set[str] = set()
    total_crashes = 0
    for tr in report.verdict_flips:
        if tr.old.verdict != "crash" and tr.new.verdict == "crash":
            total_crashes += 1
            affected.add(f"{tr.key.product} `{tr.key.version}` / `{tr.key.suite}`")
    for key, st in report.added_rows:
        if st.verdict == "crash":
            total_crashes += 1
            affected.add(f"{key.product} `{key.version}` / `{key.suite}`")
    for d in report.suite_deltas:
        if d.status == "added" and d.crash_rows > 0:
            total_crashes += d.crash_rows
            affected.add(f"{d.product} `{d.version}` / `{d.suite}`")
    return total_crashes, sorted(affected)


def _scan_dir_tsvs(root: Path) -> dict[str, ParsedTsv]:
    out: dict[str, ParsedTsv] = {}
    if not root.is_dir():
        return out
    for tsv in sorted(root.glob("results/*/*/oracle/full/*.tsv")):
        rel = str(tsv.relative_to(root)).replace("\\", "/")
        out[rel] = parse_tsv_text(tsv.read_text(encoding="utf-8"), rel_path=rel)
    for tsv in sorted(root.glob("*/*/oracle/full/*.tsv")):
        rel = "results/" + str(tsv.relative_to(root)).replace("\\", "/")
        if rel not in out:
            out[rel] = parse_tsv_text(tsv.read_text(encoding="utf-8"), rel_path=rel)
    return out


def compare_dirs(old_root: Path, new_root: Path) -> VerdictDiffReport:
    """Compare oracle TSVs between two directory trees."""
    return diff_parsed_tsvs(_scan_dir_tsvs(old_root), _scan_dir_tsvs(new_root))


def _git_show_text(ref: str, rel_path: str, root: Path) -> str | None:
    res = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if res.returncode != 0:
        return None
    return res.stdout


def compare_git_revs(
    base_ref: str, head_ref: str = "WORKTREE", root: Path = ROOT
) -> VerdictDiffReport:
    """Compare oracle TSVs between base_ref and head_ref (or WORKTREE)."""
    cmd = ["git", "diff", "--name-status", base_ref]
    if head_ref != "WORKTREE":
        cmd.append(head_ref)
    cmd.extend(["--", "results/*/*/oracle/full/*.tsv"])
    res = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    old_files: dict[str, ParsedTsv] = {}
    new_files: dict[str, ParsedTsv] = {}

    for raw_line in res.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        rel_path = parts[-1].replace("\\", "/")

        old_text = _git_show_text(base_ref, rel_path, root)
        if old_text is not None:
            old_files[rel_path] = parse_tsv_text(old_text, rel_path=rel_path)

        if head_ref == "WORKTREE":
            disk_path = root / rel_path
            new_text = (
                disk_path.read_text(encoding="utf-8") if disk_path.is_file() else None
            )
        else:
            new_text = _git_show_text(head_ref, rel_path, root)

        if new_text is not None:
            new_files[rel_path] = parse_tsv_text(new_text, rel_path=rel_path)

    return diff_parsed_tsvs(old_files, new_files)


def summarize_tsv_pair(old_text: str, new_text: str, rel_path: str) -> list[str]:
    """Return a compact Markdown summary for a single old/new TSV pair."""
    old_tsv = parse_tsv_text(old_text, rel_path=rel_path)
    new_tsv = parse_tsv_text(new_text, rel_path=rel_path)
    report = diff_parsed_tsvs({rel_path: old_tsv}, {rel_path: new_tsv})
    delta = report.suite_deltas[0]

    lines: list[str] = []
    if delta.header_changes:
        hdr_items = [
            f"`{k}` (`{ov[:12]}` -> `{nv[:12]}`)"
            for k, (ov, nv) in delta.header_changes.items()
        ]
        lines.append(f"- **Header changes**: {', '.join(hdr_items)}")
    if delta.status == "header-only":
        lines.append("- **Row verdicts**: 0 changes (header-only refresh)")
        return lines

    lines.append(
        f"- **Row deltas**: {len(report.verdict_flips)} verdict flip(s), "
        f"{len(report.output_deltas)} output change(s), "
        f"+{len(report.added_rows)}/-{len(report.removed_rows)} row(s)"
    )
    for flip in report.verdict_flips[:10]:
        lines.append(
            f"  - `{flip.key.direction} {flip.key.input}`: "
            f"`{flip.old.reply}/{flip.old.verdict}` -> "
            f"`{flip.new.reply}/{flip.new.verdict}`"
        )
    for out_d in report.output_deltas[:10]:
        lines.append(
            f"  - `{out_d.key.direction} {out_d.key.input}` output: "
            f"`{out_d.old.output}` -> `{out_d.new.output}`"
        )
    return lines


def format_markdown(report: VerdictDiffReport) -> str:
    """Format VerdictDiffReport as a human-reviewable Markdown summary."""
    added_suites = [d for d in report.suite_deltas if d.status == "added"]
    removed_suites = [d for d in report.suite_deltas if d.status == "removed"]
    modified_suites = [d for d in report.suite_deltas if d.status == "modified"]
    hdr_only = [d for d in report.suite_deltas if d.status == "header-only"]
    new_crashes, crash_targets = count_new_crashes(report)

    lines = [
        "## Oracle Verdict Diff Summary",
        "",
    ]
    if new_crashes > 0:
        targets_str = ", ".join(crash_targets)
        lines.extend(
            [
                f"> [!WARNING]\n> **{new_crashes} new `crash` verdict(s) "
                f"introduced** across {len(crash_targets)} suite(s): {targets_str}",
                "",
            ]
        )

    lines.extend(
        [
            f"- **Total suite TSVs compared**: {len(report.suite_deltas)}",
            f"- **Behavioural modifications**: {len(modified_suites)} suite(s) "
            f"({len(report.verdict_flips)} verdict flip(s), "
            f"{len(report.output_deltas)} bus/OWN output delta(s), "
            f"+{len(report.added_rows)}/-{len(report.removed_rows)} step(s))",
            f"- **New suite TSVs**: {len(added_suites)}",
            f"- **Removed suite TSVs**: {len(removed_suites)}",
            f"- **Header-only refreshes (0 row changes)**: {len(hdr_only)}",
        ]
    )

    if not (added_suites or removed_suites or modified_suites or hdr_only):
        lines.extend(["", "_No oracle TSV changes detected._"])
        return "\n".join(lines) + "\n"

    if report.verdict_flips:
        lines.extend(
            [
                "",
                "### Verdict / Reply Flips",
                "",
                (
                    "| Suite | Input | Old (`reply/verdict`) | "
                    "New (`reply/verdict`) | Gateways |"
                ),
                "| :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        grouped_flips: dict[tuple[str, str, str, str, str, str, str], list[str]] = (
            defaultdict(list)
        )
        for tr in report.verdict_flips:
            gkey = (
                tr.key.suite,
                tr.key.direction,
                tr.key.input,
                tr.old.reply,
                tr.old.verdict,
                tr.new.reply,
                tr.new.verdict,
            )
            grouped_flips[gkey].append(f"{tr.key.product} `{tr.key.version}`")
        for (
            suite,
            direction,
            inp,
            old_r,
            old_v,
            new_r,
            new_v,
        ), gws in sorted(grouped_flips.items()):
            gw_list = ", ".join(sorted(set(gws)))
            lines.append(
                f"| `{suite}` | `{direction} {inp}` | `{old_r}/{old_v}` | "
                f"`{new_r}/{new_v}` | {gw_list} |"
            )

    if report.output_deltas:
        lines.extend(
            [
                "",
                "### Bus / Emitted Output Deltas (Same Verdict)",
                "",
                "| Gateway | Suite | Input | Old Output | New Output |",
                "| :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for tr in report.output_deltas:
            lines.append(
                f"| **{tr.key.product}** `{tr.key.version}` | `{tr.key.suite}` | "
                f"`{tr.key.direction} {tr.key.input}` | `{tr.old.output}` | "
                f"`{tr.new.output}` |"
            )

    if report.added_rows or report.removed_rows:
        lines.extend(
            [
                "",
                "### Step Additions / Removals in Existing Suites",
                "",
                "| Change | Gateway | Suite | Input | Reply / Verdict | Output |",
                "| :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for key, st in report.added_rows:
            lines.append(
                f"| `+added` | **{key.product}** `{key.version}` | `{key.suite}` | "
                f"`{key.direction} {key.input}` | `{st.reply}/{st.verdict}` | "
                f"`{st.output}` |"
            )
        for key, st in report.removed_rows:
            lines.append(
                f"| `-removed` | **{key.product}** `{key.version}` | `{key.suite}` | "
                f"`{key.direction} {key.input}` | `{st.reply}/{st.verdict}` | "
                f"`{st.output}` |"
            )

    if added_suites:
        lines.extend(
            [
                "",
                "### New Suite TSVs",
                "",
                (
                    "| Gateway | Suite | Total Rows | `ack/out` | "
                    "`nack/out` | `nack/silent` | Other |"
                ),
                "| :--- | :--- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for d in added_suites:
            lines.append(
                f"| **{d.product}** `{d.version}` | `{d.suite}` | {d.total_rows} | "
                f"{d.ack_out} | {d.nack_out} | {d.nack_silent} | {d.other_rows} |"
            )

    if removed_suites:
        lines.extend(
            [
                "",
                "### Removed Suite TSVs",
                "",
                "| Gateway | Suite | Total Rows |",
                "| :--- | :--- | ---: |",
            ]
        )
        for d in removed_suites:
            lines.append(
                f"| **{d.product}** `{d.version}` | `{d.suite}` | {d.total_rows} |"
            )

    if hdr_only:
        lines.extend(
            [
                "",
                "<details>",
                f"<summary>{len(hdr_only)} suite TSV(s) refreshed header metadata "
                "only with 0 row changes</summary>",
                "",
            ]
        )
        for d in hdr_only:
            keys_str = ", ".join(sorted(d.header_changes))
            lines.append(f"- `{d.rel_path}` ({keys_str})")
        lines.append("</details>")

    return "\n".join(lines) + "\n"


def format_json(report: VerdictDiffReport) -> str:
    """Serialize VerdictDiffReport as deterministic indented JSON."""
    payload = {
        "suite_deltas": [asdict(d) for d in report.suite_deltas],
        "verdict_flips": [asdict(f) for f in report.verdict_flips],
        "output_deltas": [asdict(o) for o in report.output_deltas],
        "added_rows": [
            {"key": asdict(k), "state": asdict(s)} for k, s in report.added_rows
        ],
        "removed_rows": [
            {"key": asdict(k), "state": asdict(s)} for k, s in report.removed_rows
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="base git revision (e.g. origin/main)")
    ap.add_argument(
        "--head",
        default="WORKTREE",
        help="head git revision or WORKTREE (default: WORKTREE)",
    )
    ap.add_argument("--old-dir", type=Path, help="old results root directory")
    ap.add_argument("--new-dir", type=Path, help="new results root directory")
    ap.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="output format (default: markdown)",
    )
    ap.add_argument("-o", "--out", type=Path, help="write report to file")
    ap.add_argument(
        "--fail-on-flip",
        action="store_true",
        help="exit 1 if any verdict/reply flip is detected",
    )
    ap.add_argument(
        "--fail-on-crash",
        action="store_true",
        help="exit 1 if any new crash verdict is introduced",
    )
    args = ap.parse_args(argv)

    if args.old_dir and args.new_dir:
        report = compare_dirs(args.old_dir, args.new_dir)
    elif args.base:
        report = compare_git_revs(args.base, args.head, root=ROOT)
    else:
        ap.error("provide either --base <git-ref> or both --old-dir and --new-dir")

    rendered = format_json(report) if args.format == "json" else format_markdown(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(rendered)

    new_crashes, _ = count_new_crashes(report)
    if (args.fail_on_flip and report.verdict_flips) or (
        args.fail_on_crash and new_crashes > 0
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
