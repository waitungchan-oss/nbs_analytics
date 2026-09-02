# Release Gate Standardization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 Full pytest、Hermes 與 UI acceptance 固定為同一 commit-bound、fresh、fail-closed 的 PR merge 與 release gate。

**Architecture:** 三個 gate 各自執行並產生 immutable bounded evidence；deterministic aggregator 只驗證 schema、fingerprint、freshness 與 commit identity，輸出 release decision。Governance Graph、Memory Hub、Memory Sidecar 與 Hermes 只提供 read-only context／inspection，不取得 release authority。

**Tech Stack:** Python 3.10、pytest、FastAPI／Streamlit、GitHub Actions、JSON canonical fingerprint、既有 verification_chain.py、hermes_post_change_check.py 與 run_gmv_ui_acceptance.py。

**Spec:** docs/superpowers/specs/2026-09-01-release-gates-design.md

## Global Constraints

- 正式業務 scope 固定為「不含掛賬核銷與 TT 退款轉團款」。
- 2026-05 frozen baseline 固定為 HKD 12,057,968。
- Strict Review、full pytest、Hermes、UI acceptance 與 sandbox capability 是獨立 gates。
- Governance Graph、Memory Hub、Memory Sidecar、Agent Operations 與 Hermes 維持 read-only／non-authoritative。
- 不修改正式 SQLite、baseline、revenue、GMV／退款規則、export schema 或 production business state。
- Gate evidence 必須綁定同一 commitSha、sourceFingerprint、schema version、時間與 SHA-256 fingerprint。
- FAIL、BLOCKED、MISSING、stale 或 identity mismatch 一律阻擋 release。
- 每個 Task 使用獨立 checkpoint commit；Implementation Agent 不得自行 commit、push 或 merge。

---

### Task 1: 建立 release gate evidence models 與 exact validator

**Files:**
- Create: backend/agents/release_gate_models.py
- Create: tests/test_release_gate_models.py
- Modify: backend/agents/verification_evidence_writer.py（只重用或補強既有 bounded helper）

**Interfaces:**
- Consumes: existing canonical_fingerprint。
- Produces: frozen ReleaseGateEvidence、ReleaseGateAggregate、ReleaseGateValidationError、validate_release_gate_evidence(payload, expected_commit_sha, expected_source_fingerprint, now)、validate_release_gate_aggregate(payload, expected_commit_sha, now)。

- [ ] **Step 1: Write failing tests**
  - 覆蓋四種 exact schema、round-trip、deterministic fingerprint、missing/unknown field、invalid SHA、stale、commit/source mismatch、secret、absolute path、over-cap 與 child gate 非 PASS。
- [ ] **Step 2: Run focused tests**
  - Run: .venv/bin/python -m pytest tests/test_release_gate_models.py -q
  - Expected: missing module/interface failure。
- [ ] **Step 3: Implement minimal immutable models**
  - 以 schema-defined unsigned payload 計算 fingerprint；individual gate status 只允許 PASS、FAIL、BLOCKED；aggregate 必須由三個 validated child evidence deterministic 推導；freshness default 為 1800 秒。
- [ ] **Step 4: Verify and checkpoint**
  - Run: .venv/bin/python -m pytest tests/test_release_gate_models.py -q
  - 使用 Review Agent read-only collection 後 commit：git commit -m "checkpoint(task-01): add release gate evidence contract"

### Task 2: 建立 Full pytest gate adapter

**Files:**
- Create: scripts/full_pytest_gate.py
- Create: tests/test_full_pytest_gate.py
- Modify: scripts/verification_chain.py

**Interfaces:**
- Consumes: ReleaseGateEvidence、既有 _python_bin、pytest output 與 verification-chain execution conventions。
- Produces: run_full_pytest_gate(project_root, commit_sha, source_fingerprint, command=None, timeout_seconds=1800) -> dict；CLI schema full-pytest-gate-v1。

- [ ] **Step 1: Write failing tests**
  - 驗證 passed/failed/skipped、duration、exit code、command identity、bounded output tail、commit/source binding；required sandbox blocked、timeout、nonzero、malformed summary 與 over-cap 必須 fail-closed。
