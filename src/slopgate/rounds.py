import hashlib
import json
from pathlib import Path

from slopgate.snapshot import state_dir

MAX_BLOCKING_ROUNDS = 2  # round 3 on never blocks


def load_rounds(repo: Path, task_id: str) -> dict:
    """This task's round state; a different or unreadable task starts at round 0."""
    try:
        data = json.loads((state_dir(repo) / "rounds.json").read_text())
        if isinstance(data, dict) and data.get("task_id") == task_id and isinstance(data.get("round"), int):
            return data
    except (OSError, ValueError):
        pass
    return {"task_id": task_id, "round": 0, "last_findings_sha": None, "last_findings": "", "asked": []}


def should_block(repo: Path, task_id: str, findings_text: str) -> bool:
    state = load_rounds(repo, task_id)
    sha = hashlib.sha256(findings_text.encode()).hexdigest()
    block = state["round"] < MAX_BLOCKING_ROUNDS and sha != state.get("last_findings_sha")
    state.update(round=state["round"] + 1, last_findings_sha=sha, last_findings=findings_text)
    (state_dir(repo) / "rounds.json").write_text(json.dumps(state))
    return block


def asked(repo: Path, task_id: str) -> set[str]:
    """Keys already asked about in this task; a malformed value reads as empty."""
    value = load_rounds(repo, task_id).get("asked")
    if isinstance(value, list) and all(isinstance(k, str) for k in value):
        return set(value)
    return set()


def record_asked(repo: Path, task_id: str, keys: list[str]) -> None:
    """Remember keys as asked. Best-effort: a failed write only means a question may repeat."""
    state = load_rounds(repo, task_id)
    state["asked"] = sorted(asked(repo, task_id) | set(keys))
    try:
        (state_dir(repo) / "rounds.json").write_text(json.dumps(state))
    except OSError:
        pass
