# NBS Governance Graph Phase E-3 Owner／Dependency Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立兩個獨立 immutable Owner／Dependency catalog 的 strict public contracts、共用 read-only validation service、bounded consumer adapters 與 E-2 callback boundary，讓 Governance Graph 可追蹤治理責任角色與明確宣告的 dependency，而不推測不存在的關係。

**Architecture:** `governance_graph_catalog_models.py` 定義 owner／dependency envelopes、safe identifiers、fingerprints 與 diagnostics；`governance_graph_catalog_service.py` 只消費 caller 提供的 validated mapping，獨立驗證兩個 catalog 後組合 deterministic read model。CLI、D-1～D-4 adapters 與 E-2 Streamlit 只消費 read model，不取得 writer、path、SQLite、Git 或 runtime authority。

**Tech Stack:** Python 3、dataclasses／既有 workflow validation helpers、Streamlit callback、stdin-only CLI、pytest、system acceptance、Hermes。

### Reconciliation (2026-07-30)

Task 1–5 已依本 plan 完成並通過 immutable strict Review；Task 6 final acceptance evidence：focused E-3 tests 192 passed、full pytest 1389 passed、受影響 Python `py_compile` passed、`scripts/system_manager.py acceptance` passed、final Review PASS（immutable range `1c10cb9..a548ef4`）、`scripts/hermes_post_change_check.py` exit 0 / Overall PASS。Hermes system-monitor 仍觀察到既有 cache generation signature mismatch（overall Hermes 仍 PASS，SQLite integrity、baseline 與正式口徑 matched）；此為既有監測狀態，未由 E-3 變更修正或寫入。E-3 未 push、未建立 PR、未 merge、未刪除 branch。

## Global Constraints

- Owner 只代表治理角色／責任群組，不保存個人姓名、email、GitHub handle、Git author 或聯絡資料。
- Owner schema 固定為 `governance-graph-owner-catalog-v1`；policy 固定為 `e3-owner-policy-v1`。
- Dependency schema 固定為 `governance-graph-dependency-catalog-v1`；policy 固定為 `e3-dependency-policy-v1`。
- Combined read model 固定為 `governance-graph-owner-dependency-read-v1`。
- Owner role allowlist 固定為 `spec_owner`、`plan_owner`、`implementation_owner`、`review_owner`、`verification_owner`、`hermes_owner`、`documentation_owner`。
- Dependency `relation` 只允許 `requires`、`produces`、`implements`、`reviews`、`verifies`、`blocks`、`derived_from`、`committed_as`、`documented_by`。
- Dependency `relationKind` 只允許 `workflow_edge` 與 `declared_dependency`；workflow edge 不得改名為 business、causal 或 downstream dependency。
- 狀態 precedence 固定為 `invalid > stale > blocked > unknown > missing > unavailable > available`。
- 完全相同 duplicate 一律 deterministic dedupe；相同 identity 的 metadata／source／owner／relation conflict 一律 `invalid`；不得 last-write-wins。
- `source.fingerprint` 與 `snapshotFingerprint` 必須是 lowercase SHA-256；cross-run 或 snapshot mismatch 必須為 `stale`。
- `source.kind` 是 closed allowlist：`approved_catalog`、`graph_contract`、`canonical_evidence`；source identity 不得是 URI、absolute path、raw JSON、secret、prompt 或 command。
- Service 必須 side-effect free：不得讀 raw runtime、SQLite、Git、network、target governance 或任意 filesystem path；不得寫 runtime、Graph snapshot、canonical artifacts、cache、Git 或正式業務資料。
- 不得由 nodeId、edge order、artifact filename、registry writer、timestamp、Git blame、finding、risk／impact category 或文字內容推導 owner／dependency。
- 不修改 baseline `HKD 12,057,968`、正式口徑「不含掛賬核銷與TT退款轉團款」、SQLite、revenue、business rules、rollback 或 export schema。
- 每個 Task 都必須 TDD RED→GREEN、allowlisted files、focused tests、strict Review；Implementation Agent 不 commit／merge／push。

---

## File Map

