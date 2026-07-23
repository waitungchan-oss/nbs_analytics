# Persistent Receipt Exclusion Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可由使用者確認一次、日後精確自動排除、具 quarantine evidence 與安全撤銷預演的永久收款單治理流程。

**Architecture:** Upload preflight 在 `process_raw_files` 前，以 read-only registry snapshot 對營收主表做完整 identity matching；初次 drift 只產生 fingerprinted proposal，不寫治理資料。使用者確認後，在同一 cross-process upload lease 內重算 proposal、以 in-memory overlay 重跑全部 blocking gates，通過後才以 transaction 寫入 registry、raw/prepared quarantine evidence 與 event，並沿用既有 Orchestrator 完成 upsert、rollback、history 與 cache generation。

**Tech Stack:** Python 3、pandas、SQLite、FastAPI/Pydantic、Streamlit、Vue 3/Vite、pytest、現有 NBS upload single-writer 與 Hermes 工具。

## Global Constraints

- 正式口徑固定為：`不含掛賬核銷與TT退款轉團款`。
- `2026-05` frozen baseline 固定為 `HKD 12,057,968`。
- `2026-06` baseline 固定為 `HKD 9,083,241`。
- 不修改 baseline registry、金額、人數、交易數量、分社歸屬或報表計算。
- 排除只使用正規化後的 `receipt_no + source_order_no + exclusion_kind` 完全匹配。
- 不提供 prefix、regex、模糊匹配、批量全部接受或 force revoke。
- 一般 preflight 必須 read-only；只有使用者確認且 overlay preflight 全部 matched 後才可啟用規則。
- Streamlit、FastAPI 與 Vue 不得自行重算 driver、baseline 或 exclusion identity。
- 所有治理與 upload 寫入必須使用明確 `live_db_path` 及既有 cross-process upload lease。
- 既有 Implementation Agent 禁止修改 `upload`、`sqlite`、`baseline`、`rollback`、`revenue` 與 `business_rules` risk surfaces；Tasks 2-10 必須由主 Codex 在使用者逐 Task 授權下執行。
- 每個 Task 完成後先跑 focused tests，再交 Review Agent；最後才執行 full verification 與 Hermes。
- 不自動在正式 DB 啟用 `SK2606005393`；正式 activation 是所有實作驗收完成後的獨立人工治理動作。

---

## File Structure

### New backend units

- `backend/services/receipt_exclusion_models.py`
  - immutable identity、rule snapshot、match result 與 canonical hashing。
- `backend/services/receipt_exclusion_matcher.py`
  - pure normalization、exact matching、collision detection 與 raw main-frame filtering。
- `backend/services/receipt_exclusion_registry_service.py`
  - 三張 SQLite governance tables、read-only snapshot、transactional activation/events/revoke。
- `backend/services/receipt_exclusion_proposal_service.py`
  - eligible Drift Diagnosis、candidate evidence 與 proposal fingerprint。
- `backend/services/receipt_exclusion_governance_service.py`
  - confirmation verification、activation payload 與 revocation preview/confirm orchestration。
- `backend/services/receipt_exclusion_read_model_service.py`
  - Streamlit/API 共用 bounded registry read model。
- `backend/schemas/receipt_exclusions.py`
  - API request/response models。
- `receipt_exclusion_rendering.py`
  - Streamlit confirmation dialog、registry list 與 revocation controls。

### Existing backend changes

- `pipeline.py`
  - 將 Excel-like source parser 暴露為可重用 `read_excel_source`，保留舊 alias。
- `backend/services/upload_preflight_service.py`
  - pre-process registry matching、overlay、collision、proposal 與 private evidence。
- `backend/services/upload_orchestrator_service.py`
  - confirmation second preflight、revision gate、activation/auto-apply audit，再沿用既有 write path。
- `backend/services/upload_action_service.py`
  - FastAPI multipart bytes replay 與 confirmation action。
- `backend/services/stability_history_service.py`
  - 保存 registry revision、rule IDs、match counts 與 proposal fingerprint。
- `backend/routers/upload.py`
  - list、confirm、revocation preview/confirm endpoints。
- `backend/schemas/dashboard.py`
  - Upload response 加入 receipt exclusion governance summary。
- `backend/main.py`
  - 若 router 分拆，註冊 receipt exclusion router。
- `app_pages.py`
  - upload/config tab 只負責呼叫 rendering/service。

### Frontend changes

- `frontend/src/lib/api.js`
  - confirmation/list/revocation API functions。
- `frontend/src/App.vue`
  - explicit confirmation、active/revoked registry 與 revoke preview UI。
- `frontend/src/styles.css`
  - restrained governance panel、collision/error state。
- `frontend/scripts/verify-cockpit-contract.mjs`
  - 靜態 contract assertions。

### Tests and acceptance

- `tests/test_receipt_exclusion_matcher.py`
- `tests/test_receipt_exclusion_registry_service.py`
- `tests/test_receipt_exclusion_proposal_service.py`
- `tests/test_receipt_exclusion_governance_service.py`
- `tests/test_receipt_exclusion_read_model_service.py`
- `tests/test_receipt_exclusion_api.py`
- `tests/test_receipt_exclusion_rendering.py`
- Modify existing upload/preflight/orchestrator/action/API/history tests。
- `docs/agents/RECEIPT_EXCLUSION_GOVERNANCE_ACCEPTANCE.md`

---

### Task 1: Pure Identity Models and Matcher

**Risk surface:** pure data only; no SQLite or upload write。

**Files:**
- Create: `backend/services/receipt_exclusion_models.py`
- Create: `backend/services/receipt_exclusion_matcher.py`
- Create: `tests/test_receipt_exclusion_matcher.py`

**Interfaces:**
- Produces:
  - `ReceiptExclusionIdentity(receipt_no: str, source_order_no: str, exclusion_kind: str)`
  - `ReceiptExclusionRule(id: int, identity: ReceiptExclusionIdentity, status: str)`
  - `ReceiptExclusionMatchResult(filtered_frame, matches, collisions)`
  - `normalize_identity_text(value: object) -> str`
  - `classify_exclusion_kind(row: Mapping[str, object]) -> str`
  - `match_receipt_exclusions(main_frame: pd.DataFrame, rules: Sequence[ReceiptExclusionRule]) -> ReceiptExclusionMatchResult`

- [ ] **Step 1: Write failing identity and matching tests**

```python
import pandas as pd

from backend.services.receipt_exclusion_models import (
    ReceiptExclusionIdentity,
    ReceiptExclusionRule,
)
from backend.services.receipt_exclusion_matcher import match_receipt_exclusions


def _rule() -> ReceiptExclusionRule:
    return ReceiptExclusionRule(
        id=7,
        identity=ReceiptExclusionIdentity(
            receipt_no="SK2606005393",
            source_order_no="31NZY6629115617",
            exclusion_kind="payment_method:TT 退款轉團款",
        ),
        status="active",
    )


def test_exact_active_rule_filters_only_target_receipt():
    frame = pd.DataFrame([
        {
            "收款單號": " sk2606005393　",
            "來源單據號": "31nzy6629115617",
            "收款方式": "TT 退款轉團款",
            "收款類型": "旅費",
            "收款原幣金額": 1630,
        },
        {
            "收款單號": "SK2606005395",
            "來源單據號": "31NZY6629115617",
            "收款方式": "現金",
            "收款類型": "旅費",
            "收款原幣金額": 1270,
        },
    ])

    result = match_receipt_exclusions(frame, [_rule()])

    assert result.filtered_frame["收款單號"].tolist() == ["SK2606005395"]
    assert result.matches[0]["registryId"] == 7
    assert result.matches[0]["receiptNo"] == "SK2606005393"
    assert result.collisions == ()


def test_same_receipt_with_different_order_is_collision_and_not_filtered():
    frame = pd.DataFrame([{
        "收款單號": "SK2606005393",
        "來源單據號": "DIFFERENT",
        "收款方式": "TT 退款轉團款",
        "收款類型": "旅費",
    }])

    result = match_receipt_exclusions(frame, [_rule()])

    assert len(result.filtered_frame) == 1
    assert result.matches == ()
    assert result.collisions[0]["reason"] == "source_order_mismatch"


def test_corrected_normal_payment_is_collision_and_not_filtered():
    frame = pd.DataFrame([{
        "收款單號": "SK2606005393",
        "來源單據號": "31NZY6629115617",
        "收款方式": "現金",
        "收款類型": "旅費",
    }])

    result = match_receipt_exclusions(frame, [_rule()])

    assert len(result.filtered_frame) == 1
    assert result.collisions[0]["reason"] == "exclusion_kind_mismatch"
```

- [ ] **Step 2: Run matcher tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_matcher.py -q
```

Expected: collection fails because the new modules do not exist。

- [ ] **Step 3: Implement immutable models and canonical hashing**

```python
# backend/services/receipt_exclusion_models.py
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ReceiptExclusionIdentity:
    receipt_no: str
    source_order_no: str
    exclusion_kind: str

    @property
    def candidate_id(self) -> str:
        return canonical_json_hash({
            "receiptNo": self.receipt_no,
            "sourceOrderNo": self.source_order_no,
            "exclusionKind": self.exclusion_kind,
        })


@dataclass(frozen=True)
class ReceiptExclusionRule:
    id: int
    identity: ReceiptExclusionIdentity
    status: str


