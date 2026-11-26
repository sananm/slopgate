import json

from slopgate.rounds import asked, record_asked, should_block


def test_third_round_never_blocks(tmp_repo):
    assert [should_block(tmp_repo, "t", f"x{i}") for i in range(3)] == [True, True, False]


def test_identical_findings_stop_blocking(tmp_repo):
    assert [should_block(tmp_repo, "t", "same") for _ in range(2)] == [True, False]


def test_new_task_resets_rounds(tmp_repo):
    for i in range(3):
        should_block(tmp_repo, "t1", f"x{i}")
    assert should_block(tmp_repo, "t2", "x") is True


def test_corrupt_rounds_file_starts_fresh(tmp_repo):
    (tmp_repo / ".git/slopgate").mkdir()
    (tmp_repo / ".git/slopgate/rounds.json").write_text("{nope")
    assert should_block(tmp_repo, "t", "x") is True


def test_asked_is_per_task(tmp_repo):
    should_block(tmp_repo, "t1", "k")
    record_asked(tmp_repo, "t1", ["src/b.py\tD.f"])
    assert asked(tmp_repo, "t1") == {"src/b.py\tD.f"}
    assert asked(tmp_repo, "t2") == set()


def test_bad_asked_value_reads_as_empty(tmp_repo):
    (tmp_repo / ".git/slopgate").mkdir()
    (tmp_repo / ".git/slopgate/rounds.json").write_text(
        json.dumps({"task_id": "t", "round": 1, "last_findings_sha": None, "last_findings": "", "asked": "oops"})
    )
    assert asked(tmp_repo, "t") == set()
    assert should_block(tmp_repo, "t", "k") is True


def test_record_asked_write_failure_is_swallowed(tmp_repo, monkeypatch):
    def fail(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", fail)
    record_asked(tmp_repo, "t", ["k"])
