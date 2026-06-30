@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_BIN=.venv\Scripts\python.exe"
if not exist "%PYTHON_BIN%" set "PYTHON_BIN=python"

"%PYTHON_BIN%" scripts\system_manager.py stop
echo NBS 服務已停止。
pause
