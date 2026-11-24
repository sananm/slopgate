# slopgate

Coding agents duplicate code that already exists. The RepoReuse benchmark found agents leave duplicated logic in 50.8% of task chains ([arXiv 2609.35357](https://arxiv.org/abs/2609.35357)).

slopgate runs as Claude Code hooks. When the agent finishes a turn, it checks the functions the agent added or changed against the rest of the repo, and blocks the stop when one is a copy of existing code, so the agent has to reuse what is already there.

## What it found on a real repo

`slopgate scan` run on `predictive-scaling`, a private FastAPI/ML repo with 102 Python files, at a fixed commit. This is `scan` reporting slop that already existed in the code, not a live agent session, and nothing was planted. Every finding below was checked by hand against both functions.

| Finding | Where |
|---|---|
| `_infer_frequency_minutes` copied 5 times | `src/features/{derivative,fourier,lag}_features.py`, and twice in `rolling_features.py` |
| `rollback` copied (47 and 48 lines, differing only in comments and the docstring) | `src/execution/aws.py`, `src/execution/terraform.py` |
| `stop` copied 3 times | `src/collectors/{business,external,prometheus}.py` |
| `_get_client` copied twice | `src/collectors/{business,external}.py` (the third collector's version takes different arguments and was left out) |
| `within_90_ci` / `within_95_ci` differ only by one number (`0.125` vs `0.25`) | `src/monitoring/accuracy.py` |
| Accessors that only forward to a dict `.get` and are never called: `get_record`, `get_alert`, `get_cached_result` | `src/execution/rollback.py`, `src/monitoring/alerts.py`, `src/monitoring/health.py` |

In total, `scan` reported 4 copy groups, 2 near-copy groups (the second is a pair of test fixtures that differ by one number) and 7 wrapper notes on this repo.

The same scan on two other private repos (55 Python files each) found 2 more copy groups, a model-factory function copied between two packages and an audio-slicing helper copied under a new name, plus 4 wrapper notes. A third model factory that reads a different setting was correctly kept apart.

Hand-check across all three repos (19 findings):

| Kind | Reported | Genuine | Real but intentional | Wrong |
|---|---|---|---|---|
| copy | 6 groups | 6 | 0 | 0 |
| near-copy | 2 groups | 1 | 1 (test fixtures, advisory only) | 0 |
| wrapper note | 11 | 4 | 4 (`forward` on PyTorch modules and an async method a framework calls, so they have callers the name search cannot see) | 3 (each has exactly one call site, but the note says it has none) |

There were no false copy claims. The wrapper rule deliberately allows one call site, but the note's wording ("has no call sites found by name") is wrong in that case. Wrapper notes are advisory and never block.

## The checks

Each function is reduced to a normal form before comparing: the docstring, its name and type annotations are dropped, local variables are renamed in order of first use, and names from outside the function are qualified by where they come from (so `sqrt(x)` and `log(x)`, or two different imported `helper`s, never match). Two functions with the same normal form do the same thing by construction, for methods, async functions and any argument type. Only functions of at least 40 AST nodes are compared, and stubs (`pass`, `...`, `raise NotImplementedError`, `@abstractmethod`) are exempt.

- **Copy (blocks).** A new or changed function whose normal form equals an existing one. The finding names the original and says to import it, move it to a shared helper, or move it into the shared base class.
- **Near-copy (blocks).** A function equal to an existing one except for at most 3 numeric constants, at least one outside `{-1, 0, 1, 2}`, none used as an index. The finding says to add a parameter instead of copying.
- **Reimplementation (blocks).** The M1 check, described below: a differently written function that agrees with an existing one on generated inputs.
- **Wrapper notes (advisory).** A function whose body only passes its parameters unchanged to another call, and whose name appears at most once elsewhere in the repo. Decorated functions, dunders and names in `__all__` are never reported.
- **New-dependency notes (advisory).** New code that imports a third-party package the repo has never imported before.

Copies and near-copies that involve a test file are advisory, never blocks. Advisory findings never block: they are added to the block reason when something else blocks, and shown with the summary line otherwise. Wrapper notes are only raised for functions added in the turn.

`slopgate scan [path]` prints every copy group, near-copy group and wrapper note in an existing repo. It writes nothing and always exits 0.

Not handled: **roundabout logic**, a long solution where a short one would do. Structure cannot judge readability. Copies that differ in more than numeric constants (a changed string, an extra statement) are not caught either.

## Reimplementation (M1)

For differently written functions, slopgate does not trust similarity alone: it runs both functions in a sandbox on generated inputs, and blocks only if they agree on all of them.

### What a finding looks like

Simulated agent edit on a real codebase (a private FastAPI/ML repo, 102 Python files). The repo had no function slopgate could judge, so a `clamp` helper (`src/utils/numeric.py`) was added to a throwaway copy before the simulated agent edit, which then appended a differently written equivalent to `src/decision/capacity_model.py`. Exact Stop hook output:

```json
{"decision": "block", "reason": "src/decision/capacity_model.py:532 bound_utilization reimplements src/utils/numeric.py:4 clamp\n  (identical on 200 generated inputs). Delete bound_utilization (3 lines)\n  and call clamp."}
```

In the intended flow, the reason is fed back to the agent so it can reuse the existing function; no real agent session was run for this example. A first attempt written as `max(floor, min(x, ceiling))` was not flagged: it differs from `clamp` when `floor > ceiling`, and the prover found that.

#### Coverage on that repo

Measured on the same repo before the `clamp` helper was added: 1,546 functions in 102 files, 0 judgeable. 1,110 are methods and 361 are async. The remaining 75 plain functions were all rejected: 36 for reading names outside the allowed set (11 use `global`, 5 import, 4 use `argparse`, the rest module state or other modules), 25 for annotations the prover cannot generate (7 are `*args`/`**kwargs`), 7 are decorated, 5 are unannotated, 2 are generators.

Without the planted helper, slopgate would have judged nothing in this repo. That is the current limitation: it only acts on plain, builtin-typed functions, and methods and async functions are the obvious next work.

## Install

```bash
pip install -e .
slopgate install <repo>
```

`slopgate install` adds a `UserPromptSubmit` hook (`slopgate start`, snapshots the repo) and a `Stop` hook (`slopgate check`) to `<repo>/.claude/settings.json`, and does a one-time download of the embedding model. The `slopgate` command must be on the `PATH` Claude Code runs with. Python 3.11+. The repo must be a git repository. If `<repo>/.venv` exists, the functions run on its interpreter, so imports resolve.

## What the reimplementation check judges

A function is only compared if it is simple enough to run safely on generated inputs. Everything else is skipped; the status line reports how many new functions were judgeable.

Judged: module-level, synchronous, non-generator, undecorated functions where every parameter is annotated with a builtin type (`int`, `float`, `str`, `bytes`, `bool`, `list`, `dict`, `tuple`, `set`, `frozenset`, `Optional`, `Union`, `None`, and `X | Y` of these), defaults are constants, there is no `*args`/`**kwargs`, no `global`/`nonlocal`, no nested functions, lambdas or classes, no imports, no dunder attribute access, and the body reads only its parameters, its own locals and a short list of builtins (so no I/O, no module state, no calls to other repo functions).

Not judged: methods, async functions, generators, decorated functions, and anything that fails the list above. Only new functions (added in the turn) are checked, against up to 5 judgeable existing functions with the same required arity, ranked by embedding similarity.

Honest limits: "identical" means identical on the generated inputs (200 in the example), not a formal proof. Generated ints stay within ±10,000 and strings, bytes and collections hold at most 50 items. Arguments are never generated aliased to each other (`a is b`). A new function with a defaulted parameter gets no verdict, because callers may pass values the inputs never cover.

## Sandbox

Both functions run in a child process with network access denied and file writes restricted to a temporary directory (`sandbox-exec` on macOS; on Linux, `bwrap` if it is installed), with a time limit per batch and a cap on output size. On Linux the child's address space is capped at 2 GiB; macOS does not enforce that limit, so there is no memory cap there. If no sandbox is available, slopgate still runs, unsandboxed, and its status line ends with `(running unsandboxed)`. The judgeability rules are a pre-filter, not a security boundary; the OS sandbox is what contains execution.

## Limits of this milestone

Blocks at most twice per task and never on the same findings twice, so it cannot trap the agent. Any internal error allows the stop. Stats go to `.git/slopgate/stats.jsonl`.
