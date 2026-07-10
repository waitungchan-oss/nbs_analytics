# Monthly Baseline Monitoring Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立2026年1月至6月月度營收自動監測、穩定 upload cycle、Streamlit 人工升級與 Hermes 稽核，同時維持2026年5月既有 blocking gate。

**Architecture:** 使用 versioned JSON registry 保存精確基準與治理模式；`monthly_baseline_service.py` 透過正式 dashboard analytics facts 一次計算六個月，分離 monitoring 與 blocking checks。Upload history 保存監測快照，人工 promotion 以原子 JSON 更新和 SQLite audit event 完成；Streamlit 只展示 service payload 並發出 promotion 命令。

**Tech Stack:** Python 3、pandas、SQLite、Streamlit、pytest、既有 dashboard analytics、stability history、rollback 與 Hermes scripts。

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 正式母體固定為全部正式分社加正式四人專職銷售組。
- 精確比較使用 registry 小數值，容差為 `abs(delta) < HKD 1.00`。
- 2026-05 保持既有 blocking gate；初始新增 monitoring months 為2026-01、02、03、04、06。
- Monitoring drift 只警告，不可令 upload rollback。
- 系統不得自動執行 `promotion_ready -> blocking`。
- Promotion 必須重新計算、核對最新 upload Record ID、二次確認並原子寫入。
- Vue 不重算基準、不執行 promotion。

---

### Task 1: Versioned Registry 與純月度評估

**Files:**
- Create: `data/monthly_revenue_baselines.json`
- Create: `backend/services/monthly_baseline_service.py`
- Create: `tests/test_monthly_baseline_service.py`

**Interfaces:**
- `load_monthly_baseline_registry(path: Path | None = None) -> dict`
- `evaluate_monthly_baselines(registry: dict | None = None, analytics_builder: Callable | None = None) -> dict`
- `apply_monthly_blocking_checks(gate: dict, evaluation: dict) -> dict`
- `build_governed_stability_gate() -> dict`

- [ ] **Step 1: 寫 registry 與 RED tests**

Registry 使用：

```json
{
  "version": "monthly-revenue-v1",
  "scope": "不含掛賬核銷與TT退款轉團款",
  "population": "全部正式分社＋正式四人專職銷售組",
  "amountTolerance": 1.0,
  "requiredStableUploadCycles": 1,
  "baselines": [
    {"month": "2026-01", "expectedTotal": 10711053.50, "displayTotal": 10711054, "mode": "monitoring"},
    {"month": "2026-02", "expectedTotal": 9765694.54, "displayTotal": 9765695, "mode": "monitoring"},
    {"month": "2026-03", "expectedTotal": 14628841.00, "displayTotal": 14628841, "mode": "monitoring"},
    {"month": "2026-04", "expectedTotal": 10506207.78, "displayTotal": 10506208, "mode": "monitoring"},
    {"month": "2026-05", "expectedTotal": 12057967.92, "displayTotal": 12057968, "mode": "blocking", "legacyCore": true},
    {"month": "2026-06", "expectedTotal": 9083241.29, "displayTotal": 9083241, "mode": "monitoring"}
  ]
}
```

Tests 必須覆蓋：registry schema／精確累計 `66,753,006.03`；六個 matched；monitoring drift 不改 gate；promoted blocking drift 令 gate drift；legacy May 不重複注入。

- [ ] **Step 2: 執行 RED**

Run: `.venv/bin/python -m pytest tests/test_monthly_baseline_service.py -q`

Expected: FAIL，因 service 尚不存在。

- [ ] **Step 3: 實作純 service**

`evaluate_monthly_baselines` 預設 lazy import `build_dashboard_analytics`，一次查詢涵蓋 `2026-01-01` 至 `2026-06-30`，從 `monthlyTrend` 建立：

```python
{
    "registryVersion": "monthly-revenue-v1",
    "scope": "不含掛賬核銷與TT退款轉團款",
    "checks": [...],
    "monitoringChecks": [...],
    "blockingChecks": [...],
    "matchedCount": 6,
    "totalChecks": 6,
    "allMatched": True,
    "blockingStatus": "matched",
}
```

`apply_monthly_blocking_checks` 只將 `mode=blocking` 且非 `legacyCore` 的 checks 合併入 gate `coreValidation`，重新計算 matched/drift counts、status、message；monitoring checks 只掛在 `gate["monthlyBaseline"]`。

