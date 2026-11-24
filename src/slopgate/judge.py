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
    bound = {p.name for p in fn.params}
    for n in nodes:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            return "denied: import"
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            bound.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
    for n in nodes:
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound | _SAFE_BUILTINS:
            return f"denied: {n.id}"
        # dunder access (e.g. ().__class__) can reach denied modules without naming them
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            return f"denied: {n.attr}"
    return None
