import hashlib
import math
import shutil
import sys
import time

import pytest

from slopgate.changes import Changes
from slopgate.index import index_source
from slopgate.judge import why_not_judgeable
from slopgate.reinvention import Finding, find_reinventions, format_finding
from test_sandbox import fixture_repo, fn_named  # noqa: F401


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 64
        for i in range(len(t) - 2):
            v[int(hashlib.md5(t[i:i + 3].encode()).hexdigest(), 16) % 64] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def counting_embed():
    def e(texts):
        e.count += len(texts)
        return fake_embed(texts)
    e.count = 0
    return e


@pytest.fixture
def repo(fixture_repo, tmp_path):
    # state_dir() writes under <repo>/.git, so work on a copy
    dst = tmp_path / "repo"
    shutil.copytree(fixture_repo, dst)
    return dst


def all_funcs(repo):
    return [f for p in repo.glob("*.py") for f in index_source(p.name, p.read_text())]


def changes_with_new(repo, *names):
    funcs = all_funcs(repo)
    return Changes(new=[f for f in funcs if f.qualname in names],
                   existing=[f for f in funcs if f.qualname not in names])


def test_finds_proven_reimplementation(repo):
    r = find_reinventions(repo, changes_with_new(repo, "square_pow"), fake_embed, sys.executable)
    assert [(f.new.qualname, f.existing.qualname) for f in r.findings] == [("square_pow", "square_loop")]


def test_lookalike_with_different_behavior_is_not_reported(repo):
    assert find_reinventions(repo, changes_with_new(repo, "abs_wrong_at_zero"), fake_embed, sys.executable).findings == []


def test_coverage_counts(repo):
    r = find_reinventions(repo, changes_with_new(repo, "square_pow", "reads_file"), fake_embed, sys.executable)
    assert (r.new_count, r.judgeable_count) == (2, 1)


def test_embeddings_are_cached_by_source(repo):
    calls = counting_embed()
    find_reinventions(repo, changes_with_new(repo, "square_pow"), calls, sys.executable)
    first = calls.count
    find_reinventions(repo, changes_with_new(repo, "square_pow"), calls, sys.executable)
    assert first > 0 and calls.count == first


def test_corrupt_cache_is_ignored_and_rebuilt(repo):
    find_reinventions(repo, changes_with_new(repo, "square_pow"), fake_embed, sys.executable)
    cache = repo / ".git" / "slopgate" / "embeddings.json"
    for junk in ("{not json", '{"x": "y"}', "[1]"):
        cache.write_text(junk)
        r = find_reinventions(repo, changes_with_new(repo, "square_pow"), fake_embed, sys.executable)
        assert len(r.findings) == 1


def test_unjudgeable_existing_are_not_candidates(repo):
    # reads_file_copy is unjudgeable; it must never be proven against
    funcs = all_funcs(repo)
    assert why_not_judgeable(next(f for f in funcs if f.qualname == "reads_file_copy"))
    r = find_reinventions(repo, changes_with_new(repo, "hash_mod"), fake_embed, sys.executable, k=1000)
    assert all(why_not_judgeable(f.existing) is None for f in r.findings)


def test_budget_exhaustion_reports_skipped_and_returns_fast(repo):
    funcs = [f for f in all_funcs(repo) if why_not_judgeable(f) is None]
    ch = Changes(new=funcs, existing=funcs)
    start = time.monotonic()
    r = find_reinventions(repo, ch, fake_embed, sys.executable, budget_s=0.01)
    assert r.skipped > 0 and r.skipped <= r.judgeable_count and time.monotonic() - start < 15


def test_finding_text_matches_spec_format(repo):
    new = fn_named(repo, "square_pow")
    old = fn_named(repo, "square_loop")
    import dataclasses
    new = dataclasses.replace(new, path="api/views.py", qualname="fmt_date", line=41, end_line=78)
    old = dataclasses.replace(old, path="utils/dates.py", qualname="to_iso", line=12)
    assert format_finding(Finding(new, old, 200)) == (
        "api/views.py:41 fmt_date reimplements utils/dates.py:12 to_iso\n"
        "  (identical on 200 generated inputs). Delete fmt_date (38 lines)\n"
        "  and call to_iso.")
