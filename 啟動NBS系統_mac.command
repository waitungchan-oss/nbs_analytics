#!/bin/zsh

cd "$(dirname "$0")"
PYTHON_BIN=".venv/bin/python"
if [ ! -x "${PYTHON_BIN}" ]; then
  PYTHON_BIN="python3"
fi

echo "啟動 NBS Analytics：Streamlit + FastAPI + Vue"
"${PYTHON_BIN}" scripts/system_manager.py start
RESULT=$?
echo ""
if [ ${RESULT} -ne 0 ]; then
  echo "啟動失敗。請查看 .nbs_runtime/logs/ 內的服務日誌。"
fi
read -k 1 "?按任意鍵關閉..."
exit ${RESULT}
