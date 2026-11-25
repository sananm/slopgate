# slopgate M3: semantic reinvention questions

**Date:** 2026-11-25
**Status:** Approved

---

## 1. Intent and why

Sanan's core complaint is an agent writing logic that already exists in
another file of the same repo. M2 catches it when the new code is a copy, and
M1 when the two functions can be proven to behave identically. A live test
showed the common case is neither.

The test ran a headless agent on `predictive-scaling` with slopgate installed.
The task was a volatility feature extractor with windows in minutes. The
agent wrote `VolatilityFeatureExtractor._infer_frequency_seconds`: the median
spacing of the index, returned in seconds. The repo already has
`_infer_frequency_minutes`, the same idea returned in whole minutes, in five
places. slopgate let it through:

- the bodies differ, so the structural check does not fire;
- it is a method, so the M1 prover could judge 0 of the 7 new functions;
- and the two really do behave differently, so no proof is possible.

Embedding search finds it anyway. Against all 1,553 functions in the repo, the
five `_infer_frequency_minutes` copies score 0.84 cosine similarity and rank
first. The closely related `_infer_frequency` scores 0.72, and the first
unrelated function is far behind at 0.52.

M3 turns a strong, unproven match into a **question to the agent**: it blocks
once, names the existing function, and asks the agent to reuse it or say why
the new one is different. The agent fixes its own slop, and no human ever sees
an unproven claim stated as fact.

## 2. Goals

1. When a new function strongly resembles an existing one and nothing proves
   or disproves it, block once with a question naming the existing function.
2. Keep the noise low enough that a question is almost always worth answering.
   The tuning target is in §7.
3. Ask each question at most once per task. An agent that keeps its function
   and explains why is not asked again.
4. Keep the Stop hook fast. Embedding the whole repo happens outside it.

## 3. Non-goals

- Proving equivalence. That stays M1's job.
- A second model (reranker or LLM judge). Approach 3 below is the fallback if
  §7's tuning shows too much noise.
- Roundabout logic and the other slop indicators. They are separate milestones.
- Anything outside Python.

## 3a. Anti-goals

- **A question on most tasks.** If agents get a pointless question every few
  tasks, users uninstall it.
- **Stating a match as fact.** The text is a question, never "X reimplements Y".
- **Trapping the agent.** An answered question never comes back, and the round
  limit still holds.
- **A slow or memory-hungry hook.** No full-repo embedding inside the hook.
  PR #3's memory bounds (batches of 16, 2,000 characters per text) stay.

## 4. Constraints

- The existing embedding model (`jina-embeddings-v2-base-code`), run locally
  through fastembed, with the existing per-source cache.
- Cold embedding of `predictive-scaling` takes 279 s and peaks at 1.5 GB.
  Warm, only new or changed functions are embedded.
- The hook contract is unchanged. A question is a blocking finding like any
  other.

## 5. Approaches considered

1. **A fixed similarity cutoff.** The simplest, but generic functions match
   each other constantly: every extractor's `extract()`, `__init__`,
   `to_dict`. Rejected for noise.
2. **A cutoff plus filters, tuned on real repos (chosen by Sanan).** Skip the
   known-noisy cases, then set the cutoff by replaying three repos and
   hand-checking a sample.
3. **Embedding search plus a reranking model.** More accurate on paper, but it
   adds a second model download, a few seconds per check, and more to explain.
   Only worth it if approach 2's tuning shows too much noise.

## 6. Design

### 6.1 Candidates (new functions that may get a question)

