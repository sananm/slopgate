import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def git_commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c")


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 1\n")
    git_commit_all(tmp_path)
    return tmp_path
