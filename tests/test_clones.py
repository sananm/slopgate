import dataclasses
import time

import pytest

from slopgate.clones import Clone, clone_groups, find_clones, format_clone
from slopgate.structure import Shape, index_shapes

K1 = "k1"


def s(path, qn, key, line=1, cls=None, bases=(), near_key="n", consts=()):
    return Shape(path, qn, path[:-3], line, line + 9, cls, bases, key, near_key, consts, True)


def near(path, vals, sub=False, qn="f", line=1):
    return s(path, qn, f"e{path}{vals}", line, near_key="N", consts=tuple((v, sub) for v in vals))


def test_new_copy_of_existing_function_blocks_and_names_original():
    c, = find_clones([s("b.py", "f", K1)], pool=[s("a.py", "f", K1), s("b.py", "f", K1)], before=[s("a.py", "f", K1)])
    assert (c.kind, c.original.path, c.blocking) == ("copy", "a.py", True)


def test_pre_existing_relation_is_silent():
    old = [s("a.py", "f", K1), s("b.py", "f", K1)]
    assert find_clones([s("b.py", "f", K1)], pool=old, before=old) == []


def test_two_new_copies_in_one_turn_pick_first_path_as_original():
    c, = find_clones([s("b.py", "f", K1), s("a.py", "g", K1)], pool=[s("a.py", "g", K1), s("b.py", "f", K1)], before=[])
    assert (c.new.path, c.original.path) == ("b.py", "a.py")


def test_test_files_are_advisory():
    c, = find_clones([s("tests/t.py", "f", K1)], pool=[s("src/a.py", "f", K1), s("tests/t.py", "f", K1)], before=[s("src/a.py", "f", K1)])
    assert c.blocking is False


@pytest.mark.parametrize("c1,c2,is_near", [
    ((0.90,), (0.95,), True),
    ((0, 1), (1, 0), False),
    ((1, 2, 3, 4), (5, 6, 7, 8), False),
])
def test_near_clone_limits(c1, c2, is_near):
    assert bool(find_clones([near("b.py", c2)], pool=[near("a.py", c1), near("b.py", c2)], before=[near("a.py", c1)])) is is_near


def test_subscript_difference_is_not_a_near_clone():
    assert find_clones([near("b.py", (1,), sub=True)], pool=[near("a.py", (0,), sub=True), near("b.py", (1,), sub=True)], before=[near("a.py", (0,), sub=True)]) == []


def test_format_copy_matches_spec():
    c = Clone("copy", s("src/features/lag_features.py", "_infer_frequency_minutes", K1, 128),
              s("src/features/derivative_features.py", "_infer_frequency_minutes", K1, 182), None, None, True)
    assert format_clone(c) == (
        "src/features/lag_features.py:128 _infer_frequency_minutes is a copy of\n"
        "  src/features/derivative_features.py:182 _infer_frequency_minutes.\n"
        "  Delete it and import the original.")


def test_format_near_matches_spec():
    c = Clone("near", near("src/monitoring/accuracy.py", (0.95,), qn="within_95_ci", line=147),
              near("src/monitoring/accuracy.py", (0.90,), qn="within_90_ci", line=137), ((0.95,), (0.9,)), None, True)
    assert format_clone(c) == (
        "src/monitoring/accuracy.py:147 within_95_ci is src/monitoring/accuracy.py:137\n"
        "  within_90_ci with different constants (0.95 vs 0.9). Add a parameter to\n"
        "  within_90_ci instead of copying it.")


def test_sibling_methods_suggest_shared_base():
    new = s("b.py", "B.run", K1, cls="B", bases=("Executor",))
    old = s("a.py", "A.run", K1, cls="A", bases=("Base", "Executor"))
    executor = s("base.py", "Executor.run", "k2", cls="Executor")
    c, = find_clones([new], pool=[old, new, executor], before=[old, executor])
    assert c.base == "Executor"
    assert "  Move it into the shared base class Executor." in format_clone(c)


