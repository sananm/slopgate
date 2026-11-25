# Structural Slop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block an agent from leaving copied or constant-tweaked functions in the repo, note forwarding wrappers nobody calls and newly added third-party dependencies, and report existing duplication with `slopgate scan`. None of this executes any code.

**Architecture:** A new `structure` module normalizes each function's AST: locals are alpha-renamed, free names are qualified by origin, and decorators and the signature are kept. It produces an exact key and a near key. `clones` and `wrappers` turn the keys into findings. The Stop hook runs these before the M1 prover, skips proving anything already caught as a copy, and routes everything through M1's rounds, stable keys and stats. `scan` runs the same detectors repo-wide.

**Tech Stack:** Python 3.11+, stdlib `ast`/`hashlib` only (no new dependencies), `pytest`.

**Spec:** `docs/superpowers/specs/2026-11-24-structural-slop-design.md`

## Global Constraints

- Stdlib `ast` only. No new dependencies.
- Size floor: compare only functions with `sum(1 for _ in ast.walk(fn_node)) >= 40`, counted over the whole original `FunctionDef`/`AsyncFunctionDef` node.
- Stubs are always exempt: bodies made only of `pass`, `...`, a docstring or `raise NotImplementedError`, and anything decorated `@abstractmethod` or `@overload`.
- Near clones: at most 3 differing numeric constants, at least one outside `{-1, 0, 1, 2}`, and none of the differing constants inside a subscript or slice. `bool` is not numeric.
- Strings are always kept verbatim in both keys.
- Only newly introduced clone relations block. Relations that already existed in the snapshot are reported only by `scan`.
- Any pair with a test file on either side is advisory only. A test file has a `tests` or `test` path component, or a filename matching `test_*.py`, `*_test.py` or `conftest.py`.
- The original is the function that existed in the snapshot. Among equals, it is the lexicographically first `(path, qualname)`.
- Wrapper notes say "no call sites found by name", never "unused".
- M1 still holds: hooks never break the session, stdout carries only the hook JSON, state lives under the git dir, and finding keys never contain line numbers.
- Commit messages are short, with no attribution trailers.

## Review Focus

- **Two files that each define their own private `_build()`**, with callers whose bodies are identical: not clones, because the free names qualify to different modules. — Task 2
- **Relative imports** (`from .utils import h` vs `from ..utils import h`): these resolve to different absolute modules and are not merged. — Task 2
- **A nested `def` or lambda inside a function**: inner locals are renamed without colliding with an outer free name of the same spelling. — Task 2
- **A file with a syntax error** anywhere in the repo: it is skipped, and `check` and `scan` still run. — Task 2 / Task 5
- **A repo of about 2,000 functions**: structural indexing plus matching stays under 2 s. — Task 3

---

### Task 1: Copies are new, only true moves are moves

**Files:**
- Modify: `src/slopgate/changes.py`
- Test: `tests/test_changes.py`

**Interfaces:**
- Consumes: M1's `FunctionInfo` (`module`, `qualname`, `body_key`).
- Produces: `classify(before, after) -> Changes`, with the same signature. Only the move rule changes.

Rule (spec §6.3): a function absent before is a **move** only if every before-function with the same `body_key` is gone from `after` at its own `(module, qualname)`, or has a different body there. If any original still exists unchanged, the function is **new**.

- [ ] **Step 1: Write the failing test**

```python
def test_copy_with_original_still_present_is_new():
    before = [fn("a", "f", "k1")]
    after = [fn("a", "f", "k1"), fn("b", "f", "k1")]
    ch = classify(before, after)
    assert names(ch.new) == ["f"] and ch.new[0].module == "b"

def test_move_when_original_removed_is_still_not_new():
    assert classify([fn("a", "f", "k1")], [fn("b", "f", "k1")]).new == []

def test_copy_when_original_was_edited_is_not_a_copy():
    # original changed body, so the "copy" is the old version moved
    assert classify([fn("a", "f", "k1")], [fn("a", "f", "k2"), fn("b", "f", "k1")]).new == []
```

- [ ] **Step 2: Run and see it fail**

Run: `.venv/bin/pytest tests/test_changes.py -v`
Expected: `test_copy_with_original_still_present_is_new` FAILS. The copy is classified as existing.

- [ ] **Step 3: Change the move rule in `classify`**

