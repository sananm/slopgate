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
    return {"task_id": task_id, "round": 0, "last_findings_sha": None, "last_findings": ""}


def should_block(repo: Path, task_id: str, findings_text: str) -> bool:
    state = load_rounds(repo, task_id)
    sha = hashlib.sha256(findings_text.encode()).hexdigest()
    block = state["round"] < MAX_BLOCKING_ROUNDS and sha != state.get("last_findings_sha")
    state.update(round=state["round"] + 1, last_findings_sha=sha, last_findings=findings_text)
    (state_dir(repo) / "rounds.json").write_text(json.dumps(state))
    return block