@pytest.mark.parametrize("base", ["ABC", "object", "BaseModel", "Generic[T]", "Module"])
def test_third_party_or_stdlib_base_suggests_shared_helper(base):
    new = s("b.py", "B.run", K1, cls="B", bases=(base,))
    old = s("a.py", "A.run", K1, cls="A", bases=(base,))
    c, = find_clones([new], pool=[old, new], before=[old])
    assert c.base is None and format_clone(c).endswith("  Move it to a shared helper and call that from both.")


def test_int_and_float_constants_differ():
    body = "".join(f"    x{j} = a + b * {j + 3}\n" for j in range(10))
    src = "def f(n, a, b):\n" + body + "    return n // {} + {}\n"
    shapes = index_shapes({"a.py": src.format("2", "0.95"), "b.py": src.format("2.0", "0.90")})
    old, new = shapes
    assert old.comparable and old.near_key == new.near_key
    c, = find_clones([new], pool=shapes, before=[old])
    assert c.diff == ((2.0, 0.9), (2, 0.95)) and type(c.diff[0][0]) is float


def test_advisory_clone_text_is_a_note():
    c, = find_clones([s("tests/t.py", "f", K1)], pool=[s("src/a.py", "f", K1), s("tests/t.py", "f", K1)], before=[s("src/a.py", "f", K1)])
    assert format_clone(c) == "note: tests/t.py:1 f is a copy of\n  src/a.py:1 f.\n  Delete it and import the original."


def test_method_original_suggests_shared_helper():
    new = s("b.py", "B.run", K1, cls="B")
    old = s("a.py", "A.go", K1, cls="A")
    c, = find_clones([new], pool=[old, new], before=[old])
    assert format_clone(c).endswith("  Move it to a shared helper and call that from both.")


def test_non_comparable_ignored_and_clone_key():
    import dataclasses
    old = dataclasses.replace(s("a.py", "f", K1), comparable=False)
    assert find_clones([s("b.py", "f", K1)], pool=[old], before=[old]) == []
    c, = find_clones([s("b.py", "g", K1)], pool=[s("a.py", "f", K1), s("b.py", "g", K1)], before=[s("a.py", "f", K1)])
    from slopgate.clones import clone_key
    assert clone_key(c) == "copy\tb.py\tg\ta.py\tf"


def test_clone_groups_original_first():
    g, = clone_groups([s("b.py", "f", K1), s("a.py", "f", K1), s("c.py", "f", "other")])
    assert [x.path for x in g] == ["a.py", "b.py"]


def test_near_after_exact_copy_pair_is_reported():
    a, b0, b1 = near("a.py", (0.9,)), near("b.py", (0.9,)), near("b.py", (0.95,))
    a, b0 = (dataclasses.replace(x, exact_key="E") for x in (a, b0))
    assert find_clones([b1], pool=[a, b1], before=[a, b0])[0].kind == "near"


def test_near_after_edit_from_four_diffs_is_reported():
    a, b0, b1 = near("a.py", (1, 2, 3, 4)), near("b.py", (5, 6, 7, 8)), near("b.py", (1, 2, 3, 9))
    assert len(find_clones([b1], pool=[a, b1], before=[a, b0])) == 1


def test_pre_existing_near_pair_is_silent():
    a, b0, b1 = near("a.py", (0.9,)), near("b.py", (0.95,)), near("b.py", (0.97,))
    assert find_clones([b1], pool=[a, b1], before=[a, b0]) == []


def _texts():
    return {f"m{i}.py": "def f(a, b):\n" + "".join(f"    x{j} = a + b * {j}\n" for j in range(12)) + f"    return x1 + {i + 5}\n" for i in range(2000)}


def test_2000_functions_match_under_half_second():
    shapes = index_shapes(_texts())
    t = time.perf_counter(); find_clones(shapes, shapes, []); clone_groups(shapes)
    assert time.perf_counter() - t < 0.5


def test_2000_functions_cold_index_under_4s():
    t = time.perf_counter(); index_shapes(_texts())
    assert time.perf_counter() - t < 4.0


def test_huge_bucket_with_no_near_pairs_is_bounded():
    shapes = [near(f"m{i}.py", tuple(range(10 + 5 * i, 15 + 5 * i))) for i in range(2000)]
    t = time.perf_counter()
    assert find_clones(shapes, shapes, []) == []
    assert time.perf_counter() - t < 0.5