@dataclass(frozen=True)
class ReceiptExclusionMatchResult:
    filtered_frame: pd.DataFrame
    matches: tuple[dict, ...]
    collisions: tuple[dict, ...]
```

- [ ] **Step 4: Implement exact matcher**

```python
# backend/services/receipt_exclusion_matcher.py
from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from backend.services.receipt_exclusion_models import (
    ReceiptExclusionIdentity,
    ReceiptExclusionMatchResult,
    ReceiptExclusionRule,
    canonical_json_hash,
)

EXCLUDED_RECEIPT_TYPES = {"掛賬核銷"}
EXCLUDED_PAYMENT_METHODS = {"TT 退款轉團款"}


def normalize_identity_text(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("\u3000", " ").replace("\xa0", " ").strip().upper()


def classify_exclusion_kind(row: Mapping[str, object]) -> str:
    receipt_type = normalize_identity_text(row.get("收款類型"))
    payment_method = normalize_identity_text(row.get("收款方式"))
    if receipt_type in EXCLUDED_RECEIPT_TYPES:
        return f"receipt_type:{receipt_type}"
    if payment_method in EXCLUDED_PAYMENT_METHODS:
        return f"payment_method:{payment_method}"
    return ""


def _row_identity(row: Mapping[str, object]) -> ReceiptExclusionIdentity:
    return ReceiptExclusionIdentity(
        receipt_no=normalize_identity_text(row.get("收款單號")),
        source_order_no=normalize_identity_text(row.get("來源單據號")),
        exclusion_kind=classify_exclusion_kind(row),
    )


def match_receipt_exclusions(
    main_frame: pd.DataFrame,
    rules: Sequence[ReceiptExclusionRule],
) -> ReceiptExclusionMatchResult:
    active = [rule for rule in rules if rule.status == "active"]
    by_receipt = {rule.identity.receipt_no: rule for rule in active}
    drop_indexes: list[object] = []
    matches: list[dict] = []
    collisions: list[dict] = []
    for index, row in main_frame.iterrows():
        identity = _row_identity(row)
        rule = by_receipt.get(identity.receipt_no)
        if rule is None:
            continue
        reason = ""
        if identity.source_order_no != rule.identity.source_order_no:
            reason = "source_order_mismatch"
        elif identity.exclusion_kind != rule.identity.exclusion_kind:
            reason = "exclusion_kind_mismatch"
        if reason:
            collisions.append({
                "registryId": rule.id,
                "receiptNo": identity.receipt_no,
                "reason": reason,
            })
            continue
        drop_indexes.append(index)
        amount = pd.to_numeric(
            pd.Series([row.get("收款原幣金額")]), errors="coerce"
        ).fillna(0).iloc[0]
        payload = {
            "registryId": rule.id,
            "receiptNo": identity.receipt_no,
            "sourceOrderNo": identity.source_order_no,
            "exclusionKind": identity.exclusion_kind,
            "observedAmount": float(amount),
        }
        matches.append({**payload, "rowHash": canonical_json_hash(payload)})
    return ReceiptExclusionMatchResult(
        filtered_frame=main_frame.drop(index=drop_indexes).copy(),
        matches=tuple(matches),
        collisions=tuple(collisions),
    )
```

- [ ] **Step 5: Run matcher tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_matcher.py -q
```

Expected: all matcher tests PASS。

Commit:

```bash
git add backend/services/receipt_exclusion_models.py backend/services/receipt_exclusion_matcher.py tests/test_receipt_exclusion_matcher.py
git commit -m "feat: add exact receipt exclusion matcher"
```

---

### Task 2: SQLite Registry, Quarantine and Event Transactions

**Risk surface:** protected SQLite governance; main Codex only。

**Files:**
- Create: `backend/services/receipt_exclusion_registry_service.py`
- Create: `tests/test_receipt_exclusion_registry_service.py`

**Interfaces:**
- Consumes: Task 1 models and `canonical_json_hash`。
- Produces:
  - `load_active_registry_snapshot(*, db_path) -> dict`
  - `activate_receipt_exclusions(candidates, *, operation_id, created_by, proposal_fingerprint, db_path) -> dict`
  - `record_auto_applied_events(events, *, operation_id, db_path) -> list[int]`
  - `list_receipt_exclusions(*, status, limit, db_path) -> list[dict]`
  - `load_quarantine_evidence(rule_id, *, db_path) -> dict`
  - `commit_receipt_exclusion_revocation(rule_id, *, operation_id, revoked_by, preview_fingerprint, db_path) -> dict`

- [ ] **Step 1: Write failing schema and transaction tests**

```python
import sqlite3

import pytest

from backend.services.receipt_exclusion_registry_service import (
    activate_receipt_exclusions,
    list_receipt_exclusions,
    load_active_registry_snapshot,
)


def _candidate():
    return {
        "candidateId": "candidate-1",
        "receiptNo": "SK2606005393",
        "sourceOrderNo": "31NZY6629115617",
        "exclusionKind": "payment_method:TT 退款轉團款",
        "observedAmount": 1630.0,
        "rawPayload": {"收款單號": "SK2606005393"},
        "rawRowHash": "raw-hash",
        "preparedPayload": {"來源單據號": "31NZY6629115617", "收款原幣金額": 1630.0},
        "preparedRowHash": "prepared-hash",
        "sourceFileName": "財務收款總數-0101-0722.xlsx",
        "sourceFileSha256": "file-hash",
        "reason": "confirmed exact excluded receipt",
    }


def test_activation_writes_registry_quarantine_and_event_atomically(tmp_path):
    db_path = tmp_path / "live.db"

    result = activate_receipt_exclusions(
        [_candidate()],
        operation_id="op-1",
        created_by="streamlit-local",
        proposal_fingerprint="proposal-hash",
        db_path=db_path,
    )

    assert result["status"] == "activated"
    assert len(result["ruleIds"]) == 1
    snapshot = load_active_registry_snapshot(db_path=db_path)
    assert snapshot["rules"][0].identity.receipt_no == "SK2606005393"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_quarantine").fetchone()[0] == 1
        assert conn.execute("SELECT event_type FROM receipt_exclusion_events").fetchone()[0] == "activated"


def test_activation_rolls_back_all_tables_when_quarantine_insert_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    from backend.services import receipt_exclusion_registry_service as service

    monkeypatch.setattr(
        service,
        "_insert_quarantine",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.IntegrityError("forced")),
    )

    with pytest.raises(sqlite3.IntegrityError):
        activate_receipt_exclusions(
            [_candidate()],
            operation_id="op-1",
            created_by="streamlit-local",
            proposal_fingerprint="proposal-hash",
            db_path=db_path,
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_registry").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM receipt_exclusion_events").fetchone()[0] == 0


def test_read_only_snapshot_does_not_create_tables(tmp_path):
    db_path = tmp_path / "live.db"
    sqlite3.connect(db_path).close()

    assert load_active_registry_snapshot(db_path=db_path)["rules"] == ()

    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "receipt_exclusion_registry" not in names
```

- [ ] **Step 2: Run registry tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_registry_service.py -q
```

Expected: import fails for the new service。

- [ ] **Step 3: Implement additive schema**

Use these exact tables and constraints:

```sql
CREATE TABLE IF NOT EXISTS receipt_exclusion_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_no_norm TEXT NOT NULL,
    source_order_no_norm TEXT NOT NULL,
    exclusion_kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
    reason TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    proposal_fingerprint TEXT NOT NULL,
    created_operation_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_operation_id TEXT,
    revoked_by TEXT,
    revoked_at TEXT,
    UNIQUE(receipt_no_norm, source_order_no_norm, exclusion_kind)
);

CREATE TABLE IF NOT EXISTS receipt_exclusion_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registry_id INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    source_file_name TEXT NOT NULL,
    source_file_sha256 TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    raw_row_hash TEXT NOT NULL,
    prepared_payload_json TEXT NOT NULL,
    prepared_row_hash TEXT NOT NULL,
    observed_amount REAL NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(registry_id) REFERENCES receipt_exclusion_registry(id)
);

