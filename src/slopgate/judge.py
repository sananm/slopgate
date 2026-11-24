import ast
import textwrap

from slopgate.index import FunctionInfo

_SCALARS = {"int", "float", "str", "bytes", "bool"}
_CONTAINERS = {"list", "dict", "tuple", "set", "frozenset", "Optional", "Union"}
_DENIED = {
    "open", "print", "input", "exec", "eval", "compile", "__import__", "globals",
    "setattr", "delattr", "os", "sys", "subprocess", "socket", "shutil", "pathlib",
    "Path", "requests", "httpx", "urllib", "sqlite3", "time", "datetime", "random",
    "uuid", "secrets", "threading", "asyncio", "multiprocessing", "logging",
    "getattr", "vars", "type", "dir", "locals", "importlib", "__builtins__",
}


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
    tree = ast.parse(textwrap.dedent(fn.source)).body[0]
    # *args/**kwargs are not in fn.params; the prover cannot generate them.
    if tree.args.vararg or tree.args.kwarg:
        return "annotation: varargs"
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in _DENIED:
            return f"denied: {n.id}"
        # dunder access (e.g. ().__class__) can reach denied modules without naming them
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            return f"denied: {n.attr}"
    return None
