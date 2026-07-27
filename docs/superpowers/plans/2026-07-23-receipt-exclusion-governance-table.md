# Receipt Exclusion Governance Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把收款單永久排除治理頁由逐條按鈕改成可捲動的單選規則表格，並保留安全的撤銷預演與確認 gate。

**Architecture:** 在 `receipt_exclusion_rendering.py` 以 Streamlit 原生 `st.data_editor` 顯示 active registry 的唯讀 identity 欄與唯一可編輯的「選取」欄。Rendering layer 將選取的 rule ID、read model 的 registry revision 與 service 回傳的 preview fingerprint 綁定在 bounded session state；既有 `preview_revoke` 和 `confirm_revoke` callbacks 不變，仍由治理 service 執行暫存 SQLite 重播與 baseline 驗收。

**Tech Stack:** Python 3、pandas、Streamlit >= 1.28、pytest、既有 receipt exclusion registry/read model/governance service。

## Global Constraints

- 正式口徑固定為：`不含掛賬核銷與TT退款轉團款`。
- `2026-05` frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite registry schema、quarantine evidence、正式 facts、baseline、upload、rollback、revenue scope 或 export 計算。
- 不新增批量撤銷、重新啟用、刪除規則或跳過預演的入口。
- UI 只把 active rule 的 integer ID 與 preview fingerprint 交給既有 callbacks；不得把表格欄位資料作為正式寫入依據。
- 顯示資料不得包含 raw quarantine payload、evidence hash、完整 proposal fingerprint 或 created operation ID。
- Active 表格固定高度為 `320`，可垂直／水平捲動；已撤銷規則放在唯讀 expander。
- 一次只能選取一條 active 規則；選取變更、registry revision 改變或 preview 不匹配時，確認按鈕必須 fail closed。
- 本計畫只可修改 `receipt_exclusion_rendering.py` 與 `tests/test_receipt_exclusion_rendering.py`。
- 目前工作樹已有這兩個檔案的未提交 receipt-exclusion 變更；每個 Task commit 必須先以 `git diff` 核對，再用 `git add -p` 只 stage 本 Task 新增 hunk。不得用整檔 `git add` 把既有變更混入。

---

## File Structure

- `receipt_exclusion_rendering.py`
  - 將 active/revoked read model rows 轉為 allowlisted UI table rows。
  - 提供單選驗證與 preview session-state 匹配判斷。
  - 將 `render_receipt_exclusion_governance` 改為單一表格與共用 preview/confirm 操作。
  - 保留 `render_receipt_exclusion_confirmation` 的 upload proposal UI，不修改其行為。
- `tests/test_receipt_exclusion_rendering.py`
  - 擴充 Streamlit fake，以可驗證 `data_editor`、expander、spinner、error 及按鈕 click state。
  - 覆蓋欄位 allowlist、單選、stale preview、confirm eligibility、revoked table 與敏感資料不外洩。

## Shared Interfaces

既有 read model 會傳入：

```python
snapshot = {
    "registryRevision": "revision-token",
    "active": [{
        "id": 4,
        "receiptNo": "SK2607007622",
        "sourceOrderNo": "225YTLAU6227154715",
        "exclusionKind": "receipt_type:掛賬核銷",
        "createdAt": "2026-07-23T00:00:00+08:00",
        "createdBy": "streamlit-local",
        "eventCount": 2,
    }],
    "revoked": [],
}
```

既有 callbacks 保持以下 signature：

```python
preview_revoke: Callable[[int], dict]
confirm_revoke: Callable[[int, str], dict]
```

預演成功必須至少提供：

```python
{
    "ruleId": 4,
    "registryRevision": "revision-token",
    "status": "revocation_ready",
    "previewFingerprint": "sha256",
    "gate": {"status": "matched"},
}
```

### Task 1: 建立治理表格的純資料與 Preview State Guard

**Files:**

- Modify: `receipt_exclusion_rendering.py:1-80`
- Test: `tests/test_receipt_exclusion_rendering.py:1-94`

**Interfaces:**

- Consumes: active/revoked read model rows，以及上列 existing preview contract。
- Produces:
  - `GOVERNANCE_TABLE_HEIGHT: int = 320`
  - `_governance_rows(rules: list[dict], *, revoked: bool = False) -> pd.DataFrame`
  - `_selected_rule_ids(edited: pd.DataFrame) -> list[int]`
  - `_matching_governance_preview(preview: dict, *, rule_id: int | None, registry_revision: str) -> dict`
  - `GOVERNANCE_PREVIEW_STATE_KEY: str`

