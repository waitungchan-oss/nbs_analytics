# Cold Rebuild Reference 與 Equivalence 加速設計 Spec

## 1. 文件狀態

- 日期：2026-08-27
- 狀態：Proposed implementation design
- 專案：NBS Analytics
- 範圍：Data Export Center cold rebuild、trusted reference、semantic equivalence、export cache
- 不包含：SQLite schema、正式業務資料、baseline、revenue scope、GMV 退款資料流或 workbook schema 變更

## 2. 背景與證據

目前 Data Export fast path 已完成 shared facts、bounded serializer、manifest/package verification 與 Data Export UI 接線。正式 SQLite-shaped benchmark（2026-08-27，1 sample、2 workers）顯示：

| stage | observed time |
|---|---:|
| legacy reference build | 73,317 ms |
| legacy measured serialization | 70,438 ms |
| fast serializer wall time | 68,999 ms |
| equivalence validation | 82,687 ms |
| package build | 976 ms |
| fast cold total | 230,354 ms |

目前主要問題不是 ZIP 或下載，而是每次新 job 都重新建立 legacy reference workbook，並把三份 XLSX 完整讀回後逐格比較。這會令「已經驗證過的同一 generation」重複支付高昂 validation 成本。

## 3. 目標

1. 對相同 `source_fingerprint + generation_token + rules_fingerprint + export_schema_version`，重用已驗證的 trusted reference snapshot。
2. Equivalence 先執行 bounded schema、row-count、metric digest 檢查；digest 完全一致時跳過逐格 deep diff。
3. 新 generation、規則變更、schema 變更或 snapshot 不可信時，仍執行 legacy reference，重新建立 trusted snapshot。
4. 以 atomic active pointer／manifest swap 發布 reference snapshot，避免半成品被讀取。
5. 所有 fast READY 結果仍必須通過 semantic correctness gate；不能以 cache hit 取代 source identity、scope、baseline 或 checksum 驗證。
6. 量測 `reference_ms`、`equivalence_digest_ms`、`equivalence_deep_diff_ms`、`cache_hit_ms`、`total_ms` 與 peak RSS，讓效能改善可被驗證。

## 4. 硬邊界

- 正式營收口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite、SQLite schema、baseline、rollback、upload、業務規則或正式 dashboard 數值。
- 不修改既有三個 export artifact 的檔名、sheet、欄位順序、排序、數值或 workbook schema。
- Trusted reference 是衍生驗證 cache，不是 SQLite canonical data；不得回寫 SQLite。
- Snapshot 不得保存原始 Excel、SQLite、customer/payment raw data、secrets 或完整 runtime logs。
- Memory Hub 與 local agents 只提供 read-only context/hints，不得作為 reference、equivalence、rollout 或 business truth authority。
- legacy `_compute_export_workbooks()` 永遠保留為首次 materialization 與 fail-closed fallback。

## 5. 設計選項與決策

### Option A：每次都重跑 legacy reference

正確性最直觀，但每次 cold rebuild 都支付約 73 秒 reference 成本；不採用作為長期方案，只保留為 fallback。

### Option B：只保存 workbook binary，重用 XLSX 做 equivalence

可省掉 legacy builder，但每次仍需讀取並 canonicalize 大型 XLSX；而且 binary cache 的 provenance、scope 與 source identity 不夠明確。只作 transitional fallback，不作核心 contract。

### Option C：validated trusted-reference snapshot + digest-first equivalence（採用）

首次 job 由 legacy path 產生 reference canonical snapshot，與 source/rules/schema identity 一起 atomic publish。後續 identity 完全相同時重用 snapshot；candidate 先通過 digest gate，只有 digest 不一致才做 bounded deep diff。若任何 trust gate 失敗，重新 materialize legacy reference 並保留上一個 active snapshot。

## 6. 目標資料流

```text
raw frames / affected-only result
        |
        v
source + generation + rules + schema identity
        |
        +--> trusted-reference pointer lookup
        |       |
        |       +--> valid snapshot: load canonical reference
        |       +--> miss/stale/corrupt: legacy reference materialization
        |
        v
shared facts -> bounded serializers -> candidate XLSX artifacts
        |
        v
digest-first equivalence
        |
        +--> digest PASS: bounded PASS report
        +--> digest mismatch: canonical deep diff (max 20 examples)
        |
        v
checksum + package verification + baseline/generation gates
        |
        v
atomic READY manifest + trusted-reference pointer swap
        |
        v
UI reads verified package only
```

