import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Param:
    name: str
    annotation: str | None
    has_default: bool


@dataclass(frozen=True)
class FunctionInfo:
    path: str
    qualname: str
    module: str
    line: int
    end_line: int
    params: list[Param]
    is_async: bool
    is_generator: bool
    is_method: bool
    source: str
    body_key: str
    decorators: list[str] = field(default_factory=list)


def _module_name(path: str) -> str:
    parts = path.removeprefix("src/").removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _params(args: ast.arguments) -> list[Param]:
    pos = [*args.posonlyargs, *args.args]
    defaults = [False] * (len(pos) - len(args.defaults)) + [True] * len(args.defaults)
    kw = [*zip(args.kwonlyargs, (d is not None for d in args.kw_defaults))]
    pairs = [*zip(pos, defaults), *kw]
    return [Param(a.arg, ast.unparse(a.annotation) if a.annotation else None, d) for a, d in pairs]


def _is_generator(fn: ast.AST) -> bool:
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
        if not isinstance(n, (*_FUNCS, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))
    return False


def _body_key(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    dump = "\n".join(ast.dump(s) for s in body)  # no attributes, so locations are excluded
    return hashlib.sha256(dump.encode()).hexdigest()


def index_source(path: str, text: str) -> list[FunctionInfo]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    module = _module_name(path)
    out: list[FunctionInfo] = []

    def visit(body: list[ast.stmt], prefix: str, in_class: bool) -> None:
        for node in body:
            if isinstance(node, _FUNCS):
                out.append(FunctionInfo(
                    path=path, qualname=prefix + node.name, module=module,
                    line=node.lineno, end_line=node.end_lineno or node.lineno,
                    params=_params(node.args), is_async=isinstance(node, ast.AsyncFunctionDef),
                    is_generator=_is_generator(node), is_method=in_class,
                    source=ast.get_source_segment(text, node) or "", body_key=_body_key(node),
                    decorators=[ast.unparse(d) for d in node.decorator_list],
                ))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.", True)

    visit(tree.body, "", False)
    return out


def index_repo(repo: Path, texts: dict[str, str]) -> list[FunctionInfo]:
    return [f for p, t in texts.items() if p.endswith(".py") for f in index_source(p, t)]


def required_arity(fn: FunctionInfo) -> int:
    """Params without defaults, excluding a method's bound self/cls."""
    bound = fn.is_method and bool(fn.params) and fn.params[0].name in ("self", "cls")
    return sum(not p.has_default for p in fn.params) - bound
