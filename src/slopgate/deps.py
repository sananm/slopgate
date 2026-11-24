"""Advisory notes for new code that imports a third-party package the repo has never imported before."""
import ast
import sys
from dataclasses import dataclass

from slopgate.wrappers import _parse


@dataclass(frozen=True)
class NewDep:
    path: str
    line: int
    module: str


def _imports(tree: ast.Module) -> dict[str, int]:
    """Top-level module -> line of its first absolute import."""
    first: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            mods = [n.module]
        else:
            continue
        for m in mods:
            m = m.split(".")[0]
            first[m] = min(first.get(m, n.lineno), n.lineno)
    return first


def _first_party(after: dict[str, str]) -> set[str]:
    names = set()
    for p in after:
        parts = p.split("/")
        if parts[0] == "src" and len(parts) > 1:
            parts = parts[1:]
        names.add(parts[0].removesuffix(".py"))
    return names


def find_new_deps(candidate_paths: set[str], after: dict[str, str], before: dict[str, str]) -> list[NewDep]:
    seen = {m for t in _parse(before).values() for m in _imports(t)}
    skip = seen | _first_party(after) | sys.stdlib_module_names
    trees = _parse({p: after[p] for p in sorted(candidate_paths) if p in after})
    return [NewDep(p, line, m) for p, t in trees.items() for m, line in sorted(_imports(t).items(), key=lambda kv: kv[1]) if m not in skip]


def format_new_dep(d: NewDep) -> str:
    return f"note: {d.path}:{d.line} imports a new dependency `{d.module}`\n  that this repo has never used before."
