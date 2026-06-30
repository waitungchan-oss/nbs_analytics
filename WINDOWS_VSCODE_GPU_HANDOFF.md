# NBS Analytics Windows / VS Code / NVIDIA GPU 交接指南

> 目的：把目前 macOS 上的 NBS Analytics 安全搬到 Windows + VS Code，建立 NVIDIA CUDA / PyTorch 訓練實驗環境，但不改現有 Streamlit dashboard、SQLite、資料清洗、AI Forecast、WAPE 回測與 Excel 匯出主線。

---

## 1. 交接原則

這次 Windows 搬移只做三件事：

1. 讓專案能在 Windows + VS Code 啟動與驗證。
2. 讓 NVIDIA GPU / PyTorch 可用於後續模型訓練實驗。
3. 讓長訓練任務只輸出結果 JSON / log，降低對話 token 使用量。

明確不做：

- 不把 PyTorch 直接接入正式 `Daily Forecast`。
- 不改 `forecasting.py` 目前的 ARIMA / Prophet / LightGBM / Fusion 主線。
- 不改 SQLite schema、清洗規則、正式收入口徑、WAPE 計算、Excel 檔名或 sheets。
- 不搬 macOS 的 `.venv`、`__pycache__`、臨時截圖或舊 runtime cache。

正式收入口徑仍是：

```text
不含掛賬核銷與TT退款轉團款
```

---

## 2. 建議搬移清單

請從 macOS 專案根目錄複製到 Windows，例如：

```text
C:\Users\<你的使用者>\Documents\nbs_analytics
```

必搬：

- `app.py`
- `app_pages.py`
- `app_workflows.py`
- `app_styles.py`
- `streamlit_rendering.py`
- `forecasting.py`
- `pipeline.py`
- `business_calendar.py`
- `visuals.py`
- `config.py`
- `database.py`
- `backend\`
- `frontend\`
- `requirements.txt`
- `rules_config.json`
- `nbs_marketing_data.db`
- `data\business_calendar_events.json`
- `scripts\`
- `.streamlit\config.toml`
- `啟動NBS系統_windows.bat`
- `NBS_ANALYTICS_SYSTEM_MAP.md`
- `NBS_SQLITE_DATABASE_GUIDE.md`
- `WINDOWS_VSCODE_GPU_HANDOFF.md`
- `NBS_ANALYTICS_HANDOFF.md`
- `DESIGN.md`

可搬：

- `backups\`：如果你想保留模型與 UI 改版歷史。
- `NBS_ANALYTICS_HANDOFF.md`：如果要保留協作背景。

不要搬：

- `.venv\`
- `__pycache__\`
- `.nbs_runtime_cache\`，除非你只是想複製現有快取；Windows 首次建議重新預熱。
- macOS 臨時截圖、瀏覽器驗收圖片。

---

## 3. VS Code 開啟方式

在 Windows PowerShell：

```powershell
cd C:\Users\<你的使用者>\Documents\nbs_analytics
code .
```

VS Code 建議：

- 安裝 Python extension。
- 開啟內建 Terminal。
- Terminal 預設使用 PowerShell。
- Python interpreter 選擇：

```text
.\.venv\Scripts\python.exe
```

---

## 4. 一鍵初始化 Windows GPU 環境

第一次在 Windows 端執行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows_gpu.ps1 -TorchCuda cu128
```

腳本會做：

- 檢查 `python / pip / git / nvidia-smi`。
- 使用 `py -3.11` 建立 `.venv`。
- 安裝 `requirements.txt`。
- 安裝 PyTorch CUDA wheel。
- 執行 GPU 驗證。
- 執行 business calendar 驗證。
- 執行核心 Python 檔案 compile 檢查。

如果你的顯卡或 driver 暫時不支援 CUDA 12.8，可改用：

```powershell
.\scripts\setup_windows_gpu.ps1 -TorchCuda cu126
```

如果只想先跑 CPU / dashboard：

```powershell
.\scripts\setup_windows_gpu.ps1 -TorchCuda cpu
```

> PyTorch 官方 Windows 安裝入口：https://pytorch.org/get-started/locally/  
> 官方說明要求 Windows PyTorch 使用 Python 3.10+，並建議 NVIDIA GPU 透過 Windows + Pip + CUDA selector 安裝後用 `torch.cuda.is_available()` 驗證。