- Create: `backend/agents/governance_graph_catalog_models.py` — immutable public models、strict parsers、safe metadata、canonical fingerprint。
- Create: `backend/agents/governance_graph_catalog_service.py` — independent owner/dependency validation 與 combined bounded read model。
- Create: `backend/agents/governance_graph_catalog_adapters.py` — D-1～D-4 的 pure read-model projections，不改寫既有 query/comparison/risk/impact semantics。
- Modify: `scripts/governance_graph.py` — stdin-only `catalog-validate` command，禁止 path/writer/control flags。
- Modify: `governance_graph_rendering.py` — optional E-3 callback 與 bounded owner/dependency panel。
- Modify: `agent_operations_rendering.py` — 傳遞 `catalog_lookup` callback，保留既有 selected-run lifecycle。
- Modify: `app_pages.py` — 注入 validated catalog callback；不建立 catalog、不寫入 session authority。
- Create: `tests/test_governance_graph_catalog_models.py`
- Create: `tests/test_governance_graph_catalog_service.py`
- Create: `tests/test_governance_graph_catalog_adapters.py`
- Modify: `tests/test_governance_graph_cli.py`
- Modify: `tests/test_governance_graph_rendering.py`
- Modify: `tests/test_agent_operations_rendering.py`
- Create: `tests/test_app_pages_governance_graph_catalog.py`

---

### Task 1: 建立 Owner／Dependency strict public models

**Files:**
- Create: `backend/agents/governance_graph_catalog_models.py`
- Create: `tests/test_governance_graph_catalog_models.py`

**Interfaces:**
- Consumes: `Mapping[str, Any]` catalog envelopes；既有 `canonical_sha256`／safe validation patterns；envelope-level 與 entry-level source provenance。
- Produces: `GovernanceGraphOwnerCatalog.from_dict()`、`GovernanceGraphDependencyCatalog.from_dict()`、`GovernanceGraphOwnerDependencyReadModel.to_dict()`、各自 `to_dict()` 與 deterministic fingerprint。

- [x] **Step 1: Write failing model tests.** 建立 fixture 並測試 exact public keys、schema／policy version、role／relation allowlist、envelope-level 與 entry-level `source.kind` closed allowlist（`approved_catalog`、`graph_contract`、`canonical_evidence`）、bounded source identity、lowercase SHA-256、safe identifier、absolute path／URI／secret／raw JSON／prompt／command rejection、missing／unknown／stale／blocked／invalid parsing、owner subject conflict、dependency identity conflict 與 deterministic duplicate dedupe。

  測試 fixture 至少包含：

  ```python
  OWNER_ROLE = "review_owner"
  DEPENDENCY = {"from": {"kind": "node", "id": "implementation"}, "to": {"kind": "node", "id": "verification"}, "relation": "requires", "relationKind": "workflow_edge"}
  ```

- [x] **Step 2: Run model tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_catalog_models.py -q`

  Expected: import／constructor failures because E-3 catalog models do not yet exist。

- [x] **Step 3: Implement immutable models and parsers.** 使用 frozen dataclasses 或既有 model pattern，固定 schemas、policy versions、allowlists、bounded entries、diagnostics 與 fingerprint exclusion rules。完全相同 duplicate 依 canonical identity dedupe；conflict、unsafe metadata、unsupported relation 或 missing required provenance 回傳 strict validation error，不能 fallback。

- [x] **Step 4: Run model tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_catalog_models.py -q`

  Expected: 所有 model tests PASS，且 tests 不建立 runtime、Graph snapshot 或 catalog writer。

- [x] **Step 5: Submit Task 1 to strict Review.** Review 只涵蓋 models 與 model tests，確認 exact keys、safe bounds、fingerprint provenance、role-only owner 與 no-write boundary。

- [x] **Step 6: After Review PASS, Codex commits Task 1.**

  ```bash
  git add backend/agents/governance_graph_catalog_models.py tests/test_governance_graph_catalog_models.py
  git commit -m "feat: add governance graph owner dependency catalog models"
  ```

### Task 2: Implement shared read-only catalog service

**Files:**
- Create: `backend/agents/governance_graph_catalog_service.py`
- Create: `tests/test_governance_graph_catalog_service.py`

**Interfaces:**
- Consumes: Task 1 `GovernanceGraphOwnerCatalog`、`GovernanceGraphDependencyCatalog`；selected `snapshot_fingerprint`。
- Produces: `OwnerDependencyReadService.resolve(*, snapshot_fingerprint: str, owner_catalog: Mapping[str, Any] | None, dependency_catalog: Mapping[str, Any] | None) -> GovernanceGraphOwnerDependencyReadModel`。

