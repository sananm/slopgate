"""Structural normalization: one canonical key per function, equal only up to renaming of locals."""
import ast
import builtins
import copy
import hashlib
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import PurePosixPath

from slopgate.index import _FUNCS, _module_name

_FLOOR = 40
_BUILTINS = frozenset(dir(builtins))
_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPES = (*_FUNCS, ast.Lambda, ast.ClassDef)


@dataclass(frozen=True)
class Shape:
    path: str
    qualname: str
    module: str
    line: int
    end_line: int
    cls: str | None
    bases: tuple[str, ...]
    exact_key: str
    near_key: str
    consts: tuple[tuple[float | int | complex, bool], ...]
    comparable: bool


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return any(d in ("tests", "test") for d in p.parts[:-1]) or any(fnmatch(p.name, g) for g in ("test_*.py", "*_test.py", "conftest.py"))


def _absolute(module: str | None, level: int, modname: str, is_pkg: bool) -> str:
    if not level:
        return module or ""
    parts = modname.split(".") if is_pkg else modname.split(".")[:-1]
    if level - 1 >= len(parts) or not parts[0]:
        return f"<{modname}>{'.' * level}{module or ''}"  # unresolvable: unique to this file
    base = parts[:len(parts) - level + 1]
    return ".".join(base + ([module] if module else []))


def _own_nodes(node: ast.AST):
    """Nodes of this scope, not descending into nested defs, lambdas or classes."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _SCOPES):
            yield from _own_nodes(child)


def _bound_names(node: ast.AST):
    for n in _own_nodes(node):
        if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            yield n.id
        elif isinstance(n, (*_FUNCS, ast.ClassDef)):
            yield n.name
        elif isinstance(n, ast.alias):
            yield n.asname or n.name.split(".")[0]
        elif isinstance(n, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and n.name:
            yield n.name
        elif isinstance(n, ast.MatchMapping) and n.rest:
            yield n.rest


class _Module:
    def __init__(self, path: str, tree: ast.Module):
        self.name = _module_name(path)
        self.is_pkg = PurePosixPath(path).name == "__init__.py"
        counts = Counter(_bound_names(tree))
        counts.update(n for g in ast.walk(tree) if isinstance(g, ast.Global) for n in g.names)
        imports, stars = {}, []
        for n in _own_nodes(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    imports[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(n, ast.ImportFrom):
                src = _absolute(n.module, n.level, self.name, self.is_pkg)
                for a in n.names:
                    if a.name == "*":
                        stars.append(src)
                    else:
                        imports[a.asname or a.name] = f"{src}.{a.name}"
        self.bound = set(counts)
        self.imports = {n: q for n, q in imports.items() if counts[n] == 1}
        self.prefix = f"{self.name}[*{','.join(stars)}]" if stars else self.name

    def qualify(self, name: str) -> str:
        if self.prefix != self.name:  # a star import may supply or shadow any free name
            return f"{self.prefix}.{name}"
        if name in self.imports:
            return self.imports[name]
        if name in _BUILTINS and name not in self.bound:
            return f"builtins.{name}"
        return f"{self.name}.{name}"


class _Scope:
    def __init__(self, parent: "_Scope | None", kind: str):
        self.parent, self.kind = parent, kind
        self.bound: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()


def _walk_function(node, scope: _Scope, rec: list, pre: tuple[str, ...] = ()) -> None:
    for d in [*getattr(node, "decorator_list", []), *node.args.defaults, *filter(None, node.args.kw_defaults)]:
        _walk(d, scope, rec)
    if not isinstance(node, ast.Lambda):
        scope.bound.add(node.name)
        rec.append((node, "name", scope))
        node.returns = node.type_comment = None
    inner = _Scope(scope, "func")
    inner.bound.update(pre)
    a = node.args
    for arg in [*a.posonlyargs, *a.args, *filter(None, [a.vararg]), *a.kwonlyargs, *filter(None, [a.kwarg])]:
        arg.annotation = arg.type_comment = None
        inner.bound.add(arg.arg)
        rec.append((arg, "arg", inner))
    for s in node.body if isinstance(node.body, list) else [node.body]:
        _walk(s, inner, rec)


def _walk(node: ast.AST, scope: _Scope, rec: list) -> None:
    """Record every name-bearing node with the scope it is evaluated in, and collect each scope's bindings."""
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            scope.bound.add(node.id)
        rec.append((node, "id", scope))
        return
    if isinstance(node, (*_FUNCS, ast.Lambda)):
        return _walk_function(node, scope, rec)
    if isinstance(node, ast.ClassDef):
        for d in [*node.decorator_list, *node.bases, *node.keywords]:
            _walk(d, scope, rec)
        scope.bound.add(node.name)
        rec.append((node, "name", scope))
        inner = _Scope(scope, "class")
        for s in node.body:
            _walk(s, inner, rec)
        return
    if isinstance(node, _COMPS):
        gens = node.generators
        _walk(gens[0].iter, scope, rec)  # the first iterable is evaluated in the enclosing scope
        inner = _Scope(scope, "comp")
        for i, g in enumerate(gens):
            _walk(g.target, inner, rec)
            if i:
                _walk(g.iter, inner, rec)
            for c in g.ifs:
                _walk(c, inner, rec)
        for e in (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,):
            _walk(e, inner, rec)
        return
    if isinstance(node, ast.NamedExpr):
        target = scope
        while target.kind == "comp":  # a walrus in a comprehension binds in the enclosing function
            target = target.parent
        target.bound.add(node.target.id)
        rec.append((node.target, "id", target))
        return _walk(node.value, scope, rec)
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        (scope.globals if isinstance(node, ast.Global) else scope.nonlocals).update(node.names)
        rec.append((node, "names", scope))
        return
    if isinstance(node, ast.alias):
        scope.bound.add(node.asname or node.name.split(".")[0])
        rec.append((node, "alias", scope))
        return
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        scope.bound.add(node.name)
        rec.append((node, "name", scope))
    if isinstance(node, ast.MatchMapping) and node.rest:
        scope.bound.add(node.rest)
        rec.append((node, "rest", scope))
    for child in ast.iter_child_nodes(node):
        _walk(child, scope, rec)


