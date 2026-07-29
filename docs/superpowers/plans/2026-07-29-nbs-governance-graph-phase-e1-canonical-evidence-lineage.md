# NBS Governance Graph Phase E-1 Canonical Evidence Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 deterministic、read-only 的 E-1 evidence lineage read model，讓單一 Graph node、D-3 finding 或 D-4 impact 以 explicit evidence identity 追溯至 canonical registry metadata，且不暴露 raw payload 或成為任何 writer 入口。

**Architecture:** 以 immutable Pydantic/dataclass-style models 固定 input/output schema與 fingerprint 規則；service 僅透過既有 `GovernanceGraphSnapshotReader`、`CanonicalEvidenceReader` 與 `CanonicalEvidenceRegistry` 讀取並產生 bounded projection。`scripts/governance_graph.py evidence-lineage` 只接受 stdin wrapper，Streamlit 與未來 E-2 只消費此 service contract。

**Tech Stack:** Python 3、既有 `backend/agents` models/readers、JSON stdin/stdout、pytest、`scripts/system_manager.py`、Hermes read-only checks。

## Global Constraints

- 只接受 `governance-graph-evidence-lineage-input-v1`，輸出 `governance-graph-evidence-lineage-v1`；exact keys、safe identity、lowercase SHA-256 與 canonical JSON fingerprint 必須 deterministic。
- `runId` 是 safe single path component；evidence path 必須是 `CanonicalEvidenceRegistry` owned、run-relative、regular、non-symlink file；D1 legacy aliases 與一般 Graph artifact（例如 `hermes.json`）一律拒絕。
- 只允許 `source.kind` 為 `node`、`finding`、`impact`；不得從 nodeId、findingId、impact category、filename、順序或 fingerprint 推測 evidence relation。
- 固定狀態 precedence：`invalid > fingerprint_mismatch > blocked > stale > unknown > missing > available`；空 D-3/D-4 evidence identities 保持 `missing`/`unknown`。
- Output 只含 bounded identity、registry metadata、status、reason、finalizedAt、fingerprint match 與 links；不含 raw payload、absolute path、secret、prompt、command、log、SQLite/Excel rows。每個 identity/reason/writer 欄位長度、evidence、links、diagnostics 均有固定上限，且 evidence/link 不得重複。
- 每份 result 只描述一個 explicit source、最多 12 筆 evidence refs；v1 的 `evidence` 是零或一個 explicit object（以模型 normalize 成 bounded list），links 與 evidence 一一對應並依固定 tuple 排序；不可信 identity 時 fingerprint 必須為 `null`。
- Service、CLI、Context、Review、Hermes 全部 read-only；不得寫 Graph snapshot、canonical artifact、runtime、SQLite、baseline、cache、Git 或 Obsidian。
- 正式口徑維持「不含掛賬核銷與TT退款轉團款」；2026-05 baseline 維持 `HKD 12,057,968`。
- Implementation Agent 一次只執行一個 Task，不得自行 commit/merge；每個 Task 必須先 RED、後 GREEN，再交 strict Review；Codex 只在 Review PASS 後整合 commit。

## File Map

- Create: `backend/agents/governance_graph_evidence_lineage_models.py` — frozen input、evidence detail、link、diagnostic、result models；exact-key validation、bounded identity 與 lineage fingerprint。
- Create: `backend/agents/governance_graph_evidence_lineage_service.py` — snapshot/canonical reader adapter、registry allowlist、explicit link validation、state precedence 與 bounded projection；不得寫入任何 state。
- Modify: `scripts/governance_graph.py` — 新增 `evidence-lineage` stdin-only subcommand，保留既有 commands 行為與控制旗標拒絕規則。
- Create: `tests/test_governance_graph_evidence_lineage_models.py` — models schema、forbidden fields、limits、sorting 與 fingerprint tests。
- Create: `tests/test_governance_graph_evidence_lineage_service.py` — canonical fixture、state matrix、reader boundary、no-inference、no-write 與 raw-leak tests。
- Modify: `tests/test_governance_graph_cli.py` — CLI success、malformed/empty stdin exit 2、exact wrapper 與 forbidden flags tests。

---

### Task 1: E-1 immutable contract models and deterministic fingerprint

**Files:**
- Create: `backend/agents/governance_graph_evidence_lineage_models.py`
- Create: `tests/test_governance_graph_evidence_lineage_models.py`

**Interfaces:**
- Consumes: JSON-compatible mapping matching `governance-graph-evidence-lineage-input-v1`.
- Produces: `EvidenceLineageInput.from_dict(data)`, `EvidenceLineageResult.to_dict()`, and `EvidenceLineageResult.with_fingerprint()`; later service tasks must use these names and return types.

