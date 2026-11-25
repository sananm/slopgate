"""M3: ask when a new function strongly resembles existing repo logic."""
from dataclasses import dataclass

from slopgate.index import FunctionInfo
from slopgate.reinvention import _cosine
from slopgate.structure import Shape, is_test_path

SEMANTIC_CUTOFF = 0.84  # provisional until the replay is reviewed


@dataclass(frozen=True)
class Question:
    new: FunctionInfo
    existing: FunctionInfo
    score: float
    copies: int


def _dunder(f: FunctionInfo) -> bool:
    name = f.qualname.rsplit(".", 1)[-1]
    return name.startswith("__") and name.endswith("__")


def semantic_candidates(new: list[FunctionInfo], shapes: list[Shape], skip: set[tuple[str, str]]) -> list[FunctionInfo]:
    by_key = {(s.path, s.qualname): s for s in shapes}
    methods = {(s.cls, s.qualname.rsplit(".", 1)[-1]) for s in shapes if s.cls}
    out = []
    for f in new:
        s = by_key.get((f.path, f.qualname))
        if s is None or not s.comparable or is_test_path(f.path) or _dunder(f) or (f.path, f.qualname) in skip:
            continue
        name = f.qualname.rsplit(".", 1)[-1]
        if s.cls is not None and any((b, name) in methods for b in s.bases):
            continue  # overrides a first-party base method
        out.append(f)
    return out


def semantic_pool(functions: list[FunctionInfo]) -> list[FunctionInfo]:
    return [f for f in functions if not is_test_path(f.path) and not _dunder(f)]


def find_questions(candidates: list[FunctionInfo], pool: list[FunctionInfo], vecs: dict[str, list[float]],
                   exact_keys: dict[tuple[str, str], str]) -> list[Question]:
    pool = [g for g in pool if g.source in vecs]
    groups: dict[str, list[FunctionInfo]] = {}
    for g in pool:
        k = exact_keys.get((g.path, g.qualname))
        if k is not None:
            groups.setdefault(k, []).append(g)
    out = []
    for f in candidates:
        if f.source not in vecs or not pool:
            continue
        best = max(pool, key=lambda g: _cosine(vecs[f.source], vecs[g.source]))
        score = _cosine(vecs[f.source], vecs[best.source])
        if score < SEMANTIC_CUTOFF:
            continue
        group = groups.get(exact_keys.get((best.path, best.qualname)), [best])
        existing = min(group, key=lambda g: (g.path, g.qualname))
        out.append(Question(f, existing, score, len(group) - 1))
    return out


def question_key(q: Question) -> str:
    return f"{q.existing.path}\t{q.existing.qualname}"


def format_question(q: Question) -> str:
    more = f" (and {q.copies} copies of it)" if q.copies else ""
    return (f"question: {q.new.path}:{q.new.line} {q.new.qualname}\n"
            f"  looks like {q.existing.path}:{q.existing.line} {q.existing.qualname}{more}.\n"
            "  Reuse it, or reply with why the new one is different.")