- [ ] **Step 2: Run**
  - Run: .venv/bin/python -m pytest tests/test_full_pytest_gate.py -q
  - Expected: missing interface failure。
- [ ] **Step 3: Implement and integrate**
  - 使用 bounded subprocess capture；qualified macOS runner 必須使用 --sandbox-preflight required；讓 verification_chain.py run-full 消費 adapter metadata，不改 SQLite、baseline 或 Hermes transition。
- [ ] **Step 4: Verify and checkpoint**
  - Run: .venv/bin/python -m pytest tests/test_full_pytest_gate.py tests/test_verification_chain.py tests/test_verification_evidence_writer.py -q
  - Commit: git commit -m "checkpoint(task-02): bind full pytest release evidence"

### Task 3: 建立 Hermes gate adapter

**Files:**
- Create: scripts/hermes_gate.py
- Create: tests/test_hermes_gate.py
- Modify: scripts/hermes_post_change_check.py
- Modify: NBS_HERMES_MONITORING.md

**Interfaces:**
- Consumes: Hermes JSON report、ReleaseGateEvidence、read-only Graph／Memory Sidecar reports。
- Produces: run_hermes_gate(project_root, commit_sha, source_fingerprint, command=None, timeout_seconds=1800) -> dict；schema hermes-gate-v1。

- [ ] **Step 1: Write failing tests**
  - 覆蓋 overallStatus pass/non-pass、missing/stale/mismatched report、nonzero command，以及 writes/approval/dispatch/Gateway claims。
- [ ] **Step 2: Implement read-only adapter**
  - 執行既有 Hermes command，要求 overallStatus=pass，綁定 commit/source fingerprint；Graph、Memory Hub、Memory Sidecar 不能 promote non-PASS。
- [ ] **Step 3: Verify, Review, checkpoint**
  - Run: .venv/bin/python -m pytest tests/test_hermes_gate.py tests/test_hermes_post_change_check.py tests/test_memory_sidecar_hermes_boundary.py -q
  - Commit: git commit -m "checkpoint(task-03): bind Hermes release evidence"

### Task 4: 建立 UI acceptance gate adapter

**Files:**
- Create: scripts/ui_acceptance_gate.py
- Create: tests/test_ui_acceptance_gate.py
- Modify: scripts/run_gmv_ui_acceptance.py
- Modify: backend/services/gmv_ui_acceptance_service.py（只有 exact metadata 必要時）

**Interfaces:**
- Consumes: UiAcceptanceEvidence、run_ui_acceptance、HTTP probe、temporary fixture root。
- Produces: run_ui_acceptance_gate(project_root, url, fixture_root, evidence_path, commit_sha, source_fingerprint) -> dict；schema ui-acceptance-gate-v1。

- [ ] **Step 1: Write failing tests**
  - 覆蓋 HTTP success/route match、commit/source match、bounded smoke PASS、file:// rejection、production path rejection、server failure、stale/malformed/BLOCKED evidence。
- [ ] **Step 2: Implement bounded adapter**
  - 僅允許 HTTP/HTTPS 與 temporary fixture；不開 production server、不上傳 business file、不寫 SQLite/cache；server/browser failure 回報 FAIL 或 BLOCKED。
- [ ] **Step 3: Verify, Review, checkpoint**
  - Run: .venv/bin/python -m pytest tests/test_ui_acceptance_gate.py tests/test_gmv_ui_acceptance_runner.py tests/test_gmv_ui_acceptance_service.py -q
  - Commit: git commit -m "checkpoint(task-04): bind UI acceptance release evidence"

### Task 5: 建立 deterministic release-gate aggregator

**Files:**
- Create: scripts/release_gate.py
- Create: tests/test_release_gate.py
- Modify: backend/agents/release_gate_models.py

**Interfaces:**
- Consumes: exactly one validated Full pytest、Hermes、UI acceptance evidence。
- Produces: aggregate_release_gates(evidence, expected_commit_sha, expected_source_fingerprint, now) -> dict；CLI validate/aggregate；exit 0 只代表 aggregate PASS，exit 2 代表 blocked/missing/stale。

