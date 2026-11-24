# slopgate M2: structural slop detection

**Date:** 2026-11-24
**Status:** Draft for approval
**Builds on:** `2026-11-24-slopgate-design.md` (M1, merged)

---

## 1. Intent and why

M1 proves duplication by *running* both functions. On a real repo that is
impossible for almost all code: 0 of 1,546 functions in `predictive-scaling`
were judgeable (methods, async, DataFrames, imports).

A structural scan of the same repo, run without executing anything, found real
slop at once:

| Finding | Where |
|---|---|
| `_infer_frequency_minutes` copied 5 times | 4 files under `src/features/` |
| `rollback` copied (47 / 48 lines) | `src/execution/aws.py`, `terraform.py` |
| `stop` copied 3 times | `src/collectors/{business,external,prometheus}.py` |
| `_get_client` copied twice | `src/collectors/{business,external}.py` |
| `within_90_ci` / `within_95_ci` differ only by a number | `src/monitoring/accuracy.py` |
| Accessor wrappers with zero callers (`get_record`, `get_alert`, …) | 5 files |

Two functions whose code is identical after consistently renaming their local
variables do the same thing **by construction**. That is a stronger guarantee
than sampling 200 inputs, and it holds for methods, async functions and any
argument type.

**The bar this milestone is judged by** (agreed with Sanan): slopgate is
installed on one of his repos, an agent does real work, and slopgate catches
genuine slop it did not plant.

## 2. Goals

1. **Exact structural clones.** A new or changed function whose normalized code
   (§6.1) equals an existing function's is blocked. The finding names the
   original to reuse. This also covers two new functions that clone each other
   in the same turn.
2. **Constant-only near clones.** A new or changed function that equals an
   existing one except for numeric constants is blocked. The finding names the
   original and the differing values, and tells the agent to add a parameter
   rather than copy.
3. **Wrapper facts (advisory, never block).** A new function whose body only
   forwards its parameters unchanged to another call, and which has at most one
   call site, is reported in the summary.
4. **`slopgate scan`.** A whole-repo report using the same detectors, for slop
   that already exists. It writes no state and never blocks anything. *(This is
   my call: it is the only way to show real findings on a repo before an agent
   writes new code, and it reuses the same engine. Drop it if you disagree.)*
5. **New-dependency notes (advisory, never block).** New code that imports a
   third-party package the repo has never imported before is reported, so a
   reviewer can ask whether the stdlib or an existing dependency would do.
   *(Added at Sanan's request, from his slop indicator "using new libs".
   Advisory is my call: a new dependency is often legitimate, and slopgate
   cannot tell.)*
6. **Keep the M1 prover** as the second net, for duplicates written differently.
   A function with an exact structural clone skips proving, because it is
   already proven.

## 3. Non-goals

- **Roundabout logic** (the "10 lines instead of 100" complaint). Structure
  cannot judge readability. This is left for a later layer, probably
  model-based and advisory only.
- **Relaxing M1's execution gate.** Decided in principle (lean on the sandbox)
  but not needed for this milestone. It is recorded here and deferred.
- **Blocking on test files.** Copy-paste in tests is often deliberate (one test
  per case). Clones under `tests/` are advisory only.
- **Other languages.** Python only.

## 3a. Anti-goals

- **A false "duplicate" claim.** If two functions are reported as clones, their
  code must be the same up to consistent renaming of locals. Over-strict
  normalization that misses clones is acceptable; loose normalization that
  merges different code is not.
- **Blocking on legitimate small or interface code.** One-line getters,
  `pass`/`...` stubs, protocol methods and other trivial bodies must never
  block.
- **Slowing the hook.** Structural checks are pure `ast`, so they must add well
  under a second on a typical repo.

## 4. Constraints

- Everything from M1 still holds: Python 3.11+, state under the git dir, hooks
  never break the session, commit messages carry no attribution.
- Stdlib `ast` only. No new dependencies.
- Findings must use M1's stable key scheme (paths and qualnames, never line
  numbers), so rounds and the `resolved` stat keep working.

## 5. Approaches considered

**A. Structural facts first (chosen by Sanan).** Pure AST comparison with
strict normalization. Deterministic, fast, and works on all code. It does not
catch roundabout logic.

**B. Model-based reviewer.** Send the diff and similar code to an LLM. It is
the only option that judges roundabout logic, but it is nondeterministic,
costs money, and will sometimes be wrong. Rejected as the core; it is a
candidate for a later advisory layer.

**C. Both layered.** Structural findings block and model opinions are only
shown. Likely the end state, but deferred until A is measured.

## 6. Design

### 6.1 Normalization (the core)

