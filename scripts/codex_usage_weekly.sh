#!/bin/bash
# Weekly Codex usage report generator (launchd wrapper).
# Read-only w.r.t. the NBS project: only reads ~/.codex/sessions and
# writes the report file to $HOME/Desktop/report/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$HOME/Desktop/report"
STAMP="$(date +%Y-%m-%d)"

mkdir -p "$OUT_DIR"

"$ROOT/.venv/bin/python" "$ROOT/scripts/codex_usage_report.py" \
  --weeks 1 \
  --output "$OUT_DIR/codex_usage_weekly_${STAMP}.md"

# Also write a machine-readable JSON copy for long-term tracking.
"$ROOT/.venv/bin/python" "$ROOT/scripts/codex_usage_report.py" \
  --weeks 1 \
  --format json \
  --output "$OUT_DIR/codex_usage_weekly_${STAMP}.json"

echo "written: $OUT_DIR/codex_usage_weekly_${STAMP}.md / .json"