- [ ] **Step 1: 擴充 fake Streamlit 並寫入 failing tests**

在 `tests/test_receipt_exclusion_rendering.py` 的 `_FakeStreamlit` 增加最小可觀測 API：

```python
class _FakeExpander:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FakeSpinner(_FakeExpander):
    pass


class _FakeStreamlit:
    def __init__(self):
        self.data_editor_result = None
        self.data_editor_kwargs = {}
        self.data_editor_value = None
        self.errors = []
        self.expanders = []
        self.spinners = []
        # 保留既有 checkbox/buttons/session_state 欄位。

    def data_editor(self, value, **kwargs):
        self.data_editor_value = value.copy()
        self.data_editor_kwargs = kwargs
        return self.data_editor_result.copy() if self.data_editor_result is not None else value.copy()

    def error(self, value):
        self.errors.append(value)

    def expander(self, label, **kwargs):
        self.expanders.append((label, kwargs))
        return _FakeExpander()

    def spinner(self, value):
        self.spinners.append(value)
        return _FakeSpinner()
```

新增以下失敗測試：

```python
def test_governance_rows_allowlist_identity_and_hide_sensitive_fields():
    rows = rendering._governance_rows([{
        "id": 4,
        "receiptNo": "SK2607007622",
        "sourceOrderNo": "225YTLAU6227154715",
        "exclusionKind": "receipt_type:掛賬核銷",
        "createdAt": "2026-07-23T00:00:00+08:00",
        "createdBy": "streamlit-local",
        "eventCount": 2,
        "evidenceHash": "must-not-render",
        "proposalFingerprint": "must-not-render",
        "createdOperationId": "must-not-render",
    }])

    assert rows.columns.tolist() == [
        "選取", "規則 ID", "收款單號", "來源單據號", "排除類型",
        "建立時間", "建立者", "稽核事件數",
    ]
    assert rows.loc[0, "選取"] == False
    assert "must-not-render" not in rows.to_string()


def test_selected_rule_ids_preserves_no_selection_one_selection_and_multi_selection():
    edited = pd.DataFrame({"選取": [False, True, True], "規則 ID": [1, 2, 3]})

    assert rendering._selected_rule_ids(edited.iloc[0:0]) == []
    assert rendering._selected_rule_ids(edited.iloc[[1]]) == [2]
    assert rendering._selected_rule_ids(edited) == [2, 3]


def test_matching_preview_requires_same_rule_and_registry_revision():
    preview = {
        "ruleId": 4,
        "registryRevision": "revision-a",
        "status": "revocation_ready",
        "previewFingerprint": "preview-a",
    }

    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="revision-a",
    ) == preview
    assert rendering._matching_governance_preview(
        preview, rule_id=5, registry_revision="revision-a",
    ) == {}
    assert rendering._matching_governance_preview(
        preview, rule_id=4, registry_revision="revision-b",
    ) == {}
```

- [ ] **Step 2: 執行 focused tests，確認 RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

Expected: FAIL，原因是 `_governance_rows`、`_selected_rule_ids` 與
`_matching_governance_preview` 尚不存在。

- [ ] **Step 3: 實作最小 pure helpers**

在 `receipt_exclusion_rendering.py` 的常數區新增：

```python
GOVERNANCE_TABLE_HEIGHT = 320
GOVERNANCE_PREVIEW_STATE_KEY = "RECEIPT_EXCLUSION_GOVERNANCE_PREVIEW"


def _governance_rows(rules: list[dict], *, revoked: bool = False) -> pd.DataFrame:
    rows = []
    for rule in rules:
        row = {
            "規則 ID": int(rule["id"]),
            "收款單號": str(rule.get("receiptNo") or ""),
            "來源單據號": str(rule.get("sourceOrderNo") or ""),
            "排除類型": str(rule.get("exclusionKind") or ""),
            "稽核事件數": int(rule.get("eventCount") or 0),
        }
        if revoked:
            row.update({
                "撤銷時間": str(rule.get("revokedAt") or ""),
                "撤銷者": str(rule.get("revokedBy") or ""),
            })
        else:
            row = {"選取": False, **row}
            row.update({
                "建立時間": str(rule.get("createdAt") or ""),
                "建立者": str(rule.get("createdBy") or ""),
            })
        rows.append(row)
    return pd.DataFrame(rows)


def _selected_rule_ids(edited: pd.DataFrame) -> list[int]:
    if edited.empty or "選取" not in edited.columns or "規則 ID" not in edited.columns:
        return []
    selected = edited.loc[edited["選取"].astype(bool), "規則 ID"].tolist()
    return [int(rule_id) for rule_id in selected]


def _matching_governance_preview(
    preview: dict,
    *,
    rule_id: int | None,
    registry_revision: str,
) -> dict:
    if (
        rule_id is None
        or preview.get("status") != "revocation_ready"
        or int(preview.get("ruleId") or -1) != int(rule_id)
        or str(preview.get("registryRevision") or "") != str(registry_revision)
        or not str(preview.get("previewFingerprint") or "")
    ):
        return {}
    return preview
```

