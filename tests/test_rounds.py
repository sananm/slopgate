from slopgate.rounds import should_block


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
