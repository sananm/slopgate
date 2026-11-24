import pytest

from slopgate.index import index_source
from slopgate.judge import why_not_judgeable


def one(src):
    return index_source("m.py", src)[0]


def test_pure_annotated_function_is_judgeable():
    assert why_not_judgeable(one("def f(a: int, b: list[str]) -> int: return a")) is None


def test_params_locals_loops_and_safe_builtins_are_judgeable():
    src = ("def f(a: list[int], n: int = 2) -> int:\n"
           "    t = 0\n    for x in a:\n        t += x * n\n"
           "    try:\n        t = max(t, 1)\n    except ValueError as e:\n        raise e\n"
           "    return sum([y for y in sorted(a) if (z := y) > t]) + len(a)\n")
    assert why_not_judgeable(one(src)) is None


def test_comprehension_uses_its_own_target():
    assert why_not_judgeable(one("def f(a: list[int]): return [x * 2 for x in a]")) is None


@pytest.mark.parametrize("src", [
    "def f(a: int | None, b: dict[str, tuple[int, ...]]): return a",
    "def f(a: Optional[int], b: Union[str, bytes], c: frozenset[float]): return a",
])
def test_allowed_annotations(src):
    assert why_not_judgeable(one(src)) is None


@pytest.mark.parametrize("src,reason", [
    ("async def f(a: int): return a", "async"),
    ("def f(a: int):\n    yield a", "generator"),
    ("def f(a): return a", "unannotated"),
    ("def f(a: MyType): return a", "annotation"),
    ("def f(a: list[MyType]): return a", "annotation"),
    ("def f(a: 'int'): return a", "annotation"),
    ("def f(a: typing.Optional[int]): return a", "annotation"),
    ("def f(a: int, *args: int): return a", "annotation"),
    ("def f(a: str):\n    return open(a).read()", "denied: open"),
    ("def f(a: int):\n    return time.time() + a", "denied: time"),
    ("def f(a: int):\n    return os.path.join('x', 'y')", "denied: os"),
    ("def f(a: int):\n    return ().__class__", "denied: __class__"),
    ("def f(a: str):\n    return getattr(a, 'upper')()", "denied: getattr"),
    ("def f(a: int):\n    return type(a)", "denied: type"),
    ("def f(a: str):\n    import subprocess as sp\n    return sp.run(a)", "denied: import"),
    ("def f(a: str):\n    from os import system\n    return system(a)", "denied: import"),
    ("@os.system\ndef f(a: int): return a", "decorated"),
    ("def f(a: str):\n    return run(a, shell=True)", "denied: run"),
    ("def f(a: str):\n    return builtins.open(a)", "denied: builtins"),
    ("def f(a: int):\n    breakpoint()\n    return a", "denied: breakpoint"),
    ("def f(a: int):\n    return helper(a)", "denied: helper"),
    ("def f(a: str):\n    def g():\n        open = 1\n    return open(a).read()", "nested scope"),
    ("def f(a: str):\n    g = lambda open: 0\n    return open(a)", "nested scope"),
    ("def f(a: str):\n    class C:\n        open = 1\n    return open(a)", "nested scope"),
    ("def f(a: str):\n    global open\n    if False:\n        open = 1\n    return open(a)", "denied: global"),
    ("def f(a: str):\n    [open for open in []]\n    return open(a)", "denied: open"),
    ("def f(a: str):\n    return [x for x in open(a)]", "denied: open"),
])
def test_unjudgeable_reasons(src, reason):
    assert why_not_judgeable(one(src)).startswith(reason)


def test_methods_are_not_judgeable():
    assert why_not_judgeable(index_source("m.py", "class C:\n    def g(self, a: int): return a\n")[0]) == "method"
