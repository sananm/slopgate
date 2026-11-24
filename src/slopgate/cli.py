import argparse
import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

from slopgate.changes import classify
from slopgate.index import index_repo
from slopgate.reinvention import default_embed, find_reinventions, format_finding
from slopgate.rounds import load_rounds, should_block
from slopgate.sandbox import pick_interpreter, sandbox_kind
from slopgate.snapshot import changed_py_files, load_snapshot, state_dir, take_snapshot

BUDGET_S = 20.0
HOOKS = {"UserPromptSubmit": "slopgate start", "Stop": "slopgate check"}


class Allow(Exception):
    """Allow the stop; the message says why."""


def _repo_from_stdin() -> Path:
    try:
        cwd = json.loads(sys.stdin.read()).get("cwd")
    except (ValueError, AttributeError):
        raise Allow("unreadable hook input")
    if not isinstance(cwd, str):
        raise Allow("no cwd in hook input")
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
    if top.returncode != 0:
        raise Allow("not a git repo")
    return Path(top.stdout.strip())


def _check(t0: float) -> dict:
    repo = _repo_from_stdin()
    snap = load_snapshot(repo)
    if snap is None:
        raise Allow("no snapshot")
    changed = changed_py_files(repo, snap)
    now = {p: t for p, t in snap.files.items() if p not in changed}  # unchanged since the snapshot
    now.update({p: (repo / p).read_text(encoding="utf-8", errors="replace") for p in changed if (repo / p).is_file()})
    changes = classify(index_repo(repo, {p: snap.files[p] for p in changed if p in snap.files}),
                       index_repo(repo, {p: now[p] for p in changed if p in now}))
    changes.existing += index_repo(repo, {p: t for p, t in now.items() if p not in changed})
    embed = default_embed() if changes.new else (lambda texts: [])  # skip loading the model when nothing is new
    result = find_reinventions(repo, changes, embed, pick_interpreter(repo),
                               budget_s=max(0.0, BUDGET_S - (time.monotonic() - t0)))

    found = [format_finding(f) for f in result.findings]
    previous = load_rounds(repo, snap.task_id)["last_findings"]
    resolved = sum(f not in found for f in previous.split("\n\n") if f)
    text = "\n\n".join(found)
    block = should_block(repo, snap.task_id, text) and bool(found)
    stats = {"task_id": snap.task_id, "round": load_rounds(repo, snap.task_id)["round"],
             "new": result.new_count, "judgeable": result.judgeable_count, "findings": len(found),
             "resolved": resolved, "skipped": result.skipped, "seconds": round(time.monotonic() - t0, 2)}
    with open(state_dir(repo) / "stats.jsonl", "a") as f:
        f.write(json.dumps(stats) + "\n")
    if block:
        return {"decision": "block", "reason": text}
    summary = (f"slopgate: {result.new_count} new functions, {result.judgeable_count} judgeable, "
               f"{len(found)} findings, {result.skipped} skipped")
    return {"systemMessage": summary + (" (running unsandboxed)" if sandbox_kind() == "none" else "")}


def check() -> int:
    t0 = time.monotonic()
    try:
        with contextlib.redirect_stdout(sys.stderr):  # stdout carries only the hook JSON
            out = _check(t0)
    except Allow as e:
        out = {"systemMessage": f"slopgate: allowed, {e}"}
    except Exception as e:  # never break the user's session
        out = {"systemMessage": f"slopgate: allowed, internal error: {type(e).__name__}: {e}"}
    print(json.dumps(out))
    return 0


def start() -> int:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            take_snapshot(_repo_from_stdin())
    except Exception:  # never break the user's session; check reports the missing snapshot
        pass
    return 0


def install(repo: Path) -> int:
    path = repo / ".claude" / "settings.json"
    settings = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event, command in HOOKS.items():
        groups = hooks.setdefault(event, [])
        if not any(h.get("command") == command for g in groups for h in g.get("hooks", [])):
            groups.append({"hooks": [{"type": "command", "command": command}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"slopgate: hooks installed in {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slopgate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="UserPromptSubmit hook: snapshot the repo")
    sub.add_parser("check", help="Stop hook: block on proven reimplementations")
    sub.add_parser("install", help="add the hooks to <repo>/.claude/settings.json").add_argument("repo", nargs="?", default=".")
    args = parser.parse_args(argv)
    if args.cmd == "install":
        return install(Path(args.repo))
    return start() if args.cmd == "start" else check()
