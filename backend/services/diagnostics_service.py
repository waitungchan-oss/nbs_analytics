from __future__ import annotations

import json
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from backend.services.operational_monitor_service import compact_health_payload


def default_environment_payload() -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def _json_bytes(value: dict | list) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _tail_text(path: Path, max_bytes: int = 64 * 1024) -> bytes:
    data = path.read_bytes()
    return data[-max_bytes:]


def create_diagnostic_package(
    *,
    project_root: Path,
    runtime_dir: Path,
    status_payload: dict,
    health_payload: dict,
    environment_payload: dict | None = None,
) -> Path:
    output_dir = runtime_dir / "diagnostics"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"nbs_diagnostics_{stamp}.zip"
    files = []
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        payloads = {
            "system-status.json": status_payload,
            "health.json": compact_health_payload(health_payload),
            "environment.json": environment_payload or default_environment_payload(),
        }
        for name, payload in payloads.items():
            archive.writestr(name, _json_bytes(payload))
            files.append(name)

        history_path = runtime_dir / "health_history.jsonl"
        if history_path.exists():
            archive.writestr("health_history.jsonl", _tail_text(history_path, 256 * 1024))
            files.append("health_history.jsonl")
        state_path = runtime_dir / "services.json"
        if state_path.exists():
            archive.writestr("services.json", _tail_text(state_path))
            files.append("services.json")
        for report_name in ("retention_latest.json", "restore_drill_latest.json"):
            report_path = runtime_dir / report_name
            if report_path.exists():
                archive.writestr(report_name, _tail_text(report_path))
                files.append(report_name)
        for log_path in sorted((runtime_dir / "logs").glob("*.log")) if (runtime_dir / "logs").exists() else []:
            archive_name = f"logs/{log_path.name}"
            archive.writestr(archive_name, _tail_text(log_path))
            files.append(archive_name)

        manifest = {
            "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "projectRoot": str(project_root),
            "files": sorted(files + ["manifest.json"]),
            "excluded": ["*.db", "*.xlsx", ".nbs_runtime_cache/*", "uploaded source files"],
        }
        archive.writestr("manifest.json", _json_bytes(manifest))
    return output_path

