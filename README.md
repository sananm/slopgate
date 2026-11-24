# slopgate

Coding agents duplicate code that already exists. The RepoReuse benchmark found agents leave duplicated logic in 50.8% of task chains ([arXiv 2609.35357](https://arxiv.org/abs/2609.35357)).

slopgate runs as Claude Code hooks. When the agent finishes a turn, it looks for new functions that reimplement an existing function in your repo. It does not trust similarity alone: it runs both functions in a sandbox on generated inputs, and blocks only if they agree on all of them.

## What a finding looks like

Simulated agent edit on a real codebase (a private FastAPI/ML repo, 102 Python files). `clamp` already existed in `src/utils/numeric.py`; the edit appended a differently written equivalent to `src/decision/capacity_model.py`. Exact Stop hook output:

```json
{"decision": "block", "reason": "src/decision/capacity_model.py:532 bound_utilization reimplements src/utils/numeric.py:4 clamp\n  (identical on 200 generated inputs). Delete bound_utilization (3 lines)\n  and call clamp."}
```

Claude Code feeds `reason` back to the agent, which then deletes the duplicate and calls the existing function. A first attempt written as `max(floor, min(x, ceiling))` was not flagged: it differs from `clamp` when `floor > ceiling`, and the prover found that.

## Install

```bash
pip install -e .
slopgate install <repo>
```

`slopgate install` adds a `UserPromptSubmit` hook (`slopgate start`, snapshots the repo) and a `Stop` hook (`slopgate check`) to `<repo>/.claude/settings.json`, and downloads the embedding model (a few hundred MB) once. The `slopgate` command must be on the `PATH` Claude Code runs with. Python 3.11+. The repo must be a git repository. If `<repo>/.venv` exists, the functions run on its interpreter, so imports resolve.

## What is judged

A function is only compared if it is simple enough to run safely on generated inputs. Everything else is skipped silently.

Judged: module-level, synchronous, non-generator, undecorated functions where every parameter is annotated with a builtin type (`int`, `float`, `str`, `bytes`, `bool`, `list`, `dict`, `tuple`, `set`, `frozenset`, `Optional`, `Union`, `None`, and `X | Y` of these), defaults are constants, there is no `*args`/`**kwargs`, no `global`/`nonlocal`, no nested functions, lambdas or classes, no imports, no dunder attribute access, and the body reads only its parameters, its own locals and a short list of builtins (so no I/O, no module state, no calls to other repo functions).

Not judged: methods, async functions, generators, decorated functions, and anything that fails the list above. Only functions added or changed in the turn are checked, against up to 5 judgeable existing functions with the same required arity, ranked by embedding similarity.

Honest limit: "identical" means identical on the generated inputs (200 in the example), not a formal proof.

On the private repo above, 0 of 1,546 functions were judgeable (1,110 methods, 361 async); slopgate can only act on code written in the style it judges.

## Sandbox

Both functions run in a child process with network access denied and file writes restricted to a temporary directory (`sandbox-exec` on macOS, `bwrap` on Linux), with a time limit per batch and a cap on output size. If neither is available, slopgate still runs, unsandboxed, and says so in its status line. Only code that passes the checks above is ever executed.

## Limits of this milestone

Blocks at most twice per task and never on the same findings twice, so it cannot trap the agent. Any internal error allows the stop. Stats go to `.git/slopgate/stats.jsonl`.
