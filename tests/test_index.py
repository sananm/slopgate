from slopgate.index import Param, index_source


def test_extracts_module_functions_and_methods():
    fns = index_source("pkg/m.py", "def f(a: int, b: str = 'x'): return a\nclass C:\n    def g(self): pass\n")
    assert [f.qualname for f in fns] == ["f", "C.g"]
    assert fns[0].params == [Param("a", "int", False), Param("b", "str", True)]
    assert fns[0].module == "pkg.m" and fns[1].is_method


def test_src_prefix_is_stripped_from_module():
    assert index_source("src/pkg/m.py", "def f(): pass\n")[0].module == "pkg.m"


def test_init_maps_to_package():
    assert index_source("pkg/__init__.py", "def f(): pass\n")[0].module == "pkg"


def test_body_key_ignores_name_docstring_and_location():
    a = index_source("a.py", "def f(x):\n    '''doc'''\n    return x + 1\n")[0]
    b = index_source("b.py", "\n\ndef g(y):\n    return y + 1\n")[0]
    assert a.body_key != b.body_key  # different parameter names change the body
    c = index_source("c.py", "def h(x):\n    return x + 1\n")[0]
    assert a.body_key == c.body_key


def test_syntax_error_file_is_skipped_not_fatal():
    assert index_source("bad.py", "def f(:\n") == []


def test_flags_async_and_generators():
    fns = index_source("m.py", "async def a(): pass\ndef g():\n    yield 1\n")
    assert fns[0].is_async and fns[1].is_generator


def test_nested_functions_skipped_and_arity_and_repo():
    from pathlib import Path
    from slopgate.index import index_repo, required_arity

    src = "def f(a, b=1, *, c, d=2):\n    def inner(): pass\n"
    fns = index_repo(Path("."), {"m.py": src, "bad.py": "def (:"})
    assert [f.qualname for f in fns] == ["f"]
    assert required_arity(fns[0]) == 2
