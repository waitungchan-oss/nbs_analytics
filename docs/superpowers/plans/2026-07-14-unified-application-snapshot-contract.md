# P3-1 Unified Application Snapshot Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 Decision API 的 rules loading、generation pinning、read-model orchestration、retry 與 conflict policy 移入一個可測試的 Application Snapshot service，同時維持既有 HTTP contract、正式口徑與性能門檻。

**Architecture:** 新增 public Business Rules Service 與 typed Application Snapshot Service。Snapshot service 只協調現有 Dashboard Facts、Data Quality、Forecast、System Health 與 Target Config builders；Decision router 變成薄 HTTP adapter，Decision Service 繼續負責 targets、alerts 與 decision cards。

**Tech Stack:** Python 3.10、dataclasses、FastAPI、Pydantic、pytest、SQLite、現有本地 generation-aware cache。

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- `2026-05` frozen baseline 必須維持 `HKD 12,057,968`。
- `2026-01` 至 `2026-06` 六個 blocking monthly baseline 必須全部 `matched`。
- 不修改 upload、preflight、single-writer lease、upsert、rollback、history 或 generation advance。
- 不修改 branch reassignment override、report sheets、GMV、Forecast 算法、AI cache payload、WAPE、Target config schema 或 Decision 規則。
- 第一階段只接 Decision API；不改 Streamlit data flow、Dashboard API response 或 Vue 計算。
- Application service 不匯入 FastAPI、Streamlit、Pandas、pipeline 或 database detail loader。
- 現有 Forecast cache 不綁定 DB generation；不得輸出或暗示 `forecastGenerationMatched`。
- Decision API warm median 必須 `<= 300ms`。
- 所有新測試只使用 `tmp_path` 或 injected dependencies，不讀寫正式 DB、runtime、cache、rules 或 target config。

---

## File Map

| File | Responsibility |
|---|---|
| `rules.py` | 讓既有 `load_business_rules()` 支援 backward-compatible explicit path。 |
| `backend/services/business_rules_service.py` | 正規化 Facts 所需正式 rules、提供 defensive copies 與穩定 fingerprint。 |
| `backend/services/application_snapshot_service.py` | 集中 paths、dependencies、generation retry、typed snapshot 與 conflict。 |
| `backend/services/dashboard_service.py` | 將 private compatibility wrapper 委派至 public rules provider。 |
| `backend/services/decision_service.py` | 接受 snapshot provenance 並合併至既有 response provenance。 |
| `backend/routers/decisions.py` | 改為薄 adapter，將 typed conflict 映射為 HTTP 409。 |
| `tests/test_business_rules_service.py` | Rules normalization、fingerprint、defensive copy、explicit path。 |
| `tests/test_application_snapshot_service.py` | Snapshot success、retry、conflict、paths、provenance 與 import boundary。 |
| `tests/test_decision_api.py` | Router contract、409 mapping、provenance 與 OpenAPI regression。 |
| `scripts/hermes_post_change_check.py` | 將 P3-1 targeted tests 納入 read-only post-change gate。 |
| `NBS_ANALYTICS_SYSTEM_MAP.md` | 回填正式 application snapshot boundary。 |
| `docs/briefs/2026-07-14-p3-1-unified-application-snapshot-contract.md` | 回填 Task、commit 與驗收證據。 |

---

### Task 1: Public Business Rules Service

**Files:**
- Modify: `rules.py`
- Create: `backend/services/business_rules_service.py`
- Modify: `backend/services/dashboard_service.py`
- Create: `tests/test_business_rules_service.py`

**Interfaces:**
- Produces: `BusinessRulesSnapshot`, `load_business_rules_snapshot(config_path=None) -> BusinessRulesSnapshot`。
- Produces: `BusinessRulesSnapshot.facts_kwargs() -> dict[str, object]`，每次回傳新的 dict/list。
- Preserves: `rules.load_business_rules(path=None) -> dict`；無參數行為不變。

- [ ] **Step 1: Write failing explicit-path and fingerprint tests**

在 `tests/test_business_rules_service.py` 建立最小 rules JSON，驗證：

