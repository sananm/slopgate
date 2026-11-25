import math

from slopgate.index import FunctionInfo
from slopgate.reinvention import _cosine
from slopgate.semantic import (Question, find_questions, format_question, question_key,
                               semantic_candidates, semantic_pool)
from slopgate.structure import Shape


def fi(path, line, qualname):
    return FunctionInfo(path, qualname, "m", line, line + 5, [], False, False, "." in qualname,
                        f"src-{path}-{qualname}", "bk")


def sh(f, cls=None, bases=(), comparable=True):
    return Shape(f.path, f.qualname, "m", f.line, f.end_line, cls, bases, "e", "n", (), comparable)


def test_question_text_exact():
    q = Question(new=fi("src/a.py", 52, "V._infer_frequency_seconds"),
                 existing=fi("src/b.py", 182, "D._infer_frequency_minutes"), score=0.84, copies=4)
    assert format_question(q) == (
        "question: src/a.py:52 V._infer_frequency_seconds\n"
        "  looks like src/b.py:182 D._infer_frequency_minutes (and 4 copies of it).\n"
        "  Reuse it, or reply with why the new one is different.")
    q0 = Question(q.new, q.existing, 0.9, 0)
    assert format_question(q0).endswith("D._infer_frequency_minutes.\n  Reuse it, or reply with why the new one is different.")
    assert question_key(q) == "src/b.py\tD._infer_frequency_minutes"


def test_cutoff_is_inclusive(monkeypatch):
    import slopgate.semantic as sem
    n, p = fi("src/a.py", 1, "f"), fi("src/b.py", 1, "g")
    v = {n.source: [1.0, 0.0], p.source: [0.6, 0.8]}
    c = _cosine(v[n.source], v[p.source])
    monkeypatch.setattr(sem, "SEMANTIC_CUTOFF", c)
    assert len(find_questions([n], [p], v, {})) == 1
    monkeypatch.setattr(sem, "SEMANTIC_CUTOFF", math.nextafter(c, 2.0))
    assert find_questions([n], [p], v, {}) == []


def test_names_first_copy_by_path_and_counts_the_others():
    n = fi("src/n.py", 1, "f")
    a, b, c = fi("src/z.py", 1, "g"), fi("src/a.py", 9, "g"), fi("src/a.py", 3, "h")
    other = fi("src/o.py", 1, "o")
    vecs = {x.source: [1.0, 0.0] for x in (n, a, b, c)} | {other.source: [0.0, 1.0]}
    ek = {(x.path, x.qualname): "K" for x in (a, b, c)}
    (q,) = find_questions([n], [a, b, c, other], vecs, ek)
    assert (q.existing.path, q.existing.qualname, q.copies) == ("src/a.py", "g", 2)
    (q,) = find_questions([n], [a], vecs, {})  # missing from exact_keys: no copies
    assert q.copies == 0


def test_candidate_filters():
    t = fi("tests/test_x.py", 1, "test_a")
    d = fi("src/a.py", 1, "C.__init__")
    small = fi("src/a.py", 2, "small")
    skipped = fi("src/a.py", 3, "skipped")
    ov = fi("src/a.py", 4, "E.extract")
    helper = fi("src/a.py", 5, "E._helper")
    plain = fi("src/a.py", 6, "plain")
    base = fi("src/b.py", 1, "BaseExtractor.extract")
    shapes = [sh(t), sh(d, "C"), sh(small, comparable=False), sh(skipped), sh(ov, "E", ("BaseExtractor",)),
              sh(helper, "E", ("BaseExtractor",)), sh(plain), sh(base, "BaseExtractor")]
    got = semantic_candidates([t, d, small, skipped, ov, helper, plain, fi("src/a.py", 7, "noshape")], shapes,
                              {(skipped.path, skipped.qualname)})
    assert [f.qualname for f in got] == ["E._helper", "plain"]


def test_pool_keeps_small_functions_drops_tests_and_dunders():
    keep = fi("src/a.py", 1, "tiny")
    got = semantic_pool([keep, fi("tests/test_a.py", 1, "t"), fi("src/a.py", 2, "C.__repr__")])
    assert got == [keep]


def test_missing_vectors_are_ignored():
    n, p1, p2 = fi("src/n.py", 1, "f"), fi("src/a.py", 1, "g"), fi("src/b.py", 1, "h")
    vecs = {n.source: [1.0, 0.0], p2.source: [1.0, 0.1]}
    (q,) = find_questions([n], [p1, p2], vecs, {})
    assert q.existing is p2
    assert find_questions([fi("src/x.py", 1, "nv")], [p2], vecs, {}) == []
