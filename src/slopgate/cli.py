import argparse
import contextlib
import dataclasses
import functools
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from slopgate.changes import Changes, classify
from slopgate.clones import clone_groups, clone_key, find_clones, format_clone
from slopgate.deps import find_new_deps, format_new_dep
from slopgate.index import FunctionInfo, index_repo
from slopgate.judge_model import judge
from slopgate.reinvention import Embed, cached_embeddings, default_embed, find_reinventions, format_finding
from slopgate.rounds import asked, load_rounds, record_asked, should_block
from slopgate.sandbox import pick_interpreter, sandbox_kind
from slopgate.semantic import Question, find_questions, format_question, question_key, semantic_candidates, semantic_pool
from slopgate.snapshot import _read_py_files, changed_py_files, load_snapshot, state_dir, take_snapshot
from slopgate.structure import Shape, index_shapes, is_test_path, shapes_of
from slopgate.wrappers import find_wrappers, format_wrapper

BUDGET_S = 20.0
MAX_HOOK_EMBEDS = 16  # uncached pool functions embedded per Stop; keeps M3 within its 5 s worst case
SHAPES_VERSION = 1  # bump when shapes_of output changes, so cached shapes are recomputed
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


def _shape_from(d) -> Shape:
    if not (isinstance(d["bases"], list) and isinstance(d["consts"], list) and all(isinstance(c, list) for c in d["consts"])):
        raise ValueError("bad cached shape")
    s = Shape(**{**d, "bases": tuple(d["bases"]), "consts": tuple((v, f) for v, f in d["consts"])})
    if not (all(isinstance(x, str) for x in (s.path, s.qualname, s.module, s.exact_key, s.near_key, *s.bases))
            and type(s.line) is int and type(s.end_line) is int and (s.cls is None or isinstance(s.cls, str))
            and type(s.comparable) is bool and all(type(v) in (int, float) and type(f) is bool for v, f in s.consts)):
        raise ValueError("bad cached shape")
    return s


def _shape_sets(repo: Path, *text_sets: dict[str, str]) -> list[list[Shape]]:
    """index_shapes for each text set, computing each distinct (path, text) once and caching across checks."""
    path = state_dir(repo) / "shapes.json"
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError, RecursionError):  # untrusted on disk: ignore and rebuild (rewritten below)
        cache = {}
    cache = cache if isinstance(cache, dict) else {}
    memo: dict[str, list[Shape]] = {}
    keep: dict[str, list] = {}  # only entries used now, so the file never grows past the repo

    def get(p: str, t: str) -> list[Shape]:
        k = hashlib.sha256(f"{SHAPES_VERSION}\0{p}\0{t}".encode()).hexdigest()  # a Shape carries its path and module, so both key it
        if k not in memo:
            try:
                memo[k], keep[k] = [_shape_from(d) for d in cache[k]], cache[k]
            except (KeyError, TypeError, ValueError, AttributeError):
                memo[k] = shapes_of(p, t)
                if not any(isinstance(v, complex) for s in memo[k] for v, _ in s.consts):  # JSON has no complex
                    keep[k] = [dataclasses.asdict(s) for s in memo[k]]
        return memo[k]
    out = [[s for p, t in texts.items() if p.endswith(".py") for s in get(p, t)] for texts in text_sets]
    if keep != cache:
        try:
            path.write_text(json.dumps(keep))
        except OSError:
            pass
    return out


