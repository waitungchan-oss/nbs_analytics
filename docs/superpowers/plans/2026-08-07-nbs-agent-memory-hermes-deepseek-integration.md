# NBS Agent Memory Sidecar：Hermes + DeepSeek v4 Flash Integration Implementation Plan

## Goal

在保持 Scheme A pilot 預設安全與 canonical-artifact-first 的前提下，建立 provider-neutral integration、受控 Hermes runner evidence 與真實 A/B acceptance harness，驗證 `deepseek-v4-flash` 是否能降低 Context Agent 的重複探索成本。未通過 acceptance 前，recall 保持 disabled，writer 永遠 disabled。

## Architecture

- `MemorySidecarProvider` 維持 provider-neutral protocol；新增 adapter 只負責 bounded `memory-hints-v1` recall。
- desktop Hermes (`com.nousresearch.hermes`) 是外部 implementation runner；NBS `scripts/hermes_post_change_check.py` 仍是 read-only acceptance。
- A/B harness 使用 immutable run input：`git_head`、task fingerprint、brief、allowed files、commands、provider/model identity。
- sidecar telemetry 與 A/B acceptance evidence 是 diagnostic evidence，不進入 canonical artifacts、Governance Graph 或 approval state。
- fallback、timeout、schema mismatch、stale/conflict、path violation、sensitive capture 一律 fail closed。

## Tech Stack

- Python 3、dataclasses、既有 `backend/agents` service/model pattern
- pytest、`py_compile`
- Hermes desktop `deepseek-v4-flash`（僅作受控 implementation runner）
- 既有 `.nbs_agent_runtime` evidence helpers；不得新增 SQLite 或 network write path

## Global Constraints

- allowed files 必須逐 Task 宣告；不修改正式 SQLite、baseline、rollback、revenue scope、business rules、export schema、Graph authority 或 runtime acceptance state。
- `recall_enabled=false`、`writer_enabled=false`、`shadow_mode=true` 是預設值。
- 每個 Task 完成後停在 findings-first Review；Review Agent 與 Hermes 只讀，不直接修檔。
- 不得輸入 credentials、API keys 或修改 Hermes security settings。
- 不得直接把 desktop Hermes 或 DeepSeek 輸出當成 Review PASS、Hermes PASS 或 canonical evidence。

## Task 0 — Validate Hermes runner capability without file changes

**Allowed files:** none. **Evidence only:** local Hermes UI state.

1. 確認 Hermes project 是 `nbs_analytics`，workspace 是 repo 或隔離 worktree。
2. 確認模型顯示為 `deepseek-v4-flash`，並確認可宣告 task scope、allowed files、commands。
3. 確認 UI 能回報 run completion／failure，而不需要輸入 credentials 或修改安全設定。
4. 若任一項無法驗證，產生 `blocked_runner_capability`，停止後續 implementation，不以模型名稱推測能力。

**Acceptance:** capability evidence 包含 project、workspace、model、scope boundary；沒有檔案 diff。

## Task 1 — Add provider identity and controlled recall request contract

**Allowed files:**

- `backend/agents/memory_sidecar_adapter.py`
- `backend/agents/memory_sidecar_models.py`（只有必要的 provider metadata model）
- `tests/test_memory_sidecar_adapter.py`
- `tests/test_memory_sidecar_models.py`

**TDD steps:**

1. 先寫 failing tests：provider、model、request fingerprint、schema version、limits、allowed task fingerprint 必須存在且 bounded。
2. 實作 immutable provider metadata/request model 與 deterministic fingerprint。
3. 驗證 recall request 不允許絕對路徑、secret、SQLite/CSV/log payload 或未宣告 task。
4. 保留 `FakeMemorySidecarProvider` 作 deterministic fallback，writer 呼叫仍不會被 integration 啟用。

**Acceptance:** schema、fingerprint、forbidden input、fallback tests PASS；既有 memory sidecar tests 不退化。

## Task 2 — Implement a read-only provider adapter boundary

**Allowed files:**

- `backend/agents/memory_sidecar_provider_adapter.py`（new）
- `backend/agents/memory_sidecar_service.py`
- `backend/agents/memory_sidecar_policy.py`
- `tests/test_memory_sidecar_provider_adapter.py`（new）
- `tests/test_memory_sidecar_service.py`

**TDD steps:**