```python
def test_load_rules_snapshot_uses_explicit_path_and_stable_fingerprint(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({
        "BRANCH_MAPPING": {"02": "B", "01": "A"},
        "TARGET_BRANCHES_S3": ["A", "B"],
        "CRUISE_DEPTS": ["Cruise"],
        "SALES_REP_LIST": ["Amy"],
    }, ensure_ascii=False), encoding="utf-8")
    second.write_text(json.dumps({
        "BRANCH_MAPPING": {"01": "A", "02": "B"},
        "TARGET_BRANCHES_S3": ["A", "B"],
        "CRUISE_DEPTS": ["Cruise"],
        "SALES_REP_LIST": ["Amy"],
    }, ensure_ascii=False), encoding="utf-8")

    left = load_business_rules_snapshot(first)
    right = load_business_rules_snapshot(second)

    assert left.fingerprint == right.fingerprint
    assert left.branch_mapping == {"01": "A", "02": "B"}
```

加入 defensive copy test：修改 `facts_kwargs()` 回傳的 mapping/list 後，再次呼叫的內容不變。

- [ ] **Step 2: Run tests to verify RED**

Run：

```bash
.venv/bin/python -m pytest tests/test_business_rules_service.py -q
```

Expected：collection/import FAIL，因為 `backend.services.business_rules_service` 尚不存在。

- [ ] **Step 3: Add backward-compatible explicit rules path**

將 `rules.py` 改為：

```python
def load_business_rules(path: str | Path | None = None) -> dict:
    target = Path(path or CONFIG_FILE)
    if not target.exists():
        return DEFAULT_RULES.copy()
    try:
        with target.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return {**DEFAULT_RULES, **loaded}
    except Exception:
        return DEFAULT_RULES.copy()
```

既有 caller 不傳 path，行為維持不變。

- [ ] **Step 4: Implement BusinessRulesSnapshot**

在 `backend/services/business_rules_service.py`：

```python
@dataclass(frozen=True)
class BusinessRulesSnapshot:
    branch_mapping_items: tuple[tuple[str, str], ...]
    target_branches: tuple[str, ...]
    cruise_departments: tuple[str, ...]
    sales_reps: tuple[str, ...]
    fingerprint: str

    @property
    def branch_mapping(self) -> dict[str, str]:
        return dict(self.branch_mapping_items)

    def facts_kwargs(self) -> dict[str, object]:
        return {
            "branch_mapping": self.branch_mapping,
            "target_branches_s3": list(self.target_branches),
            "cruise_depts": list(self.cruise_departments),
            "sales_rep_list": list(self.sales_reps),
        }
```

`load_business_rules_snapshot(config_path=None)` 使用 `load_business_rules(config_path)`，只選取四組 Facts inputs，mapping 依 key 排序，canonical JSON 使用 `ensure_ascii=False, sort_keys=True, separators=(",", ":")`，fingerprint 使用 SHA-256。

- [ ] **Step 5: Delegate dashboard compatibility wrapper**

保留 `dashboard_service._current_rules()` 以避免擴大 consumer 改動，但改為：

```python
def _current_rules() -> tuple[dict, list[str], list[str], list[str]]:
    values = load_business_rules_snapshot().facts_kwargs()
    return (
        values["branch_mapping"],
        values["target_branches_s3"],
        values["cruise_depts"],
        values["sales_rep_list"],
    )
```

- [ ] **Step 6: Run Task 1 tests and regressions**

Run：

```bash
.venv/bin/python -m pytest tests/test_business_rules_service.py tests/test_dashboard_service.py tests/test_dashboard_api.py -q
```

Expected：PASS。

- [ ] **Step 7: Commit Task 1**

```bash
git add rules.py backend/services/business_rules_service.py backend/services/dashboard_service.py tests/test_business_rules_service.py
git commit -m "refactor: expose business rules snapshot"
```

---

### Task 2: Typed Application Snapshot Service

**Files:**
- Create: `backend/services/application_snapshot_service.py`
- Create: `tests/test_application_snapshot_service.py`

**Interfaces:**
- Consumes: `load_business_rules_snapshot(config_path) -> BusinessRulesSnapshot`。
- Produces: `SnapshotPaths`, `SnapshotDependencies`, `ApplicationSnapshotService`, `ApplicationSnapshot`, `SnapshotGenerationConflict`。
- Produces: `ApplicationSnapshotService.build() -> ApplicationSnapshot`。

- [ ] **Step 1: Write failing success-path test**

建立 injected dependencies，generation loader 連續回傳相同 token，並記錄 Facts/Data Quality 收到的參數：