## 7. Trusted reference contract

新增衍生 cache 概念 `trusted-reference-v1`，建議由 `backend/services/export_reference_cache_service.py` 負責：

```python
@dataclass(frozen=True, slots=True)
class TrustedReferenceIdentity:
    source_fingerprint: str
    generation_token: str
    rules_fingerprint: str
    export_schema_version: str
    pipeline_fingerprint: str

@dataclass(frozen=True, slots=True)
class TrustedReferenceSnapshot:
    identity: TrustedReferenceIdentity
    artifact_digests: Mapping[str, Mapping[str, object]]
    artifact_fingerprints: Mapping[str, str]
    created_at: str
    source: str  # "legacy_materialized" or "validated_ready"

def load_trusted_reference(
    cache_root: Path,
    identity: TrustedReferenceIdentity,
) -> TrustedReferenceSnapshot | None

def materialize_trusted_reference(
    cache_root: Path,
    identity: TrustedReferenceIdentity,
    legacy_artifacts: Mapping[str, bytes],
) -> TrustedReferenceSnapshot

def publish_trusted_reference(
    cache_root: Path,
    snapshot: TrustedReferenceSnapshot,
) -> Path
```

Snapshot 只保存 bounded schema/row-count/metric digest 與 artifact hash，不保存 canonical rows、原始 Excel 或其他 business detail。必須以 temporary file 寫入、fsync（若平台支援）、checksum 驗證後才更新 active pointer。pointer 只指向完整 immutable snapshot；讀取端拒絕 symlink、path escape、unknown schema、identity mismatch、hash mismatch、缺 artifact 或非 regular file。

`source="validated_ready"` 只能由已通過 semantic equivalence、package checksum、baseline 與 generation gate 的 READY manifest 建立；不能由未驗證 candidate 建立。

## 8. Equivalence digest contract

在 `backend/services/export_equivalence_service.py` 增加 bounded digest layer，不取代既有 `compare_workbooks()`：

```python
def build_workbook_metric_digest(
    data: bytes,
    *,
    money_columns: tuple[str, ...] = (),
    stable_key_columns: tuple[str, ...] = (),
) -> Mapping[str, object]

def compare_export_digests(
    reference: Mapping[str, Mapping[str, object]],
    candidate: Mapping[str, Mapping[str, object]],
) -> bool
```

Digest 至少包括 artifact id、sheet order、header fingerprint、row counts、stable-key fingerprint、money totals、quantity totals、row-level canonical hash。Digest 使用既有 Decimal two-decimal normalization，避免 float representation 造成 false mismatch。

Gate 行為：

- digest 完全一致：產生 `PASS / deep_diff_skipped=true`。
- digest 不一致：呼叫現有 canonical deep diff，保留最多 20 個 mismatch examples。
- schema、identity 或 digest 計算例外：`NOT_RUN`，fast path fail closed。

## 9. Manifest / pointer contract

既有 `export-manifest-v2` 增加 bounded fields：

```json
{
  "reference": {
    "status": "HIT | MATERIALIZED | INVALID | NOT_RUN",
    "snapshot_path": "relative/path",
    "identity_fingerprint": "sha256",
    "source": "legacy_materialized | validated_ready",
    "deep_diff_skipped": true
  },
  "telemetry": {
    "reference_lookup_ms": 0,
    "reference_materialize_ms": 0,
    "equivalence_digest_ms": 0,
    "equivalence_deep_diff_ms": 0,
    "cache_hit_ms": 0,
    "peak_rss_bytes": null
  }
}
```

所有 path 必須是 cache root 下的相對 regular file；manifest 不得暴露 raw data path、absolute path 或訂單明細。

## 10. Fallback / rollback

以下任一情況不得使用 trusted snapshot：source fingerprint、generation、rules、schema 或 pipeline fingerprint 不符；snapshot 遺失、損壞、權限錯誤、過期、checksum 不符、未知 schema、package mismatch 或 identity 不完整。

