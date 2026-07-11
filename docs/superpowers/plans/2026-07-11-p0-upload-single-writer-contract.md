# P0 Upload Single-Writer Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Streamlit 與 FastAPI / Vue 兩個 upload 入口共用同一正式寫入流程，使用跨 process lease、明確 DB target、governed monthly gate、統一 history 與 cache generation contract。

**Architecture:** 新增獨立 SQLite coordination lock 與純 Python `UploadOrchestrator`。兩個 UI adapter 先取得 lease，再讀檔並呼叫同一 orchestrator；preflight、post-write gate、rollback、history 全部以明確 `db_path` 執行，不再改寫 module-global `database.DB_FILE`。Streamlit 可在 lease 內快速重建 session cache，FastAPI 則回報 cache invalidation generation，不再聲稱已重建 Streamlit cache。

**Tech Stack:** Python 3.10、SQLite、pandas、Streamlit、FastAPI、Pydantic、pytest、multiprocessing、既有 Hermes / system manager。

## Global Constraints

- 正式口徑固定為：`不含掛賬核銷與TT退款轉團款`。
- `2026-05` frozen baseline 必須維持 `HKD 12,057,968`。
- `2026-01` 至 `2026-06` baseline 精確值與 monitoring / blocking mode 不變。
- 不修改 `pipeline.py` 的正式計算、Forecast、GMV、ranking、report sheets 或 UI filter 語義。
- 不重寫 historical validated rows 或既有 acceptance history。
- Preflight blocked 與 lease busy 不寫正式 stability history。
- 自動化測試只使用 `tmp_path` DB，不得寫正式 `nbs_marketing_data.db`。
- Streamlit 既有日期來源診斷維持 adapter-level 行為；本計劃的 parity boundary 從 shared preflight 開始。
- 第二份 Streamlit rerun Brief 不得在本計劃中實作。

## File Structure

新增：

- `backend/services/upload_lock_service.py`：SQLite coordination lease、busy error、owner sidecar、read-only probe。
- `backend/services/cache_generation_service.py`：accepted DB generation 與 SHA-256 signature 的原子 JSON contract。
- `backend/services/upload_orchestrator_service.py`：唯一 preflight / upsert / gate / rollback / history application service。
- `tests/test_database_explicit_path.py`
- `tests/test_upload_lock_service.py`
- `tests/test_cache_generation_service.py`
- `tests/test_upload_orchestrator_service.py`
- `tests/test_upload_action_service.py`
- `tests/test_upload_single_writer_integration.py`

修改：

- `database.py`：為 connection、snapshot、backup、upsert、load、restore 加入 optional `db_path`。
- `backend/services/dashboard_service.py`、`dashboard_analytics_service.py`：讓正式 facts builder 可綁定 explicit DB。
- `backend/services/stability_service.py`、`monthly_baseline_service.py`：讓 legacy + monthly governed gate 使用同一 DB target。
- `backend/services/upload_preflight_service.py`：移除 `_temporary_database_path()` 與全域 mutation。
- `backend/services/stability_history_service.py`：保存 operation / entry point / timings / cache generation。
- `backend/services/upload_action_service.py`、`backend/routers/upload.py`、`backend/schemas/dashboard.py`：FastAPI adapter 與 response contract。
- `app_pages.py`、`app_workflows.py`、`app.py`：Streamlit adapter、generation invalidation、移除 process-local upload lock。
- `backend/services/system_health_service.py`、`operational_monitor_service.py`、`scripts/hermes_post_change_check.py`：監測 single-writer 狀態。
- 對應現有 tests、Obsidian Brief、handoff 與 ADR。

---

### Task 1: Make SQLite Targets Explicit

**Files:**
- Create: `tests/test_database_explicit_path.py`
- Modify: `database.py:30-104`
- Modify: `database.py:248-300`
- Test: `tests/test_database_rollback.py`

**Interfaces:**
- Produces: `resolve_db_path(db_path: str | Path | None = None) -> Path`
- Produces: `get_db_connection(db_path: str | Path | None = None) -> sqlite3.Connection`
- Produces: `snapshot_sqlite_database(source_path: str | Path, destination_path: str | Path) -> None`
- Produces: optional `db_path` on `hot_backup_database`, `upsert_to_db`, `load_all_data_from_db`, `restore_database_from_backup`
- Preserves: all existing call sites that omit `db_path`

- [ ] **Step 1: Write failing explicit-target tests**

```python
# tests/test_database_explicit_path.py
import sqlite3

import pandas as pd

import database


def _seed(path, order_id):
    conn = sqlite3.connect(path)
    try:
        pd.DataFrame(
            [{
                "來源單據號": order_id,
                "收款單號": order_id,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "收款原幣金額": 100.0,
                "統一日期": "2026-07-01",
            }]
        ).to_sql("tour_data", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()


def test_explicit_database_load_does_not_change_default_target(tmp_path, monkeypatch):
    default_path = tmp_path / "default.db"
    alternate_path = tmp_path / "alternate.db"
    _seed(default_path, "DEFAULT001")
    _seed(alternate_path, "ALT001")
    monkeypatch.setattr(database, "DB_FILE", str(default_path))

    alternate_tour, _ = database.load_all_data_from_db(db_path=alternate_path)
    default_tour, _ = database.load_all_data_from_db()

    assert alternate_tour["來源單據號"].tolist() == ["ALT001"]
    assert default_tour["來源單據號"].tolist() == ["DEFAULT001"]
    assert database.DB_FILE == str(default_path)


def test_snapshot_sqlite_database_is_integrity_checked(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    _seed(source, "SNAP001")

    database.snapshot_sqlite_database(source, destination)

    assert database.validate_sqlite_database(destination)["ok"] is True
    snapshot_tour, _ = database.load_all_data_from_db(db_path=destination)
    assert snapshot_tour["來源單據號"].tolist() == ["SNAP001"]
```

- [ ] **Step 2: Run tests and confirm the new keyword/API fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_database_explicit_path.py -q
```

Expected: FAIL because `load_all_data_from_db()` has no `db_path` parameter and `snapshot_sqlite_database` does not exist.

- [ ] **Step 3: Add the explicit path primitives**

```python
# database.py
def resolve_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path if db_path is not None else DB_FILE)


def get_db_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    return sqlite3.connect(resolve_db_path(db_path))


def snapshot_sqlite_database(
    source_path: str | Path,
    destination_path: str | Path,
) -> None:
    source = resolve_db_path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _sqlite_backup_copy(source, destination)
    check = validate_sqlite_database(destination)
    if not check["ok"]:
        raise RuntimeError(f"snapshot integrity check failed: {check['integrity']}")
```

Change the affected public signatures and every internal connection in those functions:

```python
def hot_backup_database(db_path: str | Path | None = None) -> str | None:
    source = resolve_db_path(db_path)
    if not source.exists() or source.stat().st_size == 0:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = source.with_name(f"{source.name}.backup_{stamp}")
    snapshot_sqlite_database(source, backup_path)
    return str(backup_path)


def restore_database_from_backup(
    backup_path: str | Path,
    *,
    live_db_path: str | Path | None = None,
) -> dict:
    live_path = resolve_db_path(live_db_path)
    source_path = Path(backup_path)


def upsert_to_db(
    df_tour: pd.DataFrame,
    df_others: pd.DataFrame,
    *,
    db_path: str | Path | None = None,
) -> dict:
    target = resolve_db_path(db_path)
    backup_path = hot_backup_database(target)
    conn = get_db_connection(target)


