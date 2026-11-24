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


WEIGHTED = """def weighted_total(prices, weights):
    total = 0
    for price, weight in zip(prices, weights):
        if weight > 0:
            total += price * weight
        else:
            total -= price
    return round(total, 2)
"""


def paste_copy(repo: Path) -> None:
    """Copy a.py's weighted_total (51 nodes) into b.py under a new name, with renamed locals."""
    src = WEIGHTED.replace("weighted_total", pasted_name()).replace("total", "acc").replace("price", "p")
    (repo / "b.py").write_text((repo / "b.py").read_text() + "\n\n" + src)


def pasted_name() -> str:
    return "sum_weighted"


@pytest.fixture
def fixture_git_repo_with_two_copies(tmp_path: Path) -> Path:
    repo = tmp_path / "copies"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "a.py").write_text(WEIGHTED)
    (repo / "b.py").write_text(WEIGHTED.replace("weighted_total", "weighted_total_b"))
    git_commit_all(repo)
    return repo


def rename_local_in_one_copy(repo: Path) -> None:
    p = repo / "b.py"
    p.write_text(p.read_text().replace("total ", "acc ").replace("(total", "(acc"))


def add_forwarding_wrapper(repo: Path) -> None:
    (repo / "wrap.py").write_text("import os\n\n\ndef join_paths(a, b):\n    return os.path.join(a, b)\n")


def add_new_import(repo: Path, module: str) -> None:
    (repo / "newdep.py").write_text(f"import {module}\n")


def record_new_names(seen: list):
    from slopgate.reinvention import CheckResult

    def fake(repo, changes, *a, **k):
        seen.extend(f.qualname for f in changes.new)
        return CheckResult(new_count=len(changes.new))
    return fake