def _questions(repo: Path, task_id: str, changes: Changes, snap_index: list[FunctionInfo], before: list[Shape], after: list[Shape],
               skip: set[tuple[str, str]], embed: Embed) -> tuple[list[Question], int]:
    """M3: new functions that strongly resemble an existing one, minus those already asked this task or judged noise."""
    cands = semantic_candidates(changes.new, after, skip)
    old = {(f.path, f.qualname): f for f in snap_index}
    pool = {(f.path, f.qualname): f for f in changes.existing}
    for f in changes.changed:  # compared as it was before the agent touched it
        if (f.path, f.qualname) in old:
            pool.setdefault((f.path, f.qualname), old[f.path, f.qualname])
    own = {(c.path, c.qualname) for c in cands}
    pool = sorted((f for f in semantic_pool(list(pool.values())) if (f.path, f.qualname) not in own),
                  key=lambda f: (f.path, f.qualname))
    if not cands or not pool:
        return [], 0
    vecs, left = cached_embeddings(repo, [c.source for c in cands], [p.source for p in pool], embed, MAX_HOOK_EMBEDS)
    done = asked(repo, task_id)
    qs = find_questions(cands, pool, vecs, {(s.path, s.qualname): s.exact_key for s in before},
                        {(f.path, f.qualname): f for f in changes.changed})
    return judge(repo, [q for q in qs if question_key(q) not in done]), left


def _check(t0: float) -> dict:
    repo = _repo_from_stdin()
    snap = load_snapshot(repo)
    if snap is None:
        raise Allow("no snapshot")
    changed = changed_py_files(repo, snap)
    now = {p: t for p, t in snap.files.items() if p not in changed}  # unchanged since the snapshot
    now.update({p: (repo / p).read_text(encoding="utf-8", errors="replace") for p in changed if (repo / p).is_file()})
    snap_index = index_repo(repo, {p: snap.files[p] for p in changed if p in snap.files})
    changes = classify(snap_index, index_repo(repo, {p: now[p] for p in changed if p in now}))
    changes.existing += index_repo(repo, {p: t for p, t in now.items() if p not in changed})

    before, after = _shape_sets(repo, snap.files, now)
    new_ids = {(f.path, f.qualname) for f in changes.new}
    touched = new_ids | {(f.path, f.qualname) for f in changes.changed}
    clones = find_clones([s for s in after if (s.path, s.qualname) in touched], after, before)
    copied = {(c.new.path, c.new.qualname) for c in clones if c.kind == "copy"}
    new_count = len(changes.new)
    changes.new = [f for f in changes.new if (f.path, f.qualname) not in copied]  # proven structurally: skip the prover
    wrappers = find_wrappers(new_ids, now) if new_ids else []
    deps = find_new_deps({p for p in changed if p in now}, now, snap.files) if changed else []

    model = functools.cache(default_embed)  # loaded on the first embedding actually needed, if any
    embed = lambda texts: model()(texts)  # noqa: E731
    try:
        questions, left = _questions(repo, snap.task_id, changes, snap_index, before, after,
                                     {(c.new.path, c.new.qualname) for c in clones}, embed)
        sem_error = ""
    except Exception as e:  # M3 must never break the check: no questions
        questions, left, sem_error = [], 0, f", semantic check skipped: {type(e).__name__}"
    result = find_reinventions(repo, changes, embed, pick_interpreter(repo),
                               budget_s=max(0.0, BUDGET_S - (time.monotonic() - t0)))

    proven = {(f.new.path, f.new.qualname) for f in result.findings}
    questions = [q for q in questions if (q.new.path, q.new.qualname) not in proven]
    blocking = [c for c in clones if c.blocking]
    found = [format_clone(c) for c in blocking] + [format_finding(f) for f in result.findings]
    asks = [format_question(q) for q in questions]
    notes = ([format_clone(c) for c in clones if not c.blocking] + [format_wrapper(w) for w in wrappers]
             + [format_new_dep(d) for d in deps])
    # identity without line numbers, so edits above a finding don't make it look new
    keys = sorted({clone_key(c) for c in blocking}
                  | {f"reinvention\t{f.new.path}\t{f.new.qualname}\t{f.existing.path}\t{f.existing.qualname}" for f in result.findings}
                  | {f"question\t{q.new.path}\t{q.new.qualname}\t{question_key(q)}" for q in questions})
    previous = load_rounds(repo, snap.task_id)["last_findings"]
    resolved = sum(k not in keys for k in previous.split("\n") if k and not k.startswith("question\t"))  # a question is never "resolved"
    block = should_block(repo, snap.task_id, "\n".join(keys)) and bool(found + asks)
    if block:
        record_asked(repo, snap.task_id, [question_key(q) for q in questions])
        found += asks
    else:
        notes = [f"note: {a}" for a in asks] + notes
    text = "\n\n".join(found + notes)
    copies, near = (sum(c.kind == k for c in clones) for k in ("copy", "near"))
    stats = {"task_id": snap.task_id, "round": load_rounds(repo, snap.task_id)["round"],
             "new": new_count, "judgeable": result.judgeable_count, "findings": len(result.findings),
             "resolved": resolved, "skipped": result.skipped, "copies": copies, "near": near,
             "questions": len(questions), "left_out": left, "wrapper_notes": len(wrappers), "new_deps": len(deps),
             "seconds": round(time.monotonic() - t0, 2)}
    with open(state_dir(repo) / "stats.jsonl", "a") as f:
        f.write(json.dumps(stats) + "\n")
    summary = (f"slopgate: {copies} copies, {near} near-copies, {len(questions)} questions, {len(wrappers)} wrapper notes, "
               f"{len(deps)} new dependencies, "
               f"{new_count} new functions, {result.judgeable_count} judgeable, "
               f"{len(result.findings)} findings, {result.skipped} skipped"
               + (f" ({left} functions not yet embedded)" if left else "") + sem_error
               + (" (running unsandboxed)" if sandbox_kind() == "none" else ""))
    if block:
        return {"decision": "block", "reason": text, "systemMessage": summary}
    return {"systemMessage": "\n\n".join([summary, *found, *notes])}


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


