"""Replay M3 on a repo: treat each eligible function as new and print its best match as CSV.

Usage: python scripts/semantic_replay.py <repo>
"""
import csv
import sys
from pathlib import Path

import numpy as np

from slopgate.index import index_repo
from slopgate.reinvention import Embed, cached_embeddings, default_embed
from slopgate.semantic import semantic_candidates, semantic_pool
from slopgate.snapshot import _read_py_files
from slopgate.structure import index_shapes


def replay(repo: Path, embed: Embed) -> list[tuple[str, str, str, str, float]]:
    texts = _read_py_files(repo)
    functions = index_repo(repo, texts)
    shapes = index_shapes(texts)
    exact = {(s.path, s.qualname): s.exact_key for s in shapes}
    cands = semantic_candidates(functions, shapes, set())
    pool = sorted(semantic_pool(functions), key=lambda f: (f.path, f.qualname))  # ties go to the first, as in the hook
    vecs, _ = cached_embeddings(repo, [], [f.source for f in cands + pool], embed, None)
    m = np.array([vecs[f.source] for f in pool], dtype=float)
    m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
    keys = [exact.get((g.path, g.qualname)) for g in pool]
    rows = []
    for f in cands:
        v = np.array(vecs[f.source], dtype=float)
        scores = m @ (v / max(np.linalg.norm(v), 1e-12))
        me = exact.get((f.path, f.qualname))
        for i, g in enumerate(pool):  # the pool is the rest of the repo minus f and its exact copies
            if (g.path, g.qualname) == (f.path, f.qualname) or (me is not None and keys[i] == me):
                scores[i] = -np.inf
        if not np.isfinite(scores).any():
            continue
        best = int(np.argmax(scores))
        rows.append((f.path, f.qualname, pool[best].path, pool[best].qualname, round(float(scores[best]), 4)))
    return rows


def main() -> None:
    w = csv.writer(sys.stdout)
    w.writerow(["path", "qualname", "best_path", "best_qualname", "score"])
    w.writerows(replay(Path(sys.argv[1]), default_embed()))


if __name__ == "__main__":
    main()