- [ ] **Step 4: 執行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_monthly_baseline_service.py -q`

Expected: 全部 PASS。

---

### Task 2: Upload History 與 Stable Cycle

**Files:**
- Modify: `backend/services/stability_history_service.py`
- Modify: `tests/test_stability_history_service.py`
- Extend: `backend/services/monthly_baseline_service.py`
- Extend: `tests/test_monthly_baseline_service.py`

**Interfaces:**
- Stability history 新欄位：`monthly_baseline_json TEXT`
- `build_monthly_baseline_governance(evaluation: dict | None = None, history_records: list[dict] | None = None) -> dict`

- [ ] **Step 1: 寫 RED tests**

斷言 `record_stability_history` 可保存並讀回 `monthlyBaseline`。Governance tests 使用三種 history：沒有 feature payload 為 `0/1`；最新 accepted＋allMatched 為 `promotion_ready`、`1/1`；最新 accepted＋drift 為 `drift`、`0/1`。頁面刷新或舊記錄不得推進週期。

- [ ] **Step 2: 執行 RED**

Run: `.venv/bin/python -m pytest tests/test_stability_history_service.py tests/test_monthly_baseline_service.py -q`

Expected: FAIL，因 history migration 與 governance 尚未存在。

- [ ] **Step 3: 實作 migration 與 governance**

`_ensure_table` migrations 加入 `monthly_baseline_json`；寫入時使用 `context.get("monthly_baseline") or gate.get("monthlyBaseline")`；list payload 回傳 `monthlyBaseline`。

Governance 只接受最新 `uploadStatus == "accepted"`、`rollbackStatus == "not_required"` 且含 current registry version 的 history。Ready payload 包含 `stableUploadCycles`、`requiredStableUploadCycles`、`promotionReady`、`eligibleRecordId`、`eligibleCreatedAt`。

- [ ] **Step 4: 執行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_stability_history_service.py tests/test_monthly_baseline_service.py -q`

Expected: 全部 PASS。

---

### Task 3: Governed Gate 與 Upload Integration

**Files:**
- Modify: `backend/services/upload_preflight_service.py`
- Modify: `app_workflows.py`
- Modify: `app_pages.py`
- Modify: `tests/test_upload_preflight_service.py`
- Modify: `tests/test_streamlit_upload_feedback_contract.py`

**Interfaces:**
- Production gate alias：`build_governed_stability_gate`
- Stability history context：`monthly_baseline`

- [ ] **Step 1: 寫 RED tests**

Contract tests 斷言 preflight 與正式 upload 使用 governed gate；history context 保存 `stability_gate["monthlyBaseline"]`。Service test 斷言 monitoring drift 時 preflight status 仍由 May core gate控制；blocking drift 時 status 為 drift。

- [ ] **Step 2: 執行 RED**

Run: `.venv/bin/python -m pytest tests/test_upload_preflight_service.py tests/test_streamlit_upload_feedback_contract.py -q`

Expected: FAIL，因 production imports 仍使用舊 gate。

- [ ] **Step 3: 接入 governed gate**

`upload_preflight_service.py` 改用 `build_governed_stability_gate`。`app_workflows.py` 將該函式輸出為既有名稱 `build_phase2c_stability_gate`，避免 rendering layer 大改。`app_pages.py` 記錄 history 時加入：

```python
"monthly_baseline": stability_gate.get("monthlyBaseline") or {},
```

並把月度結果加入 `LAST_UPLOAD_AUDIT`，但 monitoring drift 不改既有 success/warning/error status。

- [ ] **Step 4: 執行 GREEN 與 rollback tests**

Run: `.venv/bin/python -m pytest tests/test_upload_preflight_service.py tests/test_streamlit_upload_feedback_contract.py tests/test_upload_rollback_service.py -q`

Expected: 全部 PASS，monitoring drift 不觸發 rollback。

---

### Task 4: Atomic Promotion 與 Audit History

**Files:**
- Extend: `backend/services/monthly_baseline_service.py`
- Extend: `tests/test_monthly_baseline_service.py`

**Interfaces:**
- `promote_monthly_baselines(*, confirmed: bool, expected_record_id: int, registry_path: Path | None = None) -> dict`
- `list_monthly_baseline_promotions(limit: int = 20) -> list[dict]`

- [ ] **Step 1: 寫 RED tests**

覆蓋：未勾確認拒絕；record ID stale 拒絕；current evaluation drift 拒絕；成功時只把2026-01、02、03、04、06改為 blocking、May保持 blocking；原子 backup 存在；promotion audit 保存 old/new modes、record ID、registry version 與 snapshot；失敗時 registry 不部分更新。

- [ ] **Step 2: 執行 RED**

Run: `.venv/bin/python -m pytest tests/test_monthly_baseline_service.py -q`

Expected: FAIL，因 promotion API 尚不存在。

- [ ] **Step 3: 實作 promotion transaction**

重新載入 governance 與 current evaluation；所有 guard 通過後，先建立 `*.backup_<timestamp>`，寫 temporary JSON，`os.replace` 原子替換，再重新載入驗證。SQLite table `monthly_baseline_promotion_history` 記錄事件；JSON 或 history 任一步失敗時恢復原 registry。