```python
def test_snapshot_uses_one_generation_and_explicit_paths(tmp_path):
    seen = {"facts": [], "quality": []}
    dependencies = fake_dependencies(
        generations=[{"cacheToken": "7:abc"}, {"cacheToken": "7:abc"}],
        seen=seen,
    )
    paths = SnapshotPaths(
        db_path=tmp_path / "live.db",
        cache_dir=tmp_path / "cache",
        runtime_dir=tmp_path / "runtime",
        rules_config_path=tmp_path / "rules.json",
        target_config_path=tmp_path / "targets.json",
    )

    snapshot = ApplicationSnapshotService(paths, dependencies=dependencies).build()

    assert snapshot.generation_token == "7:abc"
    assert seen["facts"][0]["generation_token"] == "7:abc"
    assert seen["quality"][0]["generation_token"] == "7:abc"
    assert snapshot.provenance["coreGenerationConsistent"] is True
    assert snapshot.provenance["snapshotAttemptCount"] == 1
```

- [ ] **Step 2: Run success test to verify RED**

Run：

```bash
.venv/bin/python -m pytest tests/test_application_snapshot_service.py::test_snapshot_uses_one_generation_and_explicit_paths -q
```

Expected：collection/import FAIL，因為 snapshot service 尚不存在。

- [ ] **Step 3: Implement paths and typed contracts**

在 `backend/services/application_snapshot_service.py` 建立 frozen dataclasses：

```python
@dataclass(frozen=True)
class SnapshotPaths:
    db_path: Path
    cache_dir: Path
    runtime_dir: Path
    rules_config_path: Path
    target_config_path: Path

    @property
    def generation_path(self) -> Path:
        return self.runtime_dir / "data_generation.json"

@dataclass(frozen=True)
class ApplicationSnapshot:
    generation_token: str
    rules: BusinessRulesSnapshot
    facts: dict
    forecast: dict
    quality: dict
    health: dict
    targets: dict
    provenance: dict
```

`SnapshotDependencies` 保存七個 callable，production defaults 指向現有 service functions。不要加入全域 registry。

- [ ] **Step 4: Implement one-attempt build and provenance**

`ApplicationSnapshotService.build()` 先實作單次成功路徑：

- rules 使用 explicit config path；
- generation loader 使用 `paths.generation_path` 與 `paths.db_path`；
- Facts 使用 `rules.facts_kwargs()`、generation token、DB path、cache dir；
- Quality 使用同一 token、DB path、cache dir；
- Forecast 使用 cache dir；
- Health 使用 DB path、cache dir、runtime dir；
- Targets 使用 target config path；
- end token 與 start token 相同時回傳 snapshot。

Provenance 必須直接從 read models 取 cache/status，不重新計算。

- [ ] **Step 5: Run success test to verify GREEN**

```bash
.venv/bin/python -m pytest tests/test_application_snapshot_service.py::test_snapshot_uses_one_generation_and_explicit_paths -q
```

Expected：PASS。

- [ ] **Step 6: Write failing retry and conflict tests**

加入：

```python
def test_snapshot_rebuilds_every_dependency_when_generation_changes_once(...):
    # tokens: old start, new end, new start, new end
    assert snapshot.generation_token == "2:new"
    assert snapshot.provenance["snapshotAttemptCount"] == 2
    assert facts_tokens == ["1:old", "2:new"]
    assert quality_tokens == ["1:old", "2:new"]
    assert forecast_calls == 2
    assert rules_calls == 2

def test_snapshot_raises_typed_conflict_after_second_change(...):
    with pytest.raises(SnapshotGenerationConflict) as raised:
        service.build()
    assert raised.value.attempts == 2
    assert raised.value.observed_tokens == (
        ("1:first", "2:second"),
        ("2:second", "3:third"),
    )
```

- [ ] **Step 7: Run retry tests to verify RED**

```bash
.venv/bin/python -m pytest tests/test_application_snapshot_service.py -q
```

Expected：retry/conflict tests FAIL，因為 service 尚未重試或拋 typed conflict。

- [ ] **Step 8: Implement retry and typed conflict**

`SnapshotGenerationConflict(RuntimeError)` 保存 `attempts` 與 immutable `observed_tokens`。`build()` 最多兩次完整 attempts；任何成功前 payload 不得被重用。

- [ ] **Step 9: Add import-boundary and provenance tests**

AST/import test 驗證 `application_snapshot_service.py` 不直接 import：

```text
fastapi
streamlit
pandas
pipeline
database
```

Provenance test 驗證沒有 `forecastGenerationMatched`，並包含：

```text
generationToken
coreGenerationConsistent
snapshotAttemptCount
dbPath
rulesFingerprint
factsCacheStatus
readModelCacheStatus
dataQualityCacheStatus
forecastStatus
forecastCache
systemHealthStatus
```

- [ ] **Step 10: Run all Task 2 tests**

