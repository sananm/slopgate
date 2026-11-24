# slopgate: catch reinvented and bloated code before an agent finishes

**Date:** 2026-11-24
**Status:** Draft for approval
**Name:** `slopgate` is a working name only; naming is open.

---

## 1. Intent and why

Coding agents produce two kinds of slop that tests never catch:

1. **Reinvention.** The agent writes logic that already exists elsewhere in the
   same codebase, because it never looked. RepoReuse (arXiv 2609.35357, Sept
   2026) audited 3,000 agent turns on real repositories: by turn 5, agents left
   duplicated logic in **50.8%** of task chains, "progressively stop exploring
   relevant repository code", and pass rates barely moved.
2. **Bloat.** The agent writes a long, roundabout solution where an experienced
   engineer would write a short, direct one.

slopgate runs when the agent says it is done, checks only what the task
changed, and hands the agent proven findings to fix before a human sees the
code.

**Why "just index the codebase" is not enough:** agents already have grep and a
full workspace and still do not look (RepoReuse). The index must be consulted
*for* the agent, and the result *pushed* to it.

**Why this person is building it:** an engineering portfolio project on a pain
he hits daily (he codes in Python with Claude Code), with enough depth to
support a paper later. RepoReuse gives a public yardstick for that.

---

## 2. Goals

1. **Reinvention check.** For every new function in the task's diff, find an
   existing repo function with the same behavior, prove it by running both on
   generated inputs, and tell the agent to delete the new one and call the
   existing one.
2. **Shrink check.** For every new or changed function over the size threshold,
   ask the agent for the shortest clear rewrite with identical behavior, and
   accept it only if behavior is verified unchanged.
3. **Wrapper facts.** Report structural bloat that is provable from the code:
   pass-through wrappers, single-implementation abstractions introduced in the
   diff, and new parameters every caller passes the same literal.
4. **Runs in the agent loop**, as a Claude Code Stop hook, through a plain CLI
   that any other agent hook or CI job could also call.
5. **Measurable.** Duplication rate with and without slopgate, and lines removed
   per task with behavior verified.

## 3. Non-goals

- **Not a pattern linter.** aislop (772 stars) already covers dead code,
  swallowed errors, copy-paste blocks and similar patterns. slopgate does only
  what requires proof: behavior equivalence and repo-wide facts.
- **Not a PR/CI bot in v1.** The CLI makes that possible later.
- **Python only.**
- **No separate proposer model.** The agent writes its own shorter version;
  slopgate detects and verifies.
- **No whole-repo audit.** Only what the current task changed.

## 3a. Anti-goals

- **Crying wolf.** Anything reported as a fact must be proven. Unprovable
  candidates stay silent. One wrong claim and users uninstall it.
- **Breaking behavior.** A simplification that changes behavior must never be
  accepted. (A 2026 FSE study, arXiv 2602.15761, found ~26% of LLM
  simplifications are not equivalent, and over 21% of broken refactorings pass
  existing tests.)
- **Trapping the agent.** The gate must never block forever.
- **Damaging the user's machine.** Executing repo functions on generated inputs
  must not delete files, hit networks or touch databases.
- **Being slow enough to disable.** See the latency budget in §6.7.

## 4. Constraints

- Python 3.11+. Analyzes Python repos.
- Runs locally. No code leaves the machine, including for embeddings.
- Must run on a laptop CPU (no GPU requirement).
- Integration target: Claude Code hooks. Behavior to rely on, to be confirmed
  against current docs before building: a Stop hook can block the agent from
  stopping by returning a reason that is fed back to the agent, and the hook
  input says whether the agent is already continuing because of a Stop hook.

---

## 5. Approaches considered

**A. Gate when the agent finishes (chosen).** One run per task, on the finished
change. Can afford seconds of verification. The agent does all rewrites.

**B. Check after every edit (rejected).** Mid-task, a helper the agent just
created always has zero callers, so most findings would be false alarms. Too
slow for verification on every edit.

**C. Separate reviewer model proposes rewrites (rejected for v1).** More moving
parts and latency. The verifier makes the agent-as-proposer safe; if measured
savings are poor, a separate proposer can be added later.