def _normalize(fn, mod: _Module, cls: ast.ClassDef | None, cls_qual: str):
    fn = copy.deepcopy(fn)
    if ast.get_docstring(fn, clean=False) is not None:
        fn.body = fn.body[1:]
    for n in ast.walk(fn):
        if isinstance(n, ast.ImportFrom) and n.level:
            n.module, n.level = _absolute(n.module, n.level, mod.name, mod.is_pkg), 0
    pre = ()
    pos = fn.args.posonlyargs or fn.args.args
    if cls is not None and pos and pos[0].arg in ("self", "cls"):
        pre = (pos.pop(0).arg,)
    outer = _Scope(None, "outer")
    cls_names = set(_bound_names(cls)) if cls is not None else set()
    rec: list = []
    _walk_function(fn, outer, rec, pre)

    names: dict = {}

    def canon(name: str, scope: _Scope) -> str:
        s, skip = scope, False
        while s is not outer:
            if not (skip and s.kind == "class"):  # enclosed scopes do not see class bodies
                if name in s.globals:
                    break
                if name in s.bound and name not in s.nonlocals:
                    if s.kind == "class":  # class-body names are attributes, so keep them, marked apart from locals
                        return f"@{name}"
                    return names.setdefault((s, name), f"v{len(names)}")
            s, skip = s.parent, True
        if scope is outer and name in cls_names:  # decorators and defaults of a method see the class body
            return f"{mod.name}.{cls_qual}.{name}"
        return mod.qualify(name)

    for node, field, scope in rec:
        if field == "names":
            node.names = [canon(n, scope) for n in node.names]
        elif field == "alias":
            c = canon(node.asname or node.name.split(".")[0], scope)
            node.asname = c if node.asname else f"{c}=top"  # keep `import a.b` apart from `import a.b as a`
        else:
            setattr(node, field, canon(getattr(node, field), scope))
    fn.name = ""
    return fn


class _Numbers(ast.NodeTransformer):
    def __init__(self):
        self.consts: list[tuple[float | int | complex, bool]] = []
        self.depth = 0

    def visit_Subscript(self, node):
        node.value = self.visit(node.value)
        self.depth += 1
        node.slice = self.visit(node.slice)
        self.depth -= 1
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float, complex)) and not isinstance(node.value, bool):
            self.consts.append((node.value, self.depth > 0))
            return ast.Name(id="#num", ctx=ast.Load())  # '#' never appears in a real or qualified name
        return node


def _is_stub(fn) -> bool:
    if any(ast.unparse(d).split(".")[-1] in ("abstractmethod", "overload") for d in fn.decorator_list):
        return True

    def stub(s: ast.stmt) -> bool:
        if isinstance(s, ast.Pass):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant):
            return s.value.value is ... or isinstance(s.value.value, str)
        if isinstance(s, ast.Raise) and s.exc is not None:
            e = s.exc.func if isinstance(s.exc, ast.Call) else s.exc
            return isinstance(e, ast.Name) and e.id == "NotImplementedError"
        return False
    return all(map(stub, fn.body))


def _digest(node: ast.AST, extra: str = "") -> str:
    return hashlib.sha256((ast.dump(node) + extra).encode()).hexdigest()


def _uses_super(fn) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "super" and not n.args and not n.keywords
               for n in ast.walk(fn))


def shapes_of(path: str, text: str) -> list[Shape]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    mod = _Module(path, tree)
    out: list[Shape] = []

    def visit(body: list[ast.stmt], prefix: str, cls: ast.ClassDef | None) -> None:
        for node in body:
            if isinstance(node, _FUNCS):
                norm = _normalize(node, mod, cls, prefix[:-1])
                bases = tuple(ast.unparse(b).split(".")[-1] for b in cls.bases) if cls else ()
                extra = f"\nsuper:{bases!r}" if _uses_super(node) else ""  # zero-arg super() resolves via the class bases
                exact = _digest(norm, extra)
                nums = _Numbers()
                near = _digest(nums.visit(norm), extra)
                out.append(Shape(
                    path=path, qualname=prefix + node.name, module=mod.name,
                    line=node.lineno, end_line=node.end_lineno or node.lineno,
                    cls=cls.name if cls else None,
                    bases=bases,
                    exact_key=exact, near_key=near, consts=tuple(nums.consts),
                    comparable=sum(1 for _ in ast.walk(node)) >= _FLOOR and not _is_stub(node),
                ))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.", node)

    visit(tree.body, "", None)
    return out


def index_shapes(texts: dict[str, str]) -> list[Shape]:
    return [s for p, t in texts.items() if p.endswith(".py") for s in shapes_of(p, t)]
