# Pandas FutureWarning Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for Tasks 1–3, superpowers:requesting-code-review after implementation, and superpowers:verification-before-completion before any completion claim.

**Goal:** Remove the 90 repeated pandas silent-downcasting warnings from the GMV/export pipeline while preserving all business values, formal-scope rules, and workbook contracts.

**Architecture:** Keep the change local to `pipeline.py`: one private date-coalescing helper for the two warning-producing date paths plus the structurally identical ticket-date path, and explicit numeric-only fill for the warning-producing merged columns. A focused future-compatibility test module runs the affected flows with future pandas behavior and `FutureWarning` as an error. Existing trusted-reference, export, baseline, Review, and Hermes gates remain authoritative.

**Tech Stack:** Python, pandas 2.3.3, pytest, Streamlit pipeline/export code, existing NBS Review and Hermes tooling.

---

## Global Execution Constraints

- Use an isolated `codex/` worktree; do not modify the dirty local `main` worktree.
- Memory Hub and Context/Review Agents are read-only and non-authoritative.
- Do not write production SQLite or formal runtime business data.
- Do not change revenue scope, baseline, refund rules, export schema, cache manifests, or active-version pointers.
- Implementation Agent executes one approved Task at a time and does not commit, push, or merge. Git integration remains a Codex action after explicit user instruction.
- After each Task, produce a checkpoint report containing changed files, exact tests, results, and remaining risks.

### Task 1 — Checkpoint 1: Freeze the future-compatibility contract

**Files:**

- Create: `tests/test_pipeline_future_compatibility.py`
- Read/reference: `tests/test_gmv_export_performance.py`
- Read/reference: `tests/test_gmv_one_click_merge_integration.py`
- Read/reference: `pipeline.py:810-900`

**Step 1: Add a red test for date coalescing semantics**

Cover:

- valid formatted primary date
- missing primary with fallback
- both values missing
- empty primary string remaining empty
- mixed object/string operands
- non-default and non-contiguous index

Assert row values and index equality, and execute inside:

```python
with pd.option_context("future.no_silent_downcasting", True):
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        ...
```

**Step 2: Add a red test for numeric-only fill semantics**

Build the minimal merged-frame shape and call the intended private numeric helper with:

- `t_name`
- `郵輪交易人數`
- a date column
- an identifier/text column

Assert only the two numeric columns receive explicit numeric zero fill and the text/date/identifier columns retain missing values and original content. The test must fail before the helper exists.

**Step 3: Add affected-flow warning gates**

Exercise the smallest existing GMV/export fixtures that reach all three equivalent date paths and the merged numeric path with `FutureWarning` fatal under the legacy option. Add a second execution of the same flow under `future.no_silent_downcasting=True` to prove future-mode compatibility.

**Step 4: Run the red tests**

```bash
.venv/bin/python -m pytest tests/test_pipeline_future_compatibility.py -q
```

Expected: FAIL against the current implementation, demonstrating at least one silent-downcasting warning or missing explicit helper contract.

**Step 5: Record checkpoint evidence**

Report exact failing test names and warning stack locations. Do not modify implementation in this Task.

### Task 2 — Checkpoint 2: Implement the bounded dtype fix

**Files:**

- Modify: `pipeline.py:810-900`
- Test: `tests/test_pipeline_future_compatibility.py`

**Step 1: Implement `_coalesce_date_strings`**

Implement a private helper that:

- aligns by index
- converts both inputs to pandas nullable `string`
- fills only missing primary values from fallback
- preserves empty strings
- avoids implicit object downcasting

Do not broaden the helper into general dataframe normalization.

**Step 2: Replace only the bounded equivalent date expressions**

Use the helper at the current warning sites around `pipeline.py:821` and `pipeline.py:890`, plus the structurally identical ticket-date expression around `pipeline.py:843`. Leave all unrelated date paths unchanged.

**Step 3: Replace whole-frame numeric fill**

At the current warning site around `pipeline.py:868`, call `_fill_gmv_numeric_columns` for only `[t_name, "郵輪交易人數"]`. Do not call `.fillna(0)` on the complete merged DataFrame.