```bash
.venv/bin/python -m pytest tests/test_application_snapshot_service.py tests/test_business_rules_service.py tests/test_dashboard_facts_service.py tests/test_data_quality_cache.py -q
```

Expected：PASS。

- [ ] **Step 11: Commit Task 2**

```bash
git add backend/services/application_snapshot_service.py tests/test_application_snapshot_service.py
git commit -m "feat: add generation-consistent application snapshot"
```

---

### Task 3: Decision API Integration And Contract

**Files:**
- Modify: `backend/services/decision_service.py`
- Modify: `backend/routers/decisions.py`
- Modify: `tests/test_decision_api.py`
- Modify: `tests/test_decision_service.py`

**Interfaces:**
- Consumes: `ApplicationSnapshotService.build() -> ApplicationSnapshot`。
- Consumes: `SnapshotGenerationConflict`。
- Extends: `build_decision_overview(..., snapshot_provenance: dict | None = None) -> dict`。
- Preserves: `GET /api/decisions/overview` response schema與 HTTP 409 behavior。

- [ ] **Step 1: Write failing Decision Service provenance test**

在 `tests/test_decision_service.py` 驗證：

```python
payload = build_decision_overview(
    facts=facts,
    forecast=forecast,
    quality=quality,
    health=health,
    target_config=targets,
    snapshot_provenance={
        "rulesFingerprint": "rules-1",
        "snapshotAttemptCount": 2,
        "coreGenerationConsistent": True,
    },
)
assert payload["provenance"]["rulesFingerprint"] == "rules-1"
assert payload["provenance"]["snapshotAttemptCount"] == 2
assert payload["provenance"]["generationToken"] == facts["generationToken"]
```

- [ ] **Step 2: Run provenance test to verify RED**

```bash
.venv/bin/python -m pytest tests/test_decision_service.py -q
```

Expected：FAIL，因為 `snapshot_provenance` 參數尚不存在。

- [ ] **Step 3: Merge snapshot provenance without removing existing keys**

為 `build_decision_overview` 新增 optional `snapshot_provenance` keyword。先建立既有 provenance，再以 snapshot metadata 增加新 keys；`generationToken`、cache/status 等既有 keys 維持原值與名稱。

- [ ] **Step 4: Run Decision Service tests to verify GREEN**

```bash
.venv/bin/python -m pytest tests/test_decision_service.py -q
```

Expected：PASS。

- [ ] **Step 5: Rewrite router tests against snapshot boundary**

將 `tests/test_decision_api.py` 的 upstream monkeypatch 收斂為：

- mock `ApplicationSnapshotService.build()` 回傳 `ApplicationSnapshot`；
- mock/保留真實 `build_decision_overview()`；
- typed response與 OpenAPI ref 不變；
- `SnapshotGenerationConflict` 映射為 HTTP 409；
- response provenance 包含 snapshot metadata；
- source inspection/assertion 確認 router 不再引用 `_current_rules`、`load_cache_generation` 或保存 `for _ in range(2)` retry loop。

- [ ] **Step 6: Run router tests to verify RED**

```bash
.venv/bin/python -m pytest tests/test_decision_api.py -q
```

Expected：FAIL，因為 router 尚未使用 Snapshot service。

- [ ] **Step 7: Refactor Decision router**

Router 使用正式 paths：

```python
paths = SnapshotPaths(
    db_path=Path(DB_FILE),
    cache_dir=PROJECT_ROOT / ".nbs_runtime_cache",
    runtime_dir=PROJECT_ROOT / ".nbs_runtime",
    rules_config_path=Path(CONFIG_FILE),
    target_config_path=DEFAULT_TARGET_CONFIG_PATH,
)
```

呼叫 snapshot service，將 fields 交給 Decision Service；捕捉 `SnapshotGenerationConflict` 並回傳既有 409 message。不要捕捉一般 exception。

- [ ] **Step 8: Run Task 3 tests and API regressions**

```bash
.venv/bin/python -m pytest tests/test_decision_service.py tests/test_decision_api.py tests/test_decision_api_performance.py tests/test_target_governance_api.py -q
```

Expected：PASS。

- [ ] **Step 9: Run performance gate**

```bash
.venv/bin/python scripts/profile_decision_api.py --warm-limit-ms 300 --runs 5
```

Expected：exit 0，warm median `<= 300ms`。

- [ ] **Step 10: Commit Task 3**

```bash
git add backend/services/decision_service.py backend/routers/decisions.py tests/test_decision_api.py tests/test_decision_service.py
git commit -m "refactor: serve decisions from application snapshot"
```

---

### Task 4: Governance Gates, Documentation And Final Verification

