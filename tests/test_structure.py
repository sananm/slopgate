import ast

import pytest

from slopgate.structure import index_shapes, is_test_path, shapes_of

_FILLER = ast.parse("z = (" + "'s', " * 40 + ")").body


def padded(src):
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    fn.body[:0] = _FILLER
    return ast.unparse(tree) + "\n"


def key(src, path="m.py"):
    return shapes_of(path, src)[0].exact_key


def key_in(prelude, fn_src, path="m.py"):
    return next(s for s in shapes_of(path, prelude + padded(fn_src)) if s.qualname == "f").exact_key


def shape(src):
    return shapes_of("m.py", src)[0]


def test_renamed_locals_and_docstring_are_the_same():
    assert key("def f(a, b):\n    '''x'''\n    t = a * 2\n    return t + b\n") == key("def g(p, q):\n    s = p * 2\n    return s + q\n")


@pytest.mark.parametrize("a,b", [
    ("def f(x): return sqrt(x)", "def f(x): return log(x)"),
    ("def f(a, b): return a + b", "def f(a, b): return a + a"),
    ("def f(x): return max(x, 0)", "def f(x): return min(x, 0)"),
    ("def f(x): return d['a'] + x", "def f(x): return d['b'] + x"),
    ("async def f(x): return x", "def f(x): return x"),
    ("def f(x, *a): return x", "def f(x, a): return x"),
    ("@app.get('/a')\ndef f(x): return x", "@app.get('/b')\ndef f(x): return x"),
])
def test_different_code_never_shares_an_exact_key(a, b):
    assert key(padded(a)) != key(padded(b))


def test_different_imported_helpers_differ():
    assert key_in("from p import h\n", "def f(x): return h(x)") != key_in("from q import h\n", "def f(x): return h(x)")


def test_module_local_helpers_in_different_files_differ():  # Review Focus
    assert key_in("def h(x): return x\n", "def f(x): return h(x)", path="a.py") != key_in("def h(x): return x\n", "def f(x): return h(x)", path="b.py")


def test_relative_imports_resolve_to_different_modules():  # Review Focus
    assert key_in("from .u import h\n", "def f(x): return h(x)", path="pkg/sub/m.py") != key_in("from ..u import h\n", "def f(x): return h(x)", path="pkg/sub/m.py")


def test_nested_scope_locals_do_not_collide_with_outer_free_names():  # Review Focus
    a = "def f(x):\n    def g(n): return n + 1\n    return g(x) + n\n"
    b = "def f(x):\n    def g(m): return m + 1\n    return g(x) + k\n"
    assert key(padded(a)) != key(padded(b))


def test_near_key_ignores_numbers_and_records_them():
    s1, s2 = shape("def f(x):\n    return x > 0.90\n"), shape("def f(x):\n    return x > 0.95\n")
    assert s1.exact_key != s2.exact_key and s1.near_key == s2.near_key
    assert [c for c, _ in s1.consts] == [0.90]


def test_subscript_constants_are_flagged():
    assert shape("def f(x):\n    return x[0]\n").consts == ((0, True),)


def test_stubs_and_tiny_functions_are_not_comparable():
    assert not shape("def f(x):\n    raise NotImplementedError\n").comparable
    assert not shape("def f(x): return x\n").comparable


def test_syntax_error_file_yields_no_shapes():  # Review Focus
    assert shapes_of("bad.py", "def f(:\n") == []


def test_is_test_path():
    assert all(map(is_test_path, ["tests/a.py", "pkg/test/b.py", "test_x.py", "x_test.py", "conftest.py"]))
    assert not is_test_path("src/testing_utils.py")


# Self-review: attempts to make different code share an exact key.
@pytest.mark.parametrize("pa,fa,pb,fb", [
    # comprehension variables are their own scope; the outer name is a global
    ("", "def f(x): return [n for n in x] + [n]", "", "def f(x): return [m for m in x] + [m]"),
    # global statement: different module globals
    ("", "def f(x):\n    global n\n    n = x\n    return n", "", "def f(x):\n    global k\n    k = x\n    return k"),
    # inner free name bound to the outer local vs to a global
    ("", "def f(x):\n    n = x\n    def g(): return n\n    return g", "", "def f(x):\n    m = x\n    def g(): return n\n    return g"),
    # walrus in a comprehension binds in the function
    ("", "def f(x):\n    [(y := i) for i in x]\n    return y", "", "def f(x):\n    [(z := i) for i in x]\n    return y"),
    # star imports from different modules
    ("from p import *\n", "def f(x): return h(x)", "from q import *\n", "def f(x): return h(x)"),
    # a builtin shadowed at module level
    ("def len(x): return 0\n", "def f(x): return len(x)", "", "def f(x): return len(x)"),
    # a module whose name is a builtin
    ("import help\n", "def f(x): return help(x)", "", "def f(x): return help(x)"),
    # an import rebound later, or conditionally
    ("from p import h\nh = wrap(h)\n", "def f(x): return h(x)", "from p import h\n", "def f(x): return h(x)"),
    ("try:\n    from p import h\nexcept ImportError:\n    from q import h\n", "def f(x): return h(x)", "from p import h\n", "def f(x): return h(x)"),
    # class-body names are attributes, not renameable locals
    ("", "def f(x):\n    class A:\n        n = 1\n    return A", "", "def f(x):\n    class A:\n        k = 1\n    return A"),
    ("", "def f(x):\n    class A:\n        v0 = 1\n        y = v0\n    return A", "", "def f(x):\n    class A:\n        v0 = 1\n        y = x\n    return A"),
    # imports inside the body
    ("", "def f(x):\n    import a.b\n    return a.c", "", "def f(x):\n    import a.b as a\n    return a.c"),
])
def test_adversarial_pairs_differ(pa, fa, pb, fb):
    assert key_in(pa, fa) != key_in(pb, fb)


