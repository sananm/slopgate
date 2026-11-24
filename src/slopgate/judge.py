import ast
import textwrap

from slopgate.index import FunctionInfo

_SCALARS = {"int", "float", "str", "bytes", "bool"}
_CONTAINERS = {"list", "dict", "tuple", "set", "frozenset", "Optional", "Union"}
# Fail closed: a name read in the body must be a param, a local, or one of these.
_SAFE_BUILTINS = set("""
abs all any bin bool bytes callable chr dict divmod enumerate filter float format frozenset
hash hex int isinstance issubclass iter len list map max min next oct ord pow range repr
reversed round set slice sorted str sum tuple zip ArithmeticError AssertionError Exception
IndexError KeyError LookupError NotImplementedError OverflowError StopIteration TypeError
ValueError ZeroDivisionError
""".split())


_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _annotation_ok(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _SCALARS | _CONTAINERS
    if isinstance(node, ast.Constant):
        return node.value is None or node.value is Ellipsis
    if isinstance(node, ast.Subscript):
        return _annotation_ok(node.value) and _annotation_ok(node.slice)
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, ast.BitOr) and _annotation_ok(node.left) and _annotation_ok(node.right)
    if isinstance(node, ast.Tuple):
        return all(_annotation_ok(e) for e in node.elts)
    return False


def why_not_judgeable(fn: FunctionInfo) -> str | None:
    if fn.is_async:
        return "async"
    if fn.is_generator:
        return "generator"
    if fn.is_method:
        return "method"
    for p in fn.params:
        if p.annotation is None:
            return f"unannotated: {p.name}"
        if not _annotation_ok(ast.parse(p.annotation, mode="eval").body):
            return f"annotation: {p.name}"
    if fn.decorators:
        return "decorated"
    tree = ast.parse(textwrap.dedent(fn.source)).body[0]
    # *args/**kwargs are not in fn.params; the prover cannot generate them.
    if tree.args.vararg or tree.args.kwarg:
        return "annotation: varargs"
    # Param annotations are checked above, so scan only the body and default values.
    nodes = [n for r in [*tree.body, *tree.args.defaults, *tree.args.kw_defaults] if r for n in ast.walk(r)]
    for n in nodes:
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            return "denied: global"
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return "nested scope"
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            return "denied: import"
        # dunder access (e.g. ().__class__) can reach denied modules without naming them
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            return f"denied: {n.attr}"
    comps = [n for n in nodes if isinstance(n, _COMPS)]
    pure = (ast.Name, ast.Tuple, ast.List, ast.Starred, ast.expr_context)
    if any(not isinstance(t, pure) for c in comps for g in c.generators for t in ast.walk(g.target)):
        return "denied: target"
    comp_targets = {id(t) for c in comps for g in c.generators for t in ast.walk(g.target)}
    # Comprehension targets are scoped to the comprehension; walrus targets still bind here.
    bound = {p.name for p in fn.params} | {
        n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and id(n) not in comp_targets
    } | {n.name for n in nodes if isinstance(n, ast.ExceptHandler) and n.name}
    allowed = bound | _SAFE_BUILTINS

    def bad(n: ast.AST, scope: set[str]) -> str | None:
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in scope:
            return n.id
        if isinstance(n, _COMPS):
            inner = scope | {t.id for g in n.generators for t in ast.walk(g.target) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
            # The first iterable is evaluated in the enclosing scope.
            kids = [(n.generators[0].iter, scope)] + [(c, inner) for c in ast.iter_child_nodes(n) if c is not n.generators[0].iter]
        else:
            kids = [(c, scope) for c in ast.iter_child_nodes(n)]
        return next((r for c, sc in kids if (r := bad(c, sc))), None)

    roots = [r for r in [*tree.body, *tree.args.defaults, *tree.args.kw_defaults] if r]
    name = next((r for root in roots if (r := bad(root, allowed))), None)
    return f"denied: {name}" if name else None