Each function is reduced to a canonical form, then hashed:

1. Drop the docstring, the function name, the return annotation and all
   parameter annotations. None of these change what the body does.
   Decorators are kept (point 7).
2. For methods, drop the first parameter if it is `self` or `cls`. Later uses
   of it are renamed like any other local.
3. **Locals** are parameters, names assigned anywhere in the function,
   `for`/`with`/comprehension targets, `except … as` names and walrus targets.
   They are renamed `v0, v1, …` by order of first appearance, consistently, so
   `a + b` and `a + a` stay different.
4. **Free names** are everything that is not a local: globals, builtins,
   imported names, and functions defined in the same module. They are kept
   verbatim and **qualified by where they come from**: `from x import helper`
   becomes `x.helper`, `import numpy as np` makes `np` become `numpy`, and a
   module-level name becomes `<module>.<name>`. So `helper()` in two files that
   import different `helper`s is not merged, and neither is `sqrt(x)` against
   `log(x)`.
5. **Attribute names** (`self._client`, `df.rolling`) are kept verbatim.
6. **Constants.** The exact key keeps every constant. The near key replaces
   numeric constants (`int`, `float`, `complex`, but not `bool`) with a
   placeholder and records them in order. Strings are always kept, because a
   different string usually means different behavior (keys, SQL, messages).

7. **Kept in both keys** (my call): `async` vs `def`, whether the function
   is a generator, the parameter structure (kinds, count, positional-only and
   keyword-only markers, `*args`/`**kwargs`, and default values), and the
   **decorator list**, qualified like free names. Two route handlers with
   identical bodies but `@app.get("/a")` vs `@app.get("/b")` are therefore not
   clones, so "delete and import" can never break routing.

`exact_key = sha256(ast.dump(normalized))`. `near_key` is the same, but with
numeric constants placeholdered.

**Near-clone limits** (my call). Near clones block, so they are tightly
bounded. A pair counts as a near clone only if:
- they differ in at most **3** numeric constants;
- at least one differing constant is outside `{-1, 0, 1, 2}`;
- none of the differing constants sits in a subscript or slice (`x[0]` vs
  `x[1]` is indexing, not a tunable value).

Anything else is either an exact clone or no clone.

Point 4 is the strict choice. It means two files that each define their own
identical private helper produce callers that are *not* clones until the
helpers themselves are merged. That is conservative by design (anti-goal §3a).

### 6.2 Size floor

Only functions with at least **40 AST nodes**, counted as
`sum(1 for _ in ast.walk(fn_node))` over the whole original `FunctionDef`
(context nodes included), are compared. This is exactly the count used by the
scan that produced §1's table, which kept those findings and nothing trivial.

**Stubs are always exempt**, whatever their size: bodies that are only
`pass`, `...`, a docstring, or `raise NotImplementedError`, plus anything
decorated `@abstractmethod` or `@overload`.

**Identical concrete overrides are slop, not interface.** When two sibling
classes implement the same method with identical bodies (`rollback` in
`aws.py` and `terraform.py`), that is duplication that belongs in their shared
base class. These block, with the instruction in §6.4. *(My call: that is the
slop you described. The anti-goal's "protocol methods" means stubs, which
are exempt above.)*

### 6.3 What gets compared

- **In the hook (`check`).** Every new or changed function is compared
  against every existing function in the repo, and against the other new or
  changed ones.

**Fix to M1's move rule (required).** M1's `classify` treats any function
whose body already existed anywhere as a "move or rename". That would hide
the headline case: a verbatim paste of `_infer_frequency_minutes` into a
second file would never count as new. The rule changes to:

> A function absent before is a **move** only if the function it matches is
> gone from the after-state, meaning its original location no longer has it.
> If the original still exists, the function is a **copy** and is *new*.

M1's tests for moves and renames still pass, since the original is gone in
those cases. A new test covers copy-while-original-remains. Verbatim copies
hit the structural check first, so §6.5's "skip proving" applies.

**Only newly introduced clones block** (my call). A clone pair whose
relation already existed in the snapshot (same two functions, same kind) is
never blocked. It appears in `scan`, not in the hook. The hook reports a
clone only if the relation is new in this turn. This stops the agent being
punished for duplication it didn't write, for example after editing one of
the five pre-existing `_infer_frequency_minutes` copies.

**Which copy is "the original"** (my call). A function that existed in the
snapshot wins over a new one. Among equals, the lexicographically first
`(path, qualname)` wins. This is deterministic, so M1's finding keys stay
stable across rounds, and the `resolved` count and no-progress guard keep
working. `scan` reports each clone group once, with the original first.