CREATE TABLE IF NOT EXISTS receipt_exclusion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registry_id INTEGER,
    operation_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'activated', 'activation_rejected', 'auto_applied',
        'collision_blocked', 'revocation_preview_passed',
        'revocation_preview_failed', 'revoked'
    )),
    proposal_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(registry_id) REFERENCES receipt_exclusion_registry(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_receipt_exclusion_auto_event
ON receipt_exclusion_events(operation_id, registry_id, event_type, proposal_fingerprint);
```

Implementation requirements:

- `load_active_registry_snapshot(*, db_path)` opens only the explicit path, checks
  `sqlite_master`, and returns `{"revision": canonical_json_hash([]), "rules": ()}`
  without executing DDL when the registry table is absent。
- When rows exist, map every active row into `ReceiptExclusionRule` and compute revision
  with `canonical_json_hash` over rows sorted by `(receipt_no_norm, source_order_no_norm,
  exclusion_kind, id)`。
- `activate_receipt_exclusions(...)` opens `database.get_db_connection(db_path)`, executes
  `BEGIN IMMEDIATE`, calls `_ensure_schema(conn)`, inserts or resolves each exact rule,
  calls `_insert_quarantine(conn, ...)`, inserts one `activated` event, and commits。
- The `except BaseException` branch calls `conn.rollback()` and re-raises; `finally`
  closes the connection。

- [ ] **Step 4: Add idempotency, bounds and revoke persistence tests**

Add tests asserting:

```python
def test_duplicate_activation_returns_same_rule_without_duplicate_evidence(tmp_path):
    first = activate_receipt_exclusions(
        [_candidate()], operation_id="op-1", created_by="streamlit-local",
        proposal_fingerprint="p1", db_path=tmp_path / "live.db",
    )
    second = activate_receipt_exclusions(
        [_candidate()], operation_id="op-2", created_by="streamlit-local",
        proposal_fingerprint="p2", db_path=tmp_path / "live.db",
    )
    assert second["ruleIds"] == first["ruleIds"]
    assert second["status"] == "already_active"


def test_auto_applied_event_is_idempotent_per_operation_rule_and_fingerprint(tmp_path):
    db_path = tmp_path / "live.db"
    activation = activate_receipt_exclusions(
        [_candidate()], operation_id="op-1", created_by="streamlit-local",
        proposal_fingerprint="p1", db_path=db_path,
    )
    event = {
        "registryId": activation["ruleIds"][0],
        "proposalFingerprint": "row-hash-1",
        "payload": {"rowHash": "row-hash-1"},
    }
    first = record_auto_applied_events([event], operation_id="op-2", db_path=db_path)
    second = record_auto_applied_events([event], operation_id="op-2", db_path=db_path)
    assert second == first
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT count(*) FROM receipt_exclusion_events "
            "WHERE event_type='auto_applied'"
        ).fetchone()[0]
    assert count == 1


def test_revoke_updates_rule_and_event_in_one_transaction(tmp_path):
    db_path = tmp_path / "live.db"
    activation = activate_receipt_exclusions(
        [_candidate()], operation_id="op-1", created_by="streamlit-local",
        proposal_fingerprint="p1", db_path=db_path,
    )
    result = commit_receipt_exclusion_revocation(
        activation["ruleIds"][0],
        operation_id="revoke-1",
        revoked_by="streamlit-local",
        preview_fingerprint="preview-1",
        db_path=db_path,
    )
    assert result["status"] == "revoked"
    assert list_receipt_exclusions(status="active", db_path=db_path) == []
    assert list_receipt_exclusions(status="revoked", db_path=db_path)[0]["id"] == activation["ruleIds"][0]
```

Use `limit = max(1, min(int(limit), 100))` for list APIs and reject payload JSON above
the caps declared in `agent_config/token_budgets.json` only if such a shared cap already
exists; otherwise define local constants `MAX_EVENT_JSON_CHARS = 20000` and
`MAX_QUARANTINE_JSON_CHARS = 50000`。

- [ ] **Step 5: Run registry tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_registry_service.py -q
```

Expected: all registry tests PASS。

Commit:

```bash
git add backend/services/receipt_exclusion_registry_service.py tests/test_receipt_exclusion_registry_service.py
git commit -m "feat: add receipt exclusion governance storage"
```

---

### Task 3: Fingerprinted Proposal and Private Evidence

**Risk surface:** read-only diagnosis transformation。

**Files:**
- Create: `backend/services/receipt_exclusion_proposal_service.py`
- Create: `tests/test_receipt_exclusion_proposal_service.py`

**Interfaces:**
- Consumes: Task 1 matcher/models and Drift Diagnosis output。
- Produces:
  - `build_receipt_exclusion_proposal(...) -> tuple[dict, dict]`
    - first item: public `receipt-exclusion-proposal-v1`
    - second item: private evidence keyed by `candidateId`
  - `validate_candidate_selection(proposal, selected_candidate_ids) -> list[dict]`

- [ ] **Step 1: Write failing eligible/ineligible proposal tests**

```python
import pandas as pd

from backend.services.receipt_exclusion_proposal_service import (
    build_receipt_exclusion_proposal,
)


def test_builds_exact_proposal_for_tt_driver():
    diagnosis = {
        "status": "drift",
        "diagnosedCheckKey": "monthlyRevenue:2026-06",
        "expectedTotal": 9083241.29,
        "actualTotal": 9081971.29,
        "deltaAmount": -1270.0,
        "topDrivers": [{
            "sourceOrderNo": "31NZY6629115617",
            "receiptNo": "SK2606005393",
            "paymentMethod": "TT 退款轉團款",
            "paymentType": "旅費",
            "deltaAmount": -1270.0,
        }],
    }
    raw = pd.DataFrame([{
        "來源單據號": "31NZY6629115617",
        "收款單號": "SK2606005393",
        "收款方式": "TT 退款轉團款",
        "收款類型": "旅費",
        "收款原幣金額": 1630.0,
    }])
    prepared = pd.DataFrame([{
        "來源單據號": "31NZY6629115617",
        "收款單號": "SK2606005393",
        "收款方式": "TT 退款轉團款",
        "收款類型": "旅費",
        "收款原幣金額": 1630.0,
        "統一日期": "2026-06-29",
    }])

    public, private = build_receipt_exclusion_proposal(
        diagnosis=diagnosis,
        raw_main_frame=raw,
        prepared_frames=[prepared],
        operation_id="op-1",
        source_files=["財務收款總數-0101-0722.xlsx"],
        source_batch_fingerprint="batch-hash",
        registry_revision="registry-hash",
        live_db_identity="db-identity",
    )

    assert public["status"] == "confirmation_required"
    assert public["candidates"][0]["receiptNo"] == "SK2606005393"
    assert "rawPayload" not in public["candidates"][0]
    candidate_id = public["candidates"][0]["candidateId"]
    assert private[candidate_id]["preparedPayload"]["收款單號"] == "SK2606005393"


def test_normal_payment_driver_does_not_create_proposal():
    diagnosis = {
        "status": "drift",
        "diagnosedCheckKey": "monthlyRevenue:2026-06",
        "expectedTotal": 9083241.29,
        "actualTotal": 9081971.29,
        "deltaAmount": -1270.0,
        "topDrivers": [{
            "sourceOrderNo": "31NZY6629115617",
            "receiptNo": "SK2606005393",
            "paymentMethod": "現金",
            "paymentType": "旅費",
            "deltaAmount": -1270.0,
        }],
    }
    public, private = build_receipt_exclusion_proposal(
        diagnosis=diagnosis,
        raw_main_frame=pd.DataFrame(),
        prepared_frames=[],
        operation_id="op-1",
        source_files=["main.xlsx"],
        source_batch_fingerprint="batch-hash",
        registry_revision="registry-hash",
        live_db_identity="db-identity",
    )
    assert public == {}
    assert private == {}


def test_proposal_fingerprint_changes_with_gate_row_or_registry_revision():
    diagnosis = {
        "status": "drift",
        "diagnosedCheckKey": "monthlyRevenue:2026-06",
        "expectedTotal": 9083241.29,
        "actualTotal": 9081971.29,
        "deltaAmount": -1270.0,
        "topDrivers": [{
            "sourceOrderNo": "31NZY6629115617",
            "receiptNo": "SK2606005393",
            "paymentMethod": "TT 退款轉團款",
            "paymentType": "旅費",
            "deltaAmount": -1270.0,
        }],
    }
    row = {
        "來源單據號": "31NZY6629115617",
        "收款單號": "SK2606005393",
        "收款方式": "TT 退款轉團款",
        "收款類型": "旅費",
        "收款原幣金額": 1630.0,
        "統一日期": "2026-06-29",
    }
    first, _ = build_receipt_exclusion_proposal(
        diagnosis=diagnosis, raw_main_frame=pd.DataFrame([row]),
        prepared_frames=[pd.DataFrame([row])], operation_id="op-1",
        source_files=["main.xlsx"], source_batch_fingerprint="batch-hash",
        registry_revision="r1", live_db_identity="db-identity",
    )
    second, _ = build_receipt_exclusion_proposal(
        diagnosis=diagnosis, raw_main_frame=pd.DataFrame([row]),
        prepared_frames=[pd.DataFrame([row])], operation_id="op-1",
        source_files=["main.xlsx"], source_batch_fingerprint="batch-hash",
        registry_revision="r2", live_db_identity="db-identity",
    )
    assert first["proposalFingerprint"] != second["proposalFingerprint"]
```

- [ ] **Step 2: Run proposal tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_proposal_service.py -q
```

Expected: import fails。

- [ ] **Step 3: Implement proposal eligibility and fingerprints**

Use fixed allowlists:

```python
RAW_EVIDENCE_FIELDS = (
    "來源單據號", "收款單號", "收款類型", "收款方式",
    "收款原幣金額", "收款時間", "銷售點", "銷售員",
)
PREPARED_EVIDENCE_FIELDS = (
    "來源單據號", "收款單號", "收款類型", "收款方式",
    "收款原幣金額", "統一日期", "銷售點", "副表_銷售點",
    "銷售員", "資料來源", "產品分類",
)


