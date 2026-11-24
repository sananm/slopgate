import builtins
import typing
from dataclasses import dataclass
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from slopgate.index import FunctionInfo, required_arity
from slopgate.judge import _CONTAINERS, _SCALARS, why_not_judgeable
from slopgate.sandbox import Outcome, run_batch

_N = 200
_MIN_RETURNS = 50
_RERUN = 20
# Only the names judge's annotation allowlist accepts; never repo code.
_NAMESPACE = {"__builtins__": {}} | {n: getattr(builtins, n, None) or getattr(typing, n) for n in _SCALARS | _CONTAINERS}
_ARITY = {"return": 3, "raise": 3, "timeout": 1, "error": 2}


@dataclass(frozen=True)
class Verdict:
    equivalent: bool | None  # None = no verdict
    inputs: int
    reason: str


def generate_inputs(fn: FunctionInfo, n: int = _N) -> list[tuple]:
    if why := why_not_judgeable(fn):
        raise ValueError(why)
    types = [eval(p.annotation, dict(_NAMESPACE)) for p in fn.params if not p.has_default]
    out: list[tuple] = []

    @settings(max_examples=n, database=None, derandomize=True, deadline=None, suppress_health_check=list(HealthCheck))
    @given(st.tuples(*map(st.from_type, types)))
    def collect(args: tuple) -> None:
        out.append(args)

    collect()
    return out


def _same(a: object, b: object) -> bool:
    # repr, not ==: == equates 1, 1.0 and True, and 0.0 with -0.0, and NaN != NaN, at any depth
    return type(a) is type(b) and repr(a) == repr(b)


def same_outcome(a: Outcome, b: Outcome) -> bool:
    if a[0] != b[0] or len(a) != len(b):
        return False
    if a[0] == "raise":
        return a[1] == b[1] and _same(a[2], b[2])
    if a[0] == "return":
        return _same(a[1], b[1]) and _same(a[2], b[2])
    return a == b


def _well_formed(batch: object, n: int) -> bool:
    # run_batch output is untrusted: repo code can forge it via the result fd
    return type(batch) is list and len(batch) == n and all(
        type(o) is tuple and o and o[0] in _ARITY and len(o) == _ARITY[o[0]]
        and (o[0] != "raise" or type(o[1]) is str) and (o[0] not in ("return", "raise") or type(o[2]) is tuple)
        for o in batch
    )


def prove(repo: Path, f: FunctionInfo, g: FunctionInfo, interpreter: str) -> Verdict:
    for fn in (f, g):
        if why := why_not_judgeable(fn):
            return Verdict(None, 0, f"{fn.qualname}: {why}")
    if required_arity(f) != required_arity(g):
        return Verdict(None, 0, "arity differs")
    try:
        return _prove(repo, f, g, interpreter)
    except Exception as e:  # includes RecursionError from deeply nested values
        return Verdict(None, 0, f"error: {type(e).__name__}")


def _prove(repo: Path, f: FunctionInfo, g: FunctionInfo, interpreter: str) -> Verdict:
    inputs = generate_inputs(f)
    if len(inputs) != _N:
        return Verdict(None, len(inputs), "too few inputs")
    runs = []
    for fn in (f, g):
        full = run_batch(repo, fn, inputs, interpreter)
        rerun = run_batch(repo, fn, inputs[:_RERUN], interpreter)
        if not (_well_formed(full, _N) and _well_formed(rerun, _RERUN)):
            return Verdict(None, _N, f"{fn.qualname}: no outcomes")
        if any(o[0] in ("timeout", "error") for o in full + rerun):
            return Verdict(None, _N, f"{fn.qualname}: timeout or error")
        if not all(same_outcome(a, b) for a, b in zip(full, rerun)):
            return Verdict(None, _N, f"{fn.qualname}: nondeterministic")
        runs.append(full)
    if sum(a[0] == b[0] == "return" for a, b in zip(*runs)) < _MIN_RETURNS:
        return Verdict(None, _N, "too few normal returns")
    for i, (a, b) in enumerate(zip(*runs)):
        if not same_outcome(a, b):
            return Verdict(False, _N, f"differs on input {i}: {inputs[i]!r}")
    return Verdict(True, _N, "all inputs agree")