- [ ] **Step 1: Write failing model tests.** Cover exact top-level input keys, allowed source kinds, safe single-component identities, lowercase SHA-256, registry-owned filename shape, max 12 evidence refs, forbidden raw/control fields, deterministic link sorting, and null fingerprints for untrusted identity.
- [ ] **Step 2: Run the focused tests to confirm RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_evidence_lineage_models.py -q`

  Expected: collection/import failures because the E-1 model module and public constructors do not yet exist.
- [ ] **Step 3: Implement the minimal immutable models.** Define explicit enums/literals for source kind, status, relation and reason codes; reject unknown keys, duplicate semantic fields, path separators, absolute paths, control flags and raw payload fields. Canonicalize JSON with sorted keys/separators, cap evidence at 12, sort links by `(relation, sourceIdentity, evidencePath, evidenceSha256)`, and hash only the contract fields excluding `lineageFingerprint`.
- [ ] **Step 4: Run focused tests to confirm GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_evidence_lineage_models.py -q`

  Expected: all model tests PASS, including byte-for-byte repeatability and no raw/absolute-path serialization.
- [ ] **Step 5: Submit the Task 1 diff to strict Review.** Review Agent must inspect only this allowlist and report findings-first; it must not edit files or claim PASS without immutable diff evidence.
- [ ] **Step 6: After Review PASS, Codex commits only Task 1.**

  Run: `git add backend/agents/governance_graph_evidence_lineage_models.py tests/test_governance_graph_evidence_lineage_models.py && git commit -m "feat: add evidence lineage contract models"`

### Task 2: Read-only canonical evidence lineage service

**Files:**
- Create: `backend/agents/governance_graph_evidence_lineage_service.py`
- Create: `tests/test_governance_graph_evidence_lineage_service.py`

**Interfaces:**
- Consumes: `EvidenceLineageInput`; existing `GovernanceGraphSnapshotReader`, `CanonicalEvidenceReader`, `CanonicalEvidenceRegistry`, `GovernanceEvidenceRef` and `GovernanceCanonicalEvidenceRef`.
- Produces: `GovernanceGraphEvidenceLineageService(snapshot_reader, canonical_reader, registry).resolve(request: EvidenceLineageInput) -> EvidenceLineageResult`; no builder/writer or raw reader is permitted. v1 只解析 request 內零或一個 explicit `evidence` object；finding/impact 不接受額外 list、D3/D4 envelope 或反查資料。

- [ ] **Step 1: Build failing fixtures/tests.** Create isolated immutable run fixtures for: available `node_evidence`; the single explicit evidence object with `node`/`finding`/`impact`; missing/unknown absent evidence; blocked envelope; stale snapshot freshness; explicit SHA mismatch; snapshot fingerprint mismatch; run binding mismatch; duplicate JSON key; traversal/symlink/non-regular/oversized artifact; and invalid registry/contract. Assert the precedence matrix, bounded metadata, `len(evidence) <= 1`, `len(links) == len(evidence)`, fixed diagnostics cap, no inference, raw-leak absence, and before/after tree/runtime/SQLite/Git equality.
- [ ] **Step 2: Run service tests to confirm RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_evidence_lineage_service.py -q`

  Expected: import/attribute failures because the service class and resolver do not yet exist.
- [ ] **Step 3: Implement explicit source/evidence resolution.** Validate the request with Task 1 models; resolve only registry-owned canonical filenames through the registry (never D1 alias mapping); read the immutable snapshot and canonical envelope through existing safe readers; compare run binding, explicit SHA, snapshot fingerprint and finalization. Classify `stale` only from the existing `GovernanceGraphSnapshotReader` freshness result/identity exposed by its compact read contract; do not read manifest Git identity directly or invent a new raw-artifact freshness adapter. Emit only registry metadata and compact reader state. For `node`, permit only the matching canonical-evidence Graph node; for `finding`/`impact`, consume only the request's single explicit evidence object and leave an absent object `missing`/`unknown` according to the validated source state. Apply the fixed precedence and return null fingerprints whenever identity cannot be trusted.
- [ ] **Step 4: Run service tests to confirm GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_evidence_lineage_service.py -q`

  Expected: all service tests PASS, including deterministic ordering/fingerprint, no-write guarantees, and rejection of `hermes.json`/legacy aliases.
- [ ] **Step 5: Submit the Task 2 diff to strict Review.** Review only the two allowlisted files; confirm no state writer, raw payload leak, reverse lookup or D3/D4 rule change.
- [ ] **Step 6: After Review PASS, Codex commits only Task 2.**

  Run: `git add backend/agents/governance_graph_evidence_lineage_service.py tests/test_governance_graph_evidence_lineage_service.py && git commit -m "feat: add canonical evidence lineage service"`

### Task 3: stdin-only Governance Graph CLI adapter

**Files:**
- Modify: `scripts/governance_graph.py` (parser and command dispatch only)
- Modify: `tests/test_governance_graph_cli.py`

**Interfaces:**
- Consumes: one JSON object on stdin matching Task 1 input schema.
- Produces: the existing `nbs-governance-graph-cli-v1` JSON envelope with `result` containing one `governance-graph-evidence-lineage-v1` `EvidenceLineageResult`; non-zero exit code `2` for empty/malformed input or rejected command flags. Malformed/empty input uses the existing CLI `blocked` envelope with a bounded error message and no lineage result. Existing `build`, `validate`, `status`, `query`, `compare`, `risk-summary`, and `change-impact` behavior remains unchanged.

