"""Verify Windows / VS Code / NVIDIA GPU readiness for NBS Analytics.

The script prints a JSON report so the user can paste back the result without
streaming a long setup or training session.
"""

from __future__ import annotations

import importlib
import json
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ok(value: Any = True, error: str | None = None) -> dict[str, Any]:
    return {"ok": bool(value), "error": error}


def _import_check(module_name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", None)
        return {"ok": True, "version": version}
    except Exception as exc:  # pragma: no cover - diagnostic script
        return {"ok": False, "error": repr(exc)}


def _run_command(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip()[-4000:],
            "stderr": completed.stderr.strip()[-4000:],
        }
    except FileNotFoundError as exc:
        return {"ok": False, "error": repr(exc)}
    except Exception as exc:  # pragma: no cover - diagnostic script
        return {"ok": False, "error": repr(exc)}


def _sqlite_check() -> dict[str, Any]:
    db_path = PROJECT_ROOT / "nbs_marketing_data.db"
    result: dict[str, Any] = {
        "ok": db_path.exists(),
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
    }
    if not db_path.exists():
        result["error"] = "nbs_marketing_data.db not found"
        return result
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            tables = [row[0] for row in rows]
            result["tables"] = tables
            result["tour_data_rows"] = _count_table(conn, "tour_data")
            result["others_data_rows"] = _count_table(conn, "others_data")
            result["ok"] = "tour_data" in tables or "others_data" in tables
            return result
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - diagnostic script
        result["ok"] = False
        result["error"] = repr(exc)
        return result


def _count_table(conn: sqlite3.Connection, table_name: str) -> int | None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not exists:
        return None
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])


def _torch_check() -> dict[str, Any]:
    try:
        import torch

        result: dict[str, Any] = {
            "ok": True,
            "version": getattr(torch, "__version__", None),
            "cuda_compiled": getattr(torch.version, "cuda", None),
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0,
        }
        if torch.cuda.is_available():
            result["device_name"] = torch.cuda.get_device_name(0)
            x = torch.rand((256, 256), device="cuda")
            y = x @ x
            torch.cuda.synchronize()
            result["tensor_smoke_test"] = {
                "ok": True,
                "device": str(y.device),
                "shape": list(y.shape),
                "mean": float(y.mean().detach().cpu().item()),
            }
        else:
            x = torch.rand((64, 64))
            result["tensor_smoke_test"] = {
                "ok": True,
                "device": str(x.device),
                "shape": list(x.shape),
                "mean": float(x.mean().item()),
            }
        return result
    except Exception as exc:  # pragma: no cover - diagnostic script
        return {"ok": False, "error": repr(exc)}


def main() -> int:
    core_modules = [
        "streamlit",
        "pandas",
        "numpy",
        "matplotlib",
        "statsmodels",
        "prophet",
        "lightgbm",
        "sklearn",
        "plotly",
        "openpyxl",
    ]
    imports = {name: _import_check(name) for name in core_modules}
    imports["forecasting"] = _import_check("forecasting")
    imports["pipeline"] = _import_check("pipeline")
    imports["business_calendar"] = _import_check("business_calendar")

    report = {
        "project_root": str(PROJECT_ROOT),
        "python": {
            "executable": sys.executable,
            "version": sys.version,
            "platform": platform.platform(),
        },
        "commands": {
            "nvidia_smi": _run_command(["nvidia-smi"]),
        },
        "files": {
            "requirements_txt": _ok((PROJECT_ROOT / "requirements.txt").exists()),
            "rules_config_json": _ok((PROJECT_ROOT / "rules_config.json").exists()),
            "business_calendar_events": _ok((PROJECT_ROOT / "data" / "business_calendar_events.json").exists()),
        },
        "sqlite": _sqlite_check(),
        "imports": imports,
        "torch": _torch_check(),
    }

    critical_ok = (
        report["sqlite"]["ok"]
        and imports["forecasting"]["ok"]
        and imports["streamlit"]["ok"]
        and imports["lightgbm"]["ok"]
        and report["torch"]["ok"]
    )
    report["summary"] = {
        "ok": bool(critical_ok),
        "cuda_ready": bool(report["torch"].get("cuda_available")),
        "next_step": "Run prewarm_ai_cache.py --status, then start dashboard." if critical_ok else "Fix failed checks above before training.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if critical_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