---

## 5. GPU 驗證

初始化完成後可單獨執行：

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_windows_gpu.py
```

成功時重點看：

```json
{
  "summary": {
    "ok": true,
    "cuda_ready": true
  },
  "torch": {
    "cuda_available": true,
    "device_name": "你的 NVIDIA GPU"
  }
}
```

如果 `summary.ok = true` 但 `cuda_ready = false`，代表 dashboard 與 Python 依賴大致可用，但 GPU 沒有被 PyTorch 看到。先檢查：

```powershell
nvidia-smi
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

---

## 6. Dashboard 啟動

沿用既有 Windows 啟動器：

```powershell
.\啟動NBS系統_windows.bat
```

預設網址：

```text
Streamlit: http://127.0.0.1:8502/
Vue:       http://127.0.0.1:5173/
API Docs:  http://127.0.0.1:8601/docs
Health:    http://127.0.0.1:8601/api/health
```

Windows 啟動器會透過 `scripts\system_manager.py` 啟動 Streamlit、FastAPI 與 Vue。若預設 port 已被同一個 `nbs_analytics` 專案服務佔用，manager 會採納該服務；若被其它未知程序佔用，需先停止該程序或調整 port。

如果啟動很慢，先看 AI cache：

```powershell
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py --status
```

如果是 miss，再預熱：

```powershell
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py
```

啟動後的 UI 檢查重點：

- 左側 sidebar 預設展開。
- `Navigation` 在上方，`Control Center` 在下方，兩者不要混在一起。
- Navigation submenu 可點擊；點擊後 URL 只變成 `#section-...`，不應觸發整個 Streamlit app 重新整理。
- Active item 會以藍色 highlight 和狀態 badge 顯示目前 section。
- Sidebar 收合按鈕應位於品牌區附近，與 `NBS Analytics` identity 對齊。
- Control Center 的淺色 / 深色、年份、月份、日期、分社、專職與套用/重設篩選仍正常。

---

## 7. AI Cache 與 Export Cache 注意事項

目前專案已有兩層加速：

- AI runtime cache：`.nbs_runtime_cache\ai_*.pkl`
- Export cache：`.nbs_runtime_cache\export_*.pkl`

Windows 初次搬移後建議重新建立 cache，因為：

- macOS `.venv` 不可搬。
- pickle 通常可跨平台，但重建更乾淨。
- 目前 AI cache key 已按資料語義 fingerprint / rules content hash 設計，Windows 上可重新命中穩定資料狀態。

推薦流程：

```powershell
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py --status
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py --status
```

---

## 8. GPU 訓練實驗策略

目前正式模型是：

- ARIMA
- Prophet
- LightGBM
- Fusion
- Daily diagnostics / baseline / normal-day / two-lane backtest
- 7-Day Macro / Month-End Macro 回測
- Forecast Governance / Feature Store / Lead Signal 作為訓練前後健康對照
- Causal Analytics 作為營收變動解釋，不是模型訓練標籤

PyTorch 第一階段只作實驗軌，不覆蓋正式 Forecast。建議後續新增模型時採用：

```text
experiments/
  gpu_daily_model/
    train.py
    dataset.py
    model.py
    metrics.py
    README.md
models/
  experimental/
reports/
  windows_gpu_training_summary.json
```

實驗模型輸出至少包含：

- train / validation split 規則
- NoFutureLeak 檢查
- Feature Store / Lead Signal 使用清單與最大特徵日期
- All Days WAPE
- Normal Days WAPE
- Extreme Days Error Share
- MAE / Bias
- Forecast Governance 對照：Accuracy / Bias / Stability / Sample
- 與目前正式 Daily 最佳 WAPE 的比較
- 是否建議接入 dashboard

沒有明確打贏之前，不接入 `app.py` 正式 Forecast 區。

---

## 9. 長訓練任務：只檢視結果，不監測全程

不要在對話裡貼完整訓練 log。用結果式 runner：

```powershell
.\scripts\run_training_result_only.ps1 `
  -RunName daily_gpu_trial_001 `
  -Command ".\.venv\Scripts\python.exe experiments\gpu_daily_model\train.py --epochs 50"
```

它會產生：

```text
reports\daily_gpu_trial_001.log
reports\windows_gpu_training_summary.json
```