**D. Search tool the agent can call (rejected as the core).** This is "just
index the codebase". RepoReuse shows agents stop exploring even with the code
in front of them, so a tool they must remember to call does not fix the
problem.

Decided by Sanan in conversation: approach A, Python, the agent proposes its
own rewrites.

---

## 6. Design

### 6.1 Flow

```
UserPromptSubmit hook → record task start (git HEAD + working-tree snapshot)
...agent works...
Stop hook → slopgate check
   1. diff: everything changed since task start
   2. index: refresh the function index for changed files
   3. checks: reinvention, shrink, wrapper facts
   4. findings? → block stop, feed findings to the agent
      none?     → allow stop, print a one-line summary for the human
```

### 6.2 Task start and diff

The diff is the working tree versus the snapshot taken at task start, so
commits the agent makes mid-task are still included. The snapshot is the HEAD
commit plus copies of files that were already dirty. State lives under
`.git/slopgate/`, never in the working tree.

**New versus changed** is decided by qualified name: a name absent from the
snapshot is *new*; a name present in both with a different body is *changed*.
A "new" function whose normalized AST body already existed anywhere in the
snapshot is a **move or rename**, not new code, and is never a reinvention
candidate. Candidates deleted by the diff are never reported as "existing".

Installation: `slopgate install` adds the two hooks to the project's
`.claude/settings.json`, merging with what is there rather than overwriting.

### 6.3 The index

Built from `git ls-files '*.py'` with stdlib `ast`. Per module-level function
and method: qualified name, file and line, parameters with annotations,
docstring, body line count, and an embedding of the source text from a small
local embedding model. Cached per file content hash so only changed files are
re-indexed.

Name resolution for callers and implementations (wrapper facts) uses `jedi`,
queried only for the symbols in the diff.

### 6.4 Reinvention check

For each new function `f` in the diff:

1. **Retrieve.** Top-k existing functions by embedding similarity, filtered to
   compatible signatures (same number of required parameters).
2. **Prove.** Run `f` and each candidate `g` on the same generated inputs (§6.6).
   Equivalent only if every input gives equal return values, or the same
   exception type, and enough inputs ran without raising.
3. **Report** proven matches only:

```
api/views.py:41 fmt_date reimplements utils/dates.py:12 to_iso
  (identical on 200 generated inputs). Delete fmt_date (38 lines)
  and call to_iso.
```

### 6.5 Shrink check

For each new or changed function whose body exceeds the size threshold **and
that the prover can judge** (§6.6):

1. Save its current source under `.git/slopgate/` as the reference.
2. Ask the agent: rewrite this function in place as the shortest clear version
   with identical behavior and the same signature, or say it is already minimal.
3. On the next Stop, compare the in-place rewrite with the reference using the
   prover only.
4. Fewer AST nodes and equivalent: accepted. Not equivalent: block with the
   counterexample input and tell the agent to restore the reference. Unchanged
   (the agent judged it minimal): accepted.

Functions the prover cannot judge are never sent for shrinking: a rewrite
nobody can verify is exactly the ~26% breakage risk this tool exists to stop.
"Shorter" is measured in AST nodes, because line count is trivially gamed by
joining lines; humans are shown line counts. The repo's tests are not part of
acceptance in v1: they cost latency, and the FSE study shows they overestimate
equivalence.

Each function gets at most one shrink request per task.

### 6.6 The prover (differential execution)

- **Inputs** come from Hypothesis strategies built from type annotations
  (`st.from_type`). 200 inputs per pair.
- **Equivalent** means, for every input: both raise the same exception type, or
  both return values that are equal with the same type (floats compared
  exactly, with NaN equal to NaN; no tolerance, because tolerance admits real
  differences), **and** the arguments are equal after the call (each run gets a
  fresh deep copy, so mutation counts as behavior). At least 50 inputs must
  return normally in both, or there is no verdict. Each function also runs
  twice on a sample of inputs; any self-disagreement (nondeterminism) means no
  verdict.
- **Environment:** the repo's own interpreter (`.venv`, `$VIRTUAL_ENV`, else
  `python3`), so the repo's dependencies import normally. A module that fails to
  import gets no verdict and counts against coverage.
