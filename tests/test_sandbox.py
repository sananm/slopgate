import pickle
import sys
import tempfile
import time
from pathlib import Path

import pytest

from slopgate.child import safe_loads
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


def test_setsid_grandchild_cannot_crash_the_caller(fixture_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # the escaped grandchild may leak the workdir here
    run_batch(fixture_repo, fn_named(fixture_repo, "setsid_churn"), [(1,)], sys.executable)


def test_unpickler_refuses_bytearray_global():
    with pytest.raises(pickle.UnpicklingError):
        safe_loads(b"cbuiltins\nbytearray\n(I5\ntR.")  # bytearray(5); any size would do


def test_oversized_child_output_returns_none(fixture_repo):
    assert run_batch(fixture_repo, fn_named(fixture_repo, "flood_result"), [(1,)], sys.executable) is None


def test_interpreter_falls_back_to_virtual_env(tmp_path, monkeypatch):
    py = tmp_path / "ve/bin/python"
    py.parent.mkdir(parents=True)
    py.touch(mode=0o755)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "ve"))
    assert pick_interpreter(tmp_path / "repo") == str(py)


def test_repo_module_shadowed_by_stdlib_returns_none(tmp_path):
    # the child has already imported stdlib copy, so "copy.copy" would resolve to the stdlib function
    src = "def copy(x: list[int]) -> list[int]:\n    return [1]\n"
    (tmp_path / "copy.py").write_text(src)
    assert run_batch(tmp_path, index_source("copy.py", src)[0], [([5],)], sys.executable) is None
