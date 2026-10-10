"""suite_plan: which emulated suites CI re-runs, and how it judges a re-run.

No firmware and no QEMU: the repository tree is synthetic and oracle.run is
replaced by a fake runner that writes the file a real run would have written.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import suite_plan

BLOB = b"synthetic firmware wrapper"
VENDOR = "https://www.bticino.be/fw/FW.zip"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _catalog(
    root: Path, product: str, version: str, source: str = "", extra: str = ""
) -> None:
    cat = root / "catalog" / product / f"{version}.yaml"
    cat.parent.mkdir(parents=True, exist_ok=True)
    source = source or f"    - vendor: '{VENDOR}'\n"
    cat.write_text(
        f"product: {product}\nversion: '{version}'\n"
        f"wrapper:\n  filename: FW.zip\n  size: {len(BLOB)}\n"
        f"  sha256: '{_sha(BLOB)}'\n  sources:\n{source}"
        f"image:\n  filename: fw.fwz\n  size: 1\n  sha256: '{'a' * 64}'\n{extra}"
    )


def _target(root: Path, product: str, version: str) -> None:
    spec = root / "oracle" / "targets" / product / f"{version}.yaml"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("product: x\n")


def _tsv(
    root: Path,
    product: str,
    version: str,
    suite: str,
    reset: str = "each",
    body: str = "down\t*1*1*21##\tack\tout\n",
    **over: str,
) -> Path:
    head = dict.fromkeys(suite_plan.INPUT_KEYS, "v")
    head.update(product=product, version=version, suite=suite, reset=reset, **over)
    path = suite_plan.result_file(root, product, version, suite)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"# {k}={v}\n" for k, v in head.items()) + "direction\tinput\n" + body
    )
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Two public emulated targets, plus targets the plan must leave out."""
    cases = tmp_path / "oracle" / "cases"
    cases.mkdir(parents=True)
    (cases / "alpha.cases").write_text("down *1*1*21##\n")
    (cases / "beta.seq").write_text("down *1*0*21##\n")
    (cases / "README.md").write_text("not a suite\n")
    (cases / "bad name.cases").write_text("not a plain name\n")
    for product, version in (("GW1", "010000"), ("GW2", "020000")):
        _catalog(tmp_path, product, version)
        _target(tmp_path, product, version)
    _target(tmp_path, "NOCAT", "010000")  # target without a catalog entry
    _catalog(tmp_path, "R2ONLY", "010000", source="    - r2: 'fw/x.zip'\n")
    _target(tmp_path, "R2ONLY", "010000")  # needs secrets
    _catalog(
        tmp_path,
        "BLOCK",
        "010000",
        extra="status: blocked\nblocked_reason: layer not opened\n",
    )
    _target(tmp_path, "BLOCK", "010000")
    return tmp_path


def test_inventory(repo: Path) -> None:
    assert list(suite_plan.suites(repo)) == ["alpha", "beta"]
    assert suite_plan.targets(repo) == [("GW1", "010000"), ("GW2", "020000")]


def test_header_stops_at_the_first_data_line(repo: Path) -> None:
    path = _tsv(repo, "GW1", "010000", "alpha")
    head = suite_plan.header(path)
    assert head["suite"] == "alpha"
    assert "direction\tinput" not in head
    only_head = repo / "h.tsv"
    only_head.write_text("# a=1\n# b=2=3\n")
    assert suite_plan.header(only_head) == {"a": "1", "b": "2=3"}


def test_default_reset_is_the_majority_then_alphabetical(repo: Path) -> None:
    assert (
        suite_plan.default_reset(repo, "GW1", "010000") == "each"
    )  # nothing committed
    _tsv(repo, "GW1", "010000", "alpha", reset="0")
    _tsv(repo, "GW1", "010000", "beta", reset="each")
    assert suite_plan.default_reset(repo, "GW1", "010000") == "0"  # tie -> sorted first
    _tsv(repo, "GW1", "010000", "gamma", reset="each")
    assert suite_plan.default_reset(repo, "GW1", "010000") == "each"


