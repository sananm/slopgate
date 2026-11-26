"""M3 judge: a model decides whether a semantic question is worth asking. Any failure means no question."""
import hashlib
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from slopgate.semantic import Question
from slopgate.snapshot import state_dir

JUDGE_MODEL = "gemini-4-hydrogen-low"
JUDGE_MAX_PAIRS = 3
JUDGE_TIMEOUT_S = 20
SCHEMA = json.dumps({"type": "object", "required": ["verdict", "reason"],
                     "properties": {"verdict": {"type": "string", "enum": ["reuse", "noise"]},
                                    "reason": {"type": "string"}}})
VERDICTS = ("reuse", "noise")


def _key(q: Question) -> str:
    return hashlib.sha256(f"{q.new.source}\0{q.existing.source}".encode()).hexdigest()


def _prompt(q: Question) -> str:
    return ("A coding agent just wrote a new function that resembles an existing one in the same repo.\n"
            "Answer reuse if an experienced engineer reviewing the new function would say 'call the existing one "
            "instead' (a small adaptation such as a unit conversion is fine); noise if they only look alike.\n\n"
            f"New function: {q.new.path} {q.new.qualname}\n```python\n{q.new.source}\n```\n\n"
            f"Existing function: {q.existing.path} {q.existing.qualname}\n```python\n{q.existing.source}\n```\n")


def _ask(q: Question) -> str | None:
    """The model's verdict, or None when the call failed in any way."""
    try:
        with tempfile.TemporaryDirectory() as empty:  # the model sees nothing but the prompt
            out = subprocess.run(["agy", "-p", _prompt(q), "--model", JUDGE_MODEL, "--json-schema", SCHEMA,
                                  "--output-format", "json", "--disable-slash-commands",
                                  "--print-timeout", f"{JUDGE_TIMEOUT_S}s"],
                                 cwd=empty, capture_output=True, text=True, timeout=JUDGE_TIMEOUT_S + 5)
        reply = json.loads(out.stdout)
        verdict = reply["structured_output"]["verdict"] if reply["status"] == "SUCCESS" else None
        return verdict if verdict in VERDICTS else None
    except Exception:
        return None


def judge(repo: Path, qs: list[Question]) -> list[Question]:
    """The questions the model judges `reuse`, in their original order; only the top JUDGE_MAX_PAIRS are judged."""
    if not qs:
        return []
    path = state_dir(repo) / "verdicts.json"
    try:
        cache = json.loads(path.read_text())
        cache = cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        cache = {}
    top = sorted(qs, key=lambda q: -q.score)[:JUDGE_MAX_PAIRS]
    todo = [q for q in top if cache.get(_key(q)) not in VERDICTS]
    if todo:
        with ThreadPoolExecutor(len(todo)) as pool:
            for q, verdict in zip(todo, pool.map(_ask, todo)):
                if verdict:  # failures are not cached, so the pair is judged again next time
                    cache[_key(q)] = verdict
        path.write_text(json.dumps(cache))
    keep = {id(q) for q in top if cache.get(_key(q)) == "reuse"}
    return [q for q in qs if id(q) in keep]
