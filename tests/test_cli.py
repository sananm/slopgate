import io
import json
import shutil
import subprocess
import sys

import pytest

from conftest import git_commit_all
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
    assert set(last_stats(fixture_git_repo)) == {"task_id", "round", "new", "judgeable", "findings", "resolved", "skipped", "seconds"}


def test_install_merges_without_duplicates(tmp_path):
    write_settings(tmp_path, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}})
    main(["install", str(tmp_path)])
    main(["install", str(tmp_path)])
    cmds = all_hook_commands(tmp_path)
    assert cmds.count("slopgate check") == 1 and cmds.count("slopgate start") == 1 and "other" in cmds


def test_install_creates_settings(tmp_path):
    assert main(["install", str(tmp_path)]) == 0
    assert sorted(all_hook_commands(tmp_path)) == ["slopgate check", "slopgate start"]