These are new functions from the existing change detection (M1 `changes.new`,
after M2's copy removal). A function is skipped when any of these hold:

- it is in a test file (the existing `is_test_path`);
- it is a dunder;
- it is below M2's size floor (40 AST nodes), with stubs counted as small;
- it is a method whose name is defined by one of its class's first-party base
  classes. Overriding a base method is the interface working as intended, like
  `extract()` implementing `BaseExtractor.extract`. Only direct bases count,
  matched by their last name against classes defined in the repo, as M2's
  base rule already does. *(Builder-check call.)*;
- it already has an M2 copy or near-copy finding, or an M1 proven finding.
  An M1 *disproof* does not skip it: the question claims a shared idea, not
  identical behavior, and the live case is exactly such a pair. *(Builder-check
  call.)*

### 6.2 The pool (existing functions it is compared against)

These are functions as they were in the task-start snapshot (M2's `before`
list), excluding test files and dunders. A function the agent modified is
compared in its snapshot version: the logic as it was before the agent touched
it. A function the agent deleted is left out, and so is anything the agent
added during the task. *(Builder-check call.)* Functions below
the size floor stay in, because a short existing helper is exactly what an
agent should reuse.

### 6.3 Matching

- Compute the cosine similarity of the candidate's embedding against every
  pool embedding. The best match is a question if its score is at least
  `SEMANTIC_CUTOFF`, which §7 sets. The live case scores 0.84.
- When the best match has exact copies in the pool (the same M2 `exact_key`),
  name the first by (path, qualname) and say how many copies there are. Only
  exact copies count toward that number. *(Builder-check call.)*
- One question per candidate at most. If two candidates match the same pool
  function, both are asked.

### 6.4 The question

```
question: src/features/volatility_features.py:52 _infer_frequency_seconds
  looks like src/features/derivative_features.py:182
  DerivativeFeatureExtractor._infer_frequency_minutes (and 4 copies of it).
  Reuse it, or reply with why the new one is different.
```

It goes out with the other blocking findings in the same round.

### 6.5 Asked once per task

- The round state records the **pool function** each question named, keyed
  by path and qualname, but only when the question went out in a blocking
  round. Keying by the pool function means a rename or rewrite of the new
  function does not bring the question back. *(Builder-check call.)*
- Before the decision to block, questions naming an already-asked pool
  function are dropped. If they were the only findings, the stop is allowed.
- Questions are counted separately from M1 and M2 findings in the stats. A
  question that disappears is never counted as a "resolved" finding.
- Questions count toward the existing round limit: round 3 never blocks.
- A question that was asked and not acted on does not appear as an unresolved
  finding in the human summary. It was a question, and the agent answered it.
  A question that first comes up in a round that cannot block (round 3 on)
  goes into the summary as a `note:`.

### 6.6 Embeddings

- **`slopgate install`** embeds the pool and fills the cache, with a progress
  line. A failure there is non-fatal, as model warm-up already is.
- **The Stop hook** embeds the candidates plus any pool function whose
  source is not cached. If more than `MAX_HOOK_EMBEDS` sources are missing, it
  embeds the candidates and only that many pool functions, and leaves the
  rest out of the comparison. This can only miss a question, never invent
  one, and the summary reports how many were left out. `MAX_HOOK_EMBEDS` is
  set so that the hook stays within its latency budget.
- **The cache** stays keyed by source hash. Its format is the builder's call,
  as long as loading it for about 1,500 functions fits the latency budget
  (§6.8).

### 6.7 `slopgate scan`

Unchanged. Questions only make sense during an agent's task.

### 6.8 Latency and order

- The order of checks is: structural (M2), then semantic (M3), then the M1
  prover.
- M3 and M1 share one embedding cache and one model load. M1 gets whatever is
  left of the existing 20 s budget.
- With a warm cache, M3 adds no more than 0.5 s to a Stop on
  `predictive-scaling`, not counting the model load and the candidates' own
  embeddings.
- With the `MAX_HOOK_EMBEDS` cap, the worst case for all of M3, model load
  included, is at most 5 s. *(Builder-check call.)*

## 7. Evaluation

1. **Tuning replay.** Use throwaway `git clone` copies of `predictive-scaling`
   and two other private repos; the originals are never touched. For every
   function that passes §6.1's filters, treat it as new and the rest of its
   repo as the pool, excluding the function itself and its exact copies,
   which M2 already catches. Record the best score.
   - Hand-check a sample of the pairs at or above each cutoff from 0.70 to
     0.90, in steps of 0.02. Label each pair either **reuse** (an engineer
     would have called the existing function) or **noise**.
   - Pick the lowest cutoff where at least 80% of the sampled questions are
     reuse, and no more than 2% of the replayed functions would get a question.
   - The repos are the same three as M2's evaluation.
   - The builder labels the pairs and lists every labeled pair in the report,
     so Sanan can review the labels before the cutoff is committed.
   - Until then, the shipped `SEMANTIC_CUTOFF` is a provisional 0.84.
     *(Builder-check call.)*
   - The live `_infer_frequency_seconds` case (0.84) must still get a
     question. If no cutoff meets both targets, report that instead of
     forcing one; it is the signal to consider approach 3.
2. **Live re-run.** Repeat the volatility task with M3 installed. Success
   means the agent gets the question and then either reuses
   `_infer_frequency_minutes` or gives a reason a reviewer would accept. Run
   2–3 more ordinary tasks as well, and record each question asked and
   whether it was worth answering.
3. **Tests.**
   - The filters, the cutoff boundary and tie naming.
   - The asked-once rule across rounds.
   - The `MAX_HOOK_EMBEDS` cap never inventing a question.
   - The hook never raising.
4. **README.** The replay numbers and the live re-run, measured, with no other
   repo named.

## 8. Risks

- **Tuning on the same repos it is evaluated on.** The README must say the
  cutoff was chosen on these repos.
- **The first-party base filter needs class information** that the M1 index
  may not carry. M2's shapes already record `cls` and `bases`.
- **The agent may "answer" a question by renaming or rewording its function.**
  The asked-once rule accepts that. The human sees the outcome in the diff.
- **Embedding drift:** a different fastembed version could change the scores.
  The cutoff is recorded together with the model name.

## 9. Left to the builder

- The cache format and the value of `MAX_HOOK_EMBEDS`.
- How to reuse M2's class and base information for the override filter.
- The hand-check sample size per cutoff (at least 10 per cutoff where
  available).
- Where the asked pairs live in the round state.
