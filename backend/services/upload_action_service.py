from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

import database
from backend.services.diagnostics_service import default_environment_payload
from backend.services.operational_monitor_service import compact_health_payload
from backend.services.system_health_service import build_system_health
from backend.services.upload_lock_service import acquire_upload_lease
from backend.services.upload_orchestrator_service import execute_upload_operation


class NamedBytesIO(BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def _wrap_named_bytes(data: bytes, name: str) -> NamedBytesIO:
    return NamedBytesIO(data, name)


def _frame_to_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.fillna("").to_dict(orient="records")
    return []


def _compact_entity_audit(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "summary": _frame_to_records(value.get("summary")),
        "source_breakdown": _frame_to_records(value.get("source_breakdown")),
        "duplicate_detail": _frame_to_records(value.get("duplicate_detail")),
        "unmatched_detail": _frame_to_records(value.get("unmatched_detail")),
        "id_cleaning_samples": _frame_to_records(value.get("id_cleaning_samples")),
    }


async def run_vue_upload_action(
    *,
    main_file,
    tour_file=None,
    other_files=None,
    receipt_exclusion_confirmation: dict | None = None,
) -> dict[str, Any]:
    other_files = list(other_files or [])
    main_name = main_file.filename or "main.xlsx"
    tour_name = tour_file.filename if tour_file is not None and tour_file.filename else "tour.xlsx"
    source_files = [main_name]
    if tour_file is not None:
        source_files.append(tour_name)
    source_files.extend(item.filename or "other.xlsx" for item in other_files)
    with acquire_upload_lease(entry_point="fastapi", source_files=source_files) as lease:
        main_bytes = await main_file.read()
        tour_bytes = await tour_file.read() if tour_file is not None else None
        other_payloads = [(item.filename or "other.xlsx", await item.read()) for item in other_files]
        execution = execute_upload_operation(
            lease.operation,
            main_file=_wrap_named_bytes(main_bytes, main_name),
            tour_file=_wrap_named_bytes(tour_bytes, tour_name) if tour_bytes is not None else None,
            other_files=[_wrap_named_bytes(payload, name) for name, payload in other_payloads],
            live_db_path=database.DB_FILE,
            receipt_exclusion_confirmation=receipt_exclusion_confirmation,
        )
        latest_health = build_system_health(
            db_path=Path(database.DB_FILE),
            cache_path=Path(".nbs_runtime_cache"),
            runtime_dir=Path(".nbs_runtime"),
        )
        return {
            **execution.response,
            "latestHealth": compact_health_payload(latest_health),
            "entityAudit": _compact_entity_audit(execution.entity_audit),
            "anmRowCount": int(len(execution.anomaly_frame)),
            "environment": default_environment_payload(),
            "receiptExclusion": execution.response.get("receiptExclusion") or {},
        }