- [x] **Step 1: Write failing service tests.** 覆蓋兩個 catalog 都 available、只有 owner、只有 dependency、caller 未提供 catalog、catalog fingerprint mismatch、使用兩個不同 synthetic snapshot fingerprint 的 cross-run binding mismatch（v1 不新增 runId，cross-run 等同 selected snapshot fingerprint mismatch）、owner available＋dependency invalid、blocked／unknown／missing precedence、conflicting duplicate、one-side isolation、deterministic repeated output 與 no-write tree/runtime assertions。

- [x] **Step 2: Run service tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_catalog_service.py -q`

  Expected: service import／method failures。

- [x] **Step 3: Implement validation and composition only.** Service 先以 Task 1 parser 驗證每一側，再以 selected snapshot fingerprint 做 binding；v1 不引入額外 runId，跨 run 僅能透過不同 selected snapshot fingerprint 判定 stale。每一側保留自己的 status／diagnostics，overall status 依 `invalid > stale > blocked > unknown > missing > unavailable > available` 計算。只輸出 bounded owners、dependencies、coverage、source fingerprints 與 read-model fingerprint；不讀檔、不呼叫 D-1～D-4 writer、不寫任何 state。

- [x] **Step 4: Run service tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_catalog_service.py -q`

  Expected: service tests PASS，重複 input 產生相同 canonical output 與 fingerprint。

- [x] **Step 5: Submit Task 2 to strict Review.** 確認 service 沒有 catalog auto-generation、filesystem／SQLite／Git access、owner/dependency inference、last-write-wins 或 cross-run fallback。

- [x] **Step 6: After Review PASS, Codex commits Task 2.**

  ```bash
  git add backend/agents/governance_graph_catalog_service.py tests/test_governance_graph_catalog_service.py
  git commit -m "feat: add read-only governance graph catalog service"
  ```

### Task 3: Add D-1～D-4 pure read-model adapters

**Files:**
- Create: `backend/agents/governance_graph_catalog_adapters.py`
- Create: `tests/test_governance_graph_catalog_adapters.py`

**Interfaces:**
- Consumes: validated `GovernanceGraphOwnerDependencyReadModel`。
- Produces: bounded projection helpers：
  - `catalog_for_query(read_model) -> dict[str, Any]`
  - `catalog_for_comparison(read_model) -> dict[str, Any]`
  - `catalog_for_risk(read_model) -> dict[str, Any]`
  - `catalog_for_impact(read_model) -> dict[str, Any]`

- [x] **Step 1: Write failing adapter tests.** 驗證每個 adapter 只保留 status、role／relation identity、source／snapshot fingerprints、coverage 與 bounded diagnostics；`unknown`／`missing` 不轉成 zero、low risk、no impact 或 PASS；workflow edge 維持原 relationKind；invalid read model 不產生推測 projection。以既有 D-1～D-4 public model fixtures 做 exact-key contract assertions，確認既有 output schema 與 semantics 完全不變，只允許明確命名的 additive `catalog` section。

- [x] **Step 2: Run adapter tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_catalog_adapters.py -q`

  Expected: module／helper failures。

- [x] **Step 3: Implement pure adapters.** 每個 helper 只做 schema-preserving projection，不呼叫 D-1～D-4 service、不重算 comparison、risk、impact、不 traversal dependency；使用 exact additive allowlist `catalog` section，不修改既有 D1～D4 public keys、status precedence、fingerprint 覆蓋範圍或既有 semantics。若 consumer 尚未支援 catalog 欄位，adapter 只回傳獨立 bounded projection，不強行注入既有 output。

- [x] **Step 4: Run adapter tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_catalog_adapters.py -q`

- [x] **Step 5: Submit Task 3 to strict Review.** Review adapter data flow、schema compatibility、no-inference、D1-D4 isolation 與 no-write boundary。

- [x] **Step 6: After Review PASS, Codex commits Task 3.**

  ```bash
  git add backend/agents/governance_graph_catalog_adapters.py tests/test_governance_graph_catalog_adapters.py
  git commit -m "feat: add governance graph catalog consumer adapters"
  ```

### Task 4: Add stdin-only catalog validation CLI