def load_all_data_from_db(
    *,
    db_path: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = get_db_connection(db_path)
    try:
        df_tour, df_others = pd.DataFrame(), pd.DataFrame()
        if _table_exists(conn, "tour_data"):
            df_tour = pd.read_sql("SELECT * FROM tour_data", conn)
        if _table_exists(conn, "others_data"):
            df_others = pd.read_sql("SELECT * FROM others_data", conn)
        return df_tour, df_others
    finally:
        conn.close()
```

For `restore_database_from_backup`, replace every path currently derived from `DB_FILE` with `live_path`; retain the current ordered operations `validate backup -> quarantine live DB -> os.replace -> validate restored DB`. For `upsert_to_db`, change only backup/connection target selection to `target`; retain the current transaction boundaries and returned audit keys. These are signature and target-binding edits, not algorithm rewrites.

- [ ] **Step 4: Run explicit-path and rollback tests**

```bash
.venv/bin/python -m pytest tests/test_database_explicit_path.py tests/test_database_rollback.py -q
```

Expected: all tests PASS; existing default-path tests remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_explicit_path.py tests/test_database_rollback.py
git commit -m "refactor: make sqlite upload targets explicit"
```

---

### Task 2: Add the Cross-Process Upload Lease

**Files:**
- Create: `backend/services/upload_lock_service.py`
- Create: `tests/test_upload_lock_service.py`

**Interfaces:**
- Produces: `UploadBusyError(owner: dict | None)`
- Produces: `UploadOperation`
- Produces: `UploadLease`, a context manager with `.operation`
- Produces: `acquire_upload_lease(...) -> UploadLease`
- Produces: `probe_upload_lease(...) -> dict`
- Produces: sanitized owner evidence with no source file names or contents

- [ ] **Step 1: Write failing process-contention and crash-release tests**

```python
# tests/test_upload_lock_service.py
import multiprocessing as mp
import os


def _try_lease(lock_path, queue):
    from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease
    try:
        with acquire_upload_lease(
            coordination_db_path=lock_path,
            entry_point="worker",
            source_files=["worker.xlsx"],
            timeout_seconds=0.05,
        ):
            queue.put("acquired")
    except UploadBusyError as exc:
        queue.put(("busy", exc.owner))


def _acquire_then_crash(lock_path, queue):
    from backend.services.upload_lock_service import acquire_upload_lease
    lease = acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="crash-worker",
        source_files=["crash.xlsx"],
        timeout_seconds=0.05,
    )
    queue.put("acquired")
    queue.close()
    queue.join_thread()
    assert lease.active is True
    os._exit(0)


def test_only_one_process_holds_upload_lease(tmp_path):
    from backend.services.upload_lock_service import acquire_upload_lease
    lock_path = tmp_path / "upload_coordination.db"
    queue = mp.Queue()
    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="parent",
        source_files=["parent.xlsx"],
        timeout_seconds=0.05,
    ):
        child = mp.Process(target=_try_lease, args=(str(lock_path), queue))
        child.start()
        child.join(5)
        outcome, owner = queue.get(timeout=2)
        assert outcome == "busy"
        assert owner["entry_point"] == "parent"
        assert owner["source_count"] == 1
        assert "source_files" not in owner


def test_process_crash_releases_sqlite_lease(tmp_path):
    from backend.services.upload_lock_service import acquire_upload_lease
    lock_path = tmp_path / "upload_coordination.db"
    queue = mp.Queue()
    child = mp.Process(target=_acquire_then_crash, args=(str(lock_path), queue))
    child.start()
    assert queue.get(timeout=2) == "acquired"
    child.join(5)

    with acquire_upload_lease(
        coordination_db_path=lock_path,
        entry_point="recovery",
        source_files=["recovery.xlsx"],
        timeout_seconds=0.2,
    ) as lease:
        assert lease.operation.entry_point == "recovery"
```

- [ ] **Step 2: Run the tests and verify the module is missing**

```bash
.venv/bin/python -m pytest tests/test_upload_lock_service.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the SQLite lease and owner sidecar**

```python
# backend/services/upload_lock_service.py
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COORDINATION_DB = PROJECT_ROOT / ".nbs_runtime" / "upload_coordination.db"


@dataclass(frozen=True)
class UploadOperation:
    operation_id: str
    entry_point: str
    pid: int
    started_at: str
    source_files: tuple[str, ...]


class UploadBusyError(RuntimeError):
    def __init__(self, owner: dict | None = None):
        super().__init__("another upload operation is already running")
        self.owner = owner or {}


def _owner_path(coordination_db_path: Path) -> Path:
    return coordination_db_path.with_name(f"{coordination_db_path.stem}_owner.json")


def _read_owner(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _public_owner(value: dict) -> dict:
    return {
        key: value.get(key)
        for key in ("operation_id", "entry_point", "pid", "started_at")
        if value.get(key) is not None
    } | {"source_count": len(value.get("source_files") or [])}


def _write_owner(path: Path, operation: UploadOperation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(operation), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


class UploadLease:
    def __init__(self, connection: sqlite3.Connection, operation: UploadOperation, owner_path: Path):
        self._connection = connection
        self.operation = operation
        self._owner_path = owner_path
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def release(self) -> None:
        if not self._active:
            return
        try:
            self._connection.rollback()
        finally:
            self._connection.close()
            owner = _read_owner(self._owner_path)
            if owner.get("operation_id") == self.operation.operation_id:
                self._owner_path.unlink(missing_ok=True)
            self._active = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def acquire_upload_lease(
    *,
    entry_point: str,
    source_files: list[str],
    coordination_db_path: str | Path = DEFAULT_COORDINATION_DB,
    timeout_seconds: float = 0.1,
    operation_id: str | None = None,
) -> UploadLease:
    path = Path(coordination_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    owner_path = _owner_path(path)
    connection = sqlite3.connect(path, timeout=max(0.0, timeout_seconds), isolation_level=None)
    try:
        connection.execute(f"PRAGMA busy_timeout = {max(0, int(timeout_seconds * 1000))}")
        connection.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError as exc:
        connection.close()
        if "locked" in str(exc).lower():
            raise UploadBusyError(_public_owner(_read_owner(owner_path))) from exc
        raise
    operation = UploadOperation(
        operation_id=operation_id or uuid.uuid4().hex,
        entry_point=str(entry_point),
        pid=os.getpid(),
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        source_files=tuple(str(item) for item in source_files),
    )
    try:
        _write_owner(owner_path, operation)
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return UploadLease(connection, operation, owner_path)


def probe_upload_lease(
    coordination_db_path: str | Path = DEFAULT_COORDINATION_DB,
) -> dict:
    path = Path(coordination_db_path)
    owner_path = _owner_path(path)
    try:
        with acquire_upload_lease(
            entry_point="health_probe",
            source_files=[],
            coordination_db_path=path,
            timeout_seconds=0.0,
        ):
            return {"locked": False, "owner": {}}
    except UploadBusyError as exc:
        return {"locked": True, "owner": exc.owner or _public_owner(_read_owner(owner_path))}
```

- [ ] **Step 4: Run lock tests repeatedly**

```bash
.venv/bin/python -m pytest tests/test_upload_lock_service.py -q
```

Expected: PASS, no owner sidecar remains after normal release. Re-run this same command twice more before committing to expose intermittent process-lock failures without adding a `pytest-repeat` dependency.

- [ ] **Step 5: Commit**

```bash
git add backend/services/upload_lock_service.py tests/test_upload_lock_service.py
git commit -m "feat: add cross-process upload lease"
```

---

### Task 3: Bind Dashboard and Governed Gates to an Explicit DB

**Files:**
- Modify: `backend/services/dashboard_service.py:79-120`
- Modify: `backend/services/dashboard_service.py:279-327`
- Modify: `backend/services/dashboard_analytics_service.py:181-199`
- Modify: `backend/services/stability_service.py:139-210`
- Modify: `backend/services/monthly_baseline_service.py:37-166`
- Modify: `tests/test_dashboard_service.py`
- Modify: `tests/test_monthly_baseline_service.py`

**Interfaces:**
- Produces: `build_dashboard_summary(filters: dict, *, db_path=None) -> dict`
- Produces: `build_dashboard_analytics(filters: dict, *, db_path=None) -> dict`
- Produces: `build_phase2c_stability_gate(..., db_path=None) -> dict`
- Produces: `build_governed_stability_gate(..., db_path=None) -> dict`

- [ ] **Step 1: Write failing propagation tests**

```python
def test_phase2c_gate_binds_summary_to_explicit_database(monkeypatch, tmp_path):
    from backend.services import dashboard_service, stability_service
    seen = []

    def fake_summary(filters, *, db_path=None):
        seen.append(db_path)
        return {
            "stabilityBaseline": {
                "status": "matched",
                "baselineMonth": "2026-05",
                "formattedExpectedTotal": "HKD 12,057,968",
                "formattedActualTotal": "HKD 12,057,968",
                "deltaAmount": 0.0,
                "deltaPct": 0.0,
                "coreValidation": {"status": "matched", "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0}, "checks": []},
                "freshnessUpdate": {"status": "stable", "summary": {"totalChecks": 0, "stableChecks": 0, "updatedChecks": 0}, "checks": []},
            }
        }

    monkeypatch.setattr(dashboard_service, "build_dashboard_summary", fake_summary)
    target = tmp_path / "target.db"
    gate = stability_service.build_phase2c_stability_gate(db_path=target)

    assert gate["status"] == "matched"
    assert seen == [target]


def test_governed_gate_binds_legacy_and_monthly_checks_to_same_db(monkeypatch, tmp_path):
    from backend.services import monthly_baseline_service
    seen = {"gate": [], "analytics": []}
    target = tmp_path / "target.db"

    monkeypatch.setattr(
        "backend.services.stability_service.build_phase2c_stability_gate",
        lambda *, db_path=None: seen["gate"].append(db_path) or {"status": "matched"},
    )
    monkeypatch.setattr(
        "backend.services.dashboard_analytics_service.build_dashboard_analytics",
        lambda filters, *, db_path=None: seen["analytics"].append(db_path) or {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "monthlyTrend": [],
        },
    )

    monthly_baseline_service.build_governed_stability_gate(db_path=target)

    assert seen == {"gate": [target], "analytics": [target]}
```

- [ ] **Step 2: Run the new tests and verify keyword failures**

```bash
.venv/bin/python -m pytest tests/test_dashboard_service.py tests/test_monthly_baseline_service.py -q
```

Expected: the new tests FAIL because `db_path` is not accepted.

- [ ] **Step 3: Add keyword-only path propagation**

```python
# backend/services/dashboard_service.py
def build_dashboard_summary(filters: dict, *, db_path=None) -> dict:
    db_tour, db_others = load_all_data_from_db(db_path=db_path)


# backend/services/dashboard_analytics_service.py
def build_dashboard_analytics(filters: dict, *, db_path=None) -> dict:
    db_tour, db_others = load_all_data_from_db(db_path=db_path)
```

```python
# backend/services/stability_service.py
def build_phase2c_stability_gate(
    summary_builder: Callable[[dict], dict] | None = None,
    *,
    db_path=None,
) -> dict:
    if summary_builder is None:
        from backend.services.dashboard_service import build_dashboard_summary
        summary_builder = lambda filters: build_dashboard_summary(filters, db_path=db_path)
    summary = summary_builder(dict(PHASE2B_BASELINE_FILTERS))
```

```python
# backend/services/monthly_baseline_service.py
def evaluate_monthly_baselines(
    registry: dict | None = None,
    analytics_builder: Callable[[dict], dict] | None = None,
    *,
    db_path=None,
) -> dict:
    registry = deepcopy(registry or load_monthly_baseline_registry())
    if analytics_builder is None:
        from backend.services.dashboard_analytics_service import build_dashboard_analytics
        analytics_builder = lambda filters: build_dashboard_analytics(filters, db_path=db_path)


def build_governed_stability_gate(
    *,
    gate_builder: Callable[[], dict] | None = None,
    analytics_builder: Callable[[dict], dict] | None = None,
    db_path=None,
) -> dict:
    if gate_builder is None:
        from backend.services.stability_service import build_phase2c_stability_gate
        gate_builder = lambda: build_phase2c_stability_gate(db_path=db_path)
    gate = gate_builder()
    evaluation = evaluate_monthly_baselines(
        analytics_builder=analytics_builder,
        db_path=db_path,
    )
    return apply_monthly_blocking_checks(gate, evaluation)
```

Apply these as narrow substitutions in the existing functions: add the keyword-only parameter, replace only the no-argument `load_all_data_from_db()` calls, and install the default lambdas shown above. Leave all subsequent dataframe transformations, reconciliation checks, registry comparisons, and response construction byte-for-byte unchanged.

- [ ] **Step 4: Run dashboard, API and monthly tests**

```bash
.venv/bin/python -m pytest tests/test_dashboard_service.py tests/test_dashboard_api.py tests/test_monthly_baseline_service.py tests/test_phase2_precheck_acceptance.py -q
```

Expected: PASS; May baseline and all current monthly checks unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/services/dashboard_service.py backend/services/dashboard_analytics_service.py backend/services/stability_service.py backend/services/monthly_baseline_service.py tests/test_dashboard_service.py tests/test_monthly_baseline_service.py
git commit -m "refactor: bind governed gates to explicit databases"
```

---

### Task 4: Remove Preflight Global DB Mutation

**Files:**
- Modify: `backend/services/upload_preflight_service.py:1-226`
- Modify: `tests/test_upload_preflight_service.py`
- Modify: `backend/services/upload_profiling_service.py`
- Test: `tests/test_upload_profiling_service.py`

**Interfaces:**
- Changes: `run_upload_preflight(..., *, source_files=None, live_db_path=None) -> dict`
- Removes: `_temporary_database_path`
- Consumes: explicit DB functions from Tasks 1 and 3

- [ ] **Step 1: Replace the source-string gate test with behavioral path assertions**

```python
def test_upload_preflight_never_changes_module_global_db_target(tmp_path, monkeypatch):
    from backend.services import upload_preflight_service
    live_path = tmp_path / "live.db"
    default_path = tmp_path / "default.db"
    monkeypatch.setattr(database, "DB_FILE", str(default_path))
    observed = {}

    def fake_process_raw_files(*args, **kwargs):
        observed["db_file_during_processing"] = database.DB_FILE
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"summary": pd.DataFrame()}

    def fake_gate(*, db_path=None):
        observed["gate_db_path"] = Path(db_path)
        return {
            "status": "matched",
            "formattedActualTotal": "HKD 12,057,968",
            "deltaAmount": 0.0,
            "driftChecks": [],
            "monthlyBaseline": {"allMatched": True},
        }

    monkeypatch.setattr(upload_preflight_service, "process_raw_files", fake_process_raw_files)
    monkeypatch.setattr(upload_preflight_service, "build_phase2c_stability_gate", fake_gate)
    monkeypatch.setattr(upload_preflight_service, "build_upload_drift_diagnosis", lambda *args, **kwargs: {"status": "no_drift"})
    monkeypatch.setattr(
        database,
        "snapshot_sqlite_database",
        lambda source, destination: observed.update(snapshot_source=Path(source), snapshot_target=Path(destination)),
    )
    monkeypatch.setattr(
        database,
        "load_all_data_from_db",
        lambda *, db_path=None: observed.setdefault("load_paths", []).append(Path(db_path)) or (pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr(
        database,
        "upsert_to_db",
        lambda *args, db_path=None, **kwargs: observed.update(upsert_path=Path(db_path)) or {
            "tour_data": {"filtered_excluded_rows": 0, "write_rows": 0},
            "others_data": {"filtered_excluded_rows": 0, "write_rows": 0},
        },
    )
    monkeypatch.setattr(upload_preflight_service, "_table_row_count", lambda *args, **kwargs: 0)

    upload_preflight_service.run_upload_preflight(
        BytesIO(b"dummy"), None, [], {}, [], [],
        source_files=["main.xlsx"],
        live_db_path=live_path,
    )

    assert observed["db_file_during_processing"] == str(default_path)
    assert observed["snapshot_source"] == live_path
    assert observed["upsert_path"] == observed["gate_db_path"]
    assert observed["gate_db_path"] != live_path
    assert observed["load_paths"] == [live_path, observed["gate_db_path"]]
    assert database.DB_FILE == str(default_path)
```

- [ ] **Step 2: Run preflight tests and confirm failure**

```bash
.venv/bin/python -m pytest tests/test_upload_preflight_service.py -q
```

Expected: FAIL because the function does not accept `live_db_path` and still mutates `database.DB_FILE`.

- [ ] **Step 3: Replace global mutation with explicit targets**

Use this structure inside `run_upload_preflight`:

```python
live_path = database.resolve_db_path(live_db_path)
with tempfile.TemporaryDirectory() as tmpdir:
    temp_db_path = Path(tmpdir) / "preflight.db"
    stage_started = time.perf_counter()
    database.snapshot_sqlite_database(live_path, temp_db_path)
    _record_stage(stage_timings, "建立 Preflight 臨時 DB", stage_started)

    stage_started = time.perf_counter()
    live_tour_before, live_others_before = database.load_all_data_from_db(db_path=live_path)
    _record_stage(stage_timings, "讀取正式 SQLite 快照", stage_started)

    live_before = {
        "tour_rows": _table_row_count(live_path, "tour_data"),
        "others_rows": _table_row_count(live_path, "others_data"),
    }
    new_t_df, new_o_df, anm_df, entity_audit = process_raw_files(
        main_file,
        tour_file,
        other_files or [],
        branch_mapping,
        exclude_prefixes,
        sales_reps,
        return_entity_audit=True,
    )
    upsert_summary = database.upsert_to_db(new_t_df, new_o_df, db_path=temp_db_path)
    stability_gate = build_phase2c_stability_gate(db_path=temp_db_path)
    temp_tour_after, temp_others_after = database.load_all_data_from_db(db_path=temp_db_path)
    latest_data_date = _combined_max_date(temp_tour_after, temp_others_after)

    live_after = {
        "tour_rows": _table_row_count(live_path, "tour_data"),
        "others_rows": _table_row_count(live_path, "others_data"),
    }
```

Delete `_temporary_database_path()` and the unused `contextmanager` / `shutil` imports. Update profiling calls to pass their disposable DB path explicitly rather than monkeypatching `database.DB_FILE`.

- [ ] **Step 4: Run preflight and profiling tests**

```bash
.venv/bin/python -m pytest tests/test_upload_preflight_service.py tests/test_upload_profiling_service.py tests/test_database_rollback.py -q
```

Expected: PASS and `liveDbUnchanged: true` remains part of the contract.

- [ ] **Step 5: Commit**

```bash
git add backend/services/upload_preflight_service.py backend/services/upload_profiling_service.py tests/test_upload_preflight_service.py tests/test_upload_profiling_service.py
git commit -m "refactor: remove preflight database global mutation"
```

---

### Task 5: Persist Cache Generation and Upload Operation Evidence

**Files:**
- Create: `backend/services/cache_generation_service.py`
- Create: `tests/test_cache_generation_service.py`
- Modify: `backend/services/stability_history_service.py:26-168`
- Modify: `backend/schemas/dashboard.py:183-230`
- Modify: `tests/test_stability_history_service.py`

**Interfaces:**
- Produces: `load_cache_generation(path=None, db_path=None) -> dict`
- Produces: `advance_cache_generation(db_path, operation_id, status, path=None) -> dict`
- Produces: `record_stability_history(..., db_path=None)` and `list_stability_history(..., db_path=None)`
- Adds history fields: `operationId`, `entryPoint`, `stageTimings`, `cacheState`, `cacheError`, `dataGeneration`

- [ ] **Step 1: Write failing generation and history round-trip tests**

```python
# tests/test_cache_generation_service.py
def test_generation_advances_atomically_with_database_signature(tmp_path):
    import sqlite3
    from backend.services.cache_generation_service import advance_cache_generation, load_cache_generation
    db_path = tmp_path / "live.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sample (value TEXT)")
    conn.execute("INSERT INTO sample VALUES ('accepted')")
    conn.commit()
    conn.close()
    generation_path = tmp_path / "data_generation.json"

    first = advance_cache_generation(
        db_path=db_path,
        operation_id="op-1",
        status="accepted",
        path=generation_path,
    )
    second = advance_cache_generation(
        db_path=db_path,
        operation_id="op-2",
        status="rejected_rolled_back",
        path=generation_path,
    )

    assert first["generation"] == 1
    assert second["generation"] == 2
    assert len(second["dbSignature"]["sha256"]) == 64
    loaded = load_cache_generation(generation_path, db_path=db_path)
    assert loaded["signatureMatched"] is True
    assert loaded["cacheToken"] == f"2:{second['dbSignature']['sha256']}"

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO sample VALUES ('newer than generation file')")
    conn.commit()
    conn.close()
    stale = load_cache_generation(generation_path, db_path=db_path)
    assert stale["signatureMatched"] is False
    assert stale["cacheToken"] != loaded["cacheToken"]
```

Add to `tests/test_stability_history_service.py`:

```python
context.update({
    "operation_id": "op-1",
    "entry_point": "streamlit",
    "stage_timings": [{"階段": "正式 SQLite upsert", "秒數": 0.2}],
    "cache_state": "streamlit_rebuilt",
    "cache_error": None,
    "data_generation": {"generation": 4, "operationId": "op-1"},
})
assert rows[0]["operationId"] == "op-1"
assert rows[0]["entryPoint"] == "streamlit"
assert rows[0]["stageTimings"] == [{"階段": "正式 SQLite upsert", "秒數": 0.2}]
assert rows[0]["cacheState"] == "streamlit_rebuilt"
assert rows[0]["cacheError"] is None
assert rows[0]["dataGeneration"]["generation"] == 4
```

Add a second history test that writes to `tmp_path / "history.db"` with `db_path=...`, reads it back with the same explicit target, and asserts the monkeypatched default DB has no new history table.

- [ ] **Step 2: Run tests and verify missing APIs/columns**

```bash
.venv/bin/python -m pytest tests/test_cache_generation_service.py tests/test_stability_history_service.py -q
```

Expected: FAIL with missing module and missing history fields.

- [ ] **Step 3: Implement atomic generation JSON**

```python
# backend/services/cache_generation_service.py
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GENERATION_PATH = PROJECT_ROOT / ".nbs_runtime" / "data_generation.json"


def _db_signature(db_path: str | Path | None) -> dict:
    if db_path is None:
        return {}
    db = Path(db_path)
    if not db.exists() or not db.is_file():
        return {}
    return {
        "sizeBytes": db.stat().st_size,
        "modifiedNs": db.stat().st_mtime_ns,
        "sha256": _sha256(db),
    }


def load_cache_generation(
    path: str | Path | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        value = {"generation": 0, "operationId": None, "status": "uninitialized", "dbSignature": {}}
    if not isinstance(value, dict):
        value = {"generation": 0, "operationId": None, "status": "uninitialized", "dbSignature": {}}
    current_signature = _db_signature(db_path)
    stored_signature = value.get("dbSignature") if isinstance(value.get("dbSignature"), dict) else {}
    signature_matched = bool(current_signature) and stored_signature.get("sha256") == current_signature.get("sha256")
    return {
        **value,
        "currentDbSignature": current_signature,
        "signatureMatched": signature_matched,
        "cacheToken": f"{int(value.get('generation', 0))}:{current_signature.get('sha256', 'missing')}",
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def advance_cache_generation(
    *,
    db_path: str | Path,
    operation_id: str,
    status: str,
    path: str | Path | None = None,
) -> dict:
    target = Path(path or DEFAULT_GENERATION_PATH)
    db = Path(db_path)
    previous = load_cache_generation(target, db_path=db)
    value = {
        "generation": int(previous.get("generation", 0)) + 1,
        "operationId": str(operation_id),
        "status": str(status),
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dbSignature": _db_signature(db),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    return load_cache_generation(target, db_path=db)
```

- [ ] **Step 4: Migrate and expose the history fields**

Add these nullable columns to `_ensure_table()` and its migration map:

```python
operation_id TEXT,
entry_point TEXT,
stage_timings_json TEXT,
cache_state TEXT,
cache_error TEXT,
data_generation_json TEXT
```

Add a partial unique index:

```python
conn.execute(
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE_NAME}_operation_id "
    f"ON {TABLE_NAME}(operation_id) WHERE operation_id IS NOT NULL"
)
```

Change `_connection()`, `_ensure_table()`, `record_stability_history()` and `list_stability_history()` to accept and propagate `db_path`; omitted values continue to resolve through `database.DB_FILE`. Extend the INSERT tuple and list response with the exact context keys used by the test. Add these matching optional fields to `StabilityHistoryItem`:

```python
operationId: str | None = None
entryPoint: str | None = None
stageTimings: list[dict] = Field(default_factory=list)
cacheState: str | None = None
cacheError: str | None = None
dataGeneration: dict = Field(default_factory=dict)
monthlyBaseline: dict = Field(default_factory=dict)
```

- [ ] **Step 5: Run generation, history and API schema tests**

```bash
.venv/bin/python -m pytest tests/test_cache_generation_service.py tests/test_stability_history_service.py tests/test_dashboard_api.py -q
```

Expected: PASS; old history rows still deserialize with empty/default values.

- [ ] **Step 6: Commit**

```bash
git add backend/services/cache_generation_service.py backend/services/stability_history_service.py backend/schemas/dashboard.py tests/test_cache_generation_service.py tests/test_stability_history_service.py
git commit -m "feat: persist upload operation and cache generation evidence"
```

---

### Task 6: Centralize the Formal Upload Orchestration

**Files:**
- Create: `backend/services/upload_orchestrator_service.py`
- Create: `tests/test_upload_orchestrator_service.py`
- Modify: `backend/services/upload_rollback_service.py` only if type hints need explicit `live_db_path` lambdas

**Interfaces:**
- Consumes: `UploadOperation`, explicit DB functions, governed gate, history and generation services
- Produces: `UploadExecution(response, anomaly_frame, entity_audit)`
- Produces: `execute_upload_operation(...) -> UploadExecution`

- [ ] **Step 1: Write failing orchestrator contract tests**

Use injected callables so tests never touch the production DB:

```python
# tests/test_upload_orchestrator_service.py
import pandas as pd

from backend.services.upload_lock_service import UploadOperation


def _operation():
    return UploadOperation(
        operation_id="op-1",
        entry_point="test",
        pid=123,
        started_at="2026-07-11T12:00:00+08:00",
        source_files=("main.xlsx",),
    )


def _execute_accepted_fixture(tmp_path, **overrides):
    from backend.services.upload_orchestrator_service import execute_upload_operation
    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
    }
    kwargs = {
        "main_file": object(),
        "live_db_path": tmp_path / "live.db",
        "preflight_runner": lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
        "upsert_runner": lambda *args, **kwargs: {"backup_path": "backup.db"},
        "load_runner": lambda **kwargs: (prepared["tour"], prepared["others"]),
        "gate_builder": lambda **kwargs: {"status": "matched", "monthlyBaseline": {"allMatched": True}},
        "rollback_handler": lambda *args, **kwargs: {"status": "accepted", "rollbackStatus": "not_required", "postRollbackGate": None},
        "generation_advancer": lambda **kwargs: {"generation": 1, "operationId": "op-1"},
        "history_writer": lambda *args, **kwargs: 1,
        "rules_loader": lambda: {"BRANCH_MAPPING": {}, "EXCLUDE_PREFIXES": [], "SALES_REP_LIST": []},
    }
    kwargs.update(overrides)
    return execute_upload_operation(_operation(), **kwargs)


def test_blocked_preflight_does_not_write_history_or_database(tmp_path):
    from backend.services.upload_orchestrator_service import execute_upload_operation
    calls = []
    execution = execute_upload_operation(
        _operation(),
        main_file=object(),
        tour_file=None,
        other_files=[],
        live_db_path=tmp_path / "live.db",
        preflight_runner=lambda *args, **kwargs: {"status": "drift", "message": "blocked", "prepared": {}},
        upsert_runner=lambda *args, **kwargs: calls.append("upsert"),
        history_writer=lambda *args, **kwargs: calls.append("history"),
        rules_loader=lambda: {"BRANCH_MAPPING": {}, "EXCLUDE_PREFIXES": [], "SALES_REP_LIST": []},
    )

    assert execution.response["status"] == "blocked"
    assert execution.response["writeCommitted"] is False
    assert calls == []


def test_empty_prepared_batch_is_blocked_without_write(tmp_path):
    execution = _execute_accepted_fixture(
        tmp_path,
        preflight_runner=lambda *args, **kwargs: {
            "status": "matched",
            "prepared": {"tour": pd.DataFrame(), "others": pd.DataFrame()},
        },
        upsert_runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not write")),
        history_writer=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not write history")),
    )
    assert execution.response["status"] == "blocked"
    assert execution.response["writeCommitted"] is False
    assert "沒有任何可寫入資料" in execution.response["message"]


def test_accepted_upload_writes_one_complete_history_record(tmp_path):
    from backend.services.upload_orchestrator_service import execute_upload_operation
    history_contexts = []
    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {"summary": pd.DataFrame()},
    }
    gate = {
        "status": "matched",
        "monthlyBaseline": {"registryVersion": "monthly-revenue-v1", "allMatched": True},
    }
    execution = execute_upload_operation(
        _operation(),
        main_file=object(),
        tour_file=None,
        other_files=[],
        live_db_path=tmp_path / "live.db",
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "message": "ok", "prepared": prepared, "stageTimings": []},
        upsert_runner=lambda *args, **kwargs: {"backup_path": "backup.db"},
        load_runner=lambda **kwargs: (prepared["tour"], prepared["others"]),
        gate_builder=lambda **kwargs: gate,
        rollback_handler=lambda *args, **kwargs: {"status": "accepted", "rollbackStatus": "not_required", "backupPath": "backup.db", "quarantinePath": None, "postRollbackGate": None, "rollbackError": None},
        generation_advancer=lambda **kwargs: {"generation": 1, "operationId": "op-1"},
        history_writer=lambda gate, context, **kwargs: history_contexts.append(context) or 7,
        rules_loader=lambda: {"BRANCH_MAPPING": {}, "EXCLUDE_PREFIXES": [], "SALES_REP_LIST": []},
    )

    assert execution.response["status"] == "success"
    assert execution.response["historyRecordId"] == 7
    assert execution.response["monthlyBaseline"]["allMatched"] is True
    assert execution.response["cacheState"] == "invalidated"
    assert len(history_contexts) == 1
    assert history_contexts[0]["operation_id"] == "op-1"
    assert history_contexts[0]["entry_point"] == "test"
    assert history_contexts[0]["latest_data_date"] == "2026-07-01"


def test_streamlit_cache_rebuild_is_recorded_before_history(tmp_path):
    from backend.services.upload_orchestrator_service import execute_upload_operation
    events = []
    prepared = {
        "tour": pd.DataFrame([{"來源單據號": "A", "統一日期": "2026-07-01"}]),
        "others": pd.DataFrame(),
        "anm": pd.DataFrame(),
        "entity_audit": {},
    }
    execution = execute_upload_operation(
        _operation(),
        main_file=object(),
        live_db_path=tmp_path / "live.db",
        preflight_runner=lambda *args, **kwargs: {"status": "matched", "prepared": prepared},
        upsert_runner=lambda *args, **kwargs: {"backup_path": "backup.db"},
        load_runner=lambda **kwargs: (prepared["tour"], prepared["others"]),
        gate_builder=lambda **kwargs: {"status": "matched", "monthlyBaseline": {"allMatched": True}},
        rollback_handler=lambda *args, **kwargs: {"status": "accepted", "rollbackStatus": "not_required", "postRollbackGate": None},
        generation_advancer=lambda **kwargs: {"generation": 2, "operationId": "op-1"},
        accepted_cache_rebuilder=lambda: events.append("cache"),
        history_writer=lambda gate, context, **kwargs: events.append(context["cache_state"]) or 8,
        rules_loader=lambda: {"BRANCH_MAPPING": {}, "EXCLUDE_PREFIXES": [], "SALES_REP_LIST": []},
    )

    assert events == ["cache", "streamlit_rebuilt"]
    assert execution.response["cacheState"] == "streamlit_rebuilt"


def test_generation_failure_returns_degraded_committed_result(tmp_path):
    execution = _execute_accepted_fixture(
        tmp_path,
        generation_advancer=lambda **kwargs: (_ for _ in ()).throw(OSError("generation write failed")),
    )
    assert execution.response["status"] == "degraded"
    assert execution.response["writeCommitted"] is True
    assert execution.response["cacheState"] == "refresh_required"
    assert "generation write failed" in execution.response["cacheError"]


def test_history_failure_is_not_reported_as_complete_success(tmp_path):
    execution = _execute_accepted_fixture(
        tmp_path,
        history_writer=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("history write failed")),
    )
    assert execution.response["status"] == "degraded"
    assert execution.response["writeCommitted"] is True
    assert "history write failed" in execution.response["historyError"]
```

- [ ] **Step 2: Run tests and verify the orchestrator module is missing**

```bash
.venv/bin/python -m pytest tests/test_upload_orchestrator_service.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the shared execution result and operation flow**

```python
# backend/services/upload_orchestrator_service.py
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import database
from backend.services.cache_generation_service import advance_cache_generation
from backend.services.monthly_baseline_service import build_governed_stability_gate
from backend.services.stability_history_service import record_stability_history
from backend.services.upload_lock_service import UploadOperation
from backend.services.upload_preflight_service import run_upload_preflight
from backend.services.upload_rollback_service import handle_core_drift_rollback
from rules import load_business_rules


@dataclass
class UploadExecution:
    response: dict[str, Any]
    anomaly_frame: pd.DataFrame
    entity_audit: dict[str, Any]


def _record(timings: list[dict], label: str, started: float) -> None:
    timings.append({"階段": label, "秒數": round(time.perf_counter() - started, 2)})


def _current_rules(rules_loader: Callable) -> tuple[dict, list[str], list[str]]:
    values = rules_loader()
    mapping = values.get("BRANCH_MAPPING", {})
    return dict(mapping) if isinstance(mapping, dict) else {}, list(values.get("EXCLUDE_PREFIXES", [])), list(values.get("SALES_REP_LIST", []))


def _combined_max_date(*frames: pd.DataFrame) -> str | None:
    candidates: list[pd.Timestamp] = []
    for frame in frames:
        if frame.empty:
            continue
        for column in ("統一日期", "收款時間", "日期"):
            if column not in frame.columns:
                continue
            values = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not values.empty:
                candidates.append(values.max())
                break
    return max(candidates).strftime("%Y-%m-%d") if candidates else None


def execute_upload_operation(
    operation: UploadOperation,
    *,
    main_file,
    tour_file=None,
    other_files=None,
    live_db_path: str | Path,
    preflight_runner: Callable = run_upload_preflight,
    upsert_runner: Callable = database.upsert_to_db,
    load_runner: Callable = database.load_all_data_from_db,
    gate_builder: Callable = build_governed_stability_gate,
    rollback_handler: Callable = handle_core_drift_rollback,
    generation_advancer: Callable = advance_cache_generation,
    history_writer: Callable = record_stability_history,
    rules_loader: Callable = load_business_rules,
    accepted_cache_rebuilder: Callable[[], None] | None = None,
) -> UploadExecution:
    live_path = database.resolve_db_path(live_db_path)
    other_files = list(other_files or [])
    branch_mapping, exclude_prefixes, sales_reps = _current_rules(rules_loader)
    timings: list[dict] = []
    preflight = preflight_runner(
        main_file,
        tour_file,
        other_files,
        branch_mapping,
        exclude_prefixes,
        sales_reps,
        source_files=list(operation.source_files),
        live_db_path=live_path,
    )
    prepared = preflight.get("prepared") or {}
    anomaly = prepared.get("anm") if isinstance(prepared.get("anm"), pd.DataFrame) else pd.DataFrame()
    entity_audit = prepared.get("entity_audit") if isinstance(prepared.get("entity_audit"), dict) else {}
    base_response = {
        "operationId": operation.operation_id,
        "entryPoint": operation.entry_point,
        "sourceFiles": list(operation.source_files),
        "preflightReport": {key: value for key, value in preflight.items() if key != "prepared"},
        "monthlyBaseline": (preflight.get("stabilityGate") or {}).get("monthlyBaseline") or {},
        "upsertSummary": None,
        "stabilityGate": None,
        "rollbackResult": None,
        "historyRecordId": None,
        "historyError": None,
        "writeCommitted": False,
        "cacheState": "unchanged",
        "dataGeneration": {},
        "stageTimings": list(preflight.get("stageTimings") or []),
    }
    if preflight.get("status") != "matched":
        return UploadExecution(
            response={**base_response, "status": "blocked", "message": preflight.get("message") or "上傳預演未通過。"},
            anomaly_frame=anomaly,
            entity_audit=entity_audit,
        )

    new_tour = prepared.get("tour") if isinstance(prepared.get("tour"), pd.DataFrame) else pd.DataFrame()
    new_others = prepared.get("others") if isinstance(prepared.get("others"), pd.DataFrame) else pd.DataFrame()
    if new_tour.empty and new_others.empty:
        return UploadExecution(
            response={**base_response, "status": "blocked", "message": "清洗後沒有任何可寫入資料；請檢查來源檔案與匹配規則。"},
            anomaly_frame=anomaly,
            entity_audit=entity_audit,
        )
    started = time.perf_counter()
    upsert = upsert_runner(new_tour, new_others, db_path=live_path)
    _record(timings, "正式 SQLite upsert", started)
    started = time.perf_counter()
    after_tour, after_others = load_runner(db_path=live_path)
    latest_data_date = _combined_max_date(after_tour, after_others)
    _record(timings, "寫入後 SQLite reload", started)
    started = time.perf_counter()
    gate = gate_builder(db_path=live_path)
    _record(timings, "Governed stability gate", started)
    started = time.perf_counter()
    rollback = rollback_handler(
        gate,
        upsert.get("backup_path"),
        restore_database=lambda backup: database.restore_database_from_backup(backup, live_db_path=live_path),
        rebuild_cache=lambda: None,
        build_gate=lambda: gate_builder(db_path=live_path),
    )
    _record(timings, "Rollback guard", started)
    final_status = str(rollback.get("status") or "rollback_failed")
    generation = {}
    cache_state = "unchanged"
    cache_error = None
    if final_status in {"accepted", "rejected_rolled_back"}:
        try:
            generation = generation_advancer(
                db_path=live_path,
                operation_id=operation.operation_id,
                status=final_status,
            )
            cache_state = "invalidated"
        except Exception as exc:
            cache_error = f"{type(exc).__name__}: {exc}"
            cache_state = "refresh_required"
    if final_status == "accepted" and cache_error is None and accepted_cache_rebuilder is not None:
        accepted_cache_rebuilder()
        cache_state = "streamlit_rebuilt"

    message = f"上傳批次已寫入；derived cache 已標記失效。SQLite 最新收款日期：{latest_data_date or '—'}。"
    public_status = "success"
    if final_status == "rejected_rolled_back":
        public_status = "error"
        message = "本次上傳因 blocking drift 已回滾；正式 SQLite 已恢復 accepted state。"
    elif final_status == "rollback_failed":
        public_status = "error"
        message = "偵測到 blocking drift，但 rollback 未完成驗證。"
    elif cache_error is not None:
        public_status = "degraded"
        message = "資料已寫入，但 cache generation 更新失敗；下次載入必須以 DB signature 強制刷新。"

    all_timings = list(preflight.get("stageTimings") or []) + timings
    history_context = {
        "operation_id": operation.operation_id,
        "entry_point": operation.entry_point,
        "upload_status": final_status,
        "upload_message": message,
        "source_files": list(operation.source_files),
        "latest_data_date": latest_data_date,
        "batch_summary": preflight.get("batchSummary") or [],
        "upsert_summary": upsert,
        "drift_diagnosis": preflight.get("driftDiagnosis") or {},
        "monthly_baseline": (rollback.get("postRollbackGate") or gate).get("monthlyBaseline") or {},
        "rollback_status": rollback.get("rollbackStatus"),
        "backup_path": rollback.get("backupPath"),
        "quarantine_path": rollback.get("quarantinePath"),
        "post_rollback_gate": rollback.get("postRollbackGate"),
        "rollback_error": rollback.get("rollbackError"),
        "stage_timings": all_timings,
        "cache_state": cache_state,
        "data_generation": generation,
        "cache_error": cache_error,
    }
    history_error = None
    history_id = None
    try:
        history_id = history_writer(
            rollback.get("postRollbackGate") or gate,
            history_context,
            db_path=live_path,
        )
    except Exception as exc:
        history_error = f"{type(exc).__name__}: {exc}"
        if public_status == "success":
            public_status = "degraded"
            message = "資料已寫入，但 stability history 未完整保存。"

    response = {
        **base_response,
        "status": public_status,
        "message": message,
        "upsertSummary": upsert,
        "stabilityGate": gate,
        "monthlyBaseline": (rollback.get("postRollbackGate") or gate).get("monthlyBaseline") or {},
        "rollbackResult": rollback,
        "historyRecordId": history_id,
        "historyError": history_error,
        "writeCommitted": final_status == "accepted",
        "cacheState": cache_state,
        "cacheError": cache_error,
        "dataGeneration": generation,
        "stageTimings": all_timings,
    }
    return UploadExecution(response=response, anomaly_frame=anomaly, entity_audit=entity_audit)
```

Add one blocked-path test whose preflight payload contains a user-facing warning and assert that `preflightReport` returns the same warning unchanged. This fixes both latest-date and warning semantics in the shared contract before adapter migration.

- [ ] **Step 4: Run orchestrator, rollback and history tests**

```bash
.venv/bin/python -m pytest tests/test_upload_orchestrator_service.py tests/test_upload_rollback_service.py tests/test_stability_history_service.py -q
```

Expected: PASS; blocked path has zero write calls and accepted path has exactly one history call.

- [ ] **Step 5: Commit**

```bash
git add backend/services/upload_orchestrator_service.py backend/services/upload_rollback_service.py tests/test_upload_orchestrator_service.py
git commit -m "feat: centralize formal upload orchestration"
```

---

### Task 7: Route FastAPI Uploads Through the Shared Contract

**Files:**
- Create: `tests/test_upload_action_service.py`
- Modify: `backend/services/upload_action_service.py:1-232`
- Modify: `backend/routers/upload.py:1-28`
- Modify: `backend/schemas/dashboard.py:216-230`
- Modify: `tests/test_upload_api.py`

**Interfaces:**
- Consumes: `acquire_upload_lease`, `execute_upload_operation`
- Produces: FastAPI 409 for `UploadBusyError`
- Preserves: named `UploadActionResponse`

- [ ] **Step 1: Write failing busy-before-read and response-schema tests**

```python
# tests/test_upload_action_service.py
import pytest


class CountingUpload:
    filename = "main.xlsx"

    def __init__(self):
        self.read_count = 0

    async def read(self):
        self.read_count += 1
        return b"main"


@pytest.mark.asyncio
async def test_busy_upload_does_not_read_file(monkeypatch):
    from backend.services import upload_action_service
    from backend.services.upload_lock_service import UploadBusyError
    upload = CountingUpload()
    monkeypatch.setattr(
        upload_action_service,
        "acquire_upload_lease",
        lambda **kwargs: (_ for _ in ()).throw(UploadBusyError({"entry_point": "streamlit"})),
    )

    with pytest.raises(UploadBusyError):
        await upload_action_service.run_vue_upload_action(main_file=upload)

    assert upload.read_count == 0
```

Extend `test_upload_api_accepts_files_and_returns_audit` to assert:

```python
assert payload["operationId"] == "op-api"
assert payload["entryPoint"] == "fastapi"
assert payload["cacheState"] == "invalidated"
assert payload["monthlyBaseline"]["allMatched"] is True
assert "已重建 dashboard cache" not in payload["message"]
```

Add a 409 test by monkeypatching `run_vue_upload_action` to raise `UploadBusyError`.

- [ ] **Step 2: Run FastAPI upload tests and confirm failure**

```bash
.venv/bin/python -m pytest tests/test_upload_action_service.py tests/test_upload_api.py -q
```

Expected: FAIL because the action still reads files before a process-local lock and the schema lacks the new fields.

- [ ] **Step 3: Replace the process-local API lock with the shared lease**

The action structure must be:

```python
source_files = [main_name] + ([tour_name] if tour_file is not None else []) + [item.filename or "other.xlsx" for item in other_files]
with acquire_upload_lease(
    entry_point="fastapi",
    source_files=source_files,
) as lease:
    main_bytes = await main_file.read()
    tour_bytes = await tour_file.read() if tour_file is not None else None
    other_payloads = [(item.filename or "other.xlsx", await item.read()) for item in other_files]
    execution = execute_upload_operation(
        lease.operation,
        main_file=_wrap_named_bytes(main_bytes, main_name),
        tour_file=_wrap_named_bytes(tour_bytes, tour_name) if tour_bytes is not None else None,
        other_files=[_wrap_named_bytes(payload, name) for name, payload in other_payloads],
        live_db_path=database.DB_FILE,
    )
    return {
        **execution.response,
        "latestHealth": compact_health_payload(build_system_health(
            db_path=Path(database.DB_FILE),
            cache_path=Path(".nbs_runtime_cache"),
            runtime_dir=Path(".nbs_runtime"),
        )),
        "entityAudit": _compact_entity_audit(execution.entity_audit),
        "anmRowCount": int(len(execution.anomaly_frame)),
        "environment": default_environment_payload(),
    }
```

Remove `UPLOAD_OPERATION_LOCK`. In the router, catch `UploadBusyError` before the general exception and return HTTP 409 with `detail={"status": "busy", "owner": exc.owner}`.

- [ ] **Step 4: Extend `UploadActionResponse`**

Add these required/optional fields with the same names as `execution.response`:

```python
operationId: str
entryPoint: str
monthlyBaseline: dict = Field(default_factory=dict)
cacheState: str
cacheError: str | None = None
dataGeneration: dict = Field(default_factory=dict)
stageTimings: list[dict] = Field(default_factory=list)
```

- [ ] **Step 5: Run API, OpenAPI and orchestrator tests**

```bash
.venv/bin/python -m pytest tests/test_upload_action_service.py tests/test_upload_api.py tests/test_upload_orchestrator_service.py tests/test_dashboard_api.py -q
```

Expected: PASS; `/api/upload` 200 uses the named response and busy uses HTTP 409 without reading file bytes.

- [ ] **Step 6: Commit**

```bash
git add backend/services/upload_action_service.py backend/routers/upload.py backend/schemas/dashboard.py tests/test_upload_action_service.py tests/test_upload_api.py
git commit -m "refactor: route api uploads through single writer"
```

---

### Task 8: Route Streamlit Uploads and Session Cache Through the Shared Contract

**Files:**
- Modify: `app.py:5-110`
- Modify: `app_workflows.py:1-130`
- Modify: `app_workflows.py:1140-1242`
- Modify: `app_pages.py:720-948`
- Modify: `app_pages.py:2128-2152`
- Modify: `tests/test_streamlit_upload_feedback_contract.py`
- Modify: `tests/test_app_module_boundaries.py`

**Interfaces:**
- Consumes: shared lease, orchestrator and generation service
- Produces: `_invalidate_session_cache_if_generation_changed() -> bool`
- Removes: Streamlit `UPLOAD_OPERATION_LOCK`

- [ ] **Step 1: Write failing static and behavioral cache-generation tests**

Replace the old process-local lock assertion with:

```python
def test_streamlit_upload_uses_shared_lease_and_orchestrator():
    source = _pages_function_source("_render_upload_area")
    assert "acquire_upload_lease(" in source
    assert "execute_upload_operation(" in source
    assert "UPLOAD_OPERATION_LOCK" not in source
    assert source.index("acquire_upload_lease(") < source.index("_uploaded_excel_frame(main_up)")


def test_dashboard_invalidates_session_cache_when_generation_changes():
    source = _workflows_function_source("_invalidate_session_cache_if_generation_changed")
    assert "load_cache_generation" in source
    assert 'st.session_state["DATA_GENERATION_TOKEN"]' in source
    assert 'st.session_state["PROCESSED_DATA_CACHE"] = None' in source
    assert 'st.session_state["DB_LOADED_FLAG"] = False' in source
```

Update the upload contract test to assert one orchestrator call and no direct `upsert_to_db`, `record_stability_history` or `handle_core_drift_rollback` inside `_render_upload_area`.

- [ ] **Step 2: Run Streamlit contract tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_streamlit_upload_feedback_contract.py tests/test_app_module_boundaries.py -q
```

Expected: FAIL because the old lock and duplicated write workflow remain.

- [ ] **Step 3: Add generation invalidation to `app_workflows.py`**

```python
from backend.services.cache_generation_service import load_cache_generation


def _invalidate_session_cache_if_generation_changed() -> bool:
    current = load_cache_generation(db_path=database.DB_FILE)
    generation = int(current.get("generation", 0))
    token = str(current.get("cacheToken") or f"{generation}:missing")
    loaded_token = st.session_state.get("DATA_GENERATION_TOKEN")
    if loaded_token is None:
        st.session_state["DATA_GENERATION"] = generation
        st.session_state["DATA_GENERATION_TOKEN"] = token
        return False
    if str(loaded_token) == token:
        return False
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["DATA_GENERATION"] = generation
    st.session_state["DATA_GENERATION_TOKEN"] = token
    return True
```

After `_load_and_compute_cache()` succeeds, set both `DATA_GENERATION` and `DATA_GENERATION_TOKEN` from `load_cache_generation(db_path=database.DB_FILE)` so a failed generation-file update is still detected through the live DB SHA-256 token.

- [ ] **Step 4: Replace the Streamlit write workflow with the shared operation**

At button click:

1. Build `source_files` from uploader names.
2. Acquire `acquire_upload_lease(entry_point="streamlit", source_files=source_files)` before any `_uploaded_excel_frame()` call.
3. Inside the lease, read each `UploadedFile` once into named bytes. Give independent `BytesIO` clones to the existing date-source diagnostics and to `execute_upload_operation()` so neither consumer depends on a shared file pointer.
4. Preserve the current Streamlit date-source diagnostics inside the lease; do not move its dataframe parsing into the orchestrator because FastAPI does not expose this UI-only evidence.
5. Pass `accepted_cache_rebuilder=lambda: _load_and_compute_cache(include_ai=False)` to `execute_upload_operation()`. The orchestrator records `cacheState="streamlit_rebuilt"` before writing history; FastAPI omits this callback and remains `invalidated`.
6. Map `execution.response` into `LAST_UPLOAD_AUDIT`; do not call DB upsert, gate, rollback or history directly.
7. Catch `UploadBusyError` and show owner entry point / started at without exposing file contents.

Remove `threading`, `UPLOAD_OPERATION_LOCK` and unused upload-service aliases from `app.py` / `app_workflows.py`. Keep `app.py` as the thin defensive entrypoint.

- [ ] **Step 5: Run Streamlit and upload tests**

```bash
.venv/bin/python -m pytest tests/test_streamlit_upload_feedback_contract.py tests/test_app_module_boundaries.py tests/test_upload_orchestrator_service.py tests/test_upload_api.py -q
```

Expected: PASS; Streamlit and FastAPI both delegate formal write behavior to the orchestrator.

- [ ] **Step 6: Commit**

```bash
git add app.py app_pages.py app_workflows.py tests/test_streamlit_upload_feedback_contract.py tests/test_app_module_boundaries.py
git commit -m "refactor: route streamlit uploads through single writer"
```

---

### Task 9: Add Operational Evidence and Cross-Process Integration Gate

**Files:**
- Create: `tests/test_upload_single_writer_integration.py`
- Modify: `backend/services/system_health_service.py:61-125`
- Modify: `backend/services/operational_monitor_service.py:20-50`
- Modify: `tests/test_system_health_service.py`
- Modify: `scripts/hermes_post_change_check.py:10-25`
- Modify: `tests/test_hermes_post_change_check.py`

**Interfaces:**
- Consumes: `probe_upload_lease`, `load_cache_generation`
- Produces health fields: `uploadCoordination`, `dataGeneration`
- Adds all new P0 test files to Hermes required targeted pack

- [ ] **Step 1: Write failing health and exactly-one-writer integration tests**

```python
# tests/test_upload_single_writer_integration.py
import multiprocessing as mp
import sqlite3
import time


def _contending_writer(lock_path, live_db_path, value, barrier, queue):
    from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease
    barrier.wait()
    try:
        with acquire_upload_lease(
            coordination_db_path=lock_path,
            entry_point=value,
            source_files=[f"{value}.xlsx"],
            timeout_seconds=0.05,
        ):
            conn = sqlite3.connect(live_db_path)
            conn.execute("INSERT INTO writes VALUES (?)", (value,))
            conn.commit()
            conn.close()
            time.sleep(0.25)
            queue.put("accepted")
    except UploadBusyError:
        queue.put("busy")


def test_two_processes_produce_exactly_one_formal_write(tmp_path):
    live_db = tmp_path / "live.db"
    lock_db = tmp_path / "upload_coordination.db"
    conn = sqlite3.connect(live_db)
    conn.execute("CREATE TABLE writes (value TEXT)")
    conn.commit()
    conn.close()
    queue = mp.Queue()
    barrier = mp.Barrier(2)
    first = mp.Process(target=_contending_writer, args=(str(lock_db), str(live_db), "streamlit", barrier, queue))
    second = mp.Process(target=_contending_writer, args=(str(lock_db), str(live_db), "fastapi", barrier, queue))
    first.start()
    second.start()
    first.join(5)
    second.join(5)
    outcomes = sorted([queue.get(timeout=2), queue.get(timeout=2)])
    assert outcomes == ["accepted", "busy"]
    conn = sqlite3.connect(live_db)
    assert conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 1
    conn.close()
```

Add health assertions:

```python
assert result["uploadCoordination"]["locked"] is False
assert result["dataGeneration"]["generation"] >= 0
assert result["uploadEvidence"]["matched"] in {True, None}
```

Add a degraded-health test where `data_generation.json` contains `operationId="missing-op"` but no history row has that operation ID; assert `status == "degraded"` and the issue names the missing upload evidence. Add a matched-health test where generation and history share `operationId="op-1"`.

- [ ] **Step 2: Run tests and confirm missing health fields**

```bash
.venv/bin/python -m pytest tests/test_upload_single_writer_integration.py tests/test_system_health_service.py tests/test_hermes_post_change_check.py -q
```

Expected: integration lock test may pass after Task 2, but health/Hermes assertions FAIL.

- [ ] **Step 3: Add lock and generation health evidence**

Inside `build_system_health()`:

```python
from backend.services.cache_generation_service import load_cache_generation
from backend.services.upload_lock_service import probe_upload_lease

coordination = probe_upload_lease(runtime_dir / "upload_coordination.db")
generation = load_cache_generation(runtime_dir / "data_generation.json", db_path=db_path)
```

Read history with `list_stability_history(limit=20, db_path=db_path)`. Match `generation["operationId"]` against each row's `operationId`, and return:

```python
upload_evidence = {
    "generationOperationId": generation.get("operationId"),
    "historyRecordId": matched_history.get("id") if matched_history else None,
    "matched": None if not generation.get("operationId") else matched_history is not None,
}
```

Return `coordination`, `generation`, and `upload_evidence` under `uploadCoordination`, `dataGeneration`, and `uploadEvidence`. Extend `compact_health_payload()` with the same compact fields. A currently locked upload is informational. `signatureMatched: false` is a cache-refresh warning; an unreadable `currentDbSignature` is degraded because the fallback token cannot prove which DB generation is loaded. A non-empty generation operation without matching history, or a matching history row with non-empty `cacheError`, is degraded and therefore visible to Hermes through `system-monitor`.

- [ ] **Step 4: Extend Hermes required tests**

Add these files to `TARGETED_TESTS`:

```python
"tests/test_database_explicit_path.py",
"tests/test_upload_lock_service.py",
"tests/test_cache_generation_service.py",
"tests/test_upload_orchestrator_service.py",
"tests/test_upload_action_service.py",
"tests/test_upload_single_writer_integration.py",
```

Update the plan test so the names are required and keep the timeout at 600 seconds.

- [ ] **Step 5: Run the complete P0 targeted pack**

```bash
.venv/bin/python -m pytest tests/test_database_explicit_path.py tests/test_upload_lock_service.py tests/test_cache_generation_service.py tests/test_upload_orchestrator_service.py tests/test_upload_action_service.py tests/test_upload_single_writer_integration.py tests/test_upload_preflight_service.py tests/test_upload_rollback_service.py tests/test_upload_api.py tests/test_stability_history_service.py tests/test_monthly_baseline_service.py tests/test_phase2_precheck_acceptance.py -q
```

Expected: PASS with no formal DB writes.

- [ ] **Step 6: Commit**

```bash
git add backend/services/system_health_service.py backend/services/operational_monitor_service.py scripts/hermes_post_change_check.py tests/test_upload_single_writer_integration.py tests/test_system_health_service.py tests/test_hermes_post_change_check.py
git commit -m "test: gate upload single-writer operations"
```

---

### Task 10: Full Validation, Hermes, ADR and Obsidian Backfill

**Files:**
- Create: `Summay/ADR-002-Upload Single-Writer Contract.md`
- Modify: `docs/briefs/2026-07-11-p0-upload-single-writer-contract.md`
- Modify: `/Users/chanwaitung2025/Documents/Obsidian Vault/NBS_Analytics_Knowledge/70_Codex_Briefs/2026-07-11 P0 Upload Single-Writer Contract.md`
- Create: `/Users/chanwaitung2025/Documents/Obsidian Vault/NBS_Analytics_Knowledge/20_Decisions/ADR-002 Upload Single-Writer Contract.md`
- Modify when facts changed: `NBS_ANALYTICS_HANDOFF.md`, `NBS_ANALYTICS_SYSTEM_MAP.md`, `NBS_HERMES_MONITORING.md`

**Interfaces:**
- Consumes: all prior tasks
- Produces: verified P0 version node and evidence for the later Streamlit rerun Brief

- [ ] **Step 1: Run compile checks**

```bash
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py database.py backend/services/upload_lock_service.py backend/services/cache_generation_service.py backend/services/upload_orchestrator_service.py backend/services/upload_action_service.py backend/services/upload_preflight_service.py backend/services/stability_history_service.py backend/services/monthly_baseline_service.py backend/services/stability_service.py backend/services/dashboard_service.py backend/services/dashboard_analytics_service.py scripts/system_manager.py scripts/hermes_post_change_check.py
```

Expected: exit 0.

- [ ] **Step 2: Run the required upload / DB / baseline suites**

```bash
.venv/bin/python -m pytest tests/test_database_explicit_path.py tests/test_upload_lock_service.py tests/test_cache_generation_service.py tests/test_upload_orchestrator_service.py tests/test_upload_action_service.py tests/test_upload_single_writer_integration.py tests/test_upload_preflight_service.py tests/test_upload_rollback_service.py tests/test_upload_api.py tests/test_stability_history_service.py tests/test_monthly_baseline_service.py tests/test_database_rollback.py tests/test_phase2_precheck_acceptance.py tests/test_dashboard_service.py tests/test_dashboard_api.py -q
```

Expected: PASS; May baseline remains matched.

- [ ] **Step 3: Run the full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 4: Run the disposable upload dry-run**

```bash
.venv/bin/python scripts/upload_profiling_dry_run.py --rows 25 --include-drift-diagnosis --json
```

Required JSON evidence:

- `liveDbUnchanged: true`
- `preflightStatus: matched`
- `stabilityStatus: matched`
- `rollbackStatus: not_required`
- `formattedActualTotal: HKD 12,057,968`

- [ ] **Step 5: Restart and accept the three services**

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
```

Expected: Streamlit, API and Vue all ready; acceptance `passed`.

- [ ] **Step 6: Run Hermes read-only acceptance**

```bash
.venv/bin/python scripts/hermes_post_change_check.py --json
```

Expected:

- `overallStatus: pass`
- `phase2-baseline` exit 0
- `monthly-baseline-governance` exit 0
- targeted tests exit 0
- system monitor reports lock/generation evidence

- [ ] **Step 7: Write ADR and backfill the Brief**

The ADR must record:

- why process-local locks were insufficient;
- why SQLite coordination lock was chosen over API-only writer and lock files;
- explicit `db_path` and no module-global mutation;
- governed gate/history/cache generation decisions;
- May and monthly baseline evidence;
- rollback and recovery consequences.

Update the repo Brief frontmatter to `status: verified`, append actual changed files, exact test counts, Hermes result and final commit IDs, then copy the same Brief to the Obsidian vault. Create the matching Obsidian ADR.

- [ ] **Step 8: Commit documentation and verify the worktree**

```bash
git add Summay/ADR-002-Upload\ Single-Writer\ Contract.md docs/briefs/2026-07-11-p0-upload-single-writer-contract.md NBS_ANALYTICS_HANDOFF.md NBS_ANALYTICS_SYSTEM_MAP.md NBS_HERMES_MONITORING.md
git commit -m "docs: record upload single-writer acceptance"
git status --short --branch
```

Expected: final `git status` contains only `## main` or the active `codex/...` branch with no dirty files.

- [ ] **Step 9: Open the second Brief gate**

Only after Steps 1-8 pass, create a new Obsidian Brief titled `Streamlit Rerun Hot Path`. Do not implement it in this branch. Its baseline evidence must quote the pre-change no-op repair timings and re-measure them after its own implementation.

---

## Implementation Completion Criteria

The implementation is complete only when all of the following are true:

1. Streamlit and FastAPI cannot hold the upload lease simultaneously.
2. A crashed writer does not leave a permanent stale lock.
3. Preflight never mutates `database.DB_FILE`.
4. Preflight, post-write and rollback verification all use the same governed monthly gate bound to the intended DB.
5. Both adapters produce the same history/monthly/cache contract.
6. API does not claim Streamlit cache was rebuilt when it was only invalidated.
7. May baseline and all current monthly checks remain matched.
8. Full pytest, disposable dry-run, service acceptance and Hermes pass.
9. Obsidian and repo documentation are backfilled and Git is clean.
