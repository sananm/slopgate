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


def _defs(body, prefix="", sub=False):
    for n in body:
        if isinstance(n, _FUNCS):
            yield prefix + n.name, (n, sub)
        elif isinstance(n, ast.ClassDef):
            based = any(not (isinstance(b, ast.Name) and b.id == "object") for b in n.bases)
            yield from _defs(n.body, f"{prefix}{n.name}.", based)


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
    pos = [p.arg for p in (*a.posonlyargs, *a.args) if p.arg not in ("self", "cls")]
    allp = set(pos) | {p.arg for p in a.kwonlyargs} | {p.arg for p in (a.vararg, a.kwarg) if p}
    covered, i = set(), 0
    for x in v.args:  # positional: same order as the parameters
        if isinstance(x, ast.Starred) and isinstance(x.value, ast.Name) and a.vararg and x.value.id == a.vararg.arg:
            covered.add(x.value.id)
        elif isinstance(x, ast.Name) and i < len(pos) and x.id == pos[i]:
            covered.add(x.id)
            i += 1
        else:
            return None
    for k in v.keywords:  # keyword: matched by name
        name = k.arg or (a.kwarg.arg if a.kwarg else None)
        if not isinstance(k.value, ast.Name) or k.value.id != name or name in covered:
            return None
        covered.add(name)
    if covered != allp:
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
    # forwarders first from the candidates' own files; the whole repo is parsed only to count their call sites
    trees = _parse({p: texts[p] for p, _ in candidate_paths_qualnames if p in texts and not is_test_path(p)})
    hits = []
    for path, qual in sorted(candidate_paths_qualnames):
        if path not in trees:
            continue
        fn, sub = dict(_defs(trees[path].body)).get(qual, (None, False))
        name = qual.rsplit(".", 1)[-1]
        # subclass methods may be called by a framework (nn.Module.forward), which a name search cannot see
        if fn is None or sub or fn.decorator_list or (name.startswith("__") and name.endswith("__")):
            continue
        if call := _forwarded_call(fn):
            hits.append((path, fn, name, call))
    if not hits:
        return []
    trees.update(_parse({p: t for p, t in texts.items() if p not in trees}))
    exported = _all_names(trees)
    # the def itself is not an ast.Name/Attribute/Constant, so it is never counted
    return [Wrapper(path, fn.lineno, name, ast.unparse(call.func)) for path, fn, name, call in hits
            if name not in exported and _mentions(trees, name) <= 1]


def format_wrapper(w: Wrapper) -> str:
    return f"note: {w.path}:{w.line} {w.name} only forwards to {w.target} and has at most one call site found by name."