Map each `body_key` to its before-locations, and check each against the after-index of `(module, qualname) -> body_key`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass, including every existing M1 move and rename test.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: a copy whose original remains is new code"
```

---

### Task 2: Structural normalization

**Files:**
- Create: `src/slopgate/structure.py`
- Test: `tests/test_structure.py`

**Interfaces:**
- Produces:
  - `Shape(path: str, qualname: str, module: str, line: int, end_line: int, cls: str | None, bases: tuple[str, ...], exact_key: str, near_key: str, consts: tuple[tuple[float | int | complex, bool], ...], comparable: bool)`. `consts` holds each numeric constant in order, with a flag for whether it sits in a subscript or slice. `comparable` is False for stubs and for functions under the size floor.
  - `shapes_of(path: str, text: str) -> list[Shape]`. Returns `[]` on a syntax error.
  - `index_shapes(texts: dict[str, str]) -> list[Shape]`.
  - `is_test_path(path: str) -> bool`.

Normalization (spec §6.1, applied in order):
1. Drop the docstring, the function name, the return annotation and the parameter annotations.
2. Drop a leading `self`/`cls` parameter on methods.
3. Rename locals `v0, v1, …` by order of first appearance. Locals are parameters, store-context names, `for`/`with`/comprehension targets, `except … as` names and walrus targets. Each nested def or lambda gets its own scope, renamed inside that scope only.
4. Qualify free names. `import x as y` makes `y` become `x`. `from x import a as b` makes `b` become `x.a`. Relative imports resolve against the file's module path. A module-level def, class or assignment `n` becomes `<module>.n`. Builtins stay as they are.
5. Keep attribute names verbatim.
6. Keep `async`, generator-ness, the parameter structure (kinds, counts, default values) and the decorator list, with decorators qualified like free names.
7. The exact key keeps every constant. The near key replaces numeric constants with a placeholder.

`exact_key` and `near_key` are `sha256(ast.dump(...))` hex digests.

- [ ] **Step 1: Write the failing tests**

```python
def test_renamed_locals_and_docstring_are_the_same():
    assert key("def f(a, b):\n    '''x'''\n    t = a * 2\n    return t + b\n") == key("def g(p, q):\n    s = p * 2\n    return s + q\n")

@pytest.mark.parametrize("a,b", [
    ("def f(x): return sqrt(x)", "def f(x): return log(x)"),
    ("def f(a, b): return a + b", "def f(a, b): return a + a"),
    ("def f(x): return max(x, 0)", "def f(x): return min(x, 0)"),
    ("def f(x): return d['a'] + x", "def f(x): return d['b'] + x"),
    ("async def f(x): return x", "def f(x): return x"),
    ("def f(x, *a): return x", "def f(x, a): return x"),
    ("@app.get('/a')\ndef f(x): return x", "@app.get('/b')\ndef f(x): return x"),
])
def test_different_code_never_shares_an_exact_key(a, b):
    assert key(padded(a)) != key(padded(b))

def test_different_imported_helpers_differ():
    assert key_in("from p import h\n", "def f(x): return h(x)") != key_in("from q import h\n", "def f(x): return h(x)")

def test_module_local_helpers_in_different_files_differ():  # Review Focus
    assert key_in("def h(x): return x\n", "def f(x): return h(x)", path="a.py") != key_in("def h(x): return x\n", "def f(x): return h(x)", path="b.py")

def test_relative_imports_resolve_to_different_modules():  # Review Focus
    assert key_in("from .u import h\n", "def f(x): return h(x)", path="pkg/sub/m.py") != key_in("from ..u import h\n", "def f(x): return h(x)", path="pkg/sub/m.py")

def test_nested_scope_locals_do_not_collide_with_outer_free_names():  # Review Focus
    a = "def f(x):\n    def g(n): return n + 1\n    return g(x) + n\n"
    b = "def f(x):\n    def g(m): return m + 1\n    return g(x) + k\n"
    assert key(padded(a)) != key(padded(b))

def test_near_key_ignores_numbers_and_records_them():
    s1, s2 = shape("def f(x):\n    return x > 0.90\n"), shape("def f(x):\n    return x > 0.95\n")
    assert s1.exact_key != s2.exact_key and s1.near_key == s2.near_key
    assert [c for c, _ in s1.consts] == [0.90]

def test_subscript_constants_are_flagged():
    assert shape("def f(x):\n    return x[0]\n").consts == ((0, True),)

def test_stubs_and_tiny_functions_are_not_comparable():
    assert not shape("def f(x):\n    raise NotImplementedError\n").comparable
    assert not shape("def f(x): return x\n").comparable

def test_syntax_error_file_yields_no_shapes():  # Review Focus
    assert shapes_of("bad.py", "def f(:\n") == []

