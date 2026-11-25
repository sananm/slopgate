import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from slopgate.changes import Changes
from slopgate.index import FunctionInfo, required_arity
from slopgate.judge import why_not_judgeable
from slopgate.prover import prove
from slopgate.snapshot import state_dir

Embed = Callable[[list[str]], list[list[float]]]

MODEL = "jinaai/jina-embeddings-v2-base-code"
# The model pads each batch to its longest text (up to 8192 tokens) and attention memory
# grows with the square of that length: 256 texts with one long function took ~165 GB.
EMBED_BATCH = 16
EMBED_CHARS = 2000


def default_embed() -> Embed:
    from fastembed import TextEmbedding  # lazy: importing this module must not need the model

    # not fastembed's default ($TMPDIR): a purged temp dir means a 600 MB re-download inside the Stop hook
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "slopgate"
    model = TextEmbedding(MODEL, cache_dir=str(cache))
    return lambda texts: [list(map(float, v)) for v in model.embed([s[:EMBED_CHARS] for s in texts], batch_size=EMBED_BATCH)]


@dataclass(frozen=True)
class Finding:
    new: FunctionInfo
    existing: FunctionInfo
    inputs: int


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)
    new_count: int = 0
    judgeable_count: int = 0
    skipped: int = 0


def format_finding(f: Finding) -> str:
    n, e = f.new, f.existing
    return (
        f"{n.path}:{n.line} {n.qualname} reimplements {e.path}:{e.line} {e.qualname}\n"
        f"  (identical on {f.inputs} generated inputs). Delete {n.qualname} ({n.end_line - n.line + 1} lines)\n"
        f"  and call {e.qualname}."
    )


def _load_cache(path: Path) -> dict[str, list[float]]:
    try:
        data = json.loads(path.read_text())
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, list)
                and all(isinstance(x, (int, float)) for x in v)}
    except (OSError, ValueError, AttributeError):  # untrusted on disk: ignore and rebuild
        return {}


def _embeddings(repo: Path, sources: list[str], embed: Embed) -> dict[str, list[float]]:
    path = state_dir(repo) / "embeddings.json"
    cache = _load_cache(path)
    key = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
    missing = list({key(s): s for s in sources if key(s) not in cache}.values())
    if missing:
        for s, v in zip(missing, embed(missing)):
            cache[key(s)] = list(v)
        try:
            path.write_text(json.dumps(cache))
        except OSError:
            pass
    return {s: cache[key(s)] for s in sources}


def _cosine(a: list[float], b: list[float]) -> float:
    d = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / d if d else 0.0


def _judgeable(f: FunctionInfo) -> bool:
    try:
        return why_not_judgeable(f) is None
    except Exception:  # one malformed function must not fail the whole check
        return False


def find_reinventions(repo: Path, changes: Changes, embed: Embed, interpreter: str,
                      budget_s: float = 20.0, k: int = 5) -> CheckResult:
    start = time.monotonic()
    over = lambda: time.monotonic() - start > budget_s  # noqa: E731
    new = [f for f in changes.new if _judgeable(f)]
    result = CheckResult(new_count=len(changes.new), judgeable_count=len(new))
    pool = [f for f in changes.existing if _judgeable(f)]
    vecs = _embeddings(repo, [f.source for f in new + pool], embed) if new and pool else {}
    for i, f in enumerate(new):
        memo: dict = {}
        cands = sorted((g for g in pool if required_arity(g) == required_arity(f)),
                       key=lambda g: -_cosine(vecs[f.source], vecs[g.source]))[:k]
        for g in cands:
            if over():
                result.skipped = len(new) - i  # this function is unfinished, plus all after it
                return result
            v = prove(repo, f, g, interpreter, deadline=start + budget_s, memo=memo)
            if v.equivalent is True:
                result.findings.append(Finding(f, g, v.inputs))
                break
        if over() and i + 1 < len(new):
            result.skipped = len(new) - i - 1
            return result
    return result