def build_receipt_exclusion_proposal(
    *,
    diagnosis: dict,
    raw_main_frame: pd.DataFrame,
    prepared_frames: list[pd.DataFrame],
    operation_id: str,
    source_files: list[str],
    source_batch_fingerprint: str,
    registry_revision: str,
    live_db_identity: str,
) -> tuple[dict, dict]:
    eligible = [
        row for row in diagnosis.get("topDrivers", [])
        if normalize_identity_text(row.get("paymentType")) == "掛賬核銷"
        or normalize_identity_text(row.get("paymentMethod")) == "TT 退款轉團款"
    ]
    if diagnosis.get("status") != "drift" or not eligible:
        return {}, {}
    # Resolve each eligible identity to exactly one raw and one prepared row.
    # Build allowlisted payloads and hashes; duplicate/missing resolution returns ({}, {}).
    candidates, private_evidence = _resolve_candidate_evidence(
        eligible, raw_main_frame, prepared_frames, source_files
    )
    if not candidates:
        return {}, {}
    fingerprint_payload = {
        "operationId": operation_id,
        "sourceBatchFingerprint": source_batch_fingerprint,
        "diagnosedCheckKey": diagnosis.get("diagnosedCheckKey"),
        "expectedTotal": diagnosis.get("expectedTotal"),
        "actualTotal": diagnosis.get("actualTotal"),
        "deltaAmount": diagnosis.get("deltaAmount"),
        "candidateIds": [row["candidateId"] for row in candidates],
        "rowHashes": [row["rowHash"] for row in candidates],
        "registryRevision": registry_revision,
        "liveDbIdentity": live_db_identity,
    }
    public = {
        "schemaVersion": "receipt-exclusion-proposal-v1",
        "status": "confirmation_required",
        "operationId": operation_id,
        "sourceBatchFingerprint": source_batch_fingerprint,
        "diagnosedCheckKey": str(diagnosis.get("diagnosedCheckKey") or ""),
        "expectedTotal": float(diagnosis.get("expectedTotal") or 0),
        "actualTotal": float(diagnosis.get("actualTotal") or 0),
        "deltaAmount": float(diagnosis.get("deltaAmount") or 0),
        "candidates": candidates,
        "proposalFingerprint": canonical_json_hash(fingerprint_payload),
    }
    return public, private_evidence
```

`candidateId` must equal Task 1 identity hash。`source_file_name` must be a basename。
The private evidence object must never be copied into `preflightReport` public output。
Implement `_resolve_candidate_evidence` in the same file: filter rows by normalized exact
identity, require exactly one raw row and one prepared row, project the two fixed allowlists,
and return no candidate for an ambiguous or missing match。

- [ ] **Step 4: Run proposal tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_proposal_service.py tests/test_receipt_exclusion_matcher.py -q
```

Expected: all tests PASS。

Commit:

```bash
git add backend/services/receipt_exclusion_proposal_service.py tests/test_receipt_exclusion_proposal_service.py
git commit -m "feat: build fingerprinted receipt exclusion proposals"
```

---

### Task 4: Read-Only Preflight Registry Integration

**Risk surface:** protected upload/baseline path; main Codex only。

**Files:**
- Modify: `pipeline.py:41-47,527-550`
- Modify: `backend/services/upload_preflight_service.py:97-198`
- Modify: `tests/test_upload_preflight_service.py`
- Create: `tests/test_receipt_exclusion_preflight.py`

**Interfaces:**
- Consumes: Task 1-3 services and Task 2 read-only snapshot。
- Produces additions to `run_upload_preflight`: keyword arguments
  `receipt_exclusion_overlay: tuple[ReceiptExclusionRule, ...] = ()`,
  `operation_id: str = ""`, and
  `registry_loader: Callable = load_active_registry_snapshot`。

Public report additions:

```python
{
    "receiptExclusion": {
        "registryRevision": "registry-sha256",
        "matchedRules": [],
        "collisions": [],
        "autoApplyAudit": []
    },
    "receiptExclusionProposal": {}
}
```

Private `prepared` addition:

```python
{
    "receipt_exclusion_evidence": {
        "candidate-id": {
            "rawPayload": {"收款單號": "SK2606005393"},
            "rawRowHash": "raw-sha256",
            "preparedPayload": {"收款單號": "SK2606005393"},
            "preparedRowHash": "prepared-sha256"
        }
    }
}
```

- [ ] **Step 1: Expose the existing structured Excel reader with compatibility alias**

Write a failing test:

```python
def test_read_excel_source_accepts_named_dataframe_tuple():
    from pipeline import read_excel_source
    frame, name = read_excel_source(("main.xlsx", pd.DataFrame([{"A": 1}])))
    assert name == "main.xlsx"
    assert frame.to_dict(orient="records") == [{"A": 1}]
```

Implement:

```python
def read_excel_source(source) -> tuple[pd.DataFrame, str]:
    if isinstance(source, tuple) and len(source) == 2 and isinstance(source[1], pd.DataFrame):
        name, frame = source
        return frame.copy(), str(name)
    if isinstance(source, pd.DataFrame):
        return source.copy(), str(getattr(source, "name", ""))
    return pd.read_excel(source, dtype=str), str(getattr(source, "name", ""))


_read_excel_source = read_excel_source
```

Change `process_raw_files` internal calls to `read_excel_source` without changing behavior。

- [ ] **Step 2: Write failing active-match, collision and proposal tests**

Create a local `_run_preflight` test helper that:

1. creates `live.db` with existing test fixture rows using `database.upsert_to_db`；
2. monkeypatches `build_phase2c_stability_gate` to return the supplied gate；
3. calls `run_upload_preflight` with a DataFrame main source, explicit `live_db_path`,
   `operation_id="op-1"` and supplied registry snapshot。

Add these exact assertions:

```python
active_report = _run_preflight(
    main_frame=_main_with_target_tt(),
    snapshot={"revision": "r1", "rules": (_active_rule(),)},
    gate=_matched_gate(),
)
assert active_report["receiptExclusion"]["matchedRules"][0]["receiptNo"] == "SK2606005393"
assert active_report["liveDbUnchanged"] is True

collision_report = _run_preflight(
    main_frame=_main_with_same_receipt_different_order(),
    snapshot={"revision": "r1", "rules": (_active_rule(),)},
    gate=_matched_gate(),
)
assert collision_report["status"] == "receipt_exclusion_collision"
assert collision_report["prepared"] == {}

proposal_report = _run_preflight(
    main_frame=_main_with_target_tt(),
    snapshot={"revision": "r1", "rules": ()},
    gate=_june_minus_1270_gate(),
)
assert proposal_report["receiptExclusionProposal"]["status"] == "confirmation_required"
assert "rawPayload" not in proposal_report["receiptExclusionProposal"]["candidates"][0]
assert proposal_report["prepared"]["receipt_exclusion_evidence"]
```

The helper functions `_main_with_target_tt`, `_main_with_same_receipt_different_order`,
`_active_rule`, `_matched_gate` and `_june_minus_1270_gate` must return the exact identities
and amounts declared in the design spec, not random values。

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_preflight.py tests/test_upload_preflight_service.py -q
```

Expected: failures for missing arguments/report fields。

- [ ] **Step 4: Integrate match before `process_raw_files`**

Implementation order:

```python
main_frame, main_source_name = read_excel_source(main_file)
snapshot = registry_loader(db_path=live_path)
rules = tuple(snapshot.get("rules") or ()) + tuple(receipt_exclusion_overlay or ())
match_result = match_receipt_exclusions(main_frame, rules)
if match_result.collisions:
    return {
        "status": "receipt_exclusion_collision",
        "message": "收款單永久排除 identity 與本次來源資料衝突，正式 SQLite 不會寫入。",
        "sourceFiles": source_files,
        "receiptExclusion": {
            "registryRevision": snapshot["revision"],
            "matchedRules": list(match_result.matches),
            "collisions": list(match_result.collisions),
            "autoApplyAudit": [],
        },
        "receiptExclusionProposal": {},
        "prepared": {},
        "liveDbUnchanged": True,
    }

new_t_df, new_o_df, anm_df, entity_audit = process_raw_files(
    (main_source_name, match_result.filtered_frame),
    tour_file,
    other_files or [],
    branch_mapping,
    exclude_prefixes,
    sales_reps,
    return_entity_audit=True,
)
```

After Drift Diagnosis:

```python
proposal, private_evidence = build_receipt_exclusion_proposal(
    diagnosis=drift_diagnosis,
    raw_main_frame=main_frame,
    prepared_frames=[new_t_df, new_o_df],
    operation_id=operation_id,
    source_files=source_files,
    source_batch_fingerprint=source_batch_fingerprint,
    registry_revision=snapshot["revision"],
    live_db_identity=str(live_path.resolve()),
)
```

Do not call any registry write function from preflight。

- [ ] **Step 5: Preserve all existing preflight behavior**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_receipt_exclusion_preflight.py \
  tests/test_upload_preflight_service.py \
  tests/test_drift_diagnosis_service.py \
  tests/test_monthly_baseline_service.py -q
```

Expected: all tests PASS。

- [ ] **Step 6: Commit**

```bash
git add pipeline.py backend/services/upload_preflight_service.py tests/test_upload_preflight_service.py tests/test_receipt_exclusion_preflight.py
git commit -m "feat: apply receipt exclusions during upload preflight"
```

---

### Task 5: Confirm, Activate and Continue Through the Canonical Orchestrator

**Risk surface:** protected upload/SQLite/rollback path; main Codex only。

**Files:**
- Create: `backend/services/receipt_exclusion_governance_service.py`
- Modify: `backend/services/upload_orchestrator_service.py`
- Modify: `backend/services/stability_history_service.py`
- Create: `tests/test_receipt_exclusion_governance_service.py`
- Modify: `tests/test_upload_orchestrator_service.py`
- Modify: `tests/test_stability_history_service.py`