之後只需要把這幾個東西交給 Codex：

- `reports\windows_gpu_training_summary.json`
- 模型自己輸出的 metrics JSON / CSV
- 如失敗，再附 log tail，不需要整份 log

這樣可以降低 token 使用量，也避免全程監測。

---

## 10. Windows 驗收命令清單

初始化後一次跑完：

```powershell
Get-Command python -All
Get-Command pip -All
Get-Command git -All
nvidia-smi

.\.venv\Scripts\python.exe .\scripts\verify_windows_gpu.py

.\.venv\Scripts\python.exe -m py_compile `
  app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py `
  forecasting.py pipeline.py business_calendar.py visuals.py `
  backend\services\upload_preflight_service.py `
  scripts\system_manager.py scripts\prewarm_ai_cache.py scripts\verify_windows_gpu.py

.\.venv\Scripts\python.exe scripts\validate_business_calendar.py

.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py --status

.\.venv\Scripts\python.exe .\scripts\system_manager.py acceptance
```

成功標準：

- `torch.cuda.is_available()` 是 `true`。
- `forecasting.py` 可 import。
- `streamlit / lightgbm / prophet` 可 import。
- `nbs_marketing_data.db` 存在且有 `tour_data` 或 `others_data`。
- `validate_business_calendar.py` 通過。
- `prewarm_ai_cache.py --status` 能輸出 cache key / payload 狀態。
- `啟動NBS系統_windows.bat` 能開啟 Streamlit / FastAPI / Vue，且 `scripts\system_manager.py acceptance` 回傳 passed。
- Sidebar `Navigation` / `Control Center` 分離正常；menu item 可跳轉、active 狀態可更新，且點擊 navigation 不會重新生成 AI cache 或 Export。
- 淺色 / 深色主題切換後，sidebar、主畫布、cards、tables、charts 不出現深淺背景混雜。
- Dashboard 內以下只讀診斷區可顯示或下載：
- Data Quality Scorecard
- Entity Resolution Audit
- Forecast Governance
- Feature Store / Lead Signal
- Causal Analytics
- AI-assisted Data Cleaning 建議
- GMV 排除訂單看板

---

## 11. 常見問題

### 11.1 找不到 `py -3.11`

安裝 Python 3.11，並勾選 Add Python to PATH。或改用：

```powershell
.\scripts\setup_windows_gpu.ps1 -PythonCommand "python" -TorchCuda cu128
```

### 11.2 `nvidia-smi` 找不到

代表 NVIDIA driver 或 PATH 未準備好。先安裝 / 更新 NVIDIA driver，重開 PowerShell 後再試：

```powershell
nvidia-smi
```

### 11.3 `torch.cuda.is_available()` 是 false

常見原因：

- 安裝到 CPU 版 torch。
- NVIDIA driver 太舊。
- Python venv 不是 VS Code 使用中的 interpreter。
- CUDA wheel 選錯，例如應改 `cu126`。

重新安裝 PyTorch：

```powershell
.\.venv\Scripts\python.exe -m pip uninstall -y torch torchvision torchaudio
.\.venv\Scripts\python.exe -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

### 11.4 Prophet 安裝失敗

先確認 Python 是 3.11，並升級 build tools：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install prophet
```

### 11.5 Dashboard 啟動但頁面慢

先不要懷疑 GPU。Dashboard 慢通常與 AI cache / Excel export 相關：

```powershell
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py --status
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py
```

Excel 已設計成 lazy export，只有需要下載時才生成。

如果是新增診斷 workbook 第一次下載較慢，先確認不是 AI cache miss：

```powershell
.\.venv\Scripts\python.exe .\scripts\prewarm_ai_cache.py --status
```

---

## 12. 給下一位協作者的邊界

任何 Windows GPU 模型優化都必須遵守：

- 正式收入口徑不變。
- 不使用 future actual 做特徵或 selector。
- 不把 trimmed WAPE 當正式準確率。
- 不把實驗模型覆蓋正式 Daily Forecast，除非回測明確打贏且經確認。
- 每次模型實驗至少輸出 WAPE / MAE / Bias / sample count / NoFutureLeak 結果。
- 每次模型實驗應附 Forecast Governance 與 Feature Store 對照摘要，避免只追 WAPE。
- 先看結果檔，不要求全程監測。
