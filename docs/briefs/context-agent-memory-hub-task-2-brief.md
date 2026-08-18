# Context Agent × Memory Hub Task 2 Brief

在 `collect-only` path 接入 Task 1 adapter。每次使用固定 `context-agent` identity
與 bounded project query；Memory Hub 只作 non-authoritative `memoryHints`，canonical
EvidenceCollector 與 `bundleFingerprint` 不變。任何 blocked、empty、degraded 或
provider unavailable 都回到 canonical-only context。
