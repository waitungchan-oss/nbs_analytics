from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class ShortTermOffloadPolicy:
    schema_version: str = "short-term-offload-v1"
    default_ttl_minutes: int = 30
    max_ttl_hours: int = 24
    max_content_bytes: int = 32000
    max_summary_bytes: int = 2048
    max_artifacts_per_run: int = 20
    max_total_bytes_per_run: int = 200000
    max_drilldown_bytes: int = 4096

    def __post_init__(self) -> None:
        if self.schema_version != "short-term-offload-v1":
            raise ValueError("unsupported schema")
        if not 1 <= self.default_ttl_minutes <= self.max_ttl_hours * 60:
            raise ValueError("invalid default ttl")
        if not 1 <= self.max_ttl_hours <= 24:
            raise ValueError("invalid max ttl")
        if not 1 <= self.max_content_bytes <= 32000:
            raise ValueError("invalid content cap")
        if not 1 <= self.max_summary_bytes <= 2048:
            raise ValueError("invalid summary cap")
        if not 1 <= self.max_artifacts_per_run <= 20 or not 1 <= self.max_total_bytes_per_run <= 200000:
            raise ValueError("invalid run cap")
        if not 1 <= self.max_drilldown_bytes <= self.max_content_bytes:
            raise ValueError("invalid drilldown cap")

    @staticmethod
    def validate_ref_id(value: str) -> None:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ValueError("unsafe identifier")

    def validate_ttl(self, created_at: datetime, expires_at: datetime) -> None:
        if not isinstance(created_at, datetime) or not isinstance(expires_at, datetime):
            raise ValueError("invalid timestamp")
        if created_at.tzinfo is None or expires_at.tzinfo is None or expires_at <= created_at:
            raise ValueError("invalid ttl")
        if expires_at > created_at + timedelta(hours=self.max_ttl_hours):
            raise ValueError("ttl exceeds cap")

    def fingerprint(self) -> str:
        payload = {"schemaVersion": self.schema_version, "defaultTtlMinutes": self.default_ttl_minutes,
                   "maxTtlHours": self.max_ttl_hours, "maxContentBytes": self.max_content_bytes,
                   "maxSummaryBytes": self.max_summary_bytes, "maxArtifactsPerRun": self.max_artifacts_per_run,
                   "maxTotalBytesPerRun": self.max_total_bytes_per_run, "maxDrilldownBytes": self.max_drilldown_bytes}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