def test_is_test_path():
    assert all(map(is_test_path, ["tests/a.py", "pkg/test/b.py", "test_x.py", "x_test.py", "conftest.py"]))
    assert not is_test_path("src/testing_utils.py")
```

`padded(src)` adds filler statements so the function clears the 40-node floor. `key(src)` and `key_in(prelude, fn_src, path=)` return `exact_key` for the first function. `shape(src)` returns the first `Shape`, with the floor disabled by padding. Define these helpers in the test module.

- [ ] **Step 2: Run and see it fail**

Run: `.venv/bin/pytest tests/test_structure.py -v`
Expected: FAIL, `No module named 'slopgate.structure'`.

- [ ] **Step 3: Implement `structure.py`**

Use one `ast.NodeTransformer` per function scope for renaming, after a pre-pass that collects the module's import map and its module-level names. `comparable = node_count >= 40 and not stub`, where the count is taken on the original node.

- [ ] **Step 4: Run and see it pass**

Run: `.venv/bin/pytest tests/test_structure.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: structural normalization with qualified free names"
```

---

### Task 3: Clone findings

**Files:**
- Create: `src/slopgate/clones.py`
- Test: `tests/test_clones.py`

**Interfaces:**
- Consumes: Task 2 `Shape`, `index_shapes`, `is_test_path`.
- Produces:
  - `Clone(kind: Literal["copy", "near"], new: Shape, original: Shape, diff: tuple[tuple, tuple] | None, base: str | None, blocking: bool)`
  - `find_clones(candidates: list[Shape], pool: list[Shape], before: list[Shape]) -> list[Clone]`. Here `candidates` are the new or changed shapes, `pool` is every after-state shape, and `before` is every snapshot shape.
  - `clone_groups(shapes: list[Shape]) -> list[list[Shape]]`, used by `scan`. Each group is ordered with the original first.
  - `format_clone(c: Clone) -> str`
  - `clone_key(c: Clone) -> str`, in the form `kind\tnew.path\tnew.qualname\toriginal.path\toriginal.qualname`.

Rules:
- Only `comparable` shapes participate.
- A copy is an equal `exact_key`. A near clone is an equal `near_key` with a different `exact_key`, and must pass the Global Constraints near-clone limits on the positionally differing constants.
- **The original** is the one present in `before` by `(path, qualname)`; otherwise the lexicographically first `(path, qualname)`. A candidate never names itself as the original.
- **Pre-existing relations:** if the candidate and the original were both in `before` with equal keys of that kind, there is no finding.
- `blocking = not (is_test_path(new.path) or is_test_path(original.path))`.
- `base` is the first syntactic base class name the two shapes' classes share, when both are methods with the same qualname tail. Otherwise None.

Finding text, exactly as in spec §6.4:
- **Copy:** `{new.path}:{new.line} {name} is a copy of\n  {orig.path}:{orig.line} {orig_name}.\n  Delete it and import the original.`
- **Copy where the original is a method:** the last line becomes `  Move it to a shared helper and call that from both.`
- **Copy with a shared base:** `  Move it into the shared base class {base}.`
- **Near:** `{new.path}:{new.line} {name} is {orig.path}:{orig.line}\n  {orig_name} with different constants ({a} vs {b}). Add a parameter to\n  {orig_name} instead of copying it.`
  Here `a` and `b` are the differing values joined by `", "`, in new-then-original order.

- [ ] **Step 1: Write the failing tests**

```python
def test_new_copy_of_existing_function_blocks_and_names_original():
    c, = find_clones([s("b.py", "f", K1)], pool=[s("a.py", "f", K1), s("b.py", "f", K1)], before=[s("a.py", "f", K1)])
    assert (c.kind, c.original.path, c.blocking) == ("copy", "a.py", True)

def test_pre_existing_relation_is_silent():
    old = [s("a.py", "f", K1), s("b.py", "f", K1)]
    assert find_clones([s("b.py", "f", K1)], pool=old, before=old) == []

def test_two_new_copies_in_one_turn_pick_first_path_as_original():
    c, = find_clones([s("b.py", "f", K1), s("a.py", "g", K1)], pool=[s("a.py", "g", K1), s("b.py", "f", K1)], before=[])
    assert (c.new.path, c.original.path) == ("b.py", "a.py")

def test_test_files_are_advisory():
    c, = find_clones([s("tests/t.py", "f", K1)], pool=[s("src/a.py", "f", K1), s("tests/t.py", "f", K1)], before=[s("src/a.py", "f", K1)])
    assert c.blocking is False