def test_plan_runs_every_suite_on_every_target(repo: Path) -> None:
    _tsv(repo, "GW1", "010000", "alpha", reset="batch-5")
    _tsv(repo, "GW1", "010000", "gamma", reset="batch-5")  # sets GW1's default
    assert suite_plan.plan(repo) == [
        {
            "catalog": "catalog/GW1/010000.yaml",
            "target": "oracle/targets/GW1/010000.yaml",
            "runs": "alpha:batch-5 beta:batch-5",
        },
        {
            "catalog": "catalog/GW2/020000.yaml",
            "target": "oracle/targets/GW2/020000.yaml",
            "runs": "alpha:each beta:each",
        },
    ]
    scoped = suite_plan.plan(repo, {("GW2", "020000", "beta")})
    assert scoped == [
        {
            "catalog": "catalog/GW2/020000.yaml",
            "target": "oracle/targets/GW2/020000.yaml",
            "runs": "beta:each",
        }
    ]


@pytest.mark.parametrize(
    ("changed", "want"),
    [
        (
            ["oracle/cases/alpha.cases"],
            {("GW1", "010000", "alpha"), ("GW2", "020000", "alpha")},
        ),
        (["oracle/cases/gone.cases"], set()),
        (
            ["oracle/targets/GW1/010000.yaml"],
            {("GW1", "010000", "alpha"), ("GW1", "010000", "beta")},
        ),
        (["oracle/targets/NOCAT/010000.yaml"], set()),
        (
            ["catalog/GW2/020000.yaml"],
            {("GW2", "020000", "alpha"), ("GW2", "020000", "beta")},
        ),
        (["catalog/F455/010102.yaml"], set()),
        (["results/GW1/010000/oracle/full/beta.tsv"], {("GW1", "010000", "beta")}),
        (["results/GW1/010000/oracle/full/gone.tsv"], set()),
        (["results/NOCAT/010000/oracle/full/alpha.tsv"], set()),
        (["results/GW1/010000/manifest.tsv", "README.md", "./findings/x.md"], set()),
        (["oracle/run.py"], None),
        (["oracle/targets/README.md"], None),
        (["tools\\suite_plan.py"], None),
        ([".github/workflows/suites.yml"], None),
    ],
)
def test_touched(repo: Path, changed: list[str], want: set | None) -> None:
    assert suite_plan.touched(repo, changed) == want


def test_verdicts(repo: Path, tmp_path: Path) -> None:
    committed = _tsv(repo, "GW1", "010000", "alpha")
    produced = tmp_path / "p.tsv"
    assert suite_plan.verdict(repo / "missing.tsv", committed) == "new"
    produced.write_bytes(committed.read_bytes())
    assert suite_plan.verdict(committed, produced) == "same"
    produced.write_text(
        committed.read_text().replace("suite_sha256=v", "suite_sha256=w")
    )
    assert suite_plan.verdict(committed, produced) == "stale"
    produced.write_text(committed.read_text().replace("\tack\t", "\tnack\t"))
    assert suite_plan.verdict(committed, produced) == "regressed"


@pytest.mark.parametrize(
    "bad",
    [
        "oracle/targets/GW1.yaml",
        "oracle/other/GW1/010000.yaml",
        "oracle/targets/GW1/010000.yml",
        "oracle/targets/G W/010000.yaml",
        "oracle/targets/GW1/0 1.yaml",
    ],
)
def test_parse_target_rejects(bad: str) -> None:
    with pytest.raises(ValueError, match="not oracle/targets"):
        suite_plan.parse_target(bad)


def test_parse_runs(repo: Path) -> None:
    assert suite_plan.parse_runs("alpha:each beta:batch-3", repo) == [
        ("alpha", "each"),
        ("beta", "batch-3"),
    ]
    for bad in ("gamma:each", "alpha:never", "alpha"):
        with pytest.raises(ValueError, match="bad run"):
            suite_plan.parse_runs(bad, repo)


def _fake_runner(
    behaviour: dict[str, str], seen: list[list[str]], committed_root: Path
):
    """Write what oracle.run would: same bytes, a changed input, a changed verdict."""

    def run(argv):
        seen.append(list(argv))
        suite = Path(argv[5]).stem
        out = Path(argv[argv.index("-o") + 1])
        mode = behaviour[suite]
        if mode == "fail":
            return 3
        if mode == "silent":
            return 0  # exit 0 but no file: still a failure
        ref = suite_plan.result_file(committed_root, "GW1", "010000", suite)
        text = ref.read_text() if ref.is_file() else "# suite=x\nrow\n"
        if mode == "stale":
            text = text.replace("image_sha256=v", "image_sha256=new")
        if mode == "regressed":
            text = text.replace("\tack\t", "\tnack\t")
        out.write_text(text)
        return 0

    return run


