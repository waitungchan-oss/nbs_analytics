# NBS Hermes Sidecar Activation Integration：Design Spec

## Status

- Status: approved bounded integration task
- Date: 2026-08-10
- Scope: standalone Hermes `MemoryProvider` bridge for one explicitly activated runner session

## Goal

讓 Hermes 在明確提供 activation receipt、immutable manifest 與 bounded hints source 時，於單一受控 session 實際執行 `recall_on`；普通 Hermes session 與 NBS 普通開發流程維持 recall-off。

## Architecture

新增 project-local standalone plugin source `integrations/hermes_nbs_sidecar/`，遵循 Hermes `MemoryProvider` ABC。Plugin 只在所有條件成立時 `is_available()`：

- activation manifest 的 immutable HEAD、workspace/task fingerprints 與目前 repository 一致；
- activation envelope 綁定單一 Hermes `sessionId`、`projectId` 與 `workspaceKind`，workspace fingerprint 由目前 project root 重算；
- model/provider/reasoning 為 `deepseek-v4-flash`／`hermes`／`medium`；
- activation receipt 是 canonical-bound，且 `recallMode=on`；
- hints source 位於 `.nbs_agent_runtime`、符合 `memory-hints-v1`、bounded、fresh、無敏感資料。

Hermes `prefetch()` 只讀取 bounded hints 並注入 non-authoritative context；`sync_turn()` 永遠 no-op，writer 永遠 disabled。Plugin 不呼叫 network、SQLite、Git write、approval、dispatch 或 workflow control。

## Explicit activation

Plugin discovery/config 不會自動開啟。受控 runner 必須以一個 per-run plugin config／environment envelope 明確指定 manifest、activation receipt 與 hints file；缺任何一項就 `is_available=False`。activation envelope 不寫入 canonical artifacts 或正式 SQLite，僅保存於 ignored `.nbs_agent_runtime`。

## Data boundary

只允許 `memory-hints-v1` 的 sourceRefs、memoryId、kind、freshness、confidence 與 bounded summary；不保存 raw prompt、raw model output、credentials、customer data 或 full logs。所有注入內容標記 `non_authoritative_memory`，不得進入 Graph authority、Review evidence 或 baseline。

## Acceptance

- Plugin disabled by default and ordinary Hermes session cannot activate it.
- Valid one-run envelope activates `recall_on` only for the matching immutable HEAD/session.
- Manifest/receipt/head/model/reasoning/workspace mismatch fail closed。
- Stale、malformed、over-cap、sensitive hints return empty recall and bounded diagnostic status。
- `sync_turn()` never writes memory；writer remains disabled。
- Unit tests prove plugin lifecycle and negative paths; live Hermes session reports actual provider activation and bounded hints provenance.

## Rollback

Remove the per-run envelope or set provider unset; no source rollback or production flag change is required. NBS defaults remain `recall_enabled=false`, `writer_enabled=false`, `shadow_mode=true`.