修正上一段 `_governance_rows` 的 active 欄位順序，讓回傳 DataFrame 依 spec 固定為：

```python
ACTIVE_GOVERNANCE_COLUMNS = [
    "選取", "規則 ID", "收款單號", "來源單據號", "排除類型",
    "建立時間", "建立者", "稽核事件數",
]
REVOKED_GOVERNANCE_COLUMNS = [
    "規則 ID", "收款單號", "來源單據號", "排除類型",
    "撤銷時間", "撤銷者", "稽核事件數",
]

# return pd.DataFrame(rows, columns=REVOKED_GOVERNANCE_COLUMNS if revoked else ACTIVE_GOVERNANCE_COLUMNS)
```

- [ ] **Step 4: 執行 focused tests，確認 GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

Expected: PASS；既有 confirmation tests 也必須維持通過。

- [ ] **Step 5: Review Task 1 與提交**

檢查：

```bash
git diff --check
git diff -- receipt_exclusion_rendering.py tests/test_receipt_exclusion_rendering.py
```

Review checklist：

- helper 不讀取或寫入 SQLite；
- UI allowlist 不包含敏感 evidence 欄位；
- 同一 rule ID 的 `0` 值不被誤判；
- preview 缺少任一安全欄位時回傳空 dict。

Commit:

```bash
git add -p receipt_exclusion_rendering.py tests/test_receipt_exclusion_rendering.py
git diff --cached --check
git commit -m "feat: add receipt exclusion governance selection state"
```

### Task 2: 將治理畫面替換為可捲動單選表格

**Files:**

- Modify: `receipt_exclusion_rendering.py:54-80`
- Modify: `tests/test_receipt_exclusion_rendering.py:87-94`

**Interfaces:**

- Consumes: Task 1 的 helper；`snapshot["registryRevision"]`；既有 `preview_revoke` 與 `confirm_revoke` callbacks。
- Produces: `render_receipt_exclusion_governance(...) -> None` 的 table-based Streamlit UI。

- [ ] **Step 1: 寫入 failing renderer tests**

加入 fixture：

```python
def _snapshot(*, revision="revision-a", active=None, revoked=None):
    return {
        "registryRevision": revision,
        "active": active or [{
            "id": 4,
            "receiptNo": "SK2607007622",
            "sourceOrderNo": "225YTLAU6227154715",
            "exclusionKind": "receipt_type:掛賬核銷",
            "createdAt": "2026-07-23T00:00:00+08:00",
            "createdBy": "streamlit-local",
            "eventCount": 2,
        }],
        "revoked": revoked or [],
    }
```

新增以下測試：