**Interfaces:**
- Produces `verify_receipt_exclusion_confirmation(...) -> list[dict]` and extends
  `execute_upload_operation` with keyword arguments
  `receipt_exclusion_confirmation: dict | None = None`,
  `registry_loader=load_active_registry_snapshot`,
  `registry_activator=activate_receipt_exclusions`, and
  `auto_event_recorder=record_auto_applied_events`。

```python
def verify_receipt_exclusion_confirmation(
    *,
    canonical_proposal: dict,
    private_evidence: dict,
    submitted_fingerprint: str,
    selected_candidate_ids: list[str],
) -> list[dict]:
    if canonical_proposal.get("proposalFingerprint") != submitted_fingerprint:
        raise ValueError("stale receipt exclusion proposal")
    public = {
        str(item["candidateId"]): item
        for item in canonical_proposal.get("candidates", [])
    }
    selected = []
    for candidate_id in selected_candidate_ids:
        if candidate_id not in public or candidate_id not in private_evidence:
            raise ValueError("unknown receipt exclusion candidate")
        selected.append({**public[candidate_id], **private_evidence[candidate_id]})
    if not selected:
        raise ValueError("at least one receipt exclusion candidate is required")
    return selected
```

- [ ] **Step 1: Write failing confirmation integrity tests**

```python
def test_confirmation_rejects_stale_proposal_fingerprint():
    with pytest.raises(ValueError, match="stale receipt exclusion proposal"):
        verify_receipt_exclusion_confirmation(
            canonical_proposal={"proposalFingerprint": "current", "candidates": []},
            private_evidence={},
            submitted_fingerprint="old",
            selected_candidate_ids=[],
        )


def test_confirmation_rejects_unknown_candidate():
    with pytest.raises(ValueError, match="unknown receipt exclusion candidate"):
        verify_receipt_exclusion_confirmation(
            canonical_proposal={
                "proposalFingerprint": "current",
                "candidates": [{"candidateId": "allowed"}],
            },
            private_evidence={"allowed": {"rawPayload": {}}},
            submitted_fingerprint="current",
            selected_candidate_ids=["forged"],
        )
```

- [ ] **Step 2: Write failing orchestrator second-preflight tests**

In `tests/test_upload_orchestrator_service.py`, add helper reports with exact minimum payloads:

```python
def _blocked_proposal_report():
    return {
        "status": "drift",
        "message": "blocked",
        "receiptExclusion": {"registryRevision": "r1", "matchedRules": [], "autoApplyAudit": []},
        "receiptExclusionProposal": {
            "proposalFingerprint": "proposal-1",
            "candidates": [{
                "candidateId": "candidate-1",
                "receiptNo": "SK2606005393",
                "sourceOrderNo": "31NZY6629115617",
                "exclusionKind": "payment_method:TT 退款轉團款",
            }],
        },
        "prepared": {
            "receipt_exclusion_evidence": {
                "candidate-1": {
                    "rawPayload": {"收款單號": "SK2606005393"},
                    "preparedPayload": {"收款單號": "SK2606005393"},
                }
            }
        },
    }


def _matched_overlay_report():
    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "31NZY6629115617", "統一日期": "2026-06-29"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
    }
    return {
        "status": "matched",
        "prepared": prepared,
        "receiptExclusion": {"registryRevision": "r1", "matchedRules": [], "autoApplyAudit": []},
        "receiptExclusionProposal": {},
    }
```

Then add four tests using the existing `_accepted_execution(tmp_path, **overrides)` helper:

```python
def test_confirmed_upload_reruns_preflight_with_overlay_before_activation(tmp_path):
    preflight_calls, activation_calls = [], []

    def preflight(*args, **kwargs):
        preflight_calls.append(kwargs.get("receipt_exclusion_overlay"))
        return _blocked_proposal_report() if len(preflight_calls) == 1 else _matched_overlay_report()

    execution = _accepted_execution(
        tmp_path,
        preflight_runner=preflight,
        receipt_exclusion_confirmation={
            "proposalFingerprint": "proposal-1",
            "selectedCandidateIds": ["candidate-1"],
            "confirmedBy": "streamlit-local",
        },
        registry_activator=lambda *args, **kwargs: activation_calls.append(kwargs) or {
            "status": "activated", "ruleIds": [7], "revision": "r2",
        },
        registry_loader=lambda **kwargs: {"revision": "r2", "rules": ()},
    )
    assert preflight_calls[0] in (None, ())
    assert preflight_calls[1][0].identity.receipt_no == "SK2606005393"
    assert len(activation_calls) == 1
    assert execution.response["writeCommitted"] is True


def test_overlay_drift_does_not_activate_or_upsert(tmp_path):
    writes = []
    reports = [_blocked_proposal_report(), _blocked_proposal_report()]
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: reports.pop(0),
        receipt_exclusion_confirmation={
            "proposalFingerprint": "proposal-1",
            "selectedCandidateIds": ["candidate-1"],
            "confirmedBy": "streamlit-local",
        },
        registry_activator=lambda *args, **kwargs: writes.append("activate"),
        upsert_runner=lambda *args, **kwargs: writes.append("upsert"),
    )
    assert execution.response["status"] == "blocked"
    assert writes == []


def test_registry_revision_change_before_upsert_blocks_operation(tmp_path):
    revisions = iter(["r1", "changed"])
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: _matched_overlay_report(),
        registry_loader=lambda **kwargs: {"revision": next(revisions), "rules": ()},
    )
    assert execution.response["status"] == "blocked"
    assert execution.response["message"] == "收款單永久排除規則已更新，請重新預演。"


def test_auto_event_failure_blocks_before_formal_upsert(tmp_path):
    writes = []
    report = _matched_overlay_report()
    report["receiptExclusion"]["autoApplyAudit"] = [{"registryId": 7}]
    execution = _accepted_execution(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: report,
        auto_event_recorder=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("audit failed")),
        upsert_runner=lambda *args, **kwargs: writes.append("upsert"),
    )
    assert execution.response["status"] == "blocked"
    assert writes == []
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_governance_service.py tests/test_upload_orchestrator_service.py -q
```

Expected: missing governance functions/arguments。

- [ ] **Step 4: Implement confirmation verification**

```python
def verify_receipt_exclusion_confirmation(
    *,
    canonical_proposal: dict,
    private_evidence: dict,
    submitted_fingerprint: str,
    selected_candidate_ids: list[str],
) -> list[dict]:
    if canonical_proposal.get("proposalFingerprint") != submitted_fingerprint:
        raise ValueError("stale receipt exclusion proposal")
    allowed = {
        str(item["candidateId"]): item
        for item in canonical_proposal.get("candidates", [])
    }
    selected = []
    for candidate_id in selected_candidate_ids:
        if candidate_id not in allowed or candidate_id not in private_evidence:
            raise ValueError("unknown receipt exclusion candidate")
        selected.append({
            **allowed[candidate_id],
            **private_evidence[candidate_id],
        })
    if not selected:
        raise ValueError("at least one receipt exclusion candidate is required")
    return selected
```

- [ ] **Step 5: Refactor Orchestrator without duplicating the write path**

Extract the existing matched branch into `_commit_matched_upload` with parameters
`operation`, `preflight`, `live_path`, `upsert_runner`, `load_runner`, `gate_builder`,
`rollback_handler`, `generation_advancer`, `history_writer`, and
`accepted_cache_rebuilder`。Move the current code from prepared-frame extraction through
`UploadExecution(response, anomaly, entity_audit)` byte-for-byte before adding receipt
governance fields; the existing tests must pass immediately after extraction。

`execute_upload_operation` then:

1. Runs canonical preflight with `operation_id`。
2. Without confirmation: returns existing blocked result or calls `_commit_matched_upload`。
3. With confirmation: validates the freshly recomputed proposal。
4. Builds overlay rules from selected exact identities。
5. Rewinds file-like inputs and reruns preflight with overlay。
6. Requires overlay report `status == matched` and same base registry revision。
7. Activates registry/quarantine/events transaction。
8. Requires returned active revision to equal the revision used for formal write。
9. Writes `auto_applied` events for already-active rules。
10. Calls the same `_commit_matched_upload`。

- [ ] **Step 6: Extend stability history additively**

Add columns:

Add these exact entries to the existing `migrations` dictionary:

```python
"receipt_exclusion_revision": "TEXT",
"receipt_exclusion_rule_ids_json": "TEXT",
"receipt_exclusion_match_count": "INTEGER NOT NULL DEFAULT 0",
"receipt_exclusion_proposal_fingerprint": "TEXT",
```

Returned history fields:

```python
{
    "receiptExclusionRevision": row["receipt_exclusion_revision"],
    "receiptExclusionRuleIds": _json_load(row["receipt_exclusion_rule_ids_json"], []),
    "receiptExclusionMatchCount": int(row["receipt_exclusion_match_count"] or 0),
    "receiptExclusionProposalFingerprint": row["receipt_exclusion_proposal_fingerprint"],
}
```

- [ ] **Step 7: Run protected focused tests and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_receipt_exclusion_governance_service.py \
  tests/test_upload_orchestrator_service.py \
  tests/test_stability_history_service.py \
  tests/test_upload_rollback_service.py \
  tests/test_upload_single_writer_integration.py -q