@pytest.mark.parametrize("c1,c2,is_near", [
    ((0.90,), (0.95,), True),
    ((0, 1), (1, 0), False),        # all within {-1,0,1,2}
    ((1, 2, 3, 4), (5, 6, 7, 8), False),  # more than 3 differ
])
def test_near_clone_limits(c1, c2, is_near):
    assert bool(find_clones([near("b.py", c2)], pool=[near("a.py", c1), near("b.py", c2)], before=[near("a.py", c1)])) is is_near

def test_subscript_difference_is_not_a_near_clone():
    assert find_clones([near("b.py", (1,), sub=True)], pool=[near("a.py", (0,), sub=True), near("b.py", (1,), sub=True)], before=[near("a.py", (0,), sub=True)]) == []

def test_format_copy_matches_spec():
    assert format_clone(copy_fixture()) == (
        "src/features/lag_features.py:128 _infer_frequency_minutes is a copy of\n"
        "  src/features/derivative_features.py:182 _infer_frequency_minutes.\n"
        "  Delete it and import the original.")

def test_format_near_matches_spec():
    assert format_clone(near_fixture()) == (
        "src/monitoring/accuracy.py:147 within_95_ci is src/monitoring/accuracy.py:137\n"
        "  within_90_ci with different constants (0.95 vs 0.9). Add a parameter to\n"
        "  within_90_ci instead of copying it.")

def test_sibling_methods_suggest_shared_base():
    assert "shared base class Executor" in format_clone(sibling_fixture())

def test_2000_functions_index_and_match_under_2s():  # Review Focus
    texts = synthetic_repo(2000)
    t = time.monotonic(); shapes = index_shapes(texts); clone_groups(shapes)
    assert time.monotonic() - t < 2.0
```

`s`, `near`, the `*_fixture` helpers and `synthetic_repo` build `Shape`s or texts directly in the test module. `K1` is a fixed key string.

- [ ] **Step 2: Run and see it fail**

Run: `.venv/bin/pytest tests/test_clones.py -v`
Expected: FAIL, `No module named 'slopgate.clones'`.

- [ ] **Step 3: Implement `clones.py`**

Group by key in a dict, so matching is linear. Format floats with `repr`, so `0.9` prints as `0.9`.

- [ ] **Step 4: Run and see it pass**

Run: `.venv/bin/pytest tests/test_clones.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: copy and near-copy findings"
```

---

### Task 4: Wrapper notes

**Files:**
- Create: `src/slopgate/wrappers.py`
- Test: `tests/test_wrappers.py`

**Interfaces:**
- Consumes: Task 2 `is_test_path`.
- Produces:
  - `Wrapper(path: str, line: int, name: str, target: str)`
  - `find_wrappers(candidate_paths_qualnames: set[tuple[str, str]], texts: dict[str, str]) -> list[Wrapper]`
  - `format_wrapper(w: Wrapper) -> str`, giving exactly `note: {path}:{line} {name} only forwards to {target} and has no call sites found by name.`

A function is a wrapper when it is listed in `candidate_paths_qualnames`, its body without the docstring is one `return <call>` or `return await <call>`, and the call's arguments are exactly its own parameters (ignoring `self`/`cls`), each passed unchanged. Its call-site count is the number of `ast.Name`/`ast.Attribute` identifiers equal to its name, plus string constants equal to its name, across all `texts`, minus its own definition. It is reported only when that count is ≤ 1. It is never reported for dunders, names in any `__all__`, decorated functions, or test files.

- [ ] **Step 1: Write the failing tests**

```python
def test_forwarding_accessor_with_no_callers_is_noted():
    w, = find_wrappers({("m.py", "C.get_record")}, {"m.py": "class C:\n    def get_record(self, k):\n        return self._records.get(k)\n"})
    assert format_wrapper(w) == "note: m.py:2 get_record only forwards to self._records.get and has no call sites found by name."

def test_called_twice_is_not_noted():
    assert find_wrappers({("m.py", "w")}, {"m.py": "def w(x): return g(x)\n", "a.py": "w(1)\nw(2)\n"}) == []

def test_changed_arguments_are_not_forwarding():
    assert find_wrappers({("m.py", "w")}, {"m.py": "def w(x): return g(x + 1)\n"}) == []

