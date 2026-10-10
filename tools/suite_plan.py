#!/usr/bin/env python3
"""suite_plan -- re-run the emulated suites in CI and say what changed.

A committed results/<product>/<version>/oracle/full/<suite>.tsv claims that
running oracle/cases/<suite>.cases on oracle/targets/<product>/<version>.yaml
gives exactly that file. suites.yml re-checks every claim in Actions, so a
change to the oracle, a target, a case file or the runner can no longer alter
a verdict unnoticed, and nobody needs the firmware or QEMU locally.

Every (emulated target, suite) pair gets one of five verdicts:

  same       the run reproduced the committed file byte for byte
  new        no file was committed yet (a new suite or target): produced
  stale      an input recorded in the header changed (image, target, case
             file, oracle version, reset policy...): produced, to be committed
  regressed  same inputs, different output: the job FAILS
  unstable   a new or stale result that a second run does not reproduce:
             the job FAILS, so a flaky result never enters results/

Produced files (new and stale, each reproduced twice) are uploaded; on main
the publish job turns them into one pull request, so results are never
computed by hand.

Planning (--emit-matrix) groups the pairs by target, one Actions job each, and
scopes a pull request to what it touched. Running (--run) executes one job.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import git_changed_files
from schema import NAME, is_blocked, load

ROOT = Path(__file__).resolve().parent.parent

# Header fields that are INPUTS of a run. If any differs between the
# committed file and a fresh run, the committed file is stale, not regressed.
INPUT_KEYS = (
    "product",
    "version",
    "image_sha256",
    "harness",
    "target_sha256",
    "adapter",
    "reset",
    "bus",
    "framer",
    "responder",
    "settle_ms",
    "suite",
    "suite_sha256",
    "oracle_version",
)
RESET = re.compile(r"^(each|0|batch-[1-9][0-9]*)$")
CASE_SUFFIXES = (".cases", ".seq")
# A change under these paths can alter any verdict: re-check everything.
GLOBAL_TRIGGERS = (
    "oracle/",
    "tools/suite_plan.py",
    "tools/fwfetch.py",
    "tools/schema.py",
    "requirements/oracle.txt",
    ".github/workflows/suites.yml",
    ".github/actions/",
)

Pair = tuple[str, str, str]  # (product, version, suite)
Runner = Callable[[Sequence[str]], int]


def header(path: Path) -> dict[str, str]:
    """The `# key=value` lines at the top of a results file."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        if not line.startswith("# "):
            break
        key, _, value = line[2:].partition("=")
        out[key] = value
    return out


