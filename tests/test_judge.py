import pytest

from slopgate.index import index_source
from slopgate.judge import why_not_judgeable


def one(src):
    return index_source("m.py", src)[0]


def test_pure_annotated_function_is_judgeable():
    assert why_not_judgeable(one("def f(a: int, b: list[str]) -> int: return a")) is None


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
])
def test_unjudgeable_reasons(src, reason):
    assert why_not_judgeable(one(src)).startswith(reason)


def test_methods_are_not_judgeable():
    assert why_not_judgeable(index_source("m.py", "class C:\n    def g(self, a: int): return a\n")[0]) == "method"