**Files:**
- Modify: `scripts/governance_graph.py`
- Modify: `tests/test_governance_graph_cli.py`

**Interfaces:**
- Consumes: stdin JSON envelope containing owner／dependency catalog mappings and selected snapshot fingerprint。
- Produces: bounded `governance-graph-catalog-cli-v1` result；non-zero exit for invalid input，stdout 不輸出 raw payload。

- [x] **Step 1: Write failing CLI tests.** 測試 `catalog-validate` 接受 stdin、輸出 bounded available／missing／unknown／stale／invalid result；`--run-id`、path、writer、approve、dispatch、repair、prune、delete、shell 或 model flags 被拒絕；無 stdin 不建立檔案；malformed／secret／absolute path 不洩漏。

- [x] **Step 2: Run CLI tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`

  Expected: command／parser assertion failures because catalog validation command does not yet exist。

- [x] **Step 3: Implement the stdin-only command.** 重用 Task 2 service；CLI 只 parse stdin、呼叫 `OwnerDependencyReadService.resolve()`、輸出 `governance-graph-catalog-cli-v1` bounded envelope。不得取得 arbitrary path 或 runtime run directory，不得呼叫 builder、persist、writer、approval、dispatch 或 subprocess。

- [x] **Step 4: Run CLI tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`

- [x] **Step 5: Submit Task 4 to strict Review.** 確認 CLI 是 read-only stdin boundary，沒有 path traversal、writer flags、raw echo 或 control-plane action。

- [x] **Step 6: After Review PASS, Codex commits Task 4.**

  ```bash
  git add scripts/governance_graph.py tests/test_governance_graph_cli.py
  git commit -m "feat: add governance graph catalog validation cli"
  ```

### Task 5: Wire bounded E-2 Streamlit catalog callback

**Files:**
- Modify: `governance_graph_rendering.py`
- Modify: `agent_operations_rendering.py`
- Modify: `app_pages.py`
- Modify: `tests/test_governance_graph_rendering.py`
- Modify: `tests/test_agent_operations_rendering.py`
- Create: `tests/test_app_pages_governance_graph_catalog.py`

**Interfaces:**
- Consumes: 外部 approved producer 注入的 Task 2 validated read-model callback；selected run／snapshot fingerprint。
- Produces: optional `catalog_lookup(run_id: str, snapshot_fingerprint: str) -> dict[str, Any]` callback；bounded owner／dependency panel in existing Agent Operations Governance Graph workspace。若沒有外部 approved producer，callback 必須保持 `None`，UI 顯示 `unavailable`。

- [x] **Step 1: Write failing UI boundary tests.** 覆蓋 callback receives exact run／fingerprint、role-only owner rendering、workflow edge rendering、unavailable／missing／unknown／stale／invalid display、malformed callback isolation、raw path／secret non-rendering、selected identity cleanup、refresh preservation 與 no CLI/subprocess/writer invocation。

- [x] **Step 2: Run UI tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_rendering.py tests/test_agent_operations_rendering.py tests/test_app_pages_governance_graph_catalog.py -q`

  Expected: callback signature／render assertions fail because E-3 catalog callback is not wired。

- [x] **Step 3: Implement callback-only UI integration.** 只在 `render_agent_operations`、`_render_run_details` 與 `app_pages.py` 傳遞 optional `catalog_lookup` dependency-injected callback；不得在 app page 讀 catalog path、建立 catalog、呼叫 Task 2 service 取得 authority 或寫 session authority。外部 approved producer 尚未注入時 callback 保持 `None`，renderer 顯示明確 `unavailable`；callback result 只接受 bounded `.to_dict()`，並只保存 bounded selected subject／relation identity，run／fingerprint 不相容時清除。

- [x] **Step 4: Run UI tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_rendering.py tests/test_agent_operations_rendering.py tests/test_app_pages_governance_graph_catalog.py -q`

- [x] **Step 5: Submit Task 5 to strict Review.** 確認 Streamlit 沒有 approval、dispatch、snapshot build、raw download、catalog writer 或 D1-D4 inference。

- [x] **Step 6: After Review PASS, Codex commits Task 5.**

  ```bash
  git add governance_graph_rendering.py agent_operations_rendering.py app_pages.py tests/test_governance_graph_rendering.py tests/test_agent_operations_rendering.py tests/test_app_pages_governance_graph_catalog.py
  git commit -m "feat: expose governance catalog in agent operations graph"
  ```

