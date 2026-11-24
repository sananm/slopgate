from slopgate.changes import classify
from slopgate.index import FunctionInfo


def fn(module, qualname, body_key):
    return FunctionInfo(
        path=module.replace(".", "/") + ".py", qualname=qualname, module=module,
        line=1, end_line=2, params=[], is_async=False, is_generator=False,
        is_method=False, source="", body_key=body_key,
    )


def names(fns):
    return [f.qualname for f in fns]


def test_function_absent_before_is_new():
    assert names(classify([], [fn("m", "f", "k1")]).new) == ["f"]


def test_moved_function_is_not_new():
    ch = classify([fn("a", "f", "k1")], [fn("b", "f", "k1")])
    assert ch.new == [] and names(ch.existing) == ["f"]


def test_renamed_function_is_not_new():
    assert classify([fn("a", "f", "k1")], [fn("a", "g", "k1")]).new == []


def test_edited_body_is_changed_not_new():
    ch = classify([fn("a", "f", "k1")], [fn("a", "f", "k2")])
    assert names(ch.changed) == ["f"] and ch.new == []


def test_deleted_functions_are_never_existing():
    assert classify([fn("a", "f", "k1")], []).existing == []


def test_move_of_shadowed_duplicate_body_is_not_new():
    before = [fn("a", "f", "k1"), fn("a", "f", "k2")]
    ch = classify(before, [fn("b", "f", "k1")])
    assert ch.new == [] and names(ch.existing) == ["f"]


def test_copy_with_original_still_present_is_new():
    before = [fn("a", "f", "k1")]
    after = [fn("a", "f", "k1"), fn("b", "f", "k1")]
    ch = classify(before, after)
    assert names(ch.new) == ["f"] and ch.new[0].module == "b"


def test_move_when_original_removed_is_still_not_new():
    assert classify([fn("a", "f", "k1")], [fn("b", "f", "k1")]).new == []


def test_copy_when_original_was_edited_is_not_a_copy():
    # original changed body, so the "copy" is the old version moved
    assert classify([fn("a", "f", "k1")], [fn("a", "f", "k2"), fn("b", "f", "k1")]).new == []
