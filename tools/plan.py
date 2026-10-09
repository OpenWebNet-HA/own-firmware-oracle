#!/usr/bin/env python3
"""plan -- decide which catalog images need (re)building, and where results go.

Results are keyed on (image sha256, tool version). An image is rebuilt only
when that key is not already recorded in its manifest header, so CI cost grows
with what changed, not with the size of the catalog.

The reverse question -- does a FRESH manifest still come out byte for byte? --
is reproduce.yml's: it re-runs every fresh entry that has a public vendor
source (no secrets needed) and fails on any diff.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema import is_blocked, load
from unpack import TOOL_VERSION  # single source of truth for the tool version

ROOT = Path(__file__).resolve().parent.parent

GLOBAL_TRIGGERS: tuple[str, ...] = (
    "tools/",
    "requirements/oracle.txt",
    ".github/workflows/reproduce.yml",
    ".github/actions/",
)


def result_path(catalog: Path) -> str:
    # product / version are validated plain names (schema.py), so this path
    # is safe to hand to a shell or a workflow matrix.
    entry = load(catalog)
    return f"{entry['product']}/{entry['version']}/manifest.tsv"


def is_stale(catalog: Path, results_root: Path) -> bool:
    """Stale unless the manifest records BOTH the current image hash and tool
    version. A change to unpack.py (new TOOL_VERSION) therefore rebuilds every
    image, which is the key documented in this module's docstring.

    A blocked entry is never stale: it cannot be unpacked, so rebuilding it
    would only fail the oracle job."""
    entry = load(catalog)
    if is_blocked(entry):
        return False
    manifest = results_root / result_path(catalog)
    if not manifest.exists():
        return True
    head = [ln for ln in manifest.read_text().splitlines() if ln.startswith("#")]
    want_image = f"# image_sha256={entry['image']['sha256']}"
    want_tool = f"# tool_version={TOOL_VERSION}"
    return want_image not in head or want_tool not in head


def is_reproducible(catalog: Path, results_root: Path) -> bool:
    """A fresh manifest whose image anyone can fetch: no R2 secrets needed.

    Stale entries are left out on purpose; their manifest is SUPPOSED to change
    and oracle.yml rebuilds it. Blocked entries have no manifest to re-check.
    """
    entry = load(catalog)
    if is_blocked(entry):
        return False
    public = any("vendor" in s for s in entry["wrapper"].get("sources", []))
    return public and not is_stale(catalog, results_root)


def touched_catalog_entries(root: Path, changed_files: list[str]) -> set[str] | None:
    """Map changed files to catalog entry paths relative to root.

    Returns None if any changed file touches global extraction/reproduction
    logic (meaning every reproducible entry should be re-tested).
    Otherwise returns a set of catalog entry paths (e.g. 'catalog/H4684/020054.yaml').
    """
    touched: set[str] = set()
    for raw in changed_files:
        p = raw.replace("\\", "/").strip().removeprefix("./")
        if any(p == trig or p.startswith(trig) for trig in GLOBAL_TRIGGERS):
            return None

        if p.startswith("catalog/") and p.endswith(".yaml"):
            cat = root / p
            if cat.is_file():
                touched.add(cat.relative_to(root).as_posix())
        elif p.startswith("results/"):
            parts = Path(p).parts
            if len(parts) >= 3:
                prod, ver = parts[1], parts[2]
                cat = root / "catalog" / prod / f"{ver}.yaml"
                if cat.is_file():
                    touched.add(cat.relative_to(root).as_posix())
    return touched


def git_changed_files(base_ref: str, root: Path) -> list[str] | None:
    """Return files changed between base_ref and HEAD, or None on failure."""
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    except (subprocess.SubprocessError, OSError) as err:
        print(f"plan: git diff against '{base_ref}' failed: {err}", file=sys.stderr)
        return None


def _matrix(
    root: Path,
    keep: Callable[[Path, Path], bool],
    scope: set[str] | None = None,
) -> str:
    # Every entry is validated by load() first, so a catalog file whose path or
    # fields are not plain names fails the plan job loudly instead of reaching
    # a later job's shell.
    picked = [
        c.relative_to(root).as_posix()
        for c in sorted((root / "catalog").rglob("*.yaml"))
        if keep(c, root / "results")
        and (scope is None or c.relative_to(root).as_posix() in scope)
    ]
    return "matrix=" + json.dumps(picked)


def _label(catalog: Path, results_root: Path) -> str:
    if is_blocked(load(catalog)):
        return "BLOCK"
    return "STALE" if is_stale(catalog, results_root) else "ok   "


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("catalog", nargs="?", help="a single catalog yaml")
    ap.add_argument(
        "--emit-matrix", action="store_true", help="stale entries (oracle.yml)"
    )
    ap.add_argument(
        "--emit-reproduce-matrix",
        action="store_true",
        help="fresh entries with a public source (reproduce.yml)",
    )
    ap.add_argument(
        "--diff-base",
        metavar="REF",
        help="scope reproduce matrix to entries changed since REF (git revision)",
    )
    ap.add_argument(
        "--changed-files",
        nargs="*",
        metavar="PATH",
        help="explicit list of changed file paths to scope the reproduce matrix",
    )
    ap.add_argument("--result-path", action="store_true")
    args = ap.parse_args()

    root = ROOT

    if args.result_path:
        print(result_path(Path(args.catalog)))
        return

    if args.emit_matrix:
        print(_matrix(root, is_stale))
        return

    if args.emit_reproduce_matrix:
        scope: set[str] | None = None
        if args.changed_files is not None:
            scope = touched_catalog_entries(root, args.changed_files)
        elif args.diff_base:
            files = git_changed_files(args.diff_base, root)
            if files is not None:
                scope = touched_catalog_entries(root, files)
        print(_matrix(root, is_reproducible, scope=scope))
        return

    for c in sorted((root / "catalog").rglob("*.yaml")):
        print(f"{_label(c, root / 'results')} {c}")


if __name__ == "__main__":
    main()