1. 先測 provider unavailable、timeout、malformed response、stale/conflict、sensitive capture、path violation 均回傳 explicit fail-closed status。
2. 實作 provider-neutral adapter；只接受 `memory-hints-v1`，套用既有 3 items／6000 bytes／800 ms caps。
3. 加入 provider/model/request fingerprint 與 fallback reason 到 bounded telemetry metadata。
4. 明確拒絕 `write_candidate`；`writer_enabled` 在本 phase 仍不可由 adapter 改變。

**Acceptance:** adapter 不執行 network、shell、SQLite、Git 或 runtime state write；所有 negative-path tests PASS。

## Task 3 — Add immutable A/B acceptance models and harness

**Allowed files:**

- `backend/agents/memory_sidecar_ab.py`（new）
- `backend/agents/memory_sidecar_telemetry.py`
- `tests/test_memory_sidecar_ab.py`（new）
- `tests/test_memory_sidecar_telemetry.py`

**TDD steps:**

1. 先寫 tests：A/B run 必須共用 HEAD、task fingerprint、brief、allowed files、commands；cohort 只能是 `recall_off` 或 `recall_on`。
2. 實作 immutable run record、token delta、p95 latency、evidence coverage、fallback、forbidden capture 與 canonical invariants 計算。
3. 實作 acceptance gate：input reduction >=20% 或明確 alternative evidence；coverage 100%；p95 <=800ms；Review/Hermes no regression；sensitive capture 0；baseline/formal scope unchanged。
4. 對 gate failure 產生 `acceptance_rejected`，不得自動打開 recall。

**Acceptance:** deterministic fixture 與 synthetic failure cases PASS；報告不包含 secret 或完整 prompt。

## Task 4 — Integrate optional recall into Context Agent without changing authority

**Allowed files:**

- `backend/agents/context_agent_service.py`
- `backend/agents/memory_sidecar_service.py`
- `tests/test_memory_sidecar_context_integration.py`
- `tests/test_context_agent_service.py`

**TDD steps:**

1. 先寫 tests：recall-off 與 provider failure 的 canonical context output 相同；recall-on hints 以 `non_authoritative_memory` 分區出現。
2. 將 adapter 呼叫接在 canonical collection 之外，任何正式判斷仍重新驗證 canonical evidence。
3. 將 stale/conflict/timeout/empty hint 明確保留為 diagnostic status，不注入正式 evidence。
4. 驗證 Graph projection、Review evidence 與 baseline payload 不讀取 memory hints。

**Acceptance:** context integration tests PASS；canonical output fingerprint 在 recall-off/fallback 時維持一致。

## Task 5 — Produce real Hermes-backed A/B evidence

**Allowed files:**

- `.nbs_agent_runtime/runs/<run-id>/` evidence files only when generated by approved runner
- `docs/agents/MEMORY_SIDECAR_CONTRACT.md` only if contract wording requires a minimal clarification
- `tests/fixtures/memory_sidecar/` deterministic fixtures only

1. 使用 Hermes `deepseek-v4-flash` 在同一 immutable HEAD、task brief、allowed files、commands 下執行 A cohort。
2. 重複相同條件執行 B cohort，唯一差異是 recall flag；writer 仍關閉。
3. 驗證每個 hint 的 sourceRefs、identity、provider/model、latency、fallback 與 task fingerprint。
4. 產生 `memory-sidecar-ab-acceptance-v1`，若模型輸出或 runner boundary 無法驗證，標示 `blocked_runner_capability`，不得偽造通過。

**Acceptance:** A/B evidence 可重現、100% provenance、無 sensitive capture；未達門檻時 recall 維持 off。

## Task 6 — Strict Review, full verification and Hermes acceptance

**Allowed files:** none beyond approved fixes from earlier Tasks.

1. 將 immutable task diff、tests 與 evidence 交給 Review Agent，採 findings-first。
2. 修正 findings 後執行 targeted pytest、`py_compile` 與完整 `pytest -q`。
3. 執行 `scripts/system_manager.py acceptance`（若涉及跨模組）與 `scripts/hermes_post_change_check.py`。
4. 確認 baseline、formal scope、SQLite integrity、Graph authority 與 writer default 未改變。
5. 只有 Review PASS、full verification PASS、Hermes PASS 且 A/B gates 全部通過後，才可提出 recall rollout；否則保留 pilot defaults。

**Acceptance:** no unresolved findings；full verification/Hermes PASS；完成報告列出 model、provider、A/B metrics、fallback 與所有 blocked/unknown 狀態。

## Rollback

關閉 provider integration feature flag 即可回到現有 deterministic/fallback path。不得刪除 canonical artifacts、修改 baseline 或回退使用者既有 dirty changes。任何 A/B gate failure 都以 recall-off 作為可逆 rollback。

