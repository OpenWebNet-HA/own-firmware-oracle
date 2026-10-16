#!/usr/bin/env python3
"""export_evidence -- export oracle TSV results to OpenWebNet-Encyclopedia bundles.

Reads a committed or freshly generated oracle TSV
(e.g., results/<Product>/<Version>/oracle/full/<suite>.tsv) and writes a
schema-valid OpenWebNet-Encyclopedia evidence bundle containing:
  - manifest.json  (conforms to evidence-manifest.schema.json)
  - frames.jsonl   (conforms to evidence-frame.schema.json)
  - privacy.json   (conforms to evidence-privacy.schema.json)

Usage:
    python tools/export_evidence.py results/MH200N/010108/oracle/full/what19.tsv \\
        --out-dir ../OpenWebNet-Encyclopedia/evidence/cap-mh200n-what19-20261015 \\
        --date 2026-10-15
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

OWN_FRAME_RE = re.compile(r"^\*[#*0-9]+##$")
TOKEN_CLEAN_RE = re.compile(r"[^A-Za-z0-9]+")


def parse_own_frame(raw: str) -> dict[str, Any]:
    """Parse an OpenWebNet ASCII frame into schema fields for frames.jsonl."""
    who: int | None = None
    where: str | None = None
    what: int | None = None
    dimension: int | None = None
    dimension_values: list[str] | None = None

    if raw in ("*#*1##", "*#*0##"):
        return {
            "who": None,
            "where": None,
            "what": None,
            "dimension": None,
            "dimension_values": None,
        }

    if raw.startswith("*#") and raw.endswith("##"):
        inner = raw[2:-2]
        parts = inner.split("*")
        if parts and parts[0].isdigit():
            who = int(parts[0])
            if len(parts) >= 2 and parts[1] != "":
                where = parts[1]
            if len(parts) >= 3:
                dim_token = parts[2].lstrip("#")
                if dim_token.isdigit():
                    dimension = int(dim_token)
                if len(parts) > 3:
                    dimension_values = parts[3:]
    elif raw.startswith("*") and raw.endswith("##"):
        inner = raw[1:-2]
        parts = inner.split("*")
        if len(parts) >= 3 and parts[0].isdigit():
            who = int(parts[0])
            what_base = parts[1].split("#", 1)[0]
            if what_base.isdigit():
                what = int(what_base)
            where = parts[2]

    return {
        "who": who,
        "where": where,
        "what": what,
        "dimension": dimension,
        "dimension_values": dimension_values,
    }


def parse_oracle_tsv(
    tsv_path: Path,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Parse header key-values and data rows from an oracle TSV file."""
    header: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    header_cols: list[str] | None = None

    for raw_line in tsv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "=" in line:
                key, _, val = line.lstrip("#").partition("=")
                header[key.strip()] = val.strip()
            continue
        cols = line.split("\t")
        if header_cols is None:
            header_cols = cols
            continue
        if len(cols) >= 5:
            rows.append(
                {
                    "direction": cols[0],
                    "input": cols[1],
                    "reply": cols[2],
                    "verdict": cols[3],
                    "output": cols[4],
                }
            )
    return header, rows


def _clean_upper_token(text: str) -> str:
    cleaned = TOKEN_CLEAN_RE.sub("-", text.strip()).strip("-").upper()
    return cleaned or "SUITE"


def _clean_lower_token(text: str) -> str:
    cleaned = TOKEN_CLEAN_RE.sub("-", text.strip()).strip("-").lower()
    return cleaned or "suite"


def default_evidence_id(product: str, suite: str) -> str:
    return f"EVID-{_clean_upper_token(product)}-{_clean_upper_token(suite)}-001"


def default_capture_id(product: str, suite: str, date_str: str) -> str:
    date_compact = date_str.replace("-", "")
    return (
        f"cap-{_clean_lower_token(product)}-{_clean_lower_token(suite)}-{date_compact}"
    )


