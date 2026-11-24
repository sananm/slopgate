import dataclasses
import subprocess
import sys

from slopgate import prover
from slopgate.index import index_source
from slopgate.prover import generate_inputs, prove, same_outcome
from test_sandbox import fixture_repo, fn_named  # noqa: F401


def test_identical_behavior_different_code_is_equivalent(fixture_repo):
    v = prove(fixture_repo, fn_named(fixture_repo, "square_loop"), fn_named(fixture_repo, "square_pow"), sys.executable)
    assert v.equivalent is True and v.inputs == 200


def test_one_differing_input_is_not_equivalent(fixture_repo):
    assert prove(fixture_repo, fn_named(fixture_repo, "abs_mine"), fn_named(fixture_repo, "abs_wrong_at_zero"), sys.executable).equivalent is False


def test_float_compare_is_exact_and_nan_equal():
    assert same_outcome(("return", float("nan"), ()), ("return", float("nan"), ()))
    assert not same_outcome(("return", 0.1 + 0.2, ()), ("return", 0.3, ()))


def test_return_type_must_match():
    assert not same_outcome(("return", 1, ()), ("return", 1.0, ()))


def test_nested_values_and_args_compare_strictly():
    assert not same_outcome(("return", [1], ()), ("return", [1.0], ()))
    assert not same_outcome(("return", (True,), ()), ("return", (1,), ()))
    assert not same_outcome(("return", 0.0, ()), ("return", -0.0, ()))
    assert not same_outcome(("return", None, ([1],)), ("return", None, ([1.0],)))
    assert same_outcome(("return", [float("nan")], ()), ("return", [float("nan")], ()))


def test_raise_compares_exception_name():
    assert same_outcome(("raise", "ValueError", (1,)), ("raise", "ValueError", (1,)))
    assert not same_outcome(("raise", "ValueError", (1,)), ("raise", "TypeError", (1,)))


def test_mostly_raising_pair_gets_no_verdict(fixture_repo):
    assert prove(fixture_repo, fn_named(fixture_repo, "raise_unless_zero_a"), fn_named(fixture_repo, "raise_unless_zero_b"), sys.executable).equivalent is None


def test_nondeterministic_function_gets_no_verdict(fixture_repo):
    assert prove(fixture_repo, fn_named(fixture_repo, "hash_mod"), fn_named(fixture_repo, "hash_mod_copy"), sys.executable).equivalent is None


def test_unjudgeable_function_is_never_run(fixture_repo, monkeypatch):
    monkeypatch.setattr(prover, "run_batch", lambda *a: 1 / 0)
    assert prove(fixture_repo, fn_named(fixture_repo, "reads_file"), fn_named(fixture_repo, "reads_file_copy"), sys.executable).equivalent is None


def test_arity_mismatch_gets_no_verdict(fixture_repo, monkeypatch):
    monkeypatch.setattr(prover, "run_batch", lambda *a: 1 / 0)
    two = index_source("m.py", "def two(a: int, b: int) -> int:\n    return a\n")[0]
    assert prove(fixture_repo, fn_named(fixture_repo, "abs_mine"), two, sys.executable).equivalent is None


def test_inputs_are_deterministic(fixture_repo):
    f = fn_named(fixture_repo, "square_loop")
    assert generate_inputs(f) == generate_inputs(f)


def _forged(outcomes_for):
    return lambda repo, fn, inputs, interpreter: outcomes_for(inputs)


def test_forged_or_malformed_batches_get_no_verdict(fixture_repo, monkeypatch):
    f, g = fn_named(fixture_repo, "abs_mine"), fn_named(fixture_repo, "abs_mine")
    nested = []
    for _ in range(100000):
        nested = [nested]
    forgeries = [
        lambda ins: [("return", 0, a) for a in ins][:-1],  # one short
        lambda ins: [("return", 0, a, "extra") for a in ins],
        lambda ins: [["return", 0, a] for a in ins],
        lambda ins: [("teleport", 0, a) for a in ins],
        lambda ins: [("raise", 7, a) for a in ins],
        lambda ins: {"not": "a list"},
        lambda ins: [("return", nested, a) for a in ins],  # RecursionError while comparing
    ]
    for forge in forgeries:
        monkeypatch.setattr(prover, "run_batch", _forged(forge))
        assert prove(fixture_repo, f, g, sys.executable).equivalent is None
    monkeypatch.setattr(prover, "run_batch", _forged(lambda ins: [("return", 0, a) for a in ins]))
    assert prove(fixture_repo, f, g, sys.executable).equivalent is True  # control: well-formed batches still prove


def test_hypothesis_never_writes_into_the_working_directory(fixture_repo, tmp_path):
    # a fresh process: Hypothesis caches its storage dir on first use, so in-process the pytest root wins
    code = ("import sys; from slopgate.index import index_source; from slopgate.prover import generate_inputs; "
            "generate_inputs(next(f for f in index_source('funcs.py', open(sys.argv[1]).read()) if f.qualname == 'abs_mine'))")
    subprocess.run([sys.executable, "-c", code, str(fixture_repo / "funcs.py")], cwd=tmp_path, check=True)
    assert not (tmp_path / ".hypothesis").exists()


def test_returning_the_argument_differs_from_returning_a_copy(fixture_repo):
    xs = [1]
    assert not same_outcome(("return", xs, (xs,)), ("return", [1], ([1],)))
    assert prove(fixture_repo, fn_named(fixture_repo, "ident_list"), fn_named(fixture_repo, "copy_list"), sys.executable).equivalent is not True


def test_prove_never_raises_on_a_broken_function_info(fixture_repo):
    broken = dataclasses.replace(fn_named(fixture_repo, "abs_mine"), source="")
    assert prove(fixture_repo, broken, fn_named(fixture_repo, "abs_mine"), sys.executable).equivalent is None


def test_defaulted_parameter_gets_no_verdict(fixture_repo):
    # callers may pass k / lo / hi; equal behavior at the defaults says nothing about those calls
    for f, g in (("add_k", "ident_int"), ("clamp", "clamp01")):
        assert prove(fixture_repo, fn_named(fixture_repo, f), fn_named(fixture_repo, g), sys.executable).equivalent is not True


def _walk(v):
    yield v
    if isinstance(v, dict):
        v = [*v.keys(), *v.values()]
    if isinstance(v, (list, tuple, set, frozenset)):
        for x in v:
            yield from _walk(x)


def test_generated_inputs_are_bounded():
    fn = index_source("m.py", "def f(a: int, b: str, c: list[bytes], d: dict[str, tuple[int, ...]], e: set[int] | None) -> int:\n    return a\n")[0]
    for args in generate_inputs(fn):
        vals = [v for a in args for v in _walk(a)]
        assert all(len(v) <= 50 for v in vals if isinstance(v, (str, bytes, list, tuple, dict, set, frozenset)))
        assert all(abs(v) <= 10**4 for v in vals if type(v) is int)


def test_repeat_inputs_stay_small():
    rep = index_source("m.py", "def rep(s: str, n: int) -> str:\n    return s * n\n")[0]
    assert max(len(s) * abs(n) for s, n in generate_inputs(rep)) <= 10**6
