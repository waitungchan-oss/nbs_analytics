# NBS Streamlit Theme Repair and Short-term Offload Evidence Design Spec

**Date:** 2026-08-14
**Status:** Approved for implementation planning
**Scope:** Streamlit UI theme correctness plus bounded real-workflow evidence for Short-term Offload on/off.

## 1. Problem and outcome

The Streamlit sidebar theme selector changes `NBS_UI_THEME`, but the dark view still renders a light page background. The root cause is CSS ordering: `streamlit_rendering._render_dynamic_theme_css()` emits theme-specific `:root` variables first, while `app_styles.apply_global_styles()` later emits a second fixed light `:root` block and overwrites the same variables.

This change will make the selected theme visible across the page shell, cards, sidebar, controls, tables and headings. It will also produce a real, bounded comparison of the same workflow with Short-term Offload disabled and enabled. The comparison may report token reduction only when both live receipts and usage evidence are complete and bound to the same workload.

## 2. Boundaries

- Canonical revenue, baseline, SQLite, Graph snapshot, approval, dispatch and workflow authority are unchanged.
- Streamlit theme is presentation-only; it cannot alter analytical values or business rules.
- Short-term Offload remains explicit opt-in. Ordinary workflow and Memory Sidecar defaults remain unchanged.
- Offload artifacts remain under `.nbs_agent_runtime/short-term-offload`; no secrets, prompts, customer rows, internal reasoning or raw credentials may be stored.
- Evidence is read-only after the live run. The evidence builder cannot fabricate token counts, latency, model identity or provenance.
- If model usage metadata, treatment receipt, control receipt, or workload identity is missing, the result is `blocked_runner_capability` or `completion_missing`, not a token-saving claim.

## 3. Theme design

`streamlit_rendering._theme_tokens(theme)` remains the single source of theme values. `app_styles.apply_global_styles()` must not redefine any dynamic `--nbs-*` color token after that function runs. Static CSS may define typography, layout, borders, component selectors and non-color fallbacks only.

Required token behavior:

- `light`: page background is light, surface/cards are light, text is dark.
- `dark`: page background is dark, surface/cards are dark, text is light.
- The same selected mode must style the main shell and sidebar.
- No CSS rule may unconditionally reset `--nbs-page-bg`, `--nbs-surface`, `--nbs-text`, `--nbs-sidebar-bg` or their theme-derived companions to light values.

## 4. Evidence design

The evidence builder consumes two immutable run records:

```text
control: short_term_offload=off
treatment: short_term_offload=on
```

Both records must share:

- workload fingerprint;
- project Git head and clean-worktree fingerprint;
- provider, model and reasoning profile;
- source/evidence fingerprints;
- task and session binding;
- comparable request sequence.

The treatment may additionally contain offload artifact references, but must not change the workload or prompt to gain an advantage.

The comparison envelope is `short-term-offload-ab-evidence-v1` and includes:

- `controlRunId`, `treatmentRunId`, `workloadFingerprint`;
- `controlReceiptRef`, `treatmentReceiptRef`;
- `controlPromptTokens`, `treatmentPromptTokens`;
- `controlCompletionTokens`, `treatmentCompletionTokens`;
- `controlTotalTokens`, `treatmentTotalTokens`;
- `controlLatencyMs`, `treatmentLatencyMs`;
- `tokenReductionRatio`, `latencyDeltaRatio`;
- `provenanceRefs`, `evidenceFingerprint`, `status`, `reasons`.

`tokenReductionRatio` is calculated only when both prompt and total token fields are observed, positive, numeric, and bound to the live receipts:

```text
tokenReductionRatio = (controlTotalTokens - treatmentTotalTokens) / controlTotalTokens
```

The result must distinguish:

- `pass`: complete live evidence and a non-negative observed reduction;
- `no_reduction`: complete live evidence but reduction is zero or negative;
- `blocked_runner_capability`: missing/invalid runner identity, model usage, provenance or offload binding;
- `completion_missing`: one or both runs lack completed receipts.

No token reduction percentage is displayed for blocked or incomplete evidence. A local test fixture may validate arithmetic, but it cannot be reported as real workflow evidence.

## 5. Real workflow acceptance

The acceptance workload is one bounded read-only workflow that produces a long tool output eligible for offload. It runs control and treatment separately with the same immutable workload descriptor. The operator records the live receipts and the evidence envelope under an isolated runtime directory, then re-reads and validates all fingerprints.

Acceptance requires:

1. Three independent control/treatment pairs, unless the first pair is blocked before transport.
2. Live provider/model/reasoning identity and observed usage fields on every accepted pair.
3. Matching workload, Git head, source refs and request sequence.
4. Treatment offload artifact is present and sanitized; control remains inline.
5. Evidence can be replayed from files without network access.
6. Any missing evidence produces a blocked result and no token-saving claim.

The final report must show each pair, the raw observed fields, the derived ratio and the exact blocked reason when not accepted.

## 6. Testing and verification

- Add a source contract test proving static CSS does not override dynamic theme variables.
- Add token tests for light/dark shell values and theme selector state.
- Add schema tests for exact evidence keys, numeric bounds, workload identity and fail-closed statuses.
- Add tamper, missing receipt, mismatched workload and missing usage regressions.
- Run the affected pytest modules, `py_compile`, `git diff --check`, full pytest, system acceptance and `scripts/hermes_post_change_check.py`.
- Perform browser verification at `http://127.0.0.1:8502/`: switch light → dark → light and inspect the page shell, sidebar, cards, controls and tables.

## 7. Explicit non-goals

- No automatic snapshot creation.
- No global recall-on/default-on change.
- No CSS rewrite of unrelated dashboard components.
- No synthetic token estimates presented as live evidence.
- No external notification, approval or workflow dispatch.