```

Expected: all tests PASS。

Commit:

```bash
git add backend/services/receipt_exclusion_governance_service.py backend/services/upload_orchestrator_service.py backend/services/stability_history_service.py tests/test_receipt_exclusion_governance_service.py tests/test_upload_orchestrator_service.py tests/test_stability_history_service.py
git commit -m "feat: confirm and activate receipt exclusions safely"
```

---

### Task 6: Safe Revocation Preview and Confirmation

**Risk surface:** protected SQLite/baseline governance; main Codex only。

**Files:**
- Modify: `backend/services/receipt_exclusion_governance_service.py`
- Modify: `backend/services/receipt_exclusion_registry_service.py`
- Create: `backend/services/receipt_exclusion_read_model_service.py`
- Modify: `tests/test_receipt_exclusion_governance_service.py`
- Create: `tests/test_receipt_exclusion_read_model_service.py`

**Interfaces:**
- Produces:

```python
def preview_receipt_exclusion_revocation(
    rule_id: int,
    *,
    operation: UploadOperation,
    live_db_path,
    registry_reader=load_active_registry_snapshot,
    evidence_loader=load_quarantine_evidence,
    snapshotter=database.snapshot_sqlite_database,
    upsert_runner=database.upsert_to_db,
    gate_builder=build_governed_stability_gate,
) -> dict:
    """Replay one quarantined prepared row into a disposable DB and return a signed preview."""


def confirm_receipt_exclusion_revocation(
    rule_id: int,
    *,
    operation: UploadOperation,
    submitted_preview_fingerprint: str,
    revoked_by: str,
    live_db_path,
    registry_reader=load_active_registry_snapshot,
    evidence_loader=load_quarantine_evidence,
    preview_runner=preview_receipt_exclusion_revocation,
    revocation_committer=commit_receipt_exclusion_revocation,
) -> dict:
    """Recompute the preview under the upload lease and revoke only on an exact match."""


def build_receipt_exclusion_read_model(*, db_path, limit=100) -> dict:
    """Return bounded active/revoked rules and event counts without quarantine payloads."""
```

- [ ] **Step 1: Write failing revocation tests**

```python
def test_revocation_preview_replays_prepared_quarantine_row_in_temp_db(tmp_path):
    result = preview_receipt_exclusion_revocation(
        7,
        operation=_operation(),
        live_db_path=tmp_path / "live.db",
        registry_reader=lambda **kwargs: _active_rule_snapshot(),
        evidence_loader=lambda *args, **kwargs: _quarantine_evidence(),
        snapshotter=_copy_fixture_db,
        upsert_runner=_record_temp_upsert,
        gate_builder=lambda **kwargs: {"status": "drift", "deltaAmount": -1270.0},
    )
    assert result["status"] == "revocation_blocked"
    assert result["deltaAmount"] == -1270.0
    assert result["previewFingerprint"]


def test_drift_preview_keeps_rule_active(tmp_path):
    result = preview_receipt_exclusion_revocation(
        7,
        operation=_operation(),
        live_db_path=tmp_path / "live.db",
        registry_reader=lambda **kwargs: _active_rule_snapshot(),
        evidence_loader=lambda *args, **kwargs: _quarantine_evidence(),
        snapshotter=_copy_fixture_db,
        upsert_runner=_record_temp_upsert,
        gate_builder=lambda **kwargs: {"status": "drift", "deltaAmount": -1270.0},
    )
    assert result["status"] == "revocation_blocked"
    assert _registry_status(tmp_path / "live.db", 7) == "active"


def test_confirm_rejects_changed_registry_revision_or_preview_fingerprint(tmp_path):
    preview = _ready_revocation_preview(tmp_path, registry_revision="r1")
    with pytest.raises(ValueError, match="stale revocation preview"):
        confirm_receipt_exclusion_revocation(
            7,
            operation=_operation(),
            submitted_preview_fingerprint=preview["previewFingerprint"],
            revoked_by="streamlit-local",
            live_db_path=tmp_path / "live.db",
            registry_reader=lambda **kwargs: _active_rule_snapshot(revision="r2"),
        )
    assert _registry_status(tmp_path / "live.db", 7) == "active"


def test_matched_preview_can_commit_revocation_with_event(tmp_path):
    preview = _ready_revocation_preview(tmp_path, registry_revision="r1")
    result = confirm_receipt_exclusion_revocation(
        7,
        operation=_operation(),
        submitted_preview_fingerprint=preview["previewFingerprint"],
        revoked_by="streamlit-local",
        live_db_path=tmp_path / "live.db",
    )
    assert result["status"] == "revoked"
    assert _registry_status(tmp_path / "live.db", 7) == "revoked"
    assert _latest_event_type(tmp_path / "live.db", 7) == "revoked"
```

Test helpers `_active_rule_snapshot`, `_quarantine_evidence`, `_copy_fixture_db`,
`_record_temp_upsert`, `_operation`, `_registry_status`, `_latest_event_type` and
`_ready_revocation_preview` must all use the same temporary `live.db` fixture. The ready
preview helper must return `status == revocation_ready`, `registryRevision == "r1"` and a
fingerprint computed from the exact preview payload; it may not hard-code an unrelated hash。

- [ ] **Step 2: Run revocation tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_governance_service.py tests/test_receipt_exclusion_read_model_service.py -q
```

Expected: missing revocation/read-model functions。

- [ ] **Step 3: Implement temporary DB replay**

```python
def preview_receipt_exclusion_revocation(
    rule_id: int,
    *,
    operation: UploadOperation,
    live_db_path,
    registry_reader=load_active_registry_snapshot,
    evidence_loader=load_quarantine_evidence,
    snapshotter=database.snapshot_sqlite_database,
    upsert_runner=database.upsert_to_db,
    gate_builder=build_governed_stability_gate,
) -> dict:
    live_path = database.resolve_db_path(live_db_path)
    evidence = evidence_loader(rule_id, db_path=live_path)
    prepared = pd.DataFrame([evidence["preparedPayload"]])
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "revocation-preview.db"
        snapshotter(live_path, temp_path)
        upsert_runner(
            prepared if evidence["tableName"] == "tour_data" else pd.DataFrame(),
            prepared if evidence["tableName"] == "others_data" else pd.DataFrame(),
            db_path=temp_path,
        )
        gate = gate_builder(db_path=temp_path)
    preview = {
        "schemaVersion": "receipt-exclusion-revocation-preview-v1",
        "ruleId": rule_id,
        "registryRevision": evidence["registryRevision"],
        "preparedRowHash": evidence["preparedRowHash"],
        "databaseIdentity": database_snapshot_identity(live_path),
        "gate": gate,
    }
    return {
        **preview,
        "status": "revocation_ready" if gate["status"] == "matched" else "revocation_blocked",
        "previewFingerprint": canonical_json_hash(preview),
        "deltaAmount": float(gate.get("deltaAmount") or 0),
    }
```

`confirm_receipt_exclusion_revocation` must acquire the existing upload lease, verify the active
registry revision before and after `preview_runner`, recompute the preview with the supplied
evidence dependencies, and call `revocation_committer` only when status is `revocation_ready`
and fingerprint matches。A revision or fingerprint mismatch raises
`ValueError("stale revocation preview")` before any formal write。

- [ ] **Step 4: Implement bounded read model**

Return:

```python
{
    "schemaVersion": "receipt-exclusion-read-model-v1",
    "registryRevision": "sha256-of-active-rules",
    "active": [],
    "revoked": [],
    "counts": {"active": 0, "revoked": 0},
}
```

Do not return raw/prepared quarantine payloads。Expose only hashes, reason, timestamps,
last match time and bounded event count。

- [ ] **Step 5: Run revocation/read-model tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_governance_service.py tests/test_receipt_exclusion_registry_service.py tests/test_receipt_exclusion_read_model_service.py -q
```

Expected: all tests PASS。

Commit:

```bash
git add backend/services/receipt_exclusion_governance_service.py backend/services/receipt_exclusion_registry_service.py backend/services/receipt_exclusion_read_model_service.py tests/test_receipt_exclusion_governance_service.py tests/test_receipt_exclusion_read_model_service.py
git commit -m "feat: add safe receipt exclusion revocation"
```

---

### Task 7: FastAPI Governance Contract

**Risk surface:** protected upload API contract; main Codex only。

**Files:**
- Create: `backend/schemas/receipt_exclusions.py`
- Modify: `backend/schemas/dashboard.py`
- Modify: `backend/services/upload_action_service.py`
- Modify: `backend/routers/upload.py`
- Modify: `tests/test_upload_action_service.py`
- Modify: `tests/test_upload_api.py`
- Create: `tests/test_receipt_exclusion_api.py`

**Interfaces:**
- Endpoints:
  - `GET /api/upload/receipt-exclusions`
  - `POST /api/upload/receipt-exclusions/confirm` multipart
  - `POST /api/upload/receipt-exclusions/{rule_id}/revocation-preview`
  - `POST /api/upload/receipt-exclusions/{rule_id}/revoke`

- [ ] **Step 1: Write failing OpenAPI and action tests**

```python
def test_confirmation_endpoint_requires_files_fingerprint_and_selected_ids(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.upload.run_vue_upload_action",
        AsyncMock(return_value={
            "status": "accepted",
            "receiptExclusion": {"activatedRuleIds": [7]},
        }),
    )
    client = TestClient(create_app())
    response = client.post(
        "/api/upload/receipt-exclusions/confirm",
        data={
            "proposal_fingerprint": "proposal-1",
            "selected_candidate_ids": '["candidate-1"]',
        },
        files=[("main_file", ("main.xlsx", b"main", XLSX_MIME))],
    )
    assert response.status_code == 200
    assert response.json()["receiptExclusion"]["activatedRuleIds"] == [7]


