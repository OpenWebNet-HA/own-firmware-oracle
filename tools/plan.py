#!/usr/bin/env python3
"""plan -- decide which catalog images need (re)building, and where results go.

Results are keyed on (image sha256, tool version). An image is rebuilt only
when that key is not already recorded in its manifest header, so CI cost grows
with what changed, not with the size of the catalog.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import load
from unpack import TOOL_VERSION  # single source of truth for the tool version


def result_path(catalog: Path) -> str:
    # product / version are validated plain names (schema.py), so this path
    # is safe to hand to a shell or a workflow matrix.
    entry = load(catalog)
    return f"{entry['product']}/{entry['version']}/manifest.tsv"


def is_stale(catalog: Path, results_root: Path) -> bool:
    """Stale unless the manifest records BOTH the current image hash and tool
    version. A change to unpack.py (new TOOL_VERSION) therefore rebuilds every
    image, which is the key documented in this module's docstring."""
    entry = load(catalog)
    manifest = results_root / result_path(catalog)
    if not manifest.exists():
        return True
    head = [ln for ln in manifest.read_text().splitlines() if ln.startswith("#")]
    want_image = f"# image_sha256={entry['image']['sha256']}"
    want_tool = f"# tool_version={TOOL_VERSION}"
    return want_image not in head or want_tool not in head


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
        # Every entry is validated by load() first, so a catalog file whose
        # path or fields are not plain names fails the plan job loudly instead
        # of reaching the oracle job's shell.
        stale = [
            c.relative_to(root).as_posix()
            for c in sorted((root / "catalog").rglob("*.yaml"))
            if is_stale(c, root / "results")
        ]
        print("matrix=" + json.dumps(stale))
        return

    for c in sorted((root / "catalog").rglob("*.yaml")):
        print(f"{'STALE' if is_stale(c, root / 'results') else 'ok   '} {c}")


if __name__ == "__main__":
    main()
