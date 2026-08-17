# Context Agent × Memory Hub Task 1 Brief

建立 read-only Context Memory Hub adapter，使用 deployment-owned C2 composition
查詢 bounded governance/evidence/skill memory，並投影到既有 non-authoritative
`memoryHints`。缺少 catalog/policy composition、identity、freshness 或 schema
驗證時必須 fail closed；不得寫入 SQLite、baseline、catalog、runtime source 或 Git。