```python
def test_governance_renders_scrollable_single_selection_table(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(), preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert fake.data_editor_kwargs["height"] == rendering.GOVERNANCE_TABLE_HEIGHT
    assert fake.data_editor_kwargs["num_rows"] == "fixed"
    assert fake.data_editor_kwargs["disabled"] == [
        "規則 ID", "收款單號", "來源單據號", "排除類型",
        "建立時間", "建立者", "稽核事件數",
    ]
    assert fake.data_editor_kwargs["key"].endswith("revision-a")
    assert "預覽撤銷 #4" not in fake.buttons
    assert fake.buttons["預覽撤銷所選規則"]["disabled"] is True


def test_governance_previews_only_the_single_selected_rule(monkeypatch):
    fake = _FakeStreamlit()
    fake.data_editor_result = pd.DataFrame([{
        "選取": True, "規則 ID": 4, "收款單號": "SK2607007622",
        "來源單據號": "225YTLAU6227154715", "排除類型": "receipt_type:掛賬核銷",
        "建立時間": "", "建立者": "streamlit-local", "稽核事件數": 2,
    }])
    fake.button_clicks = {"預覽撤銷所選規則"}
    calls = []
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(),
        preview_revoke=lambda rule_id: calls.append(rule_id) or {
            "ruleId": rule_id, "registryRevision": "revision-a",
            "status": "revocation_ready", "previewFingerprint": "preview-a", "gate": {"status": "matched"},
        },
        confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert calls == [4]
    assert fake.session_state[rendering.GOVERNANCE_PREVIEW_STATE_KEY]["ruleId"] == 4


def test_governance_disables_confirm_for_stale_selection_or_revision(monkeypatch):
    fake = _FakeStreamlit()
    fake.data_editor_result = pd.DataFrame([{
        "選取": True, "規則 ID": 4, "收款單號": "SK2607007622",
        "來源單據號": "225YTLAU6227154715", "排除類型": "receipt_type:掛賬核銷",
        "建立時間": "", "建立者": "streamlit-local", "稽核事件數": 2,
    }])
    fake.session_state[rendering.GOVERNANCE_PREVIEW_STATE_KEY] = {
        "ruleId": 4, "registryRevision": "old-revision",
        "status": "revocation_ready", "previewFingerprint": "preview-a",
    }
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(revision="revision-a"), preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert fake.buttons["確認撤銷所選規則"]["disabled"] is True
    assert rendering.GOVERNANCE_PREVIEW_STATE_KEY not in fake.session_state


def test_governance_rejects_multi_selection_and_shows_revoked_rules_in_expander(monkeypatch):
    fake = _FakeStreamlit()
    fake.data_editor_result = pd.DataFrame([
        {"選取": True, "規則 ID": 4},
        {"選取": True, "規則 ID": 5},
    ])
    monkeypatch.setattr(rendering, "st", fake)

    rendering.render_receipt_exclusion_governance(
        _snapshot(revoked=[{
            "id": 1, "receiptNo": "SK2606005393", "sourceOrderNo": "31NZY6629115617",
            "exclusionKind": "payment_method:TT 退款轉團款", "revokedAt": "", "revokedBy": "", "eventCount": 3,
        }]),
        preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )

    assert fake.errors == ["一次只能選取一條永久排除規則。"]
    assert fake.buttons["預覽撤銷所選規則"]["disabled"] is True
    assert fake.expanders == [("查看已撤銷規則", {"expanded": False})]
```

將 `_FakeStreamlit.button` 調整為支援 click state：

```python
def button(self, label, **kwargs):
    self.buttons[label] = kwargs
    return label in getattr(self, "button_clicks", set())
```

- [ ] **Step 2: 執行 focused tests，確認 RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

Expected: FAIL，因目前治理畫面仍使用 `st.dataframe` 與每條規則一個按鈕，沒有
`data_editor`、選擇／revision 綁定或 revoked expander。

- [ ] **Step 3: 以最小方式替換治理 renderer**

以以下控制流程取代既有 `render_receipt_exclusion_governance` 內的 `for rule in active`：