def test_relative_import_in_body_resolves_per_package():
    src = "def f(x):\n    from .u import h\n    return h(x)"
    assert key_in("", src, path="pkg/a/m.py") != key_in("", src, path="pkg/b/m.py")


def test_method_default_from_class_attribute_is_qualified_by_class():
    src = "class C:\n    L = 1\n    def m(self, x=L): return x\nclass D:\n    L = 2\n    def m(self, x=L): return x\n"
    a, b = shapes_of("m.py", src)
    assert a.exact_key != b.exact_key


def test_method_drops_self_and_matches_function():
    m = shapes_of("m.py", "class C(base.B, A):\n    def m(self, x):\n        return x * 2\n")[0]
    assert m.exact_key == key("def f(x):\n    return x * 2\n")
    assert (m.qualname, m.cls, m.bases, m.module) == ("C.m", "C", ("B", "A"), "m")
    used = shapes_of("m.py", "class C:\n    def m(self, x):\n        return self\n")[0]
    assert used.exact_key != key("def f(x):\n    return x\n")


def test_index_shapes_covers_files_and_skips_nested_functions():
    shapes = index_shapes({"src/pkg/__init__.py": "def f():\n    def g(): pass\n", "a.py": "class C:\n    def m(self): pass\n"})
    assert [(s.path, s.qualname, s.module) for s in shapes] == [("src/pkg/__init__.py", "f", "pkg"), ("a.py", "C.m", "a")]
    assert shapes[0].line == 1 and shapes[0].end_line == 2 and shapes[0].cls is None


def test_padded_functions_are_comparable_and_abstract_ones_are_not():
    assert shape(padded("def f(x): return x")).comparable
    assert not shape(padded("@abstractmethod\ndef f(x): return x")).comparable


def _save(base, cls="C"):
    return shapes_of("m.py", f"class {cls}({base}):\n    def save(self):\n        return super().save()\n")[0].exact_key


def test_super_keys_differ_by_class_bases():
    assert _save("A") != _save("B")


def test_super_keys_match_for_identical_bases():
    assert _save("A, M") == _save("A, M", cls="D")


def test_module_dunders_are_per_module():
    src = "import logging\n"
    fn = "def f(x): return logging.getLogger(__name__)"
    assert key_in(src, fn, path="a.py") != key_in(src, fn, path="b.py")


_SUPER = "    def save(self):\n        return super().save()\n"


def _method_key(prelude, base, path="m.py", body=_SUPER):
    return next(s for s in shapes_of(path, f"{prelude}class C({base}):\n{body}") if s.qualname == "C.save").exact_key


@pytest.mark.parametrize("a,b", [
    (("import a\n", "a.Base"), ("import b\n", "b.Base")),
    (("from p import Base\n", "Base"), ("from q import Base\n", "Base")),
    (("class Base: pass\n", "Base", "a.py"), ("class Base: pass\n", "Base", "b.py")),
])
def test_super_keys_use_qualified_bases(a, b):
    assert _method_key(*a) != _method_key(*b)


def test_super_keys_match_for_same_qualified_bases():
    assert _method_key("from p import Base\n", "Base", "a.py") == _method_key("from p import Base\n", "Base", "b.py")


def test_class_cell_depends_on_the_class():
    body = "    def save(self):\n        return __class__\n"
    c, d = shapes_of("m.py", "class C:\n" + body + "class D:\n" + body)
    assert c.exact_key != d.exact_key


def test_imported_name_never_collides_with_a_renamed_local():
    assert key_in("import v0\n", "def f(x): return v0(x)") != key_in("", "def f(x): return x(x)")


def test_deep_function_is_skipped_and_the_rest_of_the_file_still_indexes():
    deep = "+".join(["a"] * 250)
    shapes = shapes_of("m.py", f"y = {deep}\ndef deep(a):\n    return {deep}\ndef ok(x):\n    return x\n")
    assert [s.qualname for s in shapes] == ["ok"]
