"""Clone findings: which new function copies or near-copies which original, and the text the agent sees."""
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from slopgate.structure import Shape, is_test_path

_TRIVIAL = {-1, 0, 1, 2}
_MAX_DIFF = 3


@dataclass(frozen=True)
class Clone:
    kind: Literal["copy", "near"]
    new: Shape
    original: Shape
    diff: tuple[tuple, tuple] | None
    base: str | None
    blocking: bool


def _id(s: Shape) -> tuple[str, str]:
    return (s.path, s.qualname)


def _diff(a: Shape, b: Shape) -> tuple[tuple, tuple] | None:
    """Positionally differing constants (a's, b's) when the pair is a near clone, else None."""
    pairs = [(x, y) for x, y in zip(a.consts, b.consts) if x[0] != y[0]]
    if not pairs or len(pairs) > _MAX_DIFF or any(x[1] or y[1] for x, y in pairs):
        return None
    if all(x[0] in _TRIVIAL and y[0] in _TRIVIAL for x, y in pairs):
        return None
    return tuple(x[0] for x, _ in pairs), tuple(y[0] for _, y in pairs)


def _by(shapes, attr) -> dict[str, list[Shape]]:
    out = defaultdict(list)
    for s in shapes:
        if s.comparable:
            out[getattr(s, attr)].append(s)
    return out


def _base(new: Shape, orig: Shape) -> str | None:
    if new.cls is None or orig.cls is None or new.qualname.split(".")[-1] != orig.qualname.split(".")[-1]:
        return None
    return next((b for b in new.bases if b in orig.bases), None)


def find_clones(candidates: list[Shape], pool: list[Shape], before: list[Shape]) -> list[Clone]:
    old = {_id(s): s for s in before}
    exact, nears = _by(pool, "exact_key"), _by(pool, "near_key")
    out = []
    for c in candidates:
        if not c.comparable:
            continue
        kind, attr = "copy", "exact_key"
        others = [o for o in exact.get(c.exact_key, ()) if _id(o) != _id(c)]
        diffs = {}
        if not others:
            kind, attr = "near", "near_key"
            for o in nears.get(c.near_key, ()):
                if _id(o) != _id(c) and o.exact_key != c.exact_key and (d := _diff(c, o)):
                    diffs[_id(o)] = d
            others = [o for o in nears.get(c.near_key, ()) if _id(o) in diffs]
        if not others:
            continue
        seen = [o for o in others if _id(o) in old]
        orig = min(seen or others, key=_id)
        if not seen and _id(c) < _id(orig):
            continue  # c is itself the first of its group, so the others point at it
        prev = old.get(_id(c))
        if prev and _id(orig) in old and getattr(prev, attr) == getattr(old[_id(orig)], attr):
            continue  # relation already existed
        out.append(Clone(kind, c, orig, diffs.get(_id(orig)), _base(c, orig),
                         not (is_test_path(c.path) or is_test_path(orig.path))))
    return out


def clone_groups(shapes: list[Shape]) -> list[list[Shape]]:
    groups = [sorted(g, key=_id) for g in _by(shapes, "exact_key").values() if len(g) > 1]
    for g in _by(shapes, "near_key").values():
        g = sorted(g, key=_id)
        first = g[0]
        grp = [first] + [o for o in g[1:] if o.exact_key != first.exact_key and _diff(first, o)]
        if len(grp) > 1:
            groups.append(grp)
    return groups


def format_clone(c: Clone) -> str:
    n, o = c.new, c.original
    if c.kind == "near":
        a, b = (", ".join(map(repr, v)) for v in c.diff)
        return (f"{n.path}:{n.line} {n.qualname} is {o.path}:{o.line}\n"
                f"  {o.qualname} with different constants ({a} vs {b}). Add a parameter to\n"
                f"  {o.qualname} instead of copying it.")
    if c.base:
        tail = f"Move it into the shared base class {c.base}."
    elif o.cls:
        tail = "Move it to a shared helper and call that from both."
    else:
        tail = "Delete it and import the original."
    return f"{n.path}:{n.line} {n.qualname} is a copy of\n  {o.path}:{o.line} {o.qualname}.\n  {tail}"


def clone_key(c: Clone) -> str:
    return "\t".join((c.kind, c.new.path, c.new.qualname, c.original.path, c.original.qualname))