處理順序：

1. active trusted pointer 保持不變。
2. 由 legacy `_compute_export_workbooks()` materialize 新 reference。
3. 若 legacy 或 deep diff 失敗，fast artifact 不發布 READY，使用既有 legacy fallback。
4. 新 snapshot 與 export manifest 都採 atomic swap；不能先更新其中一個造成 dangling pointer。
5. UI 顯示 bounded reason code，不顯示 raw exception、raw row 或 customer/payment data。

## 11. Performance benchmark 與 acceptance

固定同一 SQLite snapshot、同一 rules fingerprint、同一 machine，至少執行：

| scenario | 必量測 |
|---|---|
| first cold materialization | legacy reference、snapshot write、digest/deep diff、serializer、total、peak RSS |
| same-identity cold rebuild | reference lookup、digest、serializer、package、total |
| source/rules/schema changed | stale rejection、legacy rebuild、total |
| affected-only rebuild | affected aggregation、trusted reference reuse、deep diff decision、total |
| READY cache hit/download | manifest lookup、package verify、download preparation |

初始 gate：

- semantic equivalence mismatch 必須為 0。
- same-identity reference lookup 必須 `< 250 ms`，不呼叫 legacy builder。
- READY cache hit preparation 必須 `< 1 s`。
- digest PASS 時 `equivalence_deep_diff_ms=0` 或接近 0，並明確標記 skipped。
- first materialization 可不承諾低於 legacy total，但後續 same-identity rebuild 必須至少比現有 230 秒 cold total 減少 50%。
- peak RSS 超過安全上限時降低 worker count；不得放寬 correctness gate。
- `database_mutated=false`，SQLite integrity 與 frozen baseline 維持原值。

## 12. Test matrix

### Unit / contract

- identity fingerprint deterministic、任何 component 改變即 miss。
- snapshot schema exact、unknown key/path escape/symlink/non-regular file fail closed。
- atomic publish 中斷不改 active pointer。
- snapshot immutability、bounded metadata、不包含 raw business rows。

### Equivalence

- digest PASS short-circuit deep diff。
- digest mismatch 進入 deep diff 並產生 bounded examples。
- sheet/header/order/row count/money/quantity/中文欄位/小數/空表。
- `掛賬核銷` 與 `TT 退款轉團款` scope、退款總額與已退款維度保持一致。

### Controller / cache

- first miss materializes legacy reference。
- same identity HIT 不呼叫 legacy reference builder。
- stale identity、corrupt snapshot、checksum mismatch、package mismatch 均 fallback。
- trusted pointer 與 export manifest 必須共同成功才成為 READY。
- previous READY pointer 在任何 failure 後保持不變。

### Integration / acceptance

- production-shaped fixed snapshot benchmark。
- Data Export UI 顯示 HIT/MATERIALIZED/FALLBACK telemetry。
- download 不重新 aggregation、reference 或 serialization。
- strict full pytest、`git diff --check`、Streamlit HTTP acceptance、Hermes。

## 13. Rollout

1. `DISABLED`：只 legacy。
2. `SHADOW`：建立/讀 trusted snapshot、執行 digest/deep diff，但使用者仍下載 legacy。
3. `OPT_IN`：只有所有 gate PASS 才提供 verified fast artifact。
4. `DEFAULT`：same-identity HIT 預設走 fast；miss 或任何 failure 自動 legacy fallback。
5. 每一階段保存 telemetry；沒有 production-shaped benchmark PASS 不升級 rollout。

## 14. Definition of Done

- trusted reference snapshot 有 exact identity、checksum、atomic pointer 與 bounded manifest contract。
- same-identity job 不重新建立 legacy reference，digest PASS 不重新逐格 deep diff。
- stale/corrupt/mismatch/exception 全部 fail closed，上一個 READY pointer 不變。
- legacy reference、現有 semantic deep diff、baseline、scope 與 export schema 仍保留。
- benchmark 能分開展示 first materialization、same-identity HIT、affected-only 與 cache hit。
- full pytest、strict warnings、Streamlit acceptance 與 Hermes 通過；任何 environment degraded 狀態獨立列示。