def detect_oracle_commit(root: Path = ROOT) -> str:
    """Resolve current git HEAD commit hash or fallback to 40 zeroes."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        sha = proc.stdout.strip()
        if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    except OSError:
        pass
    return "0" * 40


def compute_target_config_sha256(
    root: Path,
    product: str,
    version: str,
) -> str:
    """Compute SHA-256 of oracle/targets/<product>/<version>.yaml."""
    target_yaml = root / "oracle" / "targets" / product / f"{version}.yaml"
    if target_yaml.is_file():
        return hashlib.sha256(target_yaml.read_bytes()).hexdigest()
    return "0" * 64


def build_frames_and_summary(
    rows: list[dict[str, str]],
    *,
    input_filter: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build frames.jsonl records and protocol/frame statistics from TSV rows."""
    frames: list[dict[str, Any]] = []
    seq = 1
    t_rel_ms = 0
    tx_count = 0
    rx_count = 0
    bus_count = 0
    whos: set[int] = set()
    whats: set[int] = set()
    dims: set[int] = set()
    tested_inputs: list[str] = []

    for row in rows:
        inp = row["input"]
        if input_filter and inp not in input_filter:
            continue
        tested_inputs.append(inp)
        direction = row["direction"]
        reply = row["reply"]
        verdict = row["verdict"]
        output = row["output"]
        outputs = (
            [tok.strip() for tok in output.split(" | ") if tok.strip()]
            if output != "-"
            else []
        )
        bus_tokens = [tok for tok in outputs if tok.startswith("bus:")]
        own_tokens = [tok[4:] for tok in outputs if tok.startswith("own:")]
        bus_count += len(bus_tokens)

        if direction == "down" and OWN_FRAME_RE.match(inp):
            parsed = parse_own_frame(inp)
            if parsed["who"] is not None:
                whos.add(parsed["who"])
            if parsed["what"] is not None:
                whats.add(parsed["what"])
            if parsed["dimension"] is not None:
                dims.add(parsed["dimension"])

            bus_note = (
                f"bus output: {', '.join(bus_tokens)}"
                if bus_tokens
                else f"verdict: {verdict}"
            )
            frames.append(
                {
                    "seq": seq,
                    "t_rel_ms": t_rel_ms,
                    "dir": "tx",
                    "raw": inp,
                    **parsed,
                    "annotation": f"Oracle down input ({bus_note})",
                }
            )
            seq += 1
            t_rel_ms += 10
            tx_count += 1

            for own_frame in own_tokens:
                if OWN_FRAME_RE.match(own_frame):
                    p_own = parse_own_frame(own_frame)
                    frames.append(
                        {
                            "seq": seq,
                            "t_rel_ms": t_rel_ms,
                            "dir": "rx",
                            "raw": own_frame,
                            **p_own,
                            "annotation": f"Emitted OpenWebNet response to {inp}",
                        }
                    )
                    seq += 1
                    t_rel_ms += 5
                    rx_count += 1

            if reply in ("ack", "nack"):
                ack_raw = "*#*1##" if reply == "ack" else "*#*0##"
                frames.append(
                    {
                        "seq": seq,
                        "t_rel_ms": t_rel_ms,
                        "dir": "rx",
                        "raw": ack_raw,
                        "who": None,
                        "where": None,
                        "what": None,
                        "dimension": None,
                        "dimension_values": None,
                        "annotation": (
                            f"Gateway {reply.upper()} for {inp} ({bus_note})"
                        ),
                    }
                )
                seq += 1
                t_rel_ms += 90
                rx_count += 1
        elif direction == "up":
            for own_frame in own_tokens:
                if OWN_FRAME_RE.match(own_frame):
                    p_own = parse_own_frame(own_frame)
                    if p_own["who"] is not None:
                        whos.add(p_own["who"])
                    if p_own["what"] is not None:
                        whats.add(p_own["what"])
                    if p_own["dimension"] is not None:
                        dims.add(p_own["dimension"])
                    frames.append(
                        {
                            "seq": seq,
                            "t_rel_ms": t_rel_ms,
                            "dir": "rx",
                            "raw": own_frame,
                            **p_own,
                            "annotation": (
                                f"Emitted OpenWebNet frame from bus input {inp}"
                            ),
                        }
                    )
                    seq += 1
                    t_rel_ms += 100
                    rx_count += 1

    stats = {
        "total_frames": len(frames),
        "tx_frames": tx_count,
        "rx_frames": rx_count,
        "bus_frames": bus_count,
        "who": sorted(whos),
        "what": sorted(whats),
        "dimensions": sorted(dims),
        "tested_inputs": tested_inputs,
    }
    return frames, stats


