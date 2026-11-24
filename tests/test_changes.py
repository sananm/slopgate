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
