#!/bin/zsh

cd "$(dirname "$0")"
PYTHON_BIN=".venv/bin/python"
if [ ! -x "${PYTHON_BIN}" ]; then
  PYTHON_BIN="python3"
fi

"${PYTHON_BIN}" scripts/system_manager.py stop
read -k 1 "?NBS 服務已停止，按任意鍵關閉..."
