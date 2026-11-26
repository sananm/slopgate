# Semantic Reinvention Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a new function strongly resembles existing repo logic but nothing proves it, the Stop hook blocks once and asks the agent to reuse the existing function or say why the new one is different.

**Architecture:** A new `semantic.py` filters the candidates and the pool, matches them by cosine similarity over the existing embedding cache, and formats the questions. `reinvention.py` gains a capped, cache-aware embedding call that M3 and M1 share. `rounds.py` records which pool functions were already asked about. `cli._check` runs structural, then semantic, then M1, and `install` pre-embeds the repo.

**Tech Stack:** Python 3.11+, stdlib `ast`, fastembed (`jina-embeddings-v2-base-code`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-semantic-check-design.md`

## Global Constraints

- The hook never raises and never breaks the session. Any failure in M3 means no questions, and the stop is allowed.
- A question is phrased as a question, never as a fact. Exact text is in Task 2.
- `SEMANTIC_CUTOFF = 0.84` is provisional until Task 5's replay has been reviewed by Sanan.
- PR #3's embedding bounds stay: `EMBED_BATCH = 16`, `EMBED_CHARS = 2000`.
- No full-repo embedding inside the hook. With a warm cache, M3 adds at most 0.5 s to a Stop on `predictive-scaling`, excluding the model load and the candidates' own embeddings. The worst case for all of M3, model load included, is at most 5 s.
- The check order is: structural (M2), then semantic (M3), then the M1 prover. M1 gets what remains of `BUDGET_S = 20.0`.
- Nothing public may name a repo other than `predictive-scaling` or quote its code. Commits are short, with no AI or Claude mention and no co-author line.
- Never touch the original repos. Evaluate only on `git clone` copies in a scratch directory.

## Review Focus

1. **A hook run before `install` ever ran,** so most of the pool is not embedded: it must stay under 5 s, ask only about what it could compare, and report the count left out. Covered in Task 4.
2. **A corrupt or mismatched embeddings cache** (vectors of the wrong length, non-numeric values, a truncated file): treat those entries as missing and never crash. Covered in Task 1.
3. **The model fails to load or download inside the hook:** no questions, the stop is allowed, and the summary names the error. Covered in Task 4.
4. **A candidate whose best match was deleted or edited by the agent:** compare only against snapshot versions, and never name a function that no longer exists. Covered in Task 2.
5. **A task that only adds tiny, test-file or override methods:** zero questions, and the model is not loaded. Covered in Task 4.

---

### Task 1: Capped, cache-aware embeddings

**Files:**
- Modify: `src/slopgate/reinvention.py` (`_load_cache`, `_embeddings`)
- Test: `tests/test_reinvention.py` (the `tmp_repo` fixture from `tests/conftest.py`)

**Interfaces:**
- Produces: `cached_embeddings(repo: Path, required: list[str], optional: list[str], embed: Embed, cap: int | None) -> tuple[dict[str, list[float]], int]`.
  - It embeds every uncached `required` source, plus at most `cap` uncached `optional` sources (all of them when `cap is None`). Optional sources are taken in the order given.
  - It returns the vectors for every source it now has, and the number of `optional` sources left out.
  - `_embeddings(repo, sources, embed)` becomes `cached_embeddings(repo, sources, [], embed, 0)[0]`, so its behavior is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_cap_limits_optional_but_never_required(tmp_repo):
    calls = []
    fake = lambda texts: calls.append(list(texts)) or [[1.0, 0.0] for _ in texts]
    vecs, left = cached_embeddings(tmp_repo, ["r1", "r2"], ["o1", "o2", "o3"], fake, cap=1)
    assert set(vecs) == {"r1", "r2", "o1"} and left == 2
    vecs, left = cached_embeddings(tmp_repo, ["r1"], ["o1", "o2", "o3"], fake, cap=None)
    assert set(vecs) == {"r1", "o1", "o2", "o3"} and left == 0
    assert sum(len(c) for c in calls) == 5  # cached ones are never re-embedded

def test_cache_entries_of_the_wrong_length_are_treated_as_missing(tmp_repo):
    # write embeddings.json with one 2-d vector, one 3-d vector and one non-list
    # value; each entry whose length differs from the majority is re-embedded,
    # and nothing raises
    ...
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest -q tests/test_reinvention.py tests/test_cli.py -k "cap or wrong_length"`
Expected: FAIL with `ImportError` / `NameError` for `cached_embeddings`.

- [ ] **Step 3: Implement**

`_load_cache` keeps only vectors whose length equals the most common length in the file.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass, with no skips added.

- [ ] **Step 5: Commit** with `feat: capped embedding cache`

---

### Task 2: `semantic.py`: candidates, pool, matching, question text

**Files:**
- Create: `src/slopgate/semantic.py`
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: `FunctionInfo` (`index.py`), `Shape` and `is_test_path` (`structure.py`), `_cosine` (`reinvention.py`).
- Produces:
  - `SEMANTIC_CUTOFF = 0.84`
  - `@dataclass(frozen=True) class Question: new: FunctionInfo; existing: FunctionInfo; score: float; copies: int`
  - `semantic_candidates(new: list[FunctionInfo], shapes: list[Shape], skip: set[tuple[str, str]]) -> list[FunctionInfo]`. It applies spec §6.1, except the M1 rule, which Task 4 applies afterwards. Here `shapes` are the current-repo shapes, used for `comparable` and the base-class override rule, and `skip` holds the (path, qualname) of M2 copies and near-copies.
  - `semantic_pool(functions: list[FunctionInfo]) -> list[FunctionInfo]`. It drops test files and dunders, per spec §6.2. The size floor does not apply to the pool.
  - `find_questions(candidates: list[FunctionInfo], pool: list[FunctionInfo], vecs: dict[str, list[float]], exact_keys: dict[tuple[str, str], str]) -> list[Question]`. A candidate or pool function with no vector is ignored. At most one question per candidate.
  - `question_key(q: Question) -> str`, which returns `f"{q.existing.path}\t{q.existing.qualname}"`.
  - `format_question(q: Question) -> str`.
- Definitions:
  - A **dunder** is a name that starts and ends with `__`.
  - The **override rule**: a candidate method whose `Shape.cls` has a direct base `b` in `Shape.bases`, where some shape in `shapes` has `cls == b` and a qualname ending in `"." + name`.
  - **Copies**: pool functions with the same `exact_key` as the best match. The question names the first of them by (path, qualname), and `copies` is the number of others.

- [ ] **Step 1: Write the failing tests**

```python
def test_question_text_exact():
    q = Question(new=fi("src/a.py", 52, "V._infer_frequency_seconds"),
                 existing=fi("src/b.py", 182, "D._infer_frequency_minutes"), score=0.84, copies=4)
    assert format_question(q) == (
        "question: src/a.py:52 V._infer_frequency_seconds\n"
        "  looks like src/b.py:182 D._infer_frequency_minutes (and 4 copies of it).\n"
        "  Reuse it, or reply with why the new one is different.")
    # copies == 0 drops the parenthetical, leaving "...D._infer_frequency_minutes.\n"

def test_cutoff_is_inclusive():
    # vectors built so the cosine is exactly SEMANTIC_CUTOFF: asked; one ulp lower: not asked

def test_names_first_copy_by_path_and_counts_the_others():
    # the best match has 2 exact copies in the pool: the question names the
    # lexicographically first (path, qualname) and reports copies == 2

def test_candidate_filters():
    # dropped: test file, dunder, comparable=False, M2-skipped, and a method
    #   overriding a first-party base's method (class E(BaseExtractor): def extract)
    # kept: a method that is not on its base (E._helper), and a plain module function

def test_pool_keeps_small_functions_drops_tests_and_dunders():
    ...

def test_missing_vectors_are_ignored():
    # a pool function with no vector never becomes the match; a candidate with no vector gets no question
```

`fi(path, line, qualname)` is a small local helper that builds a `FunctionInfo` with a unique `source`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest -q tests/test_semantic.py`
Expected: FAIL with `ModuleNotFoundError: slopgate.semantic`.

- [ ] **Step 3: Implement the module**

- Compare by scanning the pool linearly with `_cosine` for each candidate.
- Build the pool with the `semantic_pool` filter only. The caller (Task 4) passes snapshot versions.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest -q tests/test_semantic.py`
Expected: PASS.

- [ ] **Step 5: Commit** with `feat: semantic reinvention questions`

---

### Task 3: Remember asked questions per task

**Files:**
- Modify: `src/slopgate/rounds.py`
- Test: `tests/test_rounds.py`

**Interfaces:**
- Produces:
  - `asked(repo: Path, task_id: str) -> set[str]`
  - `record_asked(repo: Path, task_id: str, keys: list[str]) -> None`
- `load_rounds`'s default state gains `"asked": []`. A state whose `asked` is not a list of strings is read as empty.
- `record_asked` adds keys to the current task's state. It must be called after `should_block` in the same check, because `should_block` writes the state.

- [ ] **Step 1: Write the failing tests**

```python
def test_asked_is_per_task(tmp_repo):
    should_block(tmp_repo, "t1", "k")
    record_asked(tmp_repo, "t1", ["src/b.py\tD.f"])
    assert asked(tmp_repo, "t1") == {"src/b.py\tD.f"}
    assert asked(tmp_repo, "t2") == set()

def test_bad_asked_value_reads_as_empty(tmp_repo):
    # rounds.json with "asked": "oops" -> asked() == set(), and should_block still works
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest -q tests/test_rounds.py`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

`record_asked` reads the state, unions in the new keys, and writes it back. It keeps the existing round fields untouched.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/pytest -q tests/test_rounds.py`
Expected: PASS.

- [ ] **Step 5: Commit** with `feat: remember asked questions per task`

---

### Task 4: Wire M3 into the Stop hook and pre-embed at install

**Files:**
- Modify: `src/slopgate/cli.py` (`_check`, `install`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `warm_embeddings(repo: Path, embed: Embed) -> int`, in `cli.py`. It embeds `semantic_pool(index_repo(repo, _read_py_files(repo)))` with `cached_embeddings(repo, [], sources, embed, None)` and returns the count.
- `install` calls `warm_embeddings` after the model warm-up. It prints `slopgate: embedding N functions (one-time, can take a few minutes)...` to stderr. Any exception there, including not being in a git repo, is printed and ignored, exactly like the existing warm-up failure.
- Constant: `MAX_HOOK_EMBEDS = 16`, in `cli.py`. This is the builder's value. At about 0.18 s per function cold, plus the model load, it stays within the 5 s worst case. Re-check it against Task 5's measurement.

**Behavior of `_check`, in order:**
1. **Structural (M2):** unchanged.
2. **Pool:** `semantic_pool(changes.existing + <index_repo of snapshot versions of changed files>)`. The snapshot index is the one already computed as `classify`'s first argument, so hoist it into a variable.
3. **Candidates:** `semantic_candidates(changes.new, after, skip=<(path, qualname) of every M2 clone>)`.
4. **Embeddings:** if there are candidates and a non-empty pool, call `cached_embeddings(repo, [c.source for c in cands], [p.source for p in pool], embed, MAX_HOOK_EMBEDS)` through the same lazily loaded `embed` that M1 uses. If this raises, there are no questions, and the summary gets `, semantic check skipped: <ExcName>`.
5. **Matching:** `find_questions` with `exact_keys` built from the `before` shapes. Then drop questions whose `question_key` is in `asked(repo, task_id)`.
6. **M1:** runs as today, with the remaining budget. Afterwards, drop questions whose `new` has an M1 finding.
7. **Blocking text:** questions join `found`, after the clone and M1 findings, using `format_question`. Their round keys are `"question\t" + new.path + "\t" + new.qualname + "\t" + question_key(q)`. These keys are left out of the `resolved` count.
8. **Decision:** if `block`, call `record_asked(repo, task_id, [question_key(q) for q in questions])`. If not blocking, the questions go into `systemMessage` with a `note: ` prefix, as advisory clones already do.
9. **Stats and summary:** stats gain `"questions": len(questions)` and `"left_out": left`. The summary gains `{Q} questions` after the near-copies count, plus `({left} functions not yet embedded)` when `left > 0`.

- [ ] **Step 1: Write the failing tests**

Use `fake_embed` from the existing `no_model` fixture, overridden per test so that chosen sources map to chosen vectors.

```python
def test_similar_new_method_gets_one_question_then_allows(fixture_git_repo, capsys, monkeypatch):
    # add a method whose fake vector is 0.9-similar to an existing helper
    # first check: blocks, and the reason contains "question:" and the existing qualname
    # second check, with the code unchanged: allows; the summary has no "question:" line
    #   (already asked) and stats["resolved"] == 0

def test_rename_after_question_does_not_reask(...):
    # after the block, rename the new method: the next check does not ask again

def test_override_and_tiny_methods_never_load_the_model(...):
    # new code is only an extract() override and a 3-line helper: no question,
    # and the model loader is never called

def test_uncached_pool_is_capped_and_reported(...):
    # 40 uncached pool functions: embed is called with at most
    # len(candidates) + MAX_HOOK_EMBEDS sources, and the summary says
    # "functions not yet embedded"

def test_model_failure_skips_semantic_and_allows(...):
    # the default_embed loader raises: allows, and the summary contains "semantic check skipped"

def test_question_dropped_when_m1_proves_it(...):
    # a fake prover proves the pair: only the M1 finding is reported, with no question

def test_deleted_original_is_never_named(...):
    # the agent deletes the original helper and adds a similar one: no question names the deleted function

def test_install_pre_embeds_the_pool(fixture_git_repo, monkeypatch):
    # install embeds exactly the semantic pool; a following cached_embeddings over those sources calls embed zero times

def test_install_outside_a_git_repo_still_installs(tmp_path, monkeypatch, capsys):
    # settings written, exit 0, and the embedding failure is printed, not raised
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/pytest -q tests/test_cli.py -k "question or reask or override or capped or semantic or deleted_original or install"`
Expected: FAIL.

- [ ] **Step 3: Implement** the behavior above in `_check`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. `test_hidden_file_write_is_blocked` must still run, not skip.

- [ ] **Step 5: Commit** with `feat: ask semantic reinvention questions in the Stop hook`

---

### Task 5: Tuning replay on three repos

**Files:**
- Create: `scripts/semantic_replay.py`. It is committed and must contain no repo names.
- Output: `.superpowers/sdd/<plan>/replay/`. This directory is gitignored: per-pair labels name private code and must never be committed.

**Interfaces:**
- `python scripts/semantic_replay.py <repo>` prints CSV to stdout with the columns `path,qualname,best_path,best_qualname,score`.
- The rows are every `semantic_candidates`-eligible function in the repo. Each is treated as new; the pool is the rest of the repo minus the function itself and its exact copies.
- `skip` is empty. Copies are already removed from the pool, which reproduces the hook's view.

- [ ] **Step 1: Clone the three repos into scratch**

Use `git clone --no-hardlinks`, the same three repos as M2's Task 7, and `predictive-scaling` at `9baf9c4`. Do not touch the originals.

- [ ] **Step 2: Run the replay** on each clone and save the CSVs to the replay directory.

- [ ] **Step 3: Label the pairs**

For each cutoff from 0.70 to 0.90 in steps of 0.02, label up to 10 sampled pairs scoring at or above it as `reuse` or `noise`, with a one-line reason each. Write all labels to `replay/labels.md`.

- [ ] **Step 4: Pick the cutoff**

Choose the lowest cutoff with at least 80% `reuse` among the sampled pairs and at most 2% of the replayed functions at or above it. The live pair `_infer_frequency_seconds` → `_infer_frequency_minutes` (0.84) must still be asked. If no cutoff qualifies, report BLOCKED with the table; do not force one.

- [ ] **Step 5: Measure latency**

On the `predictive-scaling` clone with a warm cache, time the semantic step of `_check` alone, excluding the model load. It must be at most 0.5 s. If it is over, change the cache format. The spec leaves that to the builder.

- [ ] **Step 6: Report and stop for review**

Report the per-cutoff table, the proposed cutoff and the labels path. **Stop here:** the controller shows Sanan the labels. `SEMANTIC_CUTOFF` changes, if it changes at all, only after Sanan approves.

- [ ] **Step 7: Commit** the script, plus the approved cutoff if it changed, with `feat: semantic replay script and tuned cutoff`

---

### Task 6: Live re-run and README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Re-run the live task on a fresh clone**

Clone `predictive-scaling` into scratch, run `slopgate install`, then run:

```bash
claude -p "<the volatility task prompt from the spec's §1 live test>" --setting-sources project,local --permission-mode acceptEdits --allowedTools "Read Edit Write Grep Glob Bash(python3 -m pytest:*)" --output-format stream-json --verbose
```

`--setting-sources project,local` keeps the user's global plugins out of the test agent. Record:
- whether the question fired;
- what the agent did: reused the existing function, or explained why not;
- the hook's seconds, from `stats.jsonl`.

- [ ] **Step 2: Run 2–3 more ordinary tasks** the same way, each a small feature request in `predictive-scaling`. Record every question and whether it was worth answering.

- [ ] **Step 3: Update the README**

- Add a section on semantic questions: what triggers one, and that it is a question, not a claim.
- Add the replay numbers as aggregates only: per-repo counts and percentages, with no other repo named. Say plainly that the cutoff was chosen on these same repos.
- Add the live re-run result.
- Change nothing else.

- [ ] **Step 4: Commit** with `docs: semantic questions in README`

---

## Amendment (2026-11-25): Tasks 5b–5d

Spec §10. Task 5b, the pair filters, is done: commits 755a1d9 and 2148f09.

### Task 5c: Judge through `agy`

**Files:**
- Create: `src/slopgate/judge_model.py`
- Modify: `src/slopgate/cli.py` (`_questions`)
- Test: `tests/test_judge_model.py`, `tests/test_cli.py`

**Interfaces:**
- `JUDGE_MODEL = "gemini-4-hydrogen-low"`
- `JUDGE_MAX_PAIRS = 3`
- `JUDGE_TIMEOUT_S = 20`
- `judge(repo: Path, qs: list[Question]) -> list[Question]`. It returns the questions whose verdict is `reuse`, in their original order.
  - It judges the top `JUDGE_MAX_PAIRS` by score, in parallel threads. Each is one `subprocess.run(["agy", "-p", prompt, "--model", JUDGE_MODEL, "--json-schema", SCHEMA, "--output-format", "json", "--disable-slash-commands", "--print-timeout", f"{JUDGE_TIMEOUT_S}s"], cwd=<fresh tempfile.TemporaryDirectory()>, timeout=JUDGE_TIMEOUT_S + 5)`.
  - A question beyond the top 3 is dropped, not asked.
- **Verdict:** `reuse` only when the stdout JSON has `status == "SUCCESS"` and `structured_output.verdict == "reuse"`. Every other outcome is `noise`, and the `noise` result is not cached when it came from a failure.
- **Cache:** `state_dir(repo) / "verdicts.json"`, mapping sha256(new.source NUL existing.source) to `"reuse"` or `"noise"`. A corrupt file reads as empty.
- **The prompt** holds both paths, qualnames and sources, and this reuse definition: "reuse if an experienced engineer reviewing the new function would say 'call the existing one instead' (a small adaptation such as a unit conversion is fine); noise if they only look alike".
- **`_questions` in cli.py** calls `judge` on its questions after the asked-once filter and before returning. The whole call is already inside M3's try.

**Tests:** these use a fake `agy` (monkeypatch `subprocess.run`):
- `reuse` keeps the question, and `noise` drops it.
- A missing binary, a timeout, a bad JSON reply and `status != SUCCESS` each drop the question, without raising and without caching.
- A cached verdict makes no subprocess call.
- At most 3 calls are made for 5 questions.
- The prompt contains both sources.
- In `test_cli`, a judged `noise` means the check allows the stop with no question.

**Commit:** `feat: judge semantic questions before asking`

### Task 5d: Judged replay and the cutoff (stops for Sanan)

- **Agreement:** run `judge` on every labeled pair from Tasks 5 and 5b. Report agreement with the labels, and the live pair's verdict, which must be `reuse`.
- **Judged replay:** from 0.70 to 0.90 in steps of 0.02, report the share of functions asked after the filters and the judge, and the precision against labels. Label any newly judged pairs the same way, with a one-line reason.
  - Report the number of judge calls.
  - Pick the lowest cutoff that meets the original targets.
  - Labels and verdicts stay in the gitignored workspace.
- **Stop:** report, then wait. `SEMANTIC_CUTOFF` changes only after Sanan approves.