**Files:**
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `NBS_ANALYTICS_SYSTEM_MAP.md`
- Modify: `docs/briefs/2026-07-14-p3-1-unified-application-snapshot-contract.md`
- Modify: `/Users/chanwaitung2025/Documents/Obsidian Vault/NBS_Analytics_Knowledge/70_Codex_Briefs/2026-07-14 P3-1 Unified Application Snapshot Contract.md`

**Interfaces:**
- Preserves: existing Hermes JSON report contract。
- Adds: P3-1 tests to Hermes targeted pack。

- [ ] **Step 1: Add P3-1 tests to Hermes targeted pack**

在 `scripts/hermes_post_change_check.py` 的 targeted test list 加入：

```text
tests/test_business_rules_service.py
tests/test_application_snapshot_service.py
```

保留既有 tests，不刪除 upload、baseline、dashboard、decision 或 governance coverage。

- [ ] **Step 2: Run affected compile and targeted tests**

```bash
.venv/bin/python -m py_compile \
  rules.py \
  backend/services/business_rules_service.py \
  backend/services/application_snapshot_service.py \
  backend/services/dashboard_service.py \
  backend/services/decision_service.py \
  backend/routers/decisions.py \
  scripts/hermes_post_change_check.py

.venv/bin/python -m pytest \
  tests/test_business_rules_service.py \
  tests/test_application_snapshot_service.py \
  tests/test_dashboard_facts_service.py \
  tests/test_data_quality_cache.py \
  tests/test_decision_service.py \
  tests/test_decision_api.py \
  tests/test_decision_api_performance.py \
  tests/test_target_governance_service.py \
  tests/test_target_governance_api.py -q
```

Expected：全部 PASS。

- [ ] **Step 3: Run complete Python suite**

```bash
.venv/bin/python -m pytest -q
```

Expected：全部 PASS。

- [ ] **Step 4: Verify Vue contract and build**

依現有 frontend scripts 執行：

```bash
npm --prefix frontend run verify
npm --prefix frontend run build
```

Expected：contract verification與 production build PASS。

- [ ] **Step 5: Re-run performance gate**

```bash
.venv/bin/python scripts/profile_decision_api.py --warm-limit-ms 300 --runs 5
```

Expected：exit 0，warm median `<= 300ms`。

- [ ] **Step 6: Run service acceptance and Hermes**

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --json
```

Expected：

- system acceptance `passed`；
- Hermes `overallStatus: pass`；
- SQLite integrity `ok`；
- 2026-01 至 2026-06 monthly checks全部 `matched`；
- 2026-05 `HKD 12,057,968 matched`。

- [ ] **Step 7: Update system map and Brief evidence**

`NBS_ANALYTICS_SYSTEM_MAP.md` 補充：

- Decision API 經 Application Snapshot service 取得 request-scoped snapshot；
- public Business Rules Service 取代跨模組 private rules helper；
- generation consistency 只涵蓋 generation-aware core read models；
- Forecast 仍以獨立 AI cache provenance 表示；
- P3-1 正式 commit與驗收數字。

Brief 狀態依驗收改為 `verified`，同步 Obsidian copy，並記錄實際 tests、profile median、acceptance、Hermes 與 baseline evidence。

- [ ] **Step 8: Verify documentation consistency**

```bash
cmp -s \
  docs/briefs/2026-07-14-p3-1-unified-application-snapshot-contract.md \
  '/Users/chanwaitung2025/Documents/Obsidian Vault/NBS_Analytics_Knowledge/70_Codex_Briefs/2026-07-14 P3-1 Unified Application Snapshot Contract.md'
git diff --check
```

Expected：兩份 Brief identical，diff check clean。

- [ ] **Step 9: Commit verification and documentation**

```bash
git add scripts/hermes_post_change_check.py NBS_ANALYTICS_SYSTEM_MAP.md docs/briefs/2026-07-14-p3-1-unified-application-snapshot-contract.md
git commit -m "docs: verify application snapshot contract"
```

- [ ] **Step 10: Final clean verification**

```bash
git status --short --branch
git log -5 --oneline
```

Expected：feature worktree clean，commit chain完整。

---

## Final Review Gate

完成四個 Task 後：

1. 對 feature branch 與 merge base 產生完整 diff review package。
2. 執行 spec compliance 與 code quality review。
3. 修正所有 Critical / Important findings並重跑 covering tests。
4. 再跑 full pytest、performance、system acceptance、Hermes。
5. 只有全部 PASS 才進入 merge/keep branch選項；不得自動修改 main 正式 DB。