- [ ] **Step 1: Add failing CLI tests.** Assert `evidence-lineage` accepts stdin only, wraps the lineage-v1 result in the existing `nbs-governance-graph-cli-v1` envelope, rejects `--run-id`, `--path`, `--writer`, `--approve`, and `--dispatch`, returns exit `2` with the existing bounded `blocked` envelope for empty/malformed JSON, and never creates/updates snapshot or runtime files.
- [ ] **Step 2: Run targeted CLI tests to confirm RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`

  Expected: new `evidence-lineage` cases fail while legacy command tests remain green.
- [ ] **Step 3: Implement the adapter.** Register only the new subcommand, parse stdin once, construct `EvidenceLineageInput`, instantiate the read-only service with existing readers, serialize the result through the existing `nbs-governance-graph-cli-v1` envelope helper, and map validation/JSON errors to the existing bounded `blocked` envelope plus exit `2`; do not call snapshot builders, workflow stores, subprocesses, approval, dispatch or writers.
- [ ] **Step 4: Run targeted CLI tests to confirm GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`

  Expected: all CLI tests PASS and existing command tests remain unchanged.
- [ ] **Step 5: Submit the Task 3 diff to strict Review.** Review Agent checks parser surface, stdin-only behavior, exit codes, no-write evidence, and legacy command compatibility.
- [ ] **Step 6: After Review PASS, Codex commits only Task 3.**

  Run: `git add scripts/governance_graph.py tests/test_governance_graph_cli.py && git commit -m "feat: expose evidence lineage stdin command"`

### Task 4: Full verification, governance acceptance and plan reconciliation

**Files:**
- Modify only if a strict Review finding requires it: files from Tasks 1–3; no new scope.
- Review artifact: immutable Task 4 diff/evidence bundle; no source writer changes.

**Interfaces:**
- Consumes: Task 1–3 commits, strict Review PASS records, and the approved E-1 spec.
- Produces: final acceptance evidence, updated plan checkboxes/reconciliation, and a clean worktree ready for an explicitly authorized push/PR/merge.

- [ ] **Step 1: Run compile and focused E-1 tests.**

  Run: `.venv/bin/python -m py_compile backend/agents/governance_graph_evidence_lineage_models.py backend/agents/governance_graph_evidence_lineage_service.py scripts/governance_graph.py && .venv/bin/python -m pytest tests/test_governance_graph_evidence_lineage_models.py tests/test_governance_graph_evidence_lineage_service.py tests/test_governance_graph_cli.py -q`

  Expected: compile succeeds and all E-1 tests PASS.
- [ ] **Step 2: Run full project verification.**

  Run: `.venv/bin/python -m pytest -q && .venv/bin/python scripts/system_manager.py acceptance`

  Expected: full pytest and system acceptance PASS; any failure is reported with exact stage/error and is not relabeled as success.
- [ ] **Step 3: Run strict final Review and Hermes.**

  Run the approved Review runner against the immutable Task 1–3 diff and evidence bundle, then run `.venv/bin/python scripts/hermes_post_change_check.py`.

  Expected: an explicit Review PASS artifact naming the runner, contract, immutable head/diff and test evidence, followed by Hermes PASS with no timeout/degraded/unknown result; missing/unknown Review runner blocks completion, and Hermes never replaces Review.
- [ ] **Step 4: Verify protected invariants and clean worktree.** Confirm no SQLite schema/data, baseline `HKD 12,057,968`, formal revenue scope, Graph snapshot writer, runtime state, cache, Git or Obsidian changed; preserve the Review artifact, Hermes output and command logs; run `git status --short` and preserve only intended plan/implementation commits.
- [ ] **Step 5: Reconcile this plan against the E-1 spec.** Mark only completed tasks, record compile/test/system/Review/Hermes evidence and any blocked reason, and leave future E-2/E-3/E-4 work out of scope. Do not push, create PR, merge, or delete branches without separate authorization.

## Execution and Review Protocol

1. Before implementation starts, run Context Agent with `--collect-only` and pass only its compact bundle to the active worker; Context Agent remains read-only.
2. Execute exactly one approved Task at a time. The worker performs RED → minimal implementation → GREEN and returns the actual diff and commands.
3. Run the configured approved Review Agent in findings-first strict mode after every Task. A missing/unknown runner is a blocked review, never a PASS.
4. Codex fixes only concrete findings, reruns the affected tests, and commits the task after Review PASS. No Implementation/Review agent may commit or merge.
5. Only Task 4 may run full pytest, system acceptance and Hermes. A timeout, degraded or unknown Hermes result blocks completion.

## Spec Coverage Check

- Input/output exactness, bounded fields, safe paths, registry allowlist and deterministic fingerprints: Task 1.
- Existing reader reuse, explicit links, no inference, all state precedence and no-write/raw-leak boundaries: Task 2.
- Stdin-only command, forbidden flags, malformed input exit `2`, and legacy CLI compatibility: Task 3.
- Compile, targeted/full tests, system acceptance, strict Review, Hermes, protected baseline/scope and clean worktree: Task 4.
- E-2 Streamlit, E-3 owner/dependency catalog and E-4 management summary remain future consumers and are deliberately not implemented here.