**What counts as a test** (my call). A file is a test if any path component
is `tests` or `test`, or the filename matches `test_*.py`, `*_test.py` or
`conftest.py`. Any pair with a test file on either side is advisory only.
- **In `scan`.** Every function is compared against every other.
- Matching is a dictionary lookup on the two keys, so it is linear in repo size.

### 6.4 Findings

Exact clone, which blocks:
```
src/features/lag_features.py:128 _infer_frequency_minutes is a copy of
  src/features/derivative_features.py:182 _infer_frequency_minutes.
  Delete it and import the original.
```
When the original is a method in another class, the instruction instead reads
"move it to a shared helper and call that from both". When the two are the same
method in sibling classes with a common base, it reads "move it into the shared
base class `<Base>`" (syntactic bases only; otherwise the shared-helper wording
is used).

Near clone, which blocks:
```
src/monitoring/accuracy.py:147 within_95_ci is src/monitoring/accuracy.py:137
  within_90_ci with different constants (0.95 vs 0.90). Add a parameter to
  within_90_ci instead of copying it.
```

Wrapper, which is advisory only:
```
note: src/execution/rollback.py:519 get_record only forwards to
  self._records.get and has no callers.
```

Clones involving a test file (§6.3) appear as advisory notes, never as blocks.

**Counting a wrapper's callers** (my call). There is no jedi and no new
dependency, so callers are counted by name: every `ast.Name` and
`ast.Attribute` in the repo whose identifier equals the function's name, plus
the name appearing as a string. This over-counts, which errs toward silence,
and can still miss dynamic dispatch. So the note says "no call sites found by
name", never "unused". Names listed in `__all__`, dunders, and decorated
functions (routes, fixtures, hooks) are never reported.

### 6.5 Integration with M1

- Structural checks run before the prover. A new function with an exact clone
  produces a structural finding and is not proven, which saves time.
- All findings go through M1's existing rounds, stable keys (plus a `kind`
  field: `copy`, `near`, `reinvention`) and stats. Stats gain per-kind counts.
- The summary line adds `C copies, N near-copies, W wrapper notes, D new dependencies`.

### 6.5a New-dependency notes

For each new or changed file, collect the top-level module of every absolute
`import x…` and `from x… import …` (relative imports are skipped). A module is a
**new dependency** when all of these hold:
- it is not in `sys.stdlib_module_names`;
- it is not first-party, meaning no top-level `.py` file or package directory of
  that name exists in the repo, under the root or under `src/`;
- no file in the snapshot imports it.

Note text:
```
note: src/features/lag_features.py:3 imports a new dependency `polars`
  that this repo has never used before.
```
Test files are included, since a new test-only dependency is still worth a
note. Dependency manifests (`requirements*.txt`, `pyproject.toml`) are not
parsed in this milestone; imports are what matter for slop.

### 6.6 `slopgate scan`

`slopgate scan [path]` (default: the current repo) prints every exact-clone
group, near-clone group and wrapper note, grouped by kind and with `src`
findings before `tests`. It always exits 0 and writes nothing.

## 7. Evaluation

1. **Run `scan` on throwaway copies** of three of Sanan's repos:
   `predictive-scaling`, `Multimodal RAG` and `Dev Productivity Agent`, all under
   `~/Documents/Projects/`. Make each copy with `git clone` into a scratch
   directory; the originals are never touched. Hand-check every finding.
   **Target: zero false clone claims.** **Recall check:** on
   `predictive-scaling` at commit `9baf9c4`, `scan` must reproduce §1's table
   apart from the wrapper rows, which are a separate detector.
2. **Tests built from the real findings**, reduced to fixtures, plus adversarial
   non-clones that must stay apart: `sqrt` vs `log`, `a + b` vs `a + a`,
   different imported `helper`s, `max` vs `min`, and different strings.
3. **The bar (§1).** Sanan runs one real agent session on one of his repos with
   the hooks installed. Stats and the summary line record what was caught.
   Only this step proves the milestone, and only he can run it.

## 8. Risks

- **Agents copy and then tweak.** A copy with one changed line is neither exact
  nor near. The M1 prover catches only the simple cases; true "similar code"
  detection is a later problem.
- **Strict free-name qualification may hide clones** whose helpers are
  themselves duplicated. This is the accepted cost of never merging different
  code.
- **The bar depends on the agent producing slop** during the test session. If
  it does not, the session proves nothing either way. `scan` provides evidence
  independently.

## 9. Left to the builder

- Exact representation of the normalized tree and keys.
- Module-path resolution details for relative imports.
- Output layout of `scan`, within §6.6.
