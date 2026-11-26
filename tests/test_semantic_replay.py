import importlib.util
from pathlib import Path

from conftest import WEIGHTED, _git, git_commit_all

spec = importlib.util.spec_from_file_location("semantic_replay", Path(__file__).parents[1] / "scripts/semantic_replay.py")
semantic_replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(semantic_replay)

OTHER = """def scaled(xs, k):
    out = []
    for x in xs:
        out.append(x * k + 1)
    return out
"""


def test_replay_skips_self_and_exact_copies(tmp_path: Path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "a.py").write_text(WEIGHTED)
    (tmp_path / "b.py").write_text(WEIGHTED.replace("weighted_total", "weighted_copy"))
    (tmp_path / "c.py").write_text(OTHER)
    git_commit_all(tmp_path)
    embed = lambda texts: [[1.0, 0.1 * len(t)] for t in texts]  # noqa: E731
    rows = {(p, q): (bp, bq) for p, q, bp, bq, _ in semantic_replay.replay(tmp_path, embed)}
    assert rows[("a.py", "weighted_total")] == ("c.py", "scaled")  # its copy in b.py is never the match