@pytest.mark.parametrize("src", [
    "__all__ = ['w']\ndef w(x): return g(x)\n",
    "@app.get('/')\ndef w(x): return g(x)\n",
    "def __call__(self, x): return self.f(x)\n",
])
def test_exclusions(src):
    assert find_wrappers({("m.py", "w"), ("m.py", "__call__")}, {"m.py": src}) == []
```

- [ ] **Step 2: Run and see it fail**

Run: `.venv/bin/pytest tests/test_wrappers.py -v`
Expected: FAIL, `No module named 'slopgate.wrappers'`.

- [ ] **Step 3: Implement `wrappers.py`**
- [ ] **Step 4: Run and see it pass**

Run: `.venv/bin/pytest tests/test_wrappers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: advisory wrapper notes"
```

---

### Task 5: New-dependency notes

**Files:**
- Create: `src/slopgate/deps.py`
- Test: `tests/test_deps.py`

**Interfaces:**
- Produces:
  - `NewDep(path: str, line: int, module: str)`
  - `find_new_deps(candidate_paths: set[str], after: dict[str, str], before: dict[str, str]) -> list[NewDep]`. Arguments are the changed file paths, the current texts and the snapshot texts, each keyed by repo-relative path.
  - `format_new_dep(d: NewDep) -> str`, giving exactly `note: {path}:{line} imports a new dependency \`{module}\`\n  that this repo has never used before.`

Rules (spec §6.5a): consider the top-level module of absolute imports only. A module counts as a new dependency when it is not in `sys.stdlib_module_names`, it is not first-party (no `<name>.py` or `<name>/` exists among the `after` paths, either at the root or under `src/`), and no `before` text imports it. Report each module once per file, at its first import line. Files with syntax errors are skipped.

- [ ] **Step 1: Write the failing tests**

```python
def test_third_party_import_never_seen_before_is_noted():
    d, = find_new_deps({"a.py"}, {"a.py": "import os\nimport polars as pl\n"}, {"a.py": "import os\n"})
    assert format_new_dep(d) == "note: a.py:2 imports a new dependency `polars`\n  that this repo has never used before."

def test_dependency_already_used_elsewhere_is_not_new():
    assert find_new_deps({"b.py"}, {"a.py": "import numpy\n", "b.py": "import numpy\n"}, {"a.py": "import numpy\n"}) == []

@pytest.mark.parametrize("src", ["import json\n", "from . import x\n", "from mypkg import y\n", "from collections.abc import Mapping\n"])
def test_stdlib_relative_and_first_party_are_ignored(src):
    after = {"a.py": src, "src/mypkg/__init__.py": ""}
    assert find_new_deps({"a.py"}, after, {}) == []

def test_syntax_error_file_is_skipped():
    assert find_new_deps({"a.py"}, {"a.py": "import (\n"}, {}) == []
```

- [ ] **Step 2: Run and see it fail**

Run: `.venv/bin/pytest tests/test_deps.py -v`
Expected: FAIL, `No module named 'slopgate.deps'`.

- [ ] **Step 3: Implement `deps.py`**
- [ ] **Step 4: Run and see it pass**

Run: `.venv/bin/pytest tests/test_deps.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: advisory new-dependency notes"
```

---

### Task 6: Hook integration and `slopgate scan`

**Files:**
- Modify: `src/slopgate/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–5. M1's `find_reinventions`, `format_finding`, `should_block` and `load_rounds`.
- Produces: the `slopgate scan [path]` subcommand. The `check` output gains structural findings.

`check` changes (spec §6.5):
1. Build `before` shapes from `snap.files` and `after` shapes from the current texts, both with `index_shapes`.
2. Candidates are the shapes whose `(path, qualname)` is in `changes.new` or `changes.changed`.
3. Run `find_clones(candidates, after, before)`.
4. Remove every new function that has a `copy` clone from `changes.new` before calling `find_reinventions`.
5. Run `find_wrappers` on the new functions, and `find_new_deps` on the changed files.
6. **Blocking findings** are blocking clones plus reinvention findings. **Notes** are non-blocking clones, wrappers and new dependencies.
7. Keys are `clone_key(c)` for clones, and `reinvention\t…` (M1's key with that prefix) for reinventions.
8. The block reason is the blocking findings, then the notes, separated by blank lines. Notes alone never block.
9. Stats gain `copies`, `near`, `wrapper_notes`, `new_deps`.
10. The summary becomes `slopgate: {C} copies, {N} near-copies, {W} wrapper notes, {D} new dependencies, {new} new functions, {judgeable} judgeable, {findings} findings, {skipped} skipped`, plus the unsandboxed suffix where it applies.

`scan [path]` prints each `clone_groups` group as `copy:` or `near:` followed by its members, original first, then each wrapper note, with groups that have a src member printed first. It uses the git-tracked `.py` texts of `path` and always exits 0.

- [ ] **Step 1: Write the failing tests**

```python
def test_pasted_copy_blocks_with_copy_finding(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo)
    paste_copy(fixture_git_repo)   # copies a >=40-node function from a.py into b.py with renamed locals
    run_hook(["check"], fixture_git_repo)
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "is a copy of" in out["reason"] and "1 copies" in out["systemMessage"]

