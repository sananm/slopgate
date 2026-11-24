"""Advisory notes for new functions that only forward their arguments and have no callers found by name."""
import ast
from dataclasses import dataclass

from slopgate.index import _FUNCS
from slopgate.structure import is_test_path


@dataclass(frozen=True)
class Wrapper:
    path: str
    line: int
    name: str
    target: str


def _parse(texts: dict[str, str]) -> dict[str, ast.Module]:
    trees = {}
    for p, t in texts.items():
        try:
            trees[p] = ast.parse(t)
        except (SyntaxError, ValueError):
            pass
    return trees


def _defs(body, prefix=""):
    for n in body:
        if isinstance(n, _FUNCS):
            yield prefix + n.name, n
        elif isinstance(n, ast.ClassDef):
            yield from _defs(n.body, f"{prefix}{n.name}.")


def _forwarded_call(fn) -> ast.Call | None:
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    v = body[0].value
    if isinstance(v, ast.Await):
        v = v.value
    if not isinstance(v, ast.Call):
        return None
    a = fn.args
    params = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if p]
    params = sorted(p for p in params if p not in ("self", "cls"))
    passed = [x.value if isinstance(x, ast.Starred) else x for x in v.args] + [k.value for k in v.keywords]
    if not all(isinstance(x, ast.Name) for x in passed) or sorted(x.id for x in passed) != params:
        return None
    return v


def _all_names(trees) -> set[str]:
    out = set()
    for t in trees.values():
        for n in ast.walk(t):
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                if any(isinstance(x, ast.Name) and x.id == "__all__" for x in targets) and n.value is not None:
                    out |= {c.value for c in ast.walk(n.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    return out


def _mentions(trees, name: str) -> int:
    return sum(
        (isinstance(n, ast.Name) and n.id == name) or (isinstance(n, ast.Attribute) and n.attr == name)
        or (isinstance(n, ast.Constant) and n.value == name)
        for t in trees.values() for n in ast.walk(t)
    )


def find_wrappers(candidate_paths_qualnames: set[tuple[str, str]], texts: dict[str, str]) -> list[Wrapper]:
    trees = _parse(texts)
    exported = _all_names(trees)
    found = []
    for path, qual in sorted(candidate_paths_qualnames):
        if path not in trees or is_test_path(path):
            continue
        fn = dict(_defs(trees[path].body)).get(qual)
        name = qual.rsplit(".", 1)[-1]
        if fn is None or fn.decorator_list or name in exported or (name.startswith("__") and name.endswith("__")):
            continue
        call = _forwarded_call(fn)
        # the def itself is not an ast.Name/Attribute/Constant, so it is never counted
        if call and _mentions(trees, name) <= 1:
            found.append(Wrapper(path, fn.lineno, name, ast.unparse(call.func)))
    return found


def format_wrapper(w: Wrapper) -> str:
    return f"note: {w.path}:{w.line} {w.name} only forwards to {w.target} and has no call sites found by name."
