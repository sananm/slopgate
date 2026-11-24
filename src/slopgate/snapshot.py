import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Snapshot:
    task_id: str
    files: dict[str, str]


def state_dir(repo: Path) -> Path:
    d = repo / ".git" / "slopgate"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_py_files(repo: Path) -> dict[str, str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.py"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    files = {}
    for rel in filter(None, out.split("\0")):
        p = repo / rel
        if p.is_file():  # tracked-but-deleted files are listed by --cached
            files[rel] = p.read_text(encoding="utf-8", errors="replace")
    return files


def take_snapshot(repo: Path) -> str:
    task_id = uuid.uuid4().hex
    snap = {"task_id": task_id, "files": _read_py_files(repo)}
    d = state_dir(repo)
    tmp = d / f"snapshot.{task_id}.tmp"
    tmp.write_text(json.dumps(snap))
    os.replace(tmp, d / "snapshot.json")  # atomic: readers never see a partial file
    return task_id


def load_snapshot(repo: Path) -> Snapshot | None:
    p = state_dir(repo) / "snapshot.json"
    try:
        data = json.loads(p.read_text())
        return Snapshot(data["task_id"], data["files"])
    except (ValueError, KeyError, OSError):  # missing or corrupt: hooks must not raise
        return None


def changed_py_files(repo: Path, snap: Snapshot) -> list[str]:
    now = _read_py_files(repo)
    return sorted(p for p in snap.files.keys() | now.keys() if snap.files.get(p) != now.get(p))
