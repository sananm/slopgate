import sys
import time
from pathlib import Path

import pytest

from slopgate.index import index_source
from slopgate.sandbox import pick_interpreter, run_batch, sandbox_kind


@pytest.fixture
def fixture_repo() -> Path:
    return Path(__file__).parent / "fixtures" / "repo"


def fn_named(repo: Path, name: str):
    for p in repo.glob("*.py"):
        for fn in index_source(p.name, p.read_text()):
            if fn.qualname == name:
                return fn
    raise LookupError(name)


def venv_python(root: Path) -> None:
    py = root / ".venv/bin/python"
    py.parent.mkdir(parents=True)
    py.touch(mode=0o755)


def test_runs_pure_function_and_returns_outcomes(fixture_repo):
    out = run_batch(fixture_repo, fn_named(fixture_repo, "add_one"), [(1,), (2,)], sys.executable)
    assert out == [("return", 2, (1,)), ("return", 3, (2,))]


def test_exception_type_is_recorded(fixture_repo):
    assert run_batch(fixture_repo, fn_named(fixture_repo, "div"), [(1, 0)], sys.executable)[0][:2] == ("raise", "ZeroDivisionError")


def test_argument_mutation_is_visible(fixture_repo):
    assert run_batch(fixture_repo, fn_named(fixture_repo, "append_x"), [([1],)], sys.executable)[0][2] == ([1, "x"],)


@pytest.mark.skipif(sandbox_kind() == "none", reason="needs an OS sandbox")
def test_hidden_file_write_is_blocked(fixture_repo, tmp_path):  # Review Focus
    target = tmp_path / "escaped.txt"
    run_batch(fixture_repo, fn_named(fixture_repo, "sneaky_write"), [(str(target),)], sys.executable)
    assert not target.exists()


def test_hanging_function_times_out_without_hanging_the_caller(fixture_repo):  # Review Focus
    start = time.monotonic()
    out = run_batch(fixture_repo, fn_named(fixture_repo, "spin"), [(1,)], sys.executable)
    assert out == [("timeout",)] and time.monotonic() - start < 10


def test_import_failure_returns_none(fixture_repo):
    assert run_batch(fixture_repo, fn_named(fixture_repo, "in_broken_module"), [(1,)], sys.executable) is None


def test_interpreter_prefers_repo_venv(tmp_path):
    venv_python(tmp_path)  # creates tmp_path/.venv/bin/python
    assert pick_interpreter(tmp_path) == str(tmp_path / ".venv/bin/python")


def test_child_never_inherits_pythonhashseed(fixture_repo, monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    fn = fn_named(fixture_repo, "hash_mod")
    assert run_batch(fixture_repo, fn, [("slopgate",)], sys.executable) != run_batch(fixture_repo, fn, [("slopgate",)], sys.executable)
