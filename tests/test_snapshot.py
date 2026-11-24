import subprocess


from conftest import git_commit_all
from slopgate.snapshot import changed_py_files, load_snapshot, state_dir, take_snapshot


def test_snapshot_round_trips_file_contents(tmp_repo):
    (tmp_repo / "a.py").write_text("x = 1\n")
    take_snapshot(tmp_repo)
    assert load_snapshot(tmp_repo).files["a.py"] == "x = 1\n"


def test_edits_new_and_deleted_files_are_changed(tmp_repo):
    take_snapshot(tmp_repo)
    (tmp_repo / "a.py").write_text("x = 2\n")
    (tmp_repo / "b.py").unlink()
    (tmp_repo / "c.py").write_text("")
    assert sorted(changed_py_files(tmp_repo, load_snapshot(tmp_repo))) == ["a.py", "b.py", "c.py"]


def test_commits_made_mid_task_still_count_as_changes(tmp_repo):
    take_snapshot(tmp_repo)
    (tmp_repo / "a.py").write_text("x = 2\n")
    git_commit_all(tmp_repo)
    assert changed_py_files(tmp_repo, load_snapshot(tmp_repo)) == ["a.py"]


def test_state_lives_under_git_dir(tmp_repo):
    assert state_dir(tmp_repo) == tmp_repo / ".git" / "slopgate"


def test_load_snapshot_is_none_when_never_taken(tmp_repo):
    assert load_snapshot(tmp_repo) is None


def test_load_snapshot_is_none_when_file_is_corrupt(tmp_repo):
    (state_dir(tmp_repo) / "snapshot.json").write_text('{"task_id": "x", "fi')
    assert load_snapshot(tmp_repo) is None


def add_worktree(repo, path):
    subprocess.run(["git", "worktree", "add", "-b", "wt", str(path)], cwd=repo, check=True, capture_output=True)
    return path


def test_worktree_state_lives_in_its_own_git_dir(tmp_repo, tmp_path):
    wt = add_worktree(tmp_repo, tmp_path / "wt")
    (wt / "a.py").write_text("x = 1\n")
    take_snapshot(wt)
    assert load_snapshot(wt).files["a.py"] == "x = 1\n"
    assert state_dir(wt).resolve().is_relative_to((tmp_repo / ".git" / "worktrees").resolve())
    assert not (wt / ".git").is_dir()
