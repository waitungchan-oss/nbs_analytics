# NBS Memory Hub Deployment-owned Catalog Provider Design

## 目的

將現有 Streamlit Memory Hub tab 從固定 `None` provider，提升為可受控載入 deployment-owned immutable catalog。這只配置讀取邊界，不建立 catalog artifact，也不改變 canonical authority。

## 核心邊界

- Provider 只讀固定 allowlist：`agent_config/memory_hub_catalog_deployment.json`。
- Source root 固定為 project root 下的 `docs/memory_hub_sources`；runtime catalog root 固定為 `.nbs_agent_runtime/memory-hub`。
- Manifest 只描述已批准 source／record identity、`builtFromHead` 與 `policyFingerprint`；不接受 absolute path、`..`、symlink、secret 或未知欄位。
- Provider 只呼叫既有 `load_catalog()`；禁止呼叫 `build_catalog()`、repository scan、SQLite、Git command、network 或 Streamlit write。
- Manifest 或 catalog 缺失時回傳 `None`，UI 顯示 `catalog_missing`；驗證失敗時 fail-closed 為 `invalid_catalog`。
- Catalog ready 前不啟用 recall、writer、approval、dispatch 或任何 Memory Sidecar／Short-term Offload control。

## Manifest contract

Exact top-level keys：`schemaVersion`, `sourceRoot`, `runtimeRoot`, `catalogFile`, `builtFromHead`, `policyFingerprint`, `sources`, `records`, `manifestFingerprint`。

`schemaVersion` 固定為 `memory-hub-deployment-provider-v1`；`sourceRoot`, `runtimeRoot`, `catalogFile` 固定為上述 relative values；`sources` 與 `records` 直接使用 C-0/C-1 exact envelopes；`manifestFingerprint` 由其餘欄位重新計算，不信任 caller supplied identity。

## 驗收

- valid manifest + valid immutable catalog 可被 provider 載入並交給既有 UI adapter。
- 缺 manifest／catalog 保持 `catalog_missing`。
- tampered manifest、catalog fingerprint、source hash、path traversal、symlink、unknown key 都回傳 `invalid_catalog`，不建立或覆蓋檔案。
- existing Memory Hub、Governance Graph、Agent Operations、SQLite、baseline、export 與 recall defaults 回歸測試保持通過。