def test_confirmation_rejects_invalid_candidate_json():
    client = TestClient(create_app())
    response = client.post(
        "/api/upload/receipt-exclusions/confirm",
        data={
            "proposal_fingerprint": "proposal-1",
            "selected_candidate_ids": "not-json",
        },
        files=[("main_file", ("main.xlsx", b"main", XLSX_MIME))],
    )
    assert response.status_code == 400


def test_registry_list_and_revocation_routes_are_named_in_openapi():
    schema = create_app().openapi()["paths"]
    assert "/api/upload/receipt-exclusions" in schema
    assert "/api/upload/receipt-exclusions/{rule_id}/revoke" in schema
```

- [ ] **Step 2: Run API tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_api.py tests/test_upload_api.py tests/test_upload_action_service.py -q
```

Expected: 404/missing schemas。

- [ ] **Step 3: Add exact Pydantic contracts**

```python
class ReceiptExclusionCandidate(BaseModel):
    candidateId: str
    sourceOrderNo: str
    receiptNo: str
    exclusionKind: str
    observedAmount: float
    affectedRevenue: float
    rowHash: str


class ReceiptExclusionProposal(BaseModel):
    schemaVersion: Literal["receipt-exclusion-proposal-v1"]
    status: Literal["confirmation_required"]
    operationId: str
    proposalFingerprint: str
    sourceBatchFingerprint: str
    diagnosedCheckKey: str
    expectedTotal: float
    actualTotal: float
    deltaAmount: float
    candidates: list[ReceiptExclusionCandidate]


class ReceiptExclusionRevocationRequest(BaseModel):
    previewFingerprint: str
    confirmedBy: str = "vue-local"
```

Add optional `receiptExclusion: dict = Field(default_factory=dict)` to
`UploadActionResponse` without removing existing fields。

- [ ] **Step 4: Make upload bytes replayable for confirmation**

Extend:

```python
async def run_vue_upload_action(
    *,
    main_file,
    tour_file=None,
    other_files=None,
    receipt_exclusion_confirmation: dict | None = None,
) -> dict[str, Any]:
```

Read each UploadFile once after acquiring the lease, then create fresh `NamedBytesIO`
instances for every canonical/overlay preflight pass。Busy upload must still not read bytes。

- [ ] **Step 5: Implement routes with the same lease and explicit DB path**

Confirmation route parses `selected_candidate_ids` with `json.loads`, requires a list of
non-empty strings, then calls `run_vue_upload_action` with:

```python
receipt_exclusion_confirmation={
    "proposalFingerprint": proposal_fingerprint,
    "selectedCandidateIds": selected_ids,
    "confirmedBy": "vue-local",
}
```

Revocation routes acquire:

```python
acquire_upload_lease(
    entry_point="receipt_exclusion_revocation",
    source_files=[],
)
```

and pass `database.DB_FILE` explicitly。

- [ ] **Step 6: Run API regression and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_receipt_exclusion_api.py \
  tests/test_upload_api.py \
  tests/test_upload_action_service.py \
  tests/test_upload_lock_service.py -q
```

Expected: all tests PASS。

Commit:

```bash
git add backend/schemas/receipt_exclusions.py backend/schemas/dashboard.py backend/services/upload_action_service.py backend/routers/upload.py tests/test_receipt_exclusion_api.py tests/test_upload_api.py tests/test_upload_action_service.py
git commit -m "feat: expose receipt exclusion governance API"
```

---

### Task 8: Streamlit Confirmation and Governance Controls

**Risk surface:** protected Streamlit governance UI; main Codex only。

**Files:**
- Create: `receipt_exclusion_rendering.py`
- Modify: `app_pages.py:703-834`
- Create: `tests/test_receipt_exclusion_rendering.py`
- Modify: `tests/test_streamlit_upload_feedback_contract.py`
- Modify: `tests/test_app_module_boundaries.py`

**Interfaces:**
- Consumes: proposal/read model/governance services from Tasks 3, 5 and 6。
- Produces:

```python
def render_receipt_exclusion_confirmation(
    proposal: dict,
    *,
    confirm_action: Callable[[dict], dict],
) -> None:
    """Render explicit candidate selection and dispatch one confirmed action."""


def render_receipt_exclusion_governance(
    snapshot: dict,
    *,
    preview_revoke: Callable[[int], dict],
    confirm_revoke: Callable[[int, str], dict],
) -> None:
    """Render the bounded registry read model and two-step revocation controls."""
```

- [ ] **Step 1: Write failing rendering contract tests**

```python
def test_confirmation_requires_checkbox_before_primary_action(fake_streamlit):
    fake_streamlit.checkbox_value = False
    render_receipt_exclusion_confirmation(
        {
            "proposalFingerprint": "proposal-1",
            "candidates": [{
                "candidateId": "candidate-1",
                "sourceOrderNo": "31NZY6629115617",
                "receiptNo": "SK2606005393",
                "exclusionKind": "payment_method:TT 退款轉團款",
                "observedAmount": 1630.0,
                "affectedRevenue": 1270.0,
            }],
        },
        confirm_action=lambda payload: payload,
    )
    assert fake_streamlit.checkbox_labels == [
        "我確認永久排除此精確收款單；日後相同 identity 將自動排除。"
    ]
    assert fake_streamlit.buttons["永久排除並重新預演"]["disabled"] is True


def test_governance_panel_never_exposes_quarantine_payload(fake_streamlit):
    render_receipt_exclusion_governance(
        {
            "active": [{"id": 7, "receiptNo": "SK2606005393"}],
            "rawPayload": {"secret": "must-not-render"},
        },
        preview_revoke=lambda rule_id: {},
        confirm_revoke=lambda rule_id, fingerprint: {},
    )
    assert "must-not-render" not in fake_streamlit.rendered_text
```

Static boundary assertion:

```python
def test_app_pages_delegates_receipt_exclusion_rendering():
    source = Path("app_pages.py").read_text(encoding="utf-8")
    assert "render_receipt_exclusion_confirmation(" in source
    assert "render_receipt_exclusion_governance(" in source
```

- [ ] **Step 2: Run rendering tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py tests/test_streamlit_upload_feedback_contract.py tests/test_app_module_boundaries.py -q
```

Expected: missing module/delegation。

- [ ] **Step 3: Implement confirmation dialog**

Use `st.dialog("永久排除收款單")` when supported by installed Streamlit。The dialog:

```python
candidate_rows = [{
    "來源單據號": row["sourceOrderNo"],
    "收款單號": row["receiptNo"],
    "排除類型": row["exclusionKind"],
    "觀察金額": row["observedAmount"],
    "正式收入影響": row["affectedRevenue"],
} for row in proposal["candidates"]]
st.dataframe(pd.DataFrame(candidate_rows), hide_index=True, width="stretch")
confirmed = st.checkbox(CONFIRMATION_COPY, value=False)
selected = st.multiselect(
    "選擇要永久排除的精確收款單",
    options=[row["candidateId"] for row in proposal["candidates"]],
    format_func=lambda value: labels[value],
)
if st.button(
    "永久排除並重新預演",
    type="primary",
    disabled=not confirmed or not selected,
):
    confirm_action({
        "proposalFingerprint": proposal["proposalFingerprint"],
        "selectedCandidateIds": selected,
        "confirmedBy": "streamlit-local",
    })
```

Do not persist file bytes, absolute paths or private evidence in session state。Confirmation
uses the still-selected uploader files; absent/changed files return a stale proposal error。

- [ ] **Step 4: Integrate upload and config tab**

In `_render_upload_area`, after file uploaders are available:

1. Read `LAST_UPLOAD_AUDIT.preflight_report.receiptExclusionProposal`。
2. Render confirmation only when `status == confirmation_required`。
3. Pass the same current uploader files to a new confirmed `execute_upload_operation` call。
4. Preserve existing audit notice and rerun behavior。

In `_render_config_tab`, call the new governance renderer after monthly baseline governance。
Do not place controls in Agent Operations。

- [ ] **Step 5: Run Streamlit regression and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_receipt_exclusion_rendering.py \
  tests/test_streamlit_upload_feedback_contract.py \
  tests/test_app_module_boundaries.py \
  tests/test_upload_orchestrator_service.py -q
