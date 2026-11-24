# slopgate

Coding agents duplicate code that already exists. The RepoReuse benchmark found agents leave duplicated logic in 50.8% of task chains ([arXiv 2609.35357](https://arxiv.org/abs/2609.35357)).

slopgate runs as Claude Code hooks. When the agent finishes a turn, it looks for new functions that reimplement an existing function in your repo. It does not trust similarity alone: it runs both functions in a sandbox on generated inputs, and blocks only if they agree on all of them.

## What a finding looks like

Simulated agent edit on a real codebase (a private FastAPI/ML repo, 102 Python files). The repo had no function slopgate could judge, so a `clamp` helper (`src/utils/numeric.py`) was added to a throwaway copy before the simulated agent edit, which then appended a differently written equivalent to `src/decision/capacity_model.py`. Exact Stop hook output:

```json
{"decision": "block", "reason": "src/decision/capacity_model.py:532 bound_utilization reimplements src/utils/numeric.py:4 clamp\n  (identical on 200 generated inputs). Delete bound_utilization (3 lines)\n  and call clamp."}
```

In the intended flow, the reason is fed back to the agent so it can reuse the existing function; no real agent session was run for this example. A first attempt written as `max(floor, min(x, ceiling))` was not flagged: it differs from `clamp` when `floor > ceiling`, and the prover found that.

### Coverage on that repo

Measured on the same repo before the `clamp` helper was added: 1,546 functions in 102 files, 0 judgeable. 1,110 are methods and 361 are async. The remaining 75 plain functions were all rejected: 36 for reading names outside the allowed set (11 use `global`, 5 import, 4 use `argparse`, the rest module state or other modules), 25 for annotations the prover cannot generate (7 are `*args`/`**kwargs`), 7 are decorated, 5 are unannotated, 2 are generators.

Without the planted helper, slopgate would have judged nothing in this repo. That is the current limitation: it only acts on plain, builtin-typed functions, and methods and async functions are the obvious next work.

## Install

```bash
pip install -e .
slopgate install <repo>
```

`slopgate install` adds a `UserPromptSubmit` hook (`slopgate start`, snapshots the repo) and a `Stop` hook (`slopgate check`) to `<repo>/.claude/settings.json`, and does a one-time download of the embedding model. The `slopgate` command must be on the `PATH` Claude Code runs with. Python 3.11+. The repo must be a git repository. If `<repo>/.venv` exists, the functions run on its interpreter, so imports resolve.

## What is judged

A function is only compared if it is simple enough to run safely on generated inputs. Everything else is skipped; the status line reports how many new functions were judgeable.

Judged: module-level, synchronous, non-generator, undecorated functions where every parameter is annotated with a builtin type (`int`, `float`, `str`, `bytes`, `bool`, `list`, `dict`, `tuple`, `set`, `frozenset`, `Optional`, `Union`, `None`, and `X | Y` of these), defaults are constants, there is no `*args`/`**kwargs`, no `global`/`nonlocal`, no nested functions, lambdas or classes, no imports, no dunder attribute access, and the body reads only its parameters, its own locals and a short list of builtins (so no I/O, no module state, no calls to other repo functions).

Not judged: methods, async functions, generators, decorated functions, and anything that fails the list above. Only new functions (added in the turn) are checked, against up to 5 judgeable existing functions with the same required arity, ranked by embedding similarity.

Honest limits: "identical" means identical on the generated inputs (200 in the example), not a formal proof. Generated ints stay within ±10,000 and strings, bytes and collections hold at most 50 items. Arguments are never generated aliased to each other (`a is b`). A new function with a defaulted parameter gets no verdict, because callers may pass values the inputs never cover.

## Sandbox

Both functions run in a child process with network access denied and file writes restricted to a temporary directory (`sandbox-exec` on macOS; on Linux, `bwrap` if it is installed), with a time limit per batch and a cap on output size. On Linux the child's address space is capped at 2 GiB; macOS does not enforce that limit, so there is no memory cap there. If no sandbox is available, slopgate still runs, unsandboxed, and its status line ends with `(running unsandboxed)`. The judgeability rules are a pre-filter, not a security boundary; the OS sandbox is what contains execution.

## Limits of this milestone

Blocks at most twice per task and never on the same findings twice, so it cannot trap the agent. Any internal error allows the stop. Stats go to `.git/slopgate/stats.jsonl`.