@pytest.mark.parametrize(
    ("mode", "rc", "kept", "summary"),
    [
        ("same", 0, False, "| alpha | each | same |"),
        ("new", 0, True, "| alpha | each | new |"),
        ("stale", 0, True, "| alpha | each | stale |"),
        ("regressed", 1, False, "| alpha | each | **regressed** |"),
        ("fail", 1, False, "| alpha | each | **run failed** |"),
        ("silent", 1, False, "| alpha | each | **run failed** |"),
    ],
)
def test_run_target(
    repo, tmp_path, monkeypatch, capsys, mode, rc, kept, summary
) -> None:
    if mode != "new":
        _tsv(repo, "GW1", "010000", "alpha")
    step = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
    seen: list[list[str]] = []
    out = tmp_path / "out"
    got = suite_plan.run_target(
        repo,
        "oracle/targets/GW1/010000.yaml",
        "/img.zip",
        "alpha:each",
        out,
        _fake_runner({"alpha": mode}, seen, repo),
    )
    assert got == rc
    assert seen[0][2:7] == [
        "oracle.run",
        "suite",
        "oracle/targets/GW1/010000.yaml",
        "oracle/cases/alpha.cases",
        "--image",
    ]
    produced = out / "results/GW1/010000/oracle/full/alpha.tsv"
    assert produced.is_file() is kept
    text = step.read_text()
    assert "### GW1 010000" in text
    assert summary in text
    if mode == "regressed":
        assert "```diff" in text
        assert "+down\t*1*1*21##\tnack\tout" in text
        assert (
            "::error file=results/GW1/010000/oracle/full/alpha.tsv::"
            in capsys.readouterr().out
        )


def test_run_target_without_a_step_summary(repo, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    got = suite_plan.run_target(
        repo,
        "oracle/targets/GW1/010000.yaml",
        "/img.zip",
        "beta:0",
        tmp_path / "out",
        _fake_runner({"beta": "new"}, [], repo),
    )
    assert got == 0


def test_subprocess_runner_returns_the_exit_code(monkeypatch) -> None:
    calls = []

    def fake_run(argv, cwd, check):
        calls.append((argv, cwd, check))
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(suite_plan.subprocess, "run", fake_run)
    assert suite_plan._subprocess_runner(("true",)) == 7
    assert calls == [(["true"], suite_plan.ROOT, False)]


def test_main(repo, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(suite_plan, "ROOT", repo)

    def matrix(*argv: str) -> list:
        assert suite_plan.main(list(argv)) == 0
        out = capsys.readouterr().out.strip()
        assert out.startswith("matrix=")
        return json.loads(out.removeprefix("matrix="))

    assert len(matrix("--emit-matrix")) == 2
    assert matrix(
        "--emit-matrix", "--changed-files", "results/GW2/020000/oracle/full/alpha.tsv"
    ) == [
        {
            "catalog": "catalog/GW2/020000.yaml",
            "target": "oracle/targets/GW2/020000.yaml",
            "runs": "alpha:each",
        }
    ]
    monkeypatch.setattr(
        suite_plan, "git_changed_files", lambda ref, root: ["README.md"]
    )
    assert matrix("--emit-matrix", "--diff-base", "origin/main") == []
    monkeypatch.setattr(suite_plan, "git_changed_files", lambda ref, root: None)
    assert (
        len(matrix("--emit-matrix", "--diff-base", "bad")) == 2
    )  # unknown diff: everything

    assert suite_plan.main([]) == 0
    assert (
        "oracle/targets/GW1/010000.yaml: alpha:each beta:each"
        in capsys.readouterr().out
    )

    with pytest.raises(SystemExit) as err:
        suite_plan.main(["--run", "--target", "oracle/targets/GW1/010000.yaml"])
    assert err.value.code == 2

    seen: list[list[str]] = []
    got = suite_plan.main(
        [
            "--run",
            "--target",
            "oracle/targets/GW1/010000.yaml",
            "--image",
            "/img.zip",
            "--runs",
            "alpha:each",
            "--out",
            str(tmp_path / "o"),
        ],
        runner=_fake_runner({"alpha": "new"}, seen, repo),
    )
    assert got == 0
    assert len(seen) == 1
