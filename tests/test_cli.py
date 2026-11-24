import io
import json
import shutil
import subprocess
import sys

import pytest

from conftest import (WEIGHTED, add_forwarding_wrapper, add_new_import, git_commit_all, paste_copy, pasted_name,
                      record_new_names, rename_local_in_one_copy)
from slopgate.cli import main
from test_reinvention import fake_embed
from test_snapshot import add_worktree
from test_sandbox import fixture_repo  # noqa: F401


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.setattr("slopgate.cli.default_embed", lambda: fake_embed)


@pytest.fixture
def fixture_git_repo(fixture_repo, tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(fixture_repo, dst)
    (dst / "a.py").write_text(WEIGHTED)
    (dst / "b.py").write_text("B = 1\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=dst, check=True, capture_output=True)
    git_commit_all(dst)
    return dst


def run_hook(argv, cwd, stdin=None):
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"cwd": str(cwd)}) if stdin is None else stdin)
    try:
        return main(argv)
    finally:
        sys.stdin = old


def add_reinvention(repo):
    (repo / "newmod.py").write_text("def squares(xs: list[int]) -> list[int]:\n    return [x ** 2 for x in xs]\n")


def boom(*a, **k):
    raise RuntimeError("boom")


def last_stats(repo):
    return json.loads((repo / ".git/slopgate/stats.jsonl").read_text().splitlines()[-1])


def write_settings(repo, data):
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text(json.dumps(data))


def all_hook_commands(repo):
    data = json.loads((repo / ".claude/settings.json").read_text())
    return [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]