- [ ] **Step 4: 執行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_monthly_baseline_service.py -q`

Expected: 全部 PASS。

---

### Task 5: Streamlit Governance Panel

**Files:**
- Modify: `app_workflows.py`
- Modify: `app_pages.py`
- Modify: `tests/test_app_module_boundaries.py`
- Modify: `tests/test_streamlit_upload_feedback_contract.py`

**Interfaces:**
- `_render_monthly_baseline_governance() -> None`
- Uses: `evaluate_monthly_baselines`、`build_monthly_baseline_governance`、`promote_monthly_baselines`

- [ ] **Step 1: 寫 RED contract tests**

斷言 `_render_config_tab` 呼叫 `_render_monthly_baseline_governance`；面板包含 `Monthly Baseline Governance`、`Monitoring`、`Ready`、`Blocking`、`升級為阻擋式基準`、confirmation checkbox；button disabled 依 `promotionReady`；提交傳入 `eligibleRecordId` 並在成功後 rerun。

- [ ] **Step 2: 執行 RED**

Run: `.venv/bin/python -m pytest tests/test_app_module_boundaries.py tests/test_streamlit_upload_feedback_contract.py -q`

Expected: FAIL，因 panel 尚不存在。

- [ ] **Step 3: 實作面板**

面板置於「業務規則配置」最上方，表格欄位為月份、模式、顯示基準、目前金額、精確差額、狀態。Ready 時啟用按鈕；點擊後顯示 expander/dialog、影響說明與 checkbox。確認時呼叫 promotion service；不在 `app_pages.py` 計算正式金額。

- [ ] **Step 4: 執行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_app_module_boundaries.py tests/test_streamlit_upload_feedback_contract.py -q`

Expected: 全部 PASS。

---

### Task 6: Hermes Read-Only Governance Check

**Files:**
- Create: `scripts/monthly_baseline_check.py`
- Modify: `scripts/hermes_post_change_check.py`
- Create: `tests/test_monthly_baseline_check_cli.py`
- Modify: `tests/test_hermes_post_change_check.py`

**Interfaces:**
- CLI JSON fields：`status`、`registryVersion`、`scope`、`checks`、`stableUploadCycles`、`promotionReady`、`latestPromotion`
- Exit `1` only when `blockingStatus == "drift"`; monitoring drift exits `0`。

- [ ] **Step 1: 寫 RED tests**

CLI tests monkeypatch governance payload，斷言 monitoring drift exit 0、blocking drift exit 1。Hermes plan 必須包含 required `monthly-baseline-governance` step，targeted tests 加入 monthly service／CLI tests。

- [ ] **Step 2: 執行 RED**

Run: `.venv/bin/python -m pytest tests/test_monthly_baseline_check_cli.py tests/test_hermes_post_change_check.py -q`

Expected: FAIL，因 CLI 與 Hermes step 尚不存在。

- [ ] **Step 3: 實作 CLI 與 Hermes step**

CLI read-only 評估 registry、current SQLite、history 和 promotions；不寫 monitor/history。Hermes `build_check_plan` 在 phase2 baseline 後加入 CLI step，並在 Markdown evidence 顯示 monthly status。

- [ ] **Step 4: 執行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_monthly_baseline_check_cli.py tests/test_hermes_post_change_check.py -q`

Expected: 全部 PASS。

---

### Task 7: Full Verification And Git Checkpoint

**Files:**
- Verify all modified files
- Runtime read-only validation: `nbs_marketing_data.db`

- [ ] **Step 1: Compile 與 targeted suites**

Run:

```bash
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py pipeline.py database.py backend/services/monthly_baseline_service.py backend/services/stability_history_service.py backend/services/upload_preflight_service.py scripts/monthly_baseline_check.py scripts/hermes_post_change_check.py
.venv/bin/python -m pytest tests/test_monthly_baseline_service.py tests/test_monthly_baseline_check_cli.py tests/test_stability_history_service.py tests/test_upload_preflight_service.py tests/test_upload_rollback_service.py tests/test_app_module_boundaries.py tests/test_streamlit_upload_feedback_contract.py tests/test_hermes_post_change_check.py -q
```

Expected: compile exit 0，targeted suites 全部 PASS。

- [ ] **Step 2: Live registry check**

Run: `.venv/bin/python scripts/monthly_baseline_check.py`

Expected: 六個月 matched；2026-05 blocking；其餘 monitoring；stable cycle `0/1`，因尚未發生功能部署後的正式 upload。

- [ ] **Step 3: Full suite、services、acceptance、Hermes**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py stop
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --json
```

Expected: full pytest PASS；三服務 ready；acceptance passed；Hermes overallStatus pass。

- [ ] **Step 4: Commit**

```bash
git add data/monthly_revenue_baselines.json backend/services/monthly_baseline_service.py backend/services/stability_history_service.py backend/services/upload_preflight_service.py app_workflows.py app_pages.py scripts/monthly_baseline_check.py scripts/hermes_post_change_check.py tests/test_monthly_baseline_service.py tests/test_monthly_baseline_check_cli.py tests/test_stability_history_service.py tests/test_upload_preflight_service.py tests/test_app_module_boundaries.py tests/test_streamlit_upload_feedback_contract.py tests/test_hermes_post_change_check.py
git commit -m "feat: add monthly baseline governance"
```

Expected: commit 成功，工作樹乾淨。