- **Isolation, in layers:** the AST exclusions below; a subprocess per run with
  a timeout and a throwaway working directory; an OS sandbox denying network
  access and file writes outside that directory (`sandbox-exec` on macOS,
  `bwrap` on Linux when installed). Without an OS sandbox, slopgate says it is
  running unsandboxed rather than implying a guarantee it does not have.
  Module import side effects run inside the same sandbox.
- **Out of scope (no verdict, stay silent):** functions without usable
  annotations; functions whose AST calls I/O, network, subprocess, database or
  environment APIs; methods that need instance state; async functions.
- **Identity mapping only** between parameters in v1 (same order). Pairs that
  would need reordering are missed, and counted.

This is the safety-critical component: generated inputs must never cause side
effects on the user's machine (anti-goal), and a false "equivalent" breaks the
user's code. It gets the most tests.

### 6.7 Wrapper facts

Reported only for symbols introduced or changed in the diff:

- **Pass-through:** the body is a single call forwarding its parameters
  unchanged, and the function has at most one caller.
- **Single implementation:** a base class or `Protocol` added in the diff with
  exactly one implementation.
- **Constant parameter:** a parameter added in the diff that every call site
  passes the same literal.

Wrapper facts are **advisory, never blocking**. A one-caller delegation can be
a legitimate adapter or API shim, so it is a fact worth showing, not a defect
worth stopping for. They ride along in the block reason when something else
blocks, and otherwise go in the human summary. Symbols listed in `__all__` are
excluded as public API.

### 6.8 Feedback and the loop guard

Findings go back to the agent as one block reason, each with file and line,
the proof, and one instruction. All finding types go out **together** in a
round; they never compete for separate rounds.

- **Round 1:** every reinvention finding and every shrink request.
- **Round 2:** verify the shrink rewrites; block only on counterexamples, with
  the instruction to restore the reference.
- **Round 3 never blocks:** slopgate allows the stop and prints anything still
  unresolved for the human.

Separately, if a round's findings are identical to the previous round's (no
progress), slopgate stops blocking immediately.

### 6.9 Latency budget

Target: under 20 seconds for a typical task (my call; to be measured).
Retrieval and facts must be fast. The prover gets the remaining budget and
skips candidates it cannot finish, reporting how many it skipped.

---

## 7. Evaluation

1. **RepoReuse:** duplication and reuse rates for an agent with and without
   slopgate. If the benchmark is not public, rebuild a small version of its
   protocol on a few open repos.
2. **Dogfooding** on Sanan's own repos: findings per task, how many the agent
   applied, lines removed, and findings the agent rejected as wrong (the false
   alarm rate).
3. **Prover precision:** hand-labeled equivalent and non-equivalent function
   pairs. Target: zero false "equivalent" verdicts.
4. **Coverage:** the share of new functions the prover can judge at all.
5. **Latency** per Stop.

**Go/no-go after M1** (provisional bar, to be revisited with real numbers): continue
to M2 if, on dogfooding, the prover can judge at least 30% of new functions and
the reinvention check produces at least one finding the agent applies per ten
tasks. Below that, the coverage problem comes before any new feature.

## 8. Milestones

| # | Contents |
|---|----------|
| M1 | Task snapshot, index, prover, reinvention check, Stop hook wiring. Dogfoodable. |
| M2 | Shrink check with reference snapshots and verification. |
| M3 | Wrapper facts, evaluation (§7), README led by measured numbers. |

## 9. Risks

- **Coverage.** Much real code does I/O or needs object state, so the prover
  may judge only a minority of functions. Measure in M1 before investing
  further.
- **Same agent, same habits.** The agent that wrote the bloat writes the
  shorter version. Measure lines saved; add a separate proposer only if needed.
- **Hook behavior** may differ from §4's description or change. Confirm first.
- **Embedding model weight** on a laptop. Pick something small.

## 10. Left to the builder

- Embedding model and retrieval k.
- The size threshold for shrink candidates.
- Sandbox mechanism details.
- Index cache format.
- Test-command detection.
- The final name.