**Step 4: Run focused compatibility tests**

```bash
.venv/bin/python -m pytest tests/test_pipeline_future_compatibility.py -q
```

Expected: PASS with no `FutureWarning`.

**Step 5: Run the original warning-producing tests as errors**

```bash
.venv/bin/python -m pytest \
  tests/test_gmv_export_performance.py \
  tests/test_gmv_one_click_merge_integration.py \
  -q -W error::FutureWarning
```

Expected: all tests PASS and zero `FutureWarning`.

**Step 6: Inspect the bounded diff**

```bash
git diff -- pipeline.py tests/test_pipeline_future_compatibility.py
git diff --check
```

Expected: only helper/test additions, the three bounded date call-site changes, and the one numeric-fill call-site change.

### Task 3 — Checkpoint 3: Prove business and artifact equivalence

**Files:**

- Modify only if a missing assertion is proven: `tests/test_pipeline_future_compatibility.py`
- Read/reference: existing export equivalence, formal-scope, and baseline tests discovered with `rg`

**Step 1: Discover existing authoritative tests**

```bash
rg -n "12057968|12,057,968|semantic|equivalence|official|no_writeoff|refundTotal|appliedRefundTotal" tests
```

Select the narrowest existing tests that cover:

- May 2026 frozen baseline
- formal revenue exclusions
- total-refund and paid-refund exports
- `all`, `no_writeoff`, and `official` variants
- workbook semantic equivalence

**Step 2: Run trusted-reference and business tests**

Run the discovered tests with `-W error::FutureWarning` where compatible. Record totals and artifact comparison results.

Expected:

- baseline remains `HKD 12,057,968`
- formal scope remains `不含掛賬核銷與TT退款轉團款`
- semantic mismatch count is zero
- schemas, sheet names, columns, order, and normalized values are unchanged

**Step 3: Add only genuinely missing regression assertions**

If existing tests do not cover one matrix cell, add the smallest assertion to `tests/test_pipeline_future_compatibility.py`. Do not regenerate or overwrite trusted production artifacts.

**Step 4: Re-run Task 2 gates**

```bash
.venv/bin/python -m pytest tests/test_pipeline_future_compatibility.py -q
.venv/bin/python -m pytest \
  tests/test_gmv_export_performance.py \
  tests/test_gmv_one_click_merge_integration.py \
  -q -W error::FutureWarning
git diff --check
```

Expected: PASS.

### Task 4 — Checkpoint 4: Review, full verification, and Hermes

**Files:**

- No intended source changes unless resolving a proven finding.
- Produce runtime reports only under the existing ignored agent/Hermes report paths.

**Step 1: Run findings-first Review**

Use the project Review Agent contract against `WORKTREE`, not `HEAD` versus `HEAD`. Review scope:

- dtype correctness and index alignment
- no text/identifier coercion
- no business-rule or schema drift
- warning gate quality
- bounded diff and unrelated-change isolation

Expected: PASS with no unresolved findings. If Review finds a defect, return to the owning Task, fix with TDD, and re-run its gates.

**Step 2: Run full pytest**

```bash
.venv/bin/python -m pytest -q
```

Expected:

- all tests PASS
- the prior 90 pandas warnings are absent
- no new warnings replace them

**Step 3: Run warning-as-error acceptance once more**

```bash
.venv/bin/python -m pytest \
  tests/test_pipeline_future_compatibility.py \
  tests/test_gmv_export_performance.py \
  tests/test_gmv_one_click_merge_integration.py \
  -q -W error::FutureWarning
```

Expected: PASS.

**Step 4: Run Hermes post-change check**

```bash
.venv/bin/python scripts/hermes_post_change_check.py
```

Expected: PASS under `NBS_HERMES_MONITORING.md`. Keep Hermes independent from Review.

**Step 5: Final acceptance report**

Report:

- files changed
- exact test counts and warning counts
- trusted-reference mismatch count
- frozen-baseline result
- Review result and report path
- Hermes result and report path
- confirmation that SQLite, runtime data, schemas, cache pointers, and Git were not modified

Stop before commit/push/PR/merge unless the user explicitly requests Git integration.