def export_bundle(
    tsv_path: Path,
    out_dir: Path,
    *,
    root: Path = ROOT,
    evidence_id: str | None = None,
    capture_id: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    date_str: str = "2026-10-15",
    oracle_commit: str | None = None,
    inputs: list[str] | None = None,
) -> dict[str, Any]:
    """Export an oracle TSV file into an OpenWebNet-Encyclopedia evidence bundle."""
    header, rows = parse_oracle_tsv(tsv_path)
    if not header or "product" not in header or "suite" not in header:
        raise ValueError(f"{tsv_path}: missing required oracle TSV header keys")

    product = header["product"]
    version = header["version"]
    suite = header["suite"]
    harness = header.get("harness", "full")

    input_filter = set(inputs) if inputs else None
    crash_inputs = [
        r["input"]
        for r in rows
        if (not input_filter or r["input"] in input_filter)
        and r.get("verdict") == "crash"
    ]
    if crash_inputs:
        raise ValueError(
            f"{tsv_path}: cannot export evidence bundle containing "
            f"{len(crash_inputs)} crash verdict(s) (first: {crash_inputs[0]})"
        )
    frames, stats = build_frames_and_summary(rows, input_filter=input_filter)
    if not frames:
        raise ValueError(f"{tsv_path}: no exportable OpenWebNet frames matched")

    evid_id = evidence_id or default_evidence_id(product, suite)
    cap_id = capture_id or default_capture_id(product, suite, date_str)
    commit_sha = oracle_commit or detect_oracle_commit(root)
    target_cfg_sha = compute_target_config_sha256(root, product, version)

    cases_ext = ".cases"
    if (root / "oracle" / "cases" / f"{suite}.seq").is_file():
        cases_ext = ".seq"
    source_cases = f"oracle/cases/{suite}{cases_ext}"
    source_result = (
        f"results/{product}/{version}/oracle/{harness.replace(':', '-')}/{suite}.tsv"
    )

    bundle_title = (
        title or f"{product} ({version}) firmware oracle replay of suite '{suite}'"
    )
    bundle_summary = summary or (
        f"Deterministic QEMU user-mode firmware oracle execution of suite '{suite}' "
        f"on {product} firmware {version} ({stats['total_frames']} OpenWebNet frames, "
        f"{stats['bus_frames']} SCS bus frames)."
    )

    claims: list[str] = []
    if stats["who"]:
        claims.append(
            f"WHO {', '.join(str(w) for w in stats['who'])} firmware translation "
            f"verified on {product} ({version}) in suite '{suite}'."
        )
    else:
        claims.append(
            f"Firmware oracle suite '{suite}' verified on {product} ({version})."
        )

    manifest: dict[str, Any] = {
        "evidence_id": evid_id,
        "capture_id": cap_id,
        "title": bundle_title,
        "capture_kind": "firmware_oracle",
        "epistemic_status": "firmware_observed",
        "confidence": "high",
        "evidence_class": "firmware_emulation",
        "recorded_at": f"{date_str}T00:00:00Z",
        "installation_id": None,
        "bus_id": None,
        "sub_bus_id": None,
        "gateway_id": None,
        "device_under_test": None,
        "environment": {
            "gateway_model": product,
            "gateway_firmware": version,
            "bus_topology": "Simulated QEMU + PTY bus",
        },
        "summary": bundle_summary,
        "test_recipe": [
            f"Fetch and unpack {product} {version} image "
            f"(SHA-256: {header['image_sha256'][:12]}...)",
            f"Run oracle suite {source_cases} against "
            f"oracle/targets/{product}/{version}.yaml",
            f"Verify deterministic output in {source_result}",
        ],
        "claims_supported": claims,
        "frame_count": len(frames),
        "result_outcome": "supported",
        "firmware_target": {
            "product": product,
            "version": version,
            "image_sha256": header["image_sha256"],
            "target_config_sha256": target_cfg_sha,
            "harness": harness,
            "target_sha256": header["target_sha256"],
            "adapter": header.get("adapter", "pty-1"),
            "reset": header.get("reset", "each"),
            "bus": header.get("bus", "pty"),
            "framer": header.get("framer", "line"),
            "responder": header.get("responder", "pic"),
            "settle_ms": str(header.get("settle_ms", "300")),
            "quiet_ms": str(header.get("quiet_ms", "50")),
            "suite": suite,
            "suite_sha256": header["suite_sha256"],
            "oracle_version": str(header.get("oracle_version", "1")),
            "oracle_commit": commit_sha,
            "source_result": source_result,
            "source_cases": source_cases,
        },
    }

    privacy: dict[str, Any] = {
        "classification": "public",
        "removed_value_classes": [],
        "notes": (
            "Synthetic deterministic firmware emulation run in an isolated QEMU "
            "sandbox; no physical installation data or personal identifiers exist."
        ),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    frames_lines = [json.dumps(frame, separators=(",", ":")) for frame in frames]
    (out_dir / "frames.jsonl").write_text(
        "\n".join(frames_lines) + "\n", encoding="utf-8", newline="\n"
    )
    (out_dir / "privacy.json").write_text(
        json.dumps(privacy, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export an oracle TSV into a schema-valid OpenWebNet-Encyclopedia "
            "evidence bundle."
        )
    )
    parser.add_argument(
        "tsv_path",
        type=Path,
        help="Path to oracle TSV (e.g. results/MH200N/010108/oracle/full/what19.tsv)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Destination directory for manifest.json, frames.jsonl, and privacy.json",
    )
    parser.add_argument(
        "--evidence-id",
        help="Override evidence_id (default: EVID-<PRODUCT>-<SUITE>-001)",
    )
    parser.add_argument(
        "--capture-id",
        help="Override capture_id (default: cap-<product>-<suite>-<YYYYMMDD>)",
    )
    parser.add_argument("--title", help="Custom bundle title")
    parser.add_argument("--summary", help="Custom bundle summary")
    parser.add_argument(
        "--date",
        default="2026-10-15",
        help="ISO date YYYY-MM-DD for recorded_at and default capture_id",
    )
    parser.add_argument(
        "--oracle-commit",
        help="40-hex git commit SHA of own-firmware-oracle (default: git HEAD)",
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        help="Export only specific input frame(s) (repeatable)",
    )
    args = parser.parse_args(argv)

    try:
        manifest = export_bundle(
            args.tsv_path,
            args.out_dir,
            evidence_id=args.evidence_id,
            capture_id=args.capture_id,
            title=args.title,
            summary=args.summary,
            date_str=args.date,
            oracle_commit=args.oracle_commit,
            inputs=args.inputs,
        )
    except (OSError, ValueError) as exc:
        print(f"export_evidence: ERROR - {exc}", file=sys.stderr)
        return 1

    print(
        f"Exported {manifest['evidence_id']} ({manifest['frame_count']} frames) "
        f"-> {args.out_dir}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
