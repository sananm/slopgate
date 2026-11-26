import hashlib
import json
import subprocess

import pytest

import slopgate.judge_model as jm
from slopgate.semantic import Question
from slopgate.snapshot import state_dir
from test_semantic import fi

REAL_RUN = subprocess.run


def q(i, score=0.9):
    return Question(fi(f"src/n{i}.py", 1, f"new{i}"), fi(f"src/o{i}.py", 1, f"old{i}"), score, 0)


def reply(verdict="reuse", status="SUCCESS"):
    return json.dumps({"status": status, "structured_output": {"verdict": verdict, "reason": "r"}})


@pytest.fixture
def agy(monkeypatch):
    """Fake agy: stdout is the next value from .replies (a str, or an exception to raise); git calls pass through."""
    class Agy:
        calls = []
        stdout = reply()

    def run(cmd, *a, **k):
        if cmd[0] != "agy":
            return REAL_RUN(cmd, *a, **k)
        Agy.calls.append((cmd, k))
        if isinstance(Agy.stdout, Exception):
            raise Agy.stdout
        return subprocess.CompletedProcess(cmd, 0, Agy.stdout, "")
    Agy.calls = []
    monkeypatch.setattr("slopgate.judge_model.subprocess.run", run)
    return Agy


def test_reuse_keeps_and_noise_drops(tmp_repo, agy):
    assert jm.judge(tmp_repo, [q(1)]) == [q(1)]
    agy.stdout = reply("noise")
    assert jm.judge(tmp_repo, [q(2)]) == []
    assert len(json.loads((state_dir(tmp_repo) / "verdicts.json").read_text())) == 2


@pytest.mark.parametrize("failure", [FileNotFoundError("agy"), subprocess.TimeoutExpired("agy", 25), "not json",
                                     reply(status="ERROR"), reply("maybe"), "[]"])
def test_failure_drops_without_raising_or_caching(tmp_repo, agy, failure):
    agy.stdout = failure
    assert jm.judge(tmp_repo, [q(1)]) == []
    agy.stdout = reply()
    assert jm.judge(tmp_repo, [q(1)]) == [q(1)] and len(agy.calls) == 2


def test_cached_verdict_makes_no_call(tmp_repo, agy):
    jm.judge(tmp_repo, [q(1)])
    agy.stdout = reply("noise")
    assert jm.judge(tmp_repo, [q(1)]) == [q(1)] and len(agy.calls) == 1


def test_verdict_cached_under_the_old_prompt_is_judged_again(tmp_repo, agy):
    old_key = hashlib.sha256(f"{q(1).new.source}\0{q(1).existing.source}".encode()).hexdigest()
    (state_dir(tmp_repo) / "verdicts.json").write_text(json.dumps({old_key: "noise"}))
    assert jm.judge(tmp_repo, [q(1)]) == [q(1)] and len(agy.calls) == 1


def test_corrupt_cache_reads_as_empty(tmp_repo, agy):
    (state_dir(tmp_repo) / "verdicts.json").write_text("{nope")
    assert jm.judge(tmp_repo, [q(1)]) == [q(1)] and len(agy.calls) == 1


def test_only_top_three_by_score_are_judged_in_original_order(tmp_repo, agy):
    qs = [q(i, score) for i, score in enumerate([0.85, 0.95, 0.86, 0.99, 0.84])]
    assert jm.judge(tmp_repo, qs) == [qs[1], qs[2], qs[3]] and len(agy.calls) == 3


def test_call_shape_and_prompt(tmp_repo, agy):
    jm.judge(tmp_repo, [q(1)])
    cmd, k = agy.calls[0]
    prompt = cmd[cmd.index("-p") + 1]
    for s in ("src-src/n1.py-new1", "src-src/o1.py-old1", "src/n1.py", "old1", "call the existing one", "share a helper"):
        assert s in prompt
    assert cmd[cmd.index("--model") + 1] == "gemini-4-hydrogen-low"
    assert cmd[-2:] == ["--print-timeout", "20s"] and k["timeout"] == 25
    assert k["cwd"] != str(tmp_repo) and "--disable-slash-commands" in cmd


def test_verdict_cached_under_another_model_is_judged_again(tmp_repo, agy, monkeypatch):
    monkeypatch.setattr(jm, "JUDGE_MODEL", "old-model")
    agy.stdout = reply("noise")
    assert jm.judge(tmp_repo, [q(1)]) == []
    monkeypatch.setattr(jm, "JUDGE_MODEL", "new-model")
    agy.stdout = reply()
    assert jm.judge(tmp_repo, [q(1)]) == [q(1)] and len(agy.calls) == 2


def test_failed_cache_write_still_returns_verdicts(tmp_repo, agy):
    (state_dir(tmp_repo) / "verdicts.json").mkdir()  # unreadable and unwritable as a file
    assert jm.judge(tmp_repo, [q(1)]) == [q(1)]