def test_editing_one_of_existing_copies_does_not_block(fixture_git_repo_with_two_copies, capsys):
    run_hook(["start"], fixture_git_repo_with_two_copies)
    edit_docstring_of_one_copy(fixture_git_repo_with_two_copies)
    run_hook(["check"], fixture_git_repo_with_two_copies)
    assert "decision" not in json.loads(capsys.readouterr().out)

def test_copy_skips_the_prover(fixture_git_repo, capsys, monkeypatch):
    seen = []; monkeypatch.setattr("slopgate.cli.find_reinventions", record_new_names(seen))
    run_hook(["start"], fixture_git_repo); paste_copy(fixture_git_repo); run_hook(["check"], fixture_git_repo)
    assert pasted_name() not in seen

def test_wrapper_note_never_blocks_alone(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo); add_forwarding_wrapper(fixture_git_repo); run_hook(["check"], fixture_git_repo)
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "1 wrapper notes" in out["systemMessage"]

def test_new_dependency_is_a_note_not_a_block(fixture_git_repo, capsys):
    run_hook(["start"], fixture_git_repo); add_new_import(fixture_git_repo, "polars"); run_hook(["check"], fixture_git_repo)
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "1 new dependencies" in out["systemMessage"]

def test_syntax_error_file_does_not_break_check(fixture_git_repo, capsys):  # Review Focus
    run_hook(["start"], fixture_git_repo); (fixture_git_repo / "broken.py").write_text("def f(:\n"); run_hook(["check"], fixture_git_repo)
    assert "internal error" not in json.loads(capsys.readouterr().out)["systemMessage"]

def test_scan_reports_existing_copies(fixture_git_repo_with_two_copies, capsys):
    assert main(["scan", str(fixture_git_repo_with_two_copies)]) == 0
    assert "copy:" in capsys.readouterr().out
```

Add the helpers to `tests/conftest.py`: `paste_copy`, `pasted_name`, `fixture_git_repo_with_two_copies`, `edit_docstring_of_one_copy`, `add_forwarding_wrapper`, `add_new_import`, `record_new_names`. The fixture functions must clear the 40-node floor.

- [ ] **Step 2: Run and see it fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement the `check` changes and the `scan` subcommand in `cli.py`**
- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. The sandbox write-block test still runs and is not skipped.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: structural findings in the hook and slopgate scan"
```

---

### Task 7: Evaluation on real repos, and the README

**Files:**
- Modify: `README.md`
- Create (not committed): an evaluation report in the plan workspace.

**Interfaces:**
- Consumes: the installed CLI.

- [ ] **Step 1: Clone three repos into a scratch directory, never touching the originals**

```bash
for r in predictive-scaling <repo-2> <repo-3>; do git clone -q --no-hardlinks "<projects>/$r" "<scratch>/eval/$r"; done
git -C "<scratch>/eval/predictive-scaling" checkout -q 9baf9c4
```

- [ ] **Step 2: Run `slopgate scan` on each and record the output**

Expected on `predictive-scaling` at `9baf9c4` (the recall check, spec §7.1): copy groups for `_infer_frequency_minutes` (5 members), `rollback` (`aws.py`, `terraform.py`), `stop` (3 members) and `_get_client` (2 members), and a near group for `within_90_ci`/`within_95_ci`. If any is missing, report BLOCKED with the reason; do not weaken the normalization to force a match.

- [ ] **Step 3: Hand-check every reported finding** in all three repos, and list each one as true or false in the report. Any false clone claim makes the task BLOCKED, with the pair attached.

- [ ] **Step 4: Update `README.md`**

Lead with what `scan` found on `predictive-scaling` (real findings, not planted), then describe the three checks and what blocks versus what is advisory. Keep the M1 coverage section and its numbers unchanged, and keep "roundabout logic" listed as not handled.

- [ ] **Step 5: Commit**

```bash
git add README.md && git commit -m "docs: real structural findings in README"
```
