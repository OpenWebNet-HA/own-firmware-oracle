#!/usr/bin/env python3
"""plan -- decide which catalog images need (re)building, and where results go.

Results are keyed on (image sha256, tool version). An image is rebuilt only
when that key is not already recorded in its manifest header, so CI cost grows
with what changed, not with the size of the catalog.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

TOOL_VERSION = "1"  # bump to force a rebuild of every image


def result_path(catalog: Path) -> str:
    entry = yaml.safe_load(catalog.read_text())
    return f"{entry['product']}/{entry['version']}/manifest.tsv"


def is_stale(catalog: Path, results_root: Path) -> bool:
    entry = yaml.safe_load(catalog.read_text())
    manifest = results_root / result_path(catalog)
    if not manifest.exists():
        return True
    head = manifest.read_text().splitlines()[:3]
    want = f"# image_sha256={entry['image']['sha256']}"
    return want not in head


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("catalog", nargs="?", help="a single catalog yaml")
    ap.add_argument("--emit-matrix", action="store_true")
    ap.add_argument("--result-path", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent

    if args.result_path:
        print(result_path(Path(args.catalog)))
        return

    if args.emit_matrix:
        stale = [
            str(c.relative_to(root))
            for c in sorted((root / "catalog").rglob("*.yaml"))
            if is_stale(c, root / "results")
        ]
        print("matrix=" + json.dumps(stale))
        return

    for c in sorted((root / "catalog").rglob("*.yaml")):
        print(f"{'STALE' if is_stale(c, root / 'results') else 'ok   '} {c}")


if __name__ == "__main__":
    main()