```python
def render_receipt_exclusion_governance(
    snapshot: dict,
    *,
    preview_revoke: Callable[[int], dict],
    confirm_revoke: Callable[[int, str], dict],
) -> None:
    active = list(snapshot.get("active") or [])
    revoked = list(snapshot.get("revoked") or [])
    registry_revision = str(snapshot.get("registryRevision") or "unknown")
    revision_token = registry_revision[:20]
    st.markdown("### Receipt Exclusion Governance")
    st.caption("永久排除規則只影響精確 identity；撤銷前必須在暫存資料庫重播並通過口徑驗收。")

    if not active:
        st.info("目前沒有生效中的永久排除規則。")
    else:
        edited = st.data_editor(
            _governance_rows(active),
            key=f"RECEIPT_EXCLUSION_GOVERNANCE_EDITOR_{revision_token}",
            hide_index=True,
            width="stretch",
            height=GOVERNANCE_TABLE_HEIGHT,
            num_rows="fixed",
            disabled=[column for column in ACTIVE_GOVERNANCE_COLUMNS if column != "選取"],
            column_config={
                "選取": st.column_config.CheckboxColumn("選取", help="一次只能預覽與撤銷一條規則。"),
            },
        )
        selected_rule_ids = _selected_rule_ids(edited)
        selected_rule_id = selected_rule_ids[0] if len(selected_rule_ids) == 1 else None
        if len(selected_rule_ids) > 1:
            st.error("一次只能選取一條永久排除規則。")

        stored_preview = st.session_state.get(GOVERNANCE_PREVIEW_STATE_KEY) or {}
        preview = _matching_governance_preview(
            stored_preview,
            rule_id=selected_rule_id,
            registry_revision=registry_revision,
        )
        if stored_preview and not preview:
            st.session_state.pop(GOVERNANCE_PREVIEW_STATE_KEY, None)

        if st.button(
            "預覽撤銷所選規則",
            disabled=selected_rule_id is None,
            key=f"RECEIPT_EXCLUSION_GOVERNANCE_PREVIEW_{revision_token}",
        ):
            with st.spinner("正在預演撤銷"):
                candidate_preview = preview_revoke(selected_rule_id)
            preview = _matching_governance_preview(
                candidate_preview,
                rule_id=selected_rule_id,
                registry_revision=registry_revision,
            )
            if preview:
                st.session_state[GOVERNANCE_PREVIEW_STATE_KEY] = preview
            else:
                st.session_state.pop(GOVERNANCE_PREVIEW_STATE_KEY, None)
                st.error("撤銷預演結果已失效或未通過口徑驗收。")

        if preview:
            selected_rule = next(rule for rule in active if int(rule["id"]) == selected_rule_id)
            st.write({
                "規則 ID": selected_rule_id,
                "收款單號": selected_rule.get("receiptNo"),
                "來源單據號": selected_rule.get("sourceOrderNo"),
                "排除類型": selected_rule.get("exclusionKind"),
                "預演狀態": preview.get("status"),
                "Gate": preview.get("gate"),
            })
            if st.button(
                "確認撤銷所選規則",
                type="primary",
                key=f"RECEIPT_EXCLUSION_GOVERNANCE_CONFIRM_{revision_token}",
            ):
                with st.spinner("正在確認撤銷"):
                    confirm_revoke(selected_rule_id, str(preview["previewFingerprint"]))

    if revoked:
        with st.expander("查看已撤銷規則", expanded=False):
            st.dataframe(
                _governance_rows(revoked, revoked=True),
                hide_index=True,
                width="stretch",
                height=GOVERNANCE_TABLE_HEIGHT,
            )
```

在實作中，確認按鈕必須明確傳入：

```python
disabled=not bool(preview)
```

此處不能只因 preview 變數存在就省略 disabled parameter。`preview` 一律由
`_matching_governance_preview` 取得，故 rule、revision、status 與 fingerprint 任一
不匹配時必為空 dict。

- [ ] **Step 4: 執行 rendering tests，確認 GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

Expected: PASS；測試必須證明沒有 `預覽撤銷 #N` 形式的按鈕、只顯示 allowlisted
欄位、multi-select fail closed，並且 stale preview 不會啟用確認。

- [ ] **Step 5: 執行 regression 與編譯驗收**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_receipt_exclusion_rendering.py \
  tests/test_receipt_exclusion_read_model_service.py \
  tests/test_receipt_exclusion_governance_service.py -q
.venv/bin/python -m py_compile receipt_exclusion_rendering.py app_pages.py
```

Expected: 所有測試 PASS，`py_compile` 無輸出且 exit code 為 0。

- [ ] **Step 6: Review Task 2 與提交**

檢查：

```bash
git diff --check
git diff -- receipt_exclusion_rendering.py tests/test_receipt_exclusion_rendering.py
```

Review checklist：

- active table 只有「選取」欄可編輯；
- 任何 selection／revision mismatch 都會清除舊 preview；
- confirm 只會傳 selected rule ID 與其 `previewFingerprint`；
- preview/confirm callbacks 的 upload lease 和 SQLite 寫入邊界不被改動；
- revoked table 是唯讀，沒有 re-enable 或 delete 操作；
- UI 不輸出 quarantine payload 或敏感 hash。

Commit:

```bash
git add -p receipt_exclusion_rendering.py tests/test_receipt_exclusion_rendering.py
git diff --cached --check
git commit -m "feat: render receipt exclusion governance table"
```

## Final Verification

- [ ] 在乾淨或隔離 worktree 執行 Task 2 的三組 targeted tests 與 `py_compile`。
- [ ] 執行 `.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json` 做 read-only post-change inspection；本次雖是 rendering-only，仍確認沒有非預期 SQLite/baseline/runtime 變更。
- [ ] 手動驗收 Streamlit 的「業務規則配置」頁：active rules 超過可視高度時可捲動；一次勾選兩條時不可預演；選取一條後可 preview；變更選取後舊 confirm 立即失效；revoked 規則可在 expander 查閱。
- [ ] 不需要重新上傳真實 Excel、不需要跑正式 upsert、也不需要改動 baseline。若手動驗收出現 callback contract 不足，停止實作並回到新的 plan，而不是擴大這個 renderer-only scope。
