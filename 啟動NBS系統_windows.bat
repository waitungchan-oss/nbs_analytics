@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_BIN=.venv\Scripts\python.exe"
if not exist "%PYTHON_BIN%" set "PYTHON_BIN=python"

echo 啟動 NBS Analytics：Streamlit + FastAPI + Vue
"%PYTHON_BIN%" scripts\system_manager.py start
if errorlevel 1 (
    echo.
    echo 啟動失敗。請查看 .nbs_runtime\logs\ 內的服務日誌。
)
pause