```

Expected: all tests PASS。

Commit:

```bash
git add receipt_exclusion_rendering.py app_pages.py tests/test_receipt_exclusion_rendering.py tests/test_streamlit_upload_feedback_contract.py tests/test_app_module_boundaries.py
git commit -m "feat: add Streamlit receipt exclusion governance"
```

---

### Task 9: Vue Confirmation, Registry and Revocation Views

**Risk surface:** Vue display/command client; no client-side financial computation。

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

**Interfaces:**
- Consumes Task 7 APIs only。
- Vue must display `affectedRevenue` returned by API; it must not derive it from row amounts。

- [ ] **Step 1: Add failing frontend contract assertions**

Add to `frontend/scripts/verify-cockpit-contract.mjs`:

```javascript
assertContains(apiSource, "confirmReceiptExclusions", "receipt exclusion confirm API")
assertContains(apiSource, "getReceiptExclusions", "receipt exclusion list API")
assertContains(apiSource, "previewReceiptExclusionRevocation", "revocation preview API")
assertContains(apiSource, "confirmReceiptExclusionRevocation", "revocation confirm API")
assertContains(appSource, "永久排除並重新預演", "explicit receipt exclusion confirmation")
assertContains(appSource, "預演撤銷", "revocation preview command")
assertNotContains(appSource, "affectedRevenue =", "Vue must not recompute affected revenue")
```

- [ ] **Step 2: Run frontend verification and verify RED**

Run:

```bash
cd frontend && npm run verify
```

Expected: missing API/UI contract assertions fail。

- [ ] **Step 3: Add multipart confirmation and governance APIs**

```javascript
export function getReceiptExclusions() {
  return requestJson('/api/upload/receipt-exclusions')
}

export async function confirmReceiptExclusions(formData) {
  const response = await fetch('/api/upload/receipt-exclusions/confirm', {
    method: 'POST',
    body: formData
  })
  if (!response.ok) throw new Error(await readApiError(response))
  return response.json()
}

export function previewReceiptExclusionRevocation(ruleId) {
  return requestJson(`/api/upload/receipt-exclusions/${ruleId}/revocation-preview`, {
    method: 'POST',
    body: JSON.stringify({})
  })
}

export function confirmReceiptExclusionRevocation(ruleId, payload) {
  return requestJson(`/api/upload/receipt-exclusions/${ruleId}/revoke`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}
```

- [ ] **Step 4: Add explicit confirmation and revoke state**

State:

```javascript
const receiptExclusionSnapshot = ref({ active: [], revoked: [], counts: {} })
const selectedReceiptCandidateIds = ref([])
const receiptExclusionConfirmed = ref(false)
const revocationPreview = ref(null)
```

Confirmation builds a new `FormData` from the still-selected files plus:

```javascript
formData.append('proposal_fingerprint', proposal.proposalFingerprint)
formData.append('selected_candidate_ids', JSON.stringify(selectedReceiptCandidateIds.value))
```

The button remains disabled until checkbox is true and at least one candidate selected。
On stale file/fingerprint response, keep the blocked report and ask the user to rerun upload。

Registry view uses API-supplied active/revoked rows。Revocation confirm appears only when
preview status is `revocation_ready`。

- [ ] **Step 5: Run verify/build and commit**

Run:

```bash
cd frontend && npm run verify && npm run build
```

Expected: both commands exit 0。

Commit:

```bash
git add frontend/src/lib/api.js frontend/src/App.vue frontend/src/styles.css frontend/scripts/verify-cockpit-contract.mjs
git commit -m "feat: add Vue receipt exclusion governance"
```

---

### Task 10: End-to-End Isolation Acceptance and Documentation Evidence

**Risk surface:** full protected-system verification; no formal activation。

**Files:**
- Create: `tests/test_receipt_exclusion_integration.py`
- Create: `docs/agents/RECEIPT_EXCLUSION_GOVERNANCE_ACCEPTANCE.md`
- Modify only if required by observed failures:
  - `scripts/hermes_post_change_check.py`
  - `tests/test_hermes_post_change_check.py`

**Interfaces:**
- Consumes all prior Tasks。
- Produces final evidence only; does not activate `SK2606005393` in formal DB。

- [ ] **Step 1: Write real-shape disposable DB integration test**

Use temporary SQLite and synthetic frames mirroring:

```python
SOURCE_ORDER = "31NZY6629115617"
EXCLUDED_RECEIPT = "SK2606005393"
HISTORICAL_RECEIPT = "SK2606005395"
JUNE_EXPECTED = 9083241.29
MAY_EXPECTED = 12057967.92
```

The test must prove:

1. Unconfirmed upload is blocked and public proposal identifies the exact two numbers。
2. Formal temp DB hash is unchanged while blocked。
3. Confirmed overlay returns all blocking checks matched。
4. Registry/quarantine/event rows exist atomically。
5. Repeating the same full snapshot auto-applies rule without another proposal。
6. Revocation preview reintroduces prepared evidence and returns `-1270` drift。
7. Rule remains active after failed revocation preview。

- [ ] **Step 2: Run integration test and correct only evidence-backed defects**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_integration.py -q
```

Expected: PASS。Any failure must return to the owning Task; do not weaken expected baselines。

- [ ] **Step 3: Run compile and protected focused suites**

```bash
.venv/bin/python -m py_compile \
  app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py \
  receipt_exclusion_rendering.py forecasting.py pipeline.py database.py \
  business_calendar.py visuals.py \
  backend/services/receipt_exclusion_models.py \
  backend/services/receipt_exclusion_matcher.py \
  backend/services/receipt_exclusion_registry_service.py \
  backend/services/receipt_exclusion_proposal_service.py \
  backend/services/receipt_exclusion_governance_service.py \
  backend/services/receipt_exclusion_read_model_service.py \
  backend/services/upload_preflight_service.py \
  backend/services/upload_orchestrator_service.py \
  backend/services/upload_action_service.py \
  scripts/system_manager.py

.venv/bin/python -m pytest \
  tests/test_receipt_exclusion_matcher.py \
  tests/test_receipt_exclusion_registry_service.py \
  tests/test_receipt_exclusion_proposal_service.py \
  tests/test_receipt_exclusion_preflight.py \
  tests/test_receipt_exclusion_governance_service.py \
  tests/test_receipt_exclusion_read_model_service.py \
  tests/test_receipt_exclusion_api.py \
  tests/test_receipt_exclusion_rendering.py \
  tests/test_receipt_exclusion_integration.py \
  tests/test_upload_preflight_service.py \
  tests/test_upload_orchestrator_service.py \
  tests/test_upload_action_service.py \
  tests/test_upload_api.py \
  tests/test_database_rollback.py \
  tests/test_upload_rollback_service.py \
  tests/test_stability_history_service.py \
  tests/test_upload_single_writer_integration.py \
  tests/test_phase2_precheck_acceptance.py \
  tests/test_dashboard_service.py \
  tests/test_dashboard_api.py \
  tests/test_monthly_baseline_service.py -q
```

Expected: all commands exit 0。

- [ ] **Step 4: Run frontend, full pytest and system acceptance**

```bash
cd frontend && npm run verify && npm run build
cd ..
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

Expected:

- Vue verify/build exit 0。
- Full pytest has no new failures; any pre-existing failures must be reproduced on unchanged `main`
  and recorded separately。
- System acceptance status `passed`。
- Hermes `overallStatus` is `pass`。
- Monthly governance reports all January-June blocking checks matched。

- [ ] **Step 5: Run true 0722 files against an isolated DB copy**

Create a disposable snapshot of the formal DB; never point activation at the formal path。
Run the real 0722 workbook batch through:

1. normal blocked preflight；
2. confirmation overlay；
3. activation in disposable DB；
4. repeated full snapshot；
5. revocation preview。

Required evidence:

```text
unconfirmed driver = 31NZY6629115617 / SK2606005393
unconfirmed formal DB unchanged = true
confirmed 2026-06 = HKD 9,083,241
confirmed 2026-05 = HKD 12,057,968
repeat upload auto match count >= 1
revocation preview delta = -HKD 1,270
formal production DB SHA-256 before == after
```

- [ ] **Step 6: Write acceptance evidence**

`docs/agents/RECEIPT_EXCLUSION_GOVERNANCE_ACCEPTANCE.md` must record:

- branch and commit IDs；
- focused/full test counts；
- Review Agent verdict；
- system acceptance and Hermes result；
- disposable DB path identity only, not raw absolute source workbook paths；
- formal DB SHA-256 before/after；
- January-June monthly baseline results；
- real-shape `SK2606005393` lifecycle evidence；
- confirmation that formal Registry still has no automatically activated rule；
- residual risks and the separate production activation gate。

- [ ] **Step 7: Final Review Agent, Documentation classification and commit**

Run Review Agent against the full implementation diff and verification evidence。Only after
Review PASS, full verification PASS and Hermes PASS may Documentation Agent produce a proposal。
Documentation Agent must not auto-apply System Map, ADR or Obsidian changes。

Commit:

```bash
git add tests/test_receipt_exclusion_integration.py docs/agents/RECEIPT_EXCLUSION_GOVERNANCE_ACCEPTANCE.md
git commit -m "test: verify receipt exclusion governance lifecycle"
```

---

## Production Activation Gate

This gate is deliberately outside Tasks 1-10。

After implementation is merged and the user explicitly authorizes production activation:

1. Capture formal DB SHA-256 and current monthly baseline report。
2. Upload the unchanged 0722 source batch through Streamlit。
3. Confirm only candidate `31NZY6629115617 / SK2606005393`。
4. Require overlay preflight all matched。
5. Activate the exact rule and complete upload under one lease。
6. Run post-write gate, rollback guard, history, cache generation and Hermes。
7. Confirm `2026-05 = HKD 12,057,968` and `2026-06 = HKD 9,083,241`。
8. Record the registry rule ID and event IDs in the authorized incident/ADR backfill。

No worker, Agent, test fixture or plan execution may perform this production activation without
that separate user authorization。