def test_start_then_check_blocks_on_reinvention(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    add_reinvention(fixture_git_repo)
    assert run_hook(["check"], fixture_git_repo) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "reimplements" in out["reason"]
    assert last_stats(fixture_git_repo)["findings"] == 1


def test_fixed_reinvention_counts_as_resolved(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    add_reinvention(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    (fixture_git_repo / "newmod.py").unlink()
    run_hook(["check"], fixture_git_repo)
    outs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "systemMessage" in outs[-1]
    assert (last_stats(fixture_git_repo)["round"], last_stats(fixture_git_repo)["resolved"]) == (2, 1)


def test_blocks_inside_a_git_worktree(fixture_git_repo, tmp_path, capsys):
    wt = add_worktree(fixture_git_repo, tmp_path / "wt")
    run_hook(["start"], wt)
    add_reinvention(wt)
    assert run_hook(["check"], wt) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_finding_shifted_by_edits_above_is_not_progress(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    add_reinvention(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    p = fixture_git_repo / "newmod.py"
    p.write_text("X = 1\nY = 2\n\n\n" + p.read_text())
    run_hook(["check"], fixture_git_repo)
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert "decision" not in last
    assert (last_stats(fixture_git_repo)["findings"], last_stats(fixture_git_repo)["resolved"]) == (1, 0)


def test_clean_task_allows_with_summary(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    assert json.loads(capsys.readouterr().out)["systemMessage"].startswith("slopgate:")


def test_start_prints_nothing(fixture_git_repo, capsys):
    assert run_hook(["start"], fixture_git_repo) == 0
    assert capsys.readouterr().out == ""


def test_check_from_subdirectory_uses_repo_root(fixture_git_repo, capsys):
    (fixture_git_repo / "sub").mkdir()
    run_hook(["start"], fixture_git_repo / "sub")
    add_reinvention(fixture_git_repo)
    run_hook(["check"], fixture_git_repo / "sub")
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_check_without_snapshot_allows_and_says_why(fixture_git_repo, capsys):
    assert run_hook(["check"], fixture_git_repo) == 0
    assert "no snapshot" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_check_outside_git_repo_allows(tmp_path, capsys):
    assert run_hook(["start"], tmp_path) == 0
    assert run_hook(["check"], tmp_path) == 0
    assert "not a git repo" in json.loads(capsys.readouterr().out)["systemMessage"]
    assert not (tmp_path / ".git").exists()


@pytest.mark.parametrize("stdin", ["", "not json", "[1]", "{}", '{"cwd": 5}'])
def test_bad_hook_input_allows(stdin, capsys):
    assert run_hook(["start"], None, stdin=stdin) == 0
    assert run_hook(["check"], None, stdin=stdin) == 0
    assert "systemMessage" in json.loads(capsys.readouterr().out)


def test_internal_error_never_blocks(fixture_git_repo, capsys, monkeypatch):
    monkeypatch.setattr("slopgate.cli.find_reinventions", boom)
    run_hook(["start"], fixture_git_repo)
    add_reinvention(fixture_git_repo)
    assert run_hook(["check"], fixture_git_repo) == 0
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "boom" in out["systemMessage"]


def test_unsandboxed_is_named_in_summary(fixture_git_repo, capsys, monkeypatch):
    monkeypatch.setattr("slopgate.cli.sandbox_kind", lambda: "none")
    run_hook(["start"], fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    assert json.loads(capsys.readouterr().out)["systemMessage"].endswith("(running unsandboxed)")


def test_stats_line_written_per_check(fixture_git_repo):
    run_hook(["start"], fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    assert set(last_stats(fixture_git_repo)) == {"task_id", "round", "new", "judgeable", "findings", "resolved", "skipped", "seconds",
                                                 "copies", "near", "wrapper_notes", "new_deps"}


def test_install_merges_without_duplicates(tmp_path):
    write_settings(tmp_path, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}})
    main(["install", str(tmp_path)])
    main(["install", str(tmp_path)])
    cmds = all_hook_commands(tmp_path)
    assert cmds.count("slopgate check") == 1 and cmds.count("slopgate start") == 1 and "other" in cmds


def test_install_creates_settings(tmp_path):
    assert main(["install", str(tmp_path)]) == 0
    assert sorted(all_hook_commands(tmp_path)) == ["slopgate check", "slopgate start"]


def test_install_warms_the_model_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("slopgate.cli.default_embed", lambda: calls.append(1))
    assert main(["install", str(tmp_path)]) == 0
    assert calls == [1]


def test_install_survives_warmup_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("slopgate.cli.default_embed", boom)
    assert main(["install", str(tmp_path)]) == 0
    assert "slopgate check" in all_hook_commands(tmp_path) and "boom" in capsys.readouterr().err


def test_block_carries_the_summary(fixture_git_repo, capsys, monkeypatch):
    monkeypatch.setattr("slopgate.cli.sandbox_kind", lambda: "none")
    run_hook(["start"], fixture_git_repo)
    add_reinvention(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and out["systemMessage"].endswith("1 findings, 0 skipped (running unsandboxed)")


def test_model_is_not_loaded_when_nothing_new_is_judgeable(fixture_git_repo, capsys, monkeypatch):
    monkeypatch.setattr("slopgate.cli.default_embed", boom)
    run_hook(["start"], fixture_git_repo)
    (fixture_git_repo / "newmod.py").write_text("def load(p: str) -> str:\n    return open(p).read()\n")
    run_hook(["check"], fixture_git_repo)
    msg = json.loads(capsys.readouterr().out)["systemMessage"]
    assert msg.startswith("slopgate: 0 copies, 0 near-copies, 0 wrapper notes, 0 new dependencies, 1 new functions, 0 judgeable") and "boom" not in msg


def test_pasted_copy_blocks_with_copy_finding(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    paste_copy(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "is a copy of" in out["reason"] and "1 copies" in out["systemMessage"]
    assert out["systemMessage"].startswith("slopgate: 1 copies, 0 near-copies, 0 wrapper notes, 0 new dependencies, 1 new functions")


def test_rename_local_in_one_of_existing_copies_does_not_block(fixture_git_repo_with_two_copies, capsys):
    run_hook(["start"], fixture_git_repo_with_two_copies)
    rename_local_in_one_copy(fixture_git_repo_with_two_copies)
    run_hook(["check"], fixture_git_repo_with_two_copies)
    assert "decision" not in json.loads(capsys.readouterr().out)
    assert last_stats(fixture_git_repo_with_two_copies)["copies"] == 0


def test_copy_skips_the_prover(fixture_git_repo, capsys, monkeypatch):
    seen = []
    monkeypatch.setattr("slopgate.cli.find_reinventions", record_new_names(seen))
    run_hook(["start"], fixture_git_repo)
    paste_copy(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert pasted_name() not in seen


def test_wrapper_note_never_blocks_alone(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    add_forwarding_wrapper(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "1 wrapper notes" in out["systemMessage"]


def test_notes_follow_blocking_findings(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    add_forwarding_wrapper(fixture_git_repo)
    add_reinvention(fixture_git_repo)
    paste_copy(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    reason = json.loads(capsys.readouterr().out)["reason"].split("\n\n")
    assert ["is a copy of" in reason[0], "reimplements" in reason[1], reason[2].startswith("note:")] == [True] * 3


def test_new_dependency_is_a_note_not_a_block(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    add_new_import(fixture_git_repo, "polars")
    run_hook(["check"], fixture_git_repo)
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "1 new dependencies" in out["systemMessage"]


def test_syntax_error_file_does_not_break_check(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    (fixture_git_repo / "broken.py").write_text("def f(:\n")
    run_hook(["check"], fixture_git_repo)
    assert "internal error" not in json.loads(capsys.readouterr().out)["systemMessage"]


def test_second_check_recomputes_no_shapes(fixture_git_repo, capsys, monkeypatch):
    import slopgate.cli
    calls = []
    real = slopgate.cli.shapes_of
    monkeypatch.setattr("slopgate.cli.shapes_of", lambda p, t: calls.append(p) or real(p, t))
    run_hook(["start"], fixture_git_repo)
    paste_copy(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    assert len(calls) == len(set(calls)) + 1  # only b.py differs between before and after
    calls.clear()
    run_hook(["check"], fixture_git_repo)
    assert calls == []


def test_deeply_nested_shape_cache_is_ignored_and_replaced(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    paste_copy(fixture_git_repo)
    (fixture_git_repo / ".git/slopgate/shapes.json").write_text("[" * 1_000_000 + "]" * 1_000_000)  # json.loads raises RecursionError
    run_hook(["check"], fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    first, second = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert first["decision"] == "block" and "internal error" not in second["systemMessage"]


@pytest.mark.parametrize("forge", [{"line": "1"}, {"bases": "XYZ"}])
def test_forged_cache_entry_under_real_key_is_recomputed(fixture_git_repo, capsys, forge):
    import hashlib
    run_hook(["start"], fixture_git_repo)
    paste_copy(fixture_git_repo)
    text = (fixture_git_repo / "b.py").read_text()
    bad = {"path": "b.py", "qualname": pasted_name(), "module": "b", "line": 3, "end_line": 10, "cls": None,
           "bases": [], "exact_key": "x", "near_key": "y", "consts": [], "comparable": False, **forge}
    key = hashlib.sha256(f"b.py\0{text}".encode()).hexdigest()
    (fixture_git_repo / ".git/slopgate/shapes.json").write_text(json.dumps({key: [bad]}))
    run_hook(["check"], fixture_git_repo)
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_notes_reach_the_summary_when_not_blocking(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    add_forwarding_wrapper(fixture_git_repo)
    run_hook(["check"], fixture_git_repo)
    msg = json.loads(capsys.readouterr().out)["systemMessage"]
    assert msg.endswith("\n\nnote: wrap.py:4 join_paths only forwards to os.path.join and has at most one call site found by name.")


def test_scan_reports_existing_copies(fixture_git_repo_with_two_copies, capsys):
    assert main(["scan", str(fixture_git_repo_with_two_copies)]) == 0
    assert capsys.readouterr().out == "copy:\n  a.py:1 weighted_total\n  b.py:1 weighted_total_b\n"
    assert not (fixture_git_repo_with_two_copies / ".git/slopgate").exists()


def test_scan_prints_src_groups_before_test_groups_then_wrappers(tmp_path, capsys):
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "tests").mkdir()
    other = WEIGHTED.replace("total -= price", "total += price")  # a different body, so a separate group
    (tmp_path / "tests/test_a.py").write_text(other + "\n\n" + other.replace("weighted_total", "t2"))
    (tmp_path / "z.py").write_text(WEIGHTED + "\n\n" + WEIGHTED.replace("weighted_total", "z2")
                                   + "\n\ndef fwd(a):\n    return len(a)\n")
    assert main(["scan", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.index("z.py:1") < out.index("tests/test_a.py") < out.index("note: z.py")


def test_scan_outside_git_repo(tmp_path, capsys):
    assert main(["scan", str(tmp_path)]) == 0
    assert "not a git repo" in capsys.readouterr().out
