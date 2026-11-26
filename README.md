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

In total, `scan` reported 4 copy groups, 2 near-copy groups (the second is a pair of test fixtures that differ by one number) and 5 wrapper notes on this repo.

The same scan on two other private repos (55 Python files each) found 2 more copy groups, a model-factory function copied between two packages and an audio-slicing helper copied under a new name, plus 1 wrapper note. A third model factory that reads a different setting was correctly kept apart.

Hand-check across all three repos (14 findings, a small sample):

| Kind | Reported | Genuine | Real but intentional | Wrong |
|---|---|---|---|---|
| copy | 6 groups | 6 | 0 | 0 |
| near-copy | 2 groups | 1 | 1 (test fixtures, advisory only) | 0 |
| wrapper note | 6 | 5 | 1 (a FastAPI dependency function, used once in `Depends(...)`) | 0 |

There were no false copy or near-copy claims, and those rules were not tuned on these repos. The wrapper rule was: a first run gave 11 wrapper notes, 3 with wording that was wrong and 4 on framework methods, and the note text and the subclass rule below were changed after seeing them. So the wrapper figure is in-sample. Methods of classes that have a base class are not given wrapper notes, because a framework may call them where a name search cannot see it (`nn.Module.forward`, for example). That skips 4 framework methods in these repos, but it also drops one genuine forwarder in a repository subclass. Wrapper notes are advisory and never block.

## The checks

Each function is reduced to a normal form before comparing: the docstring, its name and parameter and return annotations are dropped, local variables are renamed in order of first use, and names from outside the function are qualified by where they come from (so `sqrt(x)` and `log(x)`, or two different imported `helper`s, never match). Two functions with the same normal form are the same code up to renaming of local variables, for methods, async functions and any argument type. Only functions of at least 40 AST nodes are compared, and stubs (`pass`, `...`, `raise NotImplementedError`, `@abstractmethod`) are exempt.

- **Copy (blocks).** A new or changed function whose normal form equals an existing one. The finding names the original and says to import it, move it to a shared helper, or move it into the shared base class.
- **Near-copy (blocks).** A function equal to an existing one except for at most 3 numeric constants, at least one outside `{-1, 0, 1, 2}`, none used as an index. The finding says to add a parameter instead of copying.
- **Reimplementation (blocks).** The M1 check, described below: a differently written function that agrees with an existing one on generated inputs.
- **Wrapper notes (advisory).** A function whose body only passes its parameters unchanged to another call, and that has at most one call site found by name, since a single-use wrapper should be inlined into its caller. Methods of classes with a base other than `object`, decorated functions, dunders and names in `__all__` are never reported.
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

## Semantic questions (M3)

The common case is neither a copy nor provably the same function: the agent writes the same idea again in a different way. An earlier live run on `predictive-scaling` showed this. The agent wrote `_infer_frequency_seconds` while `_infer_frequency_minutes` already existed in five places, and slopgate let it through. M3 does not claim these are duplicates. It blocks once with a question, and the agent decides.

### What triggers a question

All of these must hold:

1. **A new function.** It is not in a test file, not a dunder, at least 40 AST nodes, not an override of a method from a base class in the repo, and has no copy, near-copy or proven finding already.
2. **A close match.** Its embedding scores at least **0.80** cosine similarity against a function in the repo as it was when the task started.
3. **Not a known look-alike.** The pair passes four filters: not the same method name on a different class (parallel implementations), not caller and callee, no `mock` or `fake` in either path, and not two methods of the same class.
4. **The judge agrees.** A model judges the pair (see below) and must answer that the new function re-implements logic that already exists: it should call the existing function, or the two should share a helper. Up to 3 pairs per check are judged, highest score first, in parallel, within 20 s.

Each existing function is asked about at most once per task, and questions count toward the round limit. The question names the existing function and the number of exact copies of it:

```
question: src/features/volatility_features.py:91 VolatilityFeatureExtractor._infer_frequency_seconds
  looks like src/features/derivative_features.py:182 DerivativeFeatureExtractor._infer_frequency_minutes (and 4 copies of it).
  Reuse it or share a helper with it, or reply with why the new one is different.
```

It is a question, not a finding. A reasoned "they differ because..." is an accepted answer, and a question the agent answered is not reported as unresolved.

### Privacy: the judge runs at Google

The judge is Gemini 4 Hydrogen, called through the `agy` CLI. **It sends the source, paths and names of both functions to Google.** Everything else in slopgate, embeddings included, runs on the machine. `agy` runs from an empty temporary directory, so it sees only the prompt. Verdicts are cached in `.git/slopgate/verdicts.json`, so the same pair is not sent twice.

If `agy` is not installed, or the call times out, fails or returns anything but a clean "reuse", no question is asked. **Without `agy`, the semantic check stays silent.**

### How the cutoff was chosen

The replay treated every eligible function in three repos as new and the rest of its repo as the pool: `predictive-scaling` and the two other private repos from the scan above, 795 functions in all. Pairs were labeled by hand as worth asking or not, by a single labeler, under the rule above.

**The cutoff was chosen on these same repos, so these numbers are in-sample.**

| Stage | At cutoff | Functions asked | Sampled questions worth asking |
|---|---|---|---|
| Similarity only | 0.84 | 16.0% | 0 of 10 |
| + pair filters | 0.84 | 2.3% | 2 of 10 |
| + filters and judge | 0.80 (shipped) | 17 of 795 (2.1%) | 15 of 17 (88%) |

The targets were at most 2% of functions asked and at least 80% worth asking. 0.80 asks 2.1%. 0.82 would ask 1.3% at 80% (8 of 10), so it sits exactly on the precision target with fewer samples. At 0.80, every pair the judge passed was labeled.

Per repo at 0.80:

| Repo | Eligible functions | Asked | Worth asking |
|---|---|---|---|
| `predictive-scaling` | 574 | 12 (2.1%) | 10 of 12 |
| second repo | 122 | 2 (1.6%) | 2 of 2 |
| third repo | 99 | 3 (3.0%) | 3 of 3 |

Each duplicate is counted once from each side, so the 17 questions cover fewer distinct situations. The judge agreed with the hand labels on 66 of 74 labeled pairs (89%), over 179 calls with no failures. Embedding scores depend on the model version, so the cutoff belongs to the current embedding model.

With a warm cache, the similarity step adds 0.34 s to a Stop on `predictive-scaling`. A judge call takes about 6 s wall-clock, and only pairs that pass the cutoff and the filters are judged.

### Live re-run

The volatility task was re-run headless on a fresh clone of `predictive-scaling` with M3 installed (`claude -p`, `--permission-mode acceptEdits`). The earlier live run had the author's global Claude Code plugins loaded, including one that pushes the agent toward reuse, which confounded it. This run used `--setting-sources project,local`, so only the repo's own settings and hooks were loaded.

- **Round 1:** the agent added a `VolatilityFeatureExtractor` with 10 new functions, including `_infer_frequency_seconds`. The Stop hook asked the question shown above and blocked, taking 9.8 s, most of it the uncached judge call.
- **The agent's answer:** it kept a separate function and said why. The existing helpers truncate the sampling interval to whole minutes, so 30-second data is read as 1-minute data and a "15-minute" window covers 30 minutes. It moved the new helper into the shared `BaseExtractor` instead of keeping it private, and added a 30-second test case. It left the five existing copies unchanged, because switching them would change their features. A reviewer would accept that reason.
- **Round 2:** no new questions, and the stop was allowed after 2.4 s.

Three ordinary small tasks were then run the same way on fresh clones: an alert summary method, an sMAPE accuracy metric, and a cooldown-remaining method on the decision engine. None of them got a question, and no pair reached the judge. Their Stop hooks took 3.6 s, 3.6 s and 2.9 s. In all four runs, the agent's attempts to run the tests were outside the allowed commands, so its tests were not run.

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
