"""Unit tests for tools/verdict_diff.py (semantic diff generator for oracle TSVs)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import verdict_diff


def test_infer_meta_from_path() -> None:
    assert verdict_diff._infer_meta_from_path(
        "results/MH200N/010108/oracle/full/lights.tsv"
    ) == ("MH200N", "010108", "lights")
    assert verdict_diff._infer_meta_from_path(
        "MH200N/010108/oracle/full/lights.tsv"
    ) == ("MH200N", "010108", "lights")
    assert verdict_diff._infer_meta_from_path("lights.tsv") == ("", "", "lights")
    assert verdict_diff._infer_meta_from_path("") == ("", "", "")


def test_parse_tsv_text_and_duplicate_steps() -> None:
    text = (
        "# comment without equals\n"
        "# product=MH200N version=010108 suite=seq-test\n"
        "\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:a8 21\n"
        "down\t*1*1*21##\tnack\tsilent\t-\n"
        "down\t*1*0*21##\tnack\tout\tbus:a8 00\n"
        "up\ta8 21\t-\tout\town:*1*1*21##\n"
        "short\tline\n"
    )
    parsed = verdict_diff.parse_tsv_text(
        text, rel_path="results/MH200N/010108/oracle/full/seq-test.tsv"
    )
    assert parsed.product == "MH200N"
    assert parsed.version == "010108"
    assert parsed.suite == "seq-test"
    assert len(parsed.rows) == 4

    # Repeated step gets step_index 1 and 2, unique steps get step_index 0
    k1 = verdict_diff.RowKey("MH200N", "010108", "seq-test", 1, "down", "*1*1*21##")
    k2 = verdict_diff.RowKey("MH200N", "010108", "seq-test", 2, "down", "*1*1*21##")
    k3 = verdict_diff.RowKey("MH200N", "010108", "seq-test", 0, "down", "*1*0*21##")
    assert parsed.rows[k1] == verdict_diff.RowState("ack", "out", "bus:a8 21")
    assert parsed.rows[k2] == verdict_diff.RowState("nack", "silent", "-")
    assert parsed.rows[k3] == verdict_diff.RowState("nack", "out", "bus:a8 00")


def test_diff_parsed_tsvs_all_categories() -> None:
    rel_mod = "results/MH200N/010108/oracle/full/mod.tsv"
    rel_hdr = "results/MH200N/010108/oracle/full/hdr.tsv"
    rel_same = "results/MH200N/010108/oracle/full/same.tsv"
    rel_add = "results/MH200N/010108/oracle/full/add.tsv"
    rel_rem = "results/MH200N/010108/oracle/full/rem.tsv"

    old_mod = verdict_diff.parse_tsv_text(
        "# product=MH200N version=010108 suite=mod oracle_version=1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n"
        "down\t*1*0*21##\tack\tout\tbus:02\n"
        "down\t*1*3*21##\tack\tout\tbus:05\n"
        "down\t*1*9*21##\tnack\tsilent\t-\n",
        rel_path=rel_mod,
    )
    new_mod = verdict_diff.parse_tsv_text(
        "# product=MH200N version=010108 suite=mod oracle_version=2\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tnack\tsilent\t-\n"
        "down\t*1*0*21##\tack\tout\tbus:03\n"
        "down\t*1*3*21##\tack\tout\tbus:05\n"
        "down\t*1*2*21##\tack\tout\tbus:04\n",
        rel_path=rel_mod,
    )
    old_hdr = verdict_diff.parse_tsv_text(
        "# product=MH200N version=010108 suite=hdr oracle_version=1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n",
        rel_path=rel_hdr,
    )
    new_hdr = verdict_diff.parse_tsv_text(
        "# product=MH200N version=010108 suite=hdr oracle_version=2\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n",
        rel_path=rel_hdr,
    )
    same_tsv = verdict_diff.parse_tsv_text(
        "# product=MH200N version=010108 suite=same\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n",
        rel_path=rel_same,
    )
    added_tsv = verdict_diff.parse_tsv_text(
        "# product=MH200N version=010108 suite=add\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n"
        "down\t*1*0*21##\tnack\tout\tbus:02\n"
        "down\t*1*2*21##\tnack\tsilent\t-\n"
        "up\ta8 21\t-\tout\town:*1*1*21##\n",
        rel_path=rel_add,
    )
    removed_tsv = verdict_diff.parse_tsv_text(
        "# product=MH200N version=010108 suite=rem\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n",
        rel_path=rel_rem,
    )

    report = verdict_diff.diff_parsed_tsvs(
        {
            rel_mod: old_mod,
            rel_hdr: old_hdr,
            rel_same: same_tsv,
            rel_rem: removed_tsv,
        },
        {
            rel_mod: new_mod,
            rel_hdr: new_hdr,
            rel_same: same_tsv,
            rel_add: added_tsv,
        },
    )

    assert len(report.verdict_flips) == 1
    assert len(report.output_deltas) == 1
    assert len(report.added_rows) == 1
    assert len(report.removed_rows) == 1

    md = verdict_diff.format_markdown(report)
    assert "### Verdict / Reply Flips" in md
    assert "### Bus / Emitted Output Deltas (Same Verdict)" in md
    assert "### Step Additions / Removals in Existing Suites" in md
    assert "### New Suite TSVs" in md
    assert "### Removed Suite TSVs" in md
    assert "refreshed header metadata only" in md

    js = json.loads(verdict_diff.format_json(report))
    assert len(js["verdict_flips"]) == 1
    assert len(js["output_deltas"]) == 1


def test_format_markdown_empty_and_crash_warning() -> None:
    empty_report = verdict_diff.VerdictDiffReport([], [], [], [], [])
    md = verdict_diff.format_markdown(empty_report)
    assert "_No oracle TSV changes detected._" in md

    hdr_only_report = verdict_diff.VerdictDiffReport(
        suite_deltas=[
            verdict_diff.SuiteDelta(
                rel_path="results/MH200N/010108/oracle/full/s1.tsv",
                product="MH200N",
                version="010108",
                suite="s1",
                status="header-only",
                header_changes={"oracle_version": ("1", "2")},
                total_rows=1,
                ack_out=1,
                nack_out=0,
                nack_silent=0,
                other_rows=0,
                row_changes_count=0,
            )
        ],
        verdict_flips=[],
        output_deltas=[],
        added_rows=[],
        removed_rows=[],
    )
    md_hdr = verdict_diff.format_markdown(hdr_only_report)
    assert "### Verdict / Reply Flips" not in md_hdr
    assert "metadata only with 0 row changes" in md_hdr
    assert "> [!WARNING]" not in md_hdr

    # Report with transitions into crash, added crashing row, and added crashing suite
    rk1 = verdict_diff.RowKey("MH202", "010024", "lights", 0, "down", "*1*1*31##")
    rk2 = verdict_diff.RowKey("MH202", "010024", "lights", 0, "down", "*1*0*31##")
    crash_report = verdict_diff.VerdictDiffReport(
        suite_deltas=[
            verdict_diff.SuiteDelta(
                rel_path="results/F450/020010/oracle/full/new_s.tsv",
                product="F450",
                version="020010",
                suite="new_s",
                status="added",
                header_changes={},
                total_rows=2,
                ack_out=0,
                nack_out=0,
                nack_silent=0,
                other_rows=2,
                row_changes_count=2,
                crash_rows=2,
            )
        ],
        verdict_flips=[
            verdict_diff.RowTransition(
                rk1,
                verdict_diff.RowState("ack", "out", "bus:01"),
                verdict_diff.RowState("ack", "crash", "bus:01"),
            )
        ],
        output_deltas=[],
        added_rows=[(rk2, verdict_diff.RowState("nack", "crash", "-"))],
        removed_rows=[],
    )
    new_c, targets = verdict_diff.count_new_crashes(crash_report)
    assert new_c == 4
    assert targets == [
        "F450 `020010` / `new_s`",
        "MH202 `010024` / `lights`",
    ]
    md_crash = verdict_diff.format_markdown(crash_report)
    assert "> [!WARNING]" in md_crash
    assert "4 new `crash` verdict(s) introduced" in md_crash


def test_summarize_tsv_pair() -> None:
    old_t = (
        "# product=MH200N version=010108 suite=s1 oracle_version=1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n"
        "down\t*1*0*21##\tack\tout\tbus:02\n"
    )
    new_hdr_only = old_t.replace("oracle_version=1", "oracle_version=2")
    s_hdr = verdict_diff.summarize_tsv_pair(
        old_t, new_hdr_only, "results/MH200N/010108/oracle/full/s1.tsv"
    )
    assert any("header-only refresh" in ln for ln in s_hdr)

    new_mod = (
        "# product=MH200N version=010108 suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tnack\tsilent\t-\n"
        "down\t*1*0*21##\tack\tout\tbus:99\n"
    )
    s_mod = verdict_diff.summarize_tsv_pair(
        old_t, new_mod, "results/MH200N/010108/oracle/full/s1.tsv"
    )
    assert any("1 verdict flip(s), 1 output change(s)" in ln for ln in s_mod)


def test_compare_dirs_and_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"

    # Missing dir handled gracefully
    assert (
        verdict_diff.compare_dirs(
            tmp_path / "missing1", tmp_path / "missing2"
        ).suite_deltas
        == []
    )

    # Direct */*/oracle/full/*.tsv in old_dir, results/*/*/oracle/full/*.tsv in new_dir
    f_old = old_dir / "MH200N" / "010108" / "oracle" / "full" / "s1.tsv"
    f_old.parent.mkdir(parents=True)
    f_old.write_text(
        "# product=MH200N version=010108 suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n",
        encoding="utf-8",
    )

    f_new = new_dir / "results" / "MH200N" / "010108" / "oracle" / "full" / "s1.tsv"
    f_new.parent.mkdir(parents=True)
    f_new.write_text(
        "# product=MH200N version=010108 suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tnack\tsilent\t-\n",
        encoding="utf-8",
    )
    # Also duplicate under non-results prefix to exercise `rel not in out` false branch
    f_new_dup = new_dir / "MH200N" / "010108" / "oracle" / "full" / "s1.tsv"
    f_new_dup.parent.mkdir(parents=True)
    f_new_dup.write_text(f_new.read_text(encoding="utf-8"), encoding="utf-8")

    out_path = tmp_path / "report.json"
    rc = verdict_diff.main(
        [
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--format",
            "json",
            "-o",
            str(out_path),
            "--fail-on-flip",
        ]
    )
    assert rc == 1
    assert out_path.is_file()

    # Without --fail-on-flip and to stdout (with --fail-on-crash when no crashes exist)
    rc2 = verdict_diff.main(
        ["--old-dir", str(old_dir), "--new-dir", str(new_dir), "--fail-on-crash"]
    )
    assert rc2 == 0
    assert "Verdict / Reply Flips" in capsys.readouterr().out

    # With --fail-on-crash when a new crash is introduced
    f_new.write_text(
        "# product=MH200N version=010108 suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tcrash\t-\n",
        encoding="utf-8",
    )
    f_new_dup.write_text(f_new.read_text(encoding="utf-8"), encoding="utf-8")
    rc_crash = verdict_diff.main(
        ["--old-dir", str(old_dir), "--new-dir", str(new_dir), "--fail-on-crash"]
    )
    assert rc_crash == 1
    assert "> [!WARNING]" in capsys.readouterr().out

    # Missing arguments error
    with pytest.raises(SystemExit):
        verdict_diff.main([])


def test_compare_git_revs(tmp_path: Path) -> None:
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    tsv1 = repo / "results" / "MH200N" / "010108" / "oracle" / "full" / "s1.tsv"
    tsv2 = repo / "results" / "MH200N" / "010108" / "oracle" / "full" / "s2.tsv"
    tsv1.parent.mkdir(parents=True)
    tsv1.write_text(
        "# product=MH200N version=010108 suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tack\tout\tbus:01\n",
        encoding="utf-8",
    )
    tsv2.write_text(
        "# product=MH200N version=010108 suite=s2\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*0*21##\tack\tout\tbus:02\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True
    )

    # Modify s1, remove s2, add s3 in a second commit
    tsv1.write_text(
        "# product=MH200N version=010108 suite=s1\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*1*21##\tnack\tsilent\t-\n",
        encoding="utf-8",
    )
    tsv2.unlink()
    tsv3 = repo / "results" / "MH200N" / "010108" / "oracle" / "full" / "s3.tsv"
    tsv3.write_text(
        "# product=MH200N version=010108 suite=s3\n"
        "direction\tinput\treply\tverdict\toutput\n"
        "down\t*1*2*21##\tack\tout\tbus:03\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "head"], cwd=repo, check=True, capture_output=True
    )

    # Compare HEAD~1 to HEAD
    rep_head = verdict_diff.compare_git_revs("HEAD~1", "HEAD", root=repo)
    assert len(rep_head.verdict_flips) == 1
    assert any(d.status == "added" for d in rep_head.suite_deltas)
    assert any(d.status == "removed" for d in rep_head.suite_deltas)

    # Compare HEAD~1 to WORKTREE (with an unstaged deletion of s3)
    tsv3.unlink()
    rep_wt = verdict_diff.compare_git_revs("HEAD~1", "WORKTREE", root=repo)
    assert len(rep_wt.verdict_flips) == 1

    # Also test main(--base) and blank line handling in git diff output
    with (
        patch("verdict_diff.ROOT", repo),
        patch(
            "verdict_diff.subprocess.run",
            wraps=subprocess.run,
        ),
    ):
        assert verdict_diff.main(["--base", "HEAD~1", "--head", "HEAD"]) == 0

    # Exercise blank line in git diff --name-status stdout
    orig_run = subprocess.run

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> object:
        res = orig_run(cmd, *args, **kwargs)  # type: ignore[arg-type]
        if "--name-status" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="\n" + res.stdout + "\n", stderr=""
            )
        return res

    with patch("verdict_diff.subprocess.run", side_effect=fake_run):
        rep_blank = verdict_diff.compare_git_revs("HEAD~1", "HEAD", root=repo)
        assert len(rep_blank.verdict_flips) == 1
