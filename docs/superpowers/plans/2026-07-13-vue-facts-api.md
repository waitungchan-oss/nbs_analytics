# Vue Facts API Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 讓 Vue read-only cockpit 消費 `/api/dashboard/facts`，顯示 Facts provenance/status，並在 upload 後刷新 generation，同時保留現有 filtered API。

**Architecture:** `frontend/src/lib/api.js` 提供單一 `getDashboardFacts()` client；`App.vue` 保存 `facts` reactive state，在 `loadAll()` 與 upload success 後載入；API Status 增加 unfiltered Facts source panel。現有 `summary` / `analytics` 仍負責篩選後畫面。

**Tech Stack:** Vue 3 Composition API, native fetch, Vite, Node static contract verification。

## Global Constraints

- Vue 只能展示 Python API 回傳結果，不自行計算正式 revenue、ranking、baseline 或 reconciliation。
- Facts API 只供 provenance/status 與全域 read model；篩選仍使用 `/summary`、`/analytics`。
- 不修改 FastAPI、SQLite、upload write path、Streamlit、正式口徑或 baseline。
- Facts 失敗不可被 summary/analytics 成功結果冒充；需顯示 Facts source unavailable。

---

### Task 1: API client 與 Facts state contract

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

**Interfaces:**
- Add `getDashboardFacts()` returning `requestJson('/api/dashboard/facts')`.
- Static verifier must require the endpoint client and reject no client-side Facts recomputation.

- [ ] **Step 1: Add failing contract assertions**

Add these required tokens/checks to `verify-cockpit-contract.mjs`:

```js
if (!api.includes('/api/dashboard/facts') || !api.includes('getDashboardFacts')) {
  throw new Error('Vue must consume the Dashboard Facts API.')
}
for (const token of ['getDashboardFacts', 'facts.value?.generationToken', 'facts.value?.factsCacheStatus', 'Facts Source']) {
  if (!app.includes(token)) throw new Error(`App.vue is missing Facts consumer token: ${token}`)
}
```

- [ ] **Step 2: Run verifier to confirm red**

Run: `npm run verify` from `frontend/`
Expected: FAIL because the new API client and App.vue tokens do not yet exist.

- [ ] **Step 3: Implement API client**

Add:

```js
export function getDashboardFacts() {
  return requestJson('/api/dashboard/facts')
}
```

- [ ] **Step 4: Run verifier**

Run: `npm run verify`
Expected: still fails only on missing App.vue Facts consumer tokens.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.js frontend/scripts/verify-cockpit-contract.mjs
git commit -m "feat: add vue dashboard facts client"
```

### Task 2: Vue load/upload refresh 與 Facts Source panel

**Files:**
- Modify: `frontend/src/App.vue`

**Interfaces:**
- Add `const facts = ref(null)` and `const factsError = ref('')`.
- `loadAll()` calls `getDashboardFacts()` with the existing initial read requests.
- `submitVueUpload()` calls `loadAll(false)` which refreshes Facts after accepted upload.
- Add computed `factsSourceStatus` without arithmetic; it reads fields directly from `facts`.

- [ ] **Step 1: Add failing UI contract tokens**

The verifier assertions from Task 1 must fail until App.vue contains:

```js
const facts = ref(null)
const factsError = ref('')
getDashboardFacts()
facts.value?.generationToken
facts.value?.factsCacheStatus
facts.value?.reconciliation?.status
```

- [ ] **Step 2: Run verifier to confirm red**

Run: `npm run verify`
Expected: FAIL on missing App.vue Facts tokens.

- [ ] **Step 3: Implement load and error handling**

Import `getDashboardFacts`; add Facts request to the initial `Promise.all`. Store its result separately. If only Facts fails, keep existing dashboard payloads and set `factsError`; the Facts panel shows unavailable status. Do not derive any revenue value in Vue.

- [ ] **Step 4: Implement API Status panel**

Add a panel with literal `Facts Source` and direct field bindings for service version, generation token, cache status, reconciliation status, combinedRevenue via existing `moneyText`, and factsError. Do not use `.reduce`, `+`, or ranking calculations on Facts fields.

- [ ] **Step 5: Run frontend verification**

Run `npm run verify` and `npm run build` from `frontend/`. Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.vue
git commit -m "feat: surface dashboard facts in vue cockpit"
```

### Task 3: Full cross-layer regression and runtime verification

**Files:**
- Modify: none unless verification exposes a regression.

- [ ] **Step 1: Run frontend checks**

Run `npm run verify` and `npm run build` in `frontend/`.

- [ ] **Step 2: Run Python regression**

Run `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest -q`. Expected: zero failures.

- [ ] **Step 3: Run service acceptance**

Run `scripts/system_manager.py acceptance`; verify Streamlit, FastAPI, and Vue are ready. Request `/api/dashboard/facts` and verify HTTP 200, formal scope, generation token, and reconciliation matched.

- [ ] **Step 4: Run Hermes inspection**

Run `scripts/hermes_post_change_check.py --json`; require PASS, SQLite integrity ok, generation signature matched, monthly baseline blockingStatus matched, and issues empty.

- [ ] **Step 5: Final clean state**

Run `git status --short --branch` and `git log -3 --oneline`; report commit and test evidence before offering merge back to `main`.