### Task 6: Full verification, strict final Review, Hermes and plan reconciliation

**Files:**
- Modify only Task 1–5 allowlisted files if a Review finding requires it。
- Modify: this plan to mark completed steps and record evidence。

**Interfaces:**
- Consumes: Task 1–5 immutable commits、approved E-3 spec、Review PASS artifacts。
- Produces: final acceptance evidence、reconciled plan、clean branch；不自動 push／PR／merge。

- [x] **Step 1: Run compile and focused E-3 verification.**

  Run: `.venv/bin/python -m py_compile backend/agents/governance_graph_catalog_models.py backend/agents/governance_graph_catalog_service.py backend/agents/governance_graph_catalog_adapters.py scripts/governance_graph.py governance_graph_rendering.py agent_operations_rendering.py app_pages.py && .venv/bin/python -m pytest tests/test_governance_graph_catalog_models.py tests/test_governance_graph_catalog_service.py tests/test_governance_graph_catalog_adapters.py tests/test_governance_graph_cli.py tests/test_governance_graph_rendering.py tests/test_agent_operations_rendering.py tests/test_app_pages_governance_graph_catalog.py -q`

  Expected: compile succeeds and all focused tests PASS。

- [x] **Step 2: Run full project verification.**

  Run: `.venv/bin/python -m pytest -q && .venv/bin/python scripts/system_manager.py acceptance`

  Expected: full pytest、Streamlit／API／Vue system acceptance PASS；timeout 或 degraded 必須保持 blocked，不得改稱 PASS。

- [x] **Step 3: Run final strict Review and Hermes.**

  使用 approved Review runner review immutable Task 1–5 diff，取得 findings-first PASS；再執行 `.venv/bin/python scripts/hermes_post_change_check.py`。Hermes 必須 explicit exit 0／Overall PASS，並確認 catalog report read-only、writes 0、baseline／formal scope matched。

- [x] **Step 4: Verify invariants and no-write boundary.** 確認 SQLite integrity、baseline、formal scope、Graph snapshots、canonical artifacts、runtime、workflow status、Git state 未被改動；確認 CLI／service／UI 沒有 writer、subprocess、network、raw path 或 control-plane action；執行 `git diff --check` 與 `git status --short`。

- [x] **Step 5: Reconcile plan against E-3 spec.** 只標記已完成 Task，記錄 focused/full/system/Review/Hermes evidence；D-3/D-4 business impact、owner assignment、approval／dispatch 與 catalog persistence 不得超出本 plan；未經另外授權不得 push、建立 PR、merge 或刪除 branch。

## Agent and Review Protocol

1. Context Agent 先以 `scripts/context_agent.py --collect-only` 收集 E-3 spec、contract、symbols 與 tests 的 compact context；Context 永遠 read-only。若 Context status 為 `unknown`、`blocked_missing_brief` 或無法產生可靠 bundle，必須記錄為 blocked context，不可宣稱 Context PASS。
2. 一次只執行一個 approved Task；Implementation Agent 只能修改該 Task allowlisted files，不得自行 commit／merge／push 或選擇下一 Task。
3. 每個 Task 完成後交 approved Review Agent 做 immutable diff findings-first review；missing runner、dirty immutable head 或 unknown runner 都是 blocked，不得虛報 PASS。
4. Review PASS 後由 Codex 修正 findings、執行 focused/full verification；Hermes 只做 read-only acceptance，不能取代 Review。
5. Documentation Agent 只有在 Review PASS、full verification PASS、Hermes PASS 後才可被呼叫；E-3 deterministic no-doc change 可 skip，但不得由主 Codex 靜默回填 catalog 或 Obsidian。

## Spec Coverage Check

- Owner role-only contract、identity allowlist、provenance：Task 1。
- Dependency relation／relationKind、duplicate／conflict、workflow edge preservation：Task 1–2。
- Shared read-only service、status precedence、deterministic fingerprint、no-write：Task 2。
- D-1～D-4 bounded read-model adapters：Task 3。
- stdin-only CLI 與 control-plane rejection：Task 4。
- E-2 bounded UI callback、unavailable／unknown rendering、session cleanup：Task 5。
- Full verification、strict Review、Hermes、protected invariants、plan reconciliation：Task 6。