def scan(path: Path) -> int:
    try:
        texts = _read_py_files(path)
    except (subprocess.CalledProcessError, OSError):
        print(f"slopgate: {path} is not a git repo")
        return 0
    shapes = index_shapes(texts)
    groups = sorted(clone_groups(shapes), key=lambda g: all(is_test_path(s.path) for s in g))  # stable: src groups first
    for g in groups:
        print("copy:" if len({s.exact_key for s in g}) == 1 else "near:")
        for s in g:
            print(f"  {s.path}:{s.line} {s.qualname}")
    for w in find_wrappers({(s.path, s.qualname) for s in shapes}, texts):
        print(format_wrapper(w))
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
    print("slopgate: loading the embedding model (first run downloads it)...", file=sys.stderr)
    try:
        model = default_embed()  # warm the cache now so the first Stop hook doesn't stall on a download
    except Exception as e:
        print(f"slopgate: model warm-up failed, the first check will retry: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    try:
        warm_embeddings(repo, model)
    except Exception as e:
        print(f"slopgate: pre-embedding failed, checks will embed as they go: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


def warm_embeddings(repo: Path, embed: Embed) -> int:
    """Fill the embedding cache with the semantic pool, so the Stop hook rarely embeds existing code."""
    sources = [f.source for f in semantic_pool(index_repo(repo, _read_py_files(repo)))]
    print(f"slopgate: embedding {len(sources)} functions (one-time, can take a few minutes)...", file=sys.stderr)
    cached_embeddings(repo, [], sources, embed, None)
    return len(sources)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slopgate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="UserPromptSubmit hook: snapshot the repo")
    sub.add_parser("check", help="Stop hook: block on proven reimplementations")
    sub.add_parser("install", help="add the hooks to <repo>/.claude/settings.json").add_argument("repo", nargs="?", default=".")
    sub.add_parser("scan", help="print existing copies, near-copies and wrapper notes").add_argument("path", nargs="?", default=".")
    args = parser.parse_args(argv)
    if args.cmd == "install":
        return install(Path(args.repo))
    if args.cmd == "scan":
        return scan(Path(args.path))
    return start() if args.cmd == "start" else check()