- [ ] **Step 1: Write failing tests**
  - 覆蓋 all PASS、單一 FAIL/BLOCKED、missing、mismatched identity、stale、invalid child fingerprint、unknown/duplicate gate、over-cap；驗證不執行 command、不寫 SQLite/Graph/Memory、不改 Git。
- [ ] **Step 2: Implement read-only aggregation**
  - 只讀 explicit evidence paths；驗證 exact schema、identity、freshness 與 fingerprint；三者非全 PASS 時輸出 bounded reasons，不從 handoff、Graph、Memory 或自由文字補推 PASS。
- [ ] **Step 3: Verify and checkpoint**
  - Run: .venv/bin/python -m pytest tests/test_release_gate.py tests/test_release_gate_models.py -q
  - Commit: git commit -m "checkpoint(task-05): aggregate release gates fail closed"

### Task 6: 固定 GitHub Actions PR 與 release workflows

**Files:**
- Create: .github/workflows/release-gates.yml
- Create: tests/test_release_gate_workflow.py
- Modify: .github/workflows/sandbox-integration.yml
- Modify: .github/workflows/hermes-governance-graph.yml

**Interfaces:**
- Consumes: Tasks 2–5 CLIs 與 immutable artifacts。
- Produces: required checks Full pytest release gate、Hermes release gate、UI acceptance release gate、Release gate aggregate；release tag 重新執行三 gates。

- [ ] **Step 1: Write failing workflow tests**
  - 驗證 dependency bootstrap、同一 commit SHA、failure artifact upload、aggregator depends-on 三 gates、required sandbox mode，以及禁止 historical fallback、Graph/Memory write、production path。
- [ ] **Step 2: Implement minimal workflows**
  - 使用 requirements.txt；Full pytest 使用 qualified runner；Hermes read-only；UI 使用 isolated HTTP/Streamlit fixture；aggregator 只驗證並回傳 nonzero。
- [ ] **Step 3: Verify and checkpoint**
  - Run: .venv/bin/python -m pytest tests/test_release_gate_workflow.py tests/test_sandbox_ci_contract.py -q
  - Commit: git commit -m "checkpoint(task-06): enforce release gates in CI"

### Task 7: 文件 reconciliation、完整驗證與 release readiness

**Files:**
- Modify: NBS_ANALYTICS_HANDOFF.md
- Modify: NBS_HERMES_MONITORING.md
- Modify: docs/agents/CODEX_AGENT_DISPATCH.md
- Create: tests/test_release_gate_documentation_contract.py

**Interfaces:**
- Consumes: final schemas、workflow names、fresh Full pytest/Hermes/UI evidence 與 read-only context。
- Produces: 文件化的 gate independence、same-commit identity、freshness、blocked fail-closed、sandbox required、HTTP-only UI 與 authority boundaries。

- [ ] **Step 1: Write documentation tests**
  - 驗證四個獨立 gates（Strict Review、Full pytest、Hermes、UI acceptance）、same-commit、fresh evidence、BLOCKED、rollback 與 formal scope。
- [ ] **Step 2: Update docs and verify**
  - Run: .venv/bin/python -m pytest tests/test_release_gate_documentation_contract.py -q
  - Run: git diff --check
- [ ] **Step 3: Run final gates separately**
  - Run: .venv/bin/python -m pytest -q
  - Run: .venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
  - Run approved temporary HTTP/Streamlit UI acceptance；baseline separately要求 HKD 12,057,968。
- [ ] **Step 4: Run Review and aggregator**
  - 使用實際 fresh evidence path、commit SHA 與 source fingerprint；unresolved placeholder 必須被 validator 拒絕。Aggregate exit code 只有三 gate fresh PASS 才能是 0。
- [ ] **Step 5: Commit docs and final evidence**
  - Commit: git commit -m "checkpoint(task-07): document release gate closeout"
  - checkpoint 不得標記 Final-Acceptance PASS；final readiness 另以 evidence 報告。