def suites(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted((root / "oracle" / "cases").iterdir()):
        if path.suffix in CASE_SUFFIXES and NAME.match(path.stem):
            found[path.stem] = path
    return found


def targets(root: Path) -> list[tuple[str, str]]:
    """Emulated targets whose image anyone can fetch (no R2 secrets)."""
    found: list[tuple[str, str]] = []
    for spec in sorted((root / "oracle" / "targets").glob("*/*.yaml")):
        product, version = spec.parent.name, spec.stem
        catalog = root / "catalog" / product / f"{version}.yaml"
        if not catalog.is_file():
            continue
        entry = load(catalog)
        public = any("vendor" in s for s in entry["wrapper"].get("sources", []))
        if public and not is_blocked(entry):
            found.append((product, version))
    return found


def result_file(root: Path, product: str, version: str, suite: str) -> Path:
    return root / "results" / product / version / "oracle" / "full" / f"{suite}.tsv"


def default_reset(root: Path, product: str, version: str) -> str:
    """The reset policy most of this target's committed suites use."""
    full = root / "results" / product / version / "oracle" / "full"
    counts = Counter(
        header(tsv).get("reset", "each") for tsv in sorted(full.glob("*.tsv"))
    )
    if not counts:
        return "each"
    best = max(counts.values())
    return sorted(r for r, n in counts.items() if n == best)[0]


def touched(root: Path, changed: list[str]) -> set[Pair] | None:
    """The pairs a change can affect; None means all of them."""
    names = suites(root)
    tgts = targets(root)
    pairs: set[Pair] = set()
    for raw in changed:
        p = raw.replace("\\", "/").strip().removeprefix("./")
        parts = p.split("/")
        if p.startswith("oracle/cases/") and len(parts) == 3:
            stem = Path(parts[2]).stem
            if stem in names:
                pairs.update((prod, ver, stem) for prod, ver in tgts)
            continue
        key: tuple[str, str] | None = None
        if p.startswith("oracle/targets/") and len(parts) == 4:
            key = (parts[2], Path(parts[3]).stem)
        elif p.startswith("catalog/") and len(parts) == 3:
            key = (parts[1], Path(parts[2]).stem)
        if key is not None:
            if key in tgts:
                pairs.update((*key, s) for s in names)
            continue
        if (
            p.startswith("results/")
            and len(parts) == 6
            and parts[3:5] == ["oracle", "full"]
        ):
            key = (parts[1], parts[2])
            stem = Path(parts[5]).stem
            if key in tgts and stem in names:
                pairs.add((*key, stem))
            continue
        if any(p == t or p.startswith(t) for t in GLOBAL_TRIGGERS):
            return None
    return pairs


def plan(root: Path, scope: set[Pair] | None = None) -> list[dict[str, str]]:
    """One matrix item per target: every case file runs on every target."""
    names = suites(root)
    jobs: list[dict[str, str]] = []
    for product, version in targets(root):
        fallback = default_reset(root, product, version)
        runs: list[str] = []
        for suite in names:
            if scope is not None and (product, version, suite) not in scope:
                continue
            committed = result_file(root, product, version, suite)
            reset = (
                header(committed).get("reset", fallback)
                if committed.is_file()
                else fallback
            )
            runs.append(f"{suite}:{reset}")
        if runs:
            jobs.append(
                {
                    "catalog": f"catalog/{product}/{version}.yaml",
                    "target": f"oracle/targets/{product}/{version}.yaml",
                    "runs": " ".join(runs),
                }
            )
    return jobs


def verdict(committed: Path, produced: Path) -> str:
    if not committed.is_file():
        return "new"
    if committed.read_bytes() == produced.read_bytes():
        return "same"
    old, new = header(committed), header(produced)
    if any(old.get(k) != new.get(k) for k in INPUT_KEYS):
        return "stale"
    return "regressed"


def _subprocess_runner(argv: Sequence[str]) -> int:
    # argv is built by run_target from validated names only.
    return subprocess.run(list(argv), cwd=ROOT, check=False).returncode  # noqa: S603


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def parse_target(target: str) -> tuple[str, str]:
    parts = target.replace("\\", "/").split("/")
    if (
        len(parts) != 4
        or parts[:2] != ["oracle", "targets"]
        or not parts[3].endswith(".yaml")
        or not NAME.match(parts[2])
        or not NAME.match(parts[3][: -len(".yaml")])
    ):
        raise ValueError(f"not oracle/targets/<product>/<version>.yaml: {target!r}")
    return parts[2], parts[3][: -len(".yaml")]


def parse_runs(runs: str, root: Path) -> list[tuple[str, str]]:
    names = suites(root)
    out: list[tuple[str, str]] = []
    for token in runs.split():
        suite, _, reset = token.partition(":")
        if suite not in names or not RESET.match(reset):
            raise ValueError(f"bad run {token!r}: want <suite>:<reset>")
        out.append((suite, reset))
    return out


def run_target(
    root: Path,
    target: str,
    image: str,
    runs: str,
    out_dir: Path,
    runner: Runner = _subprocess_runner,
) -> int:
    """Run each suite on one target; keep only new and stale files in out_dir."""
    product, version = parse_target(target)
    names = suites(root)
    failures = 0
    rows = [
        f"### {product} {version}",
        "",
        "| suite | reset | verdict |",
        "| --- | --- | --- |",
    ]
    details: list[str] = []
    for suite, reset in parse_runs(runs, root):
        rel = Path("results") / product / version / "oracle" / "full" / f"{suite}.tsv"
        produced = out_dir / rel
        produced.parent.mkdir(parents=True, exist_ok=True)
        case = str(names[suite].relative_to(root))

        def run_once(out: Path, case: str = case, reset: str = reset) -> bool:
            argv = [
                sys.executable, "-m", "oracle.run", "suite", target, case,
                "--image", image, "--reset", reset, "-o", str(out),
            ]  # fmt: skip
            return runner(argv) == 0 and out.is_file()

        if not run_once(produced):
            failures += 1
            rows.append(f"| {suite} | {reset} | **run failed** |")
            print(f"::error::{product} {version} {suite}: oracle.run failed")
            produced.unlink(missing_ok=True)
            continue
        committed = root / rel
        v = verdict(committed, produced)
        # (old, new, label) of the diff that explains a failure
        evidence: tuple[Path, Path, str] | None = None
        if v == "regressed":
            evidence = (committed, produced, "re-run")
        elif v in ("new", "stale"):
            # A result only enters results/ if it reproduces: run it again.
            again = produced.with_name(produced.name + ".again")
            if not run_once(again):
                again.write_text("", encoding="ascii")
            if again.read_bytes() != produced.read_bytes():
                v, evidence = "unstable", (produced, again, "second run")
            else:
                again.unlink()
        rows.append(f"| {suite} | {reset} | {f'**{v}**' if evidence else v} |")
        if v == "same":
            produced.unlink()
        if evidence is not None:
            failures += 1
            old, new, label = evidence
            diff = difflib.unified_diff(
                old.read_text(encoding="ascii").splitlines(),
                new.read_text(encoding="ascii").splitlines(),
                str(rel),
                label,
                lineterm="",
            )
            details += [f"#### `{rel}` {v}", "```diff", *list(diff)[:200], "```"]
            why = {
                "regressed": "same inputs, different verdicts",
                "unstable": "two runs with the same inputs disagree",
            }[v]
            print(f"::error file={rel}::{why} (diff in the job summary)")
            produced.unlink(missing_ok=True)
            new.unlink(missing_ok=True)
    _summary([*rows, "", *details])
    return 1 if failures else 0


def main(argv: list[str] | None = None, runner: Runner = _subprocess_runner) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit-matrix", action="store_true")
    ap.add_argument("--diff-base", metavar="REF")
    ap.add_argument("--changed-files", nargs="*", metavar="PATH")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--target")
    ap.add_argument("--image")
    ap.add_argument("--runs")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    if args.emit_matrix:
        scope: set[Pair] | None = None
        if args.changed_files is not None:
            scope = touched(ROOT, args.changed_files)
        elif args.diff_base:
            files = git_changed_files(args.diff_base, ROOT)
            if files is not None:
                scope = touched(ROOT, files)
        print("matrix=" + json.dumps(plan(ROOT, scope)))
        return 0
    if args.run:
        if not (args.target and args.image and args.runs and args.out):
            ap.error("--run needs --target, --image, --runs and --out")
        return run_target(ROOT, args.target, args.image, args.runs, args.out, runner)
    for job in plan(ROOT):
        print(f"{job['target']}: {job['runs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
