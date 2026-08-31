"""Exact, bounded and source-bound receipts for the Hermes CLI transport."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.agents.hermes_cli_transport import CliInvokeResult


CLI_RECEIPT_SCHEMA = "hermes-cli-transport-receipt-v1"
_STATUSES = {"ready", "blocked_runner_capability", "blocked_runner_transport", "invalid_evidence"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE = re.compile(r"(?:api[_-]?key|secret|token|password|private[_-]?key|credential)", re.IGNORECASE)
_FIELDS = {
    "schemaVersion", "status", "runnerIdentityFingerprint", "sourceFingerprint", "commandShapeFingerprint",
    "cliVersion", "observedModel", "exitCode", "timedOut", "stdoutDigest", "stderrDigest", "responseFingerprint",
    "startedAt", "finishedAt", "diagnostics", "stdoutBytes", "stderrBytes", "stdoutTruncated", "stderrTruncated",
    "receiptFingerprint",
}


class CliTransportReceiptError(ValueError):
    """Raised when a CLI transport receipt is malformed, unsafe or unbound."""


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CliTransportReceiptError(f"{label} must be lowercase sha256")
    return value


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _unsigned(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: payload[key] for key in _FIELDS if key != "receiptFingerprint"}


def _regular_file_or_new(path: Path) -> None:
    if not path.is_absolute():
        raise CliTransportReceiptError("receipt path must be absolute")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise CliTransportReceiptError("receipt path must be a regular file")


def _validate_payload(payload: Mapping[str, object], *, expected_identity_fingerprint: str, expected_source_fingerprint: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
        raise CliTransportReceiptError("receipt exact schema is invalid")
    if payload["schemaVersion"] != CLI_RECEIPT_SCHEMA or payload["status"] not in _STATUSES:
        raise CliTransportReceiptError("receipt schema or status is invalid")
    _sha(expected_identity_fingerprint, "expected identity fingerprint")
    _sha(expected_source_fingerprint, "expected source fingerprint")
    if payload["runnerIdentityFingerprint"] != expected_identity_fingerprint or payload["sourceFingerprint"] != expected_source_fingerprint:
        raise CliTransportReceiptError("receipt identity/source fingerprint mismatch")
    for key in ("runnerIdentityFingerprint", "sourceFingerprint", "commandShapeFingerprint", "stdoutDigest", "stderrDigest", "receiptFingerprint"):
        _sha(payload[key], key)
    if payload["responseFingerprint"] is not None:
        _sha(payload["responseFingerprint"], "response fingerprint")
    if payload["exitCode"] is not None and (isinstance(payload["exitCode"], bool) or not isinstance(payload["exitCode"], int) or abs(payload["exitCode"]) > 1_000_000):
        raise CliTransportReceiptError("exit code is invalid")
    for key in ("stdoutBytes", "stderrBytes"):
        if isinstance(payload[key], bool) or not isinstance(payload[key], int) or not 0 <= payload[key] <= 10_000_000:
            raise CliTransportReceiptError(f"{key} is invalid")
    if not isinstance(payload["timedOut"], bool) or not isinstance(payload["stdoutTruncated"], bool) or not isinstance(payload["stderrTruncated"], bool):
        raise CliTransportReceiptError("receipt boolean field is invalid")
    if payload["timedOut"] and payload["status"] == "ready":
        raise CliTransportReceiptError("ready receipt cannot be timed out")
    if not isinstance(payload["diagnostics"], list) or len(payload["diagnostics"]) > 20 or any(not isinstance(item, str) or len(item) > 256 for item in payload["diagnostics"]):
        raise CliTransportReceiptError("diagnostics are invalid")
    if _SENSITIVE.search(_canonical(payload)):
        raise CliTransportReceiptError("sensitive content is not allowed in receipt")
    if payload["receiptFingerprint"] != _fingerprint(_unsigned(payload)):
        raise CliTransportReceiptError("receipt fingerprint does not match payload")


@dataclass(frozen=True)
class CliTransportReceipt:
    status: str
    runner_identity_fingerprint: str
    source_fingerprint: str
    command_shape_fingerprint: str
    cli_version: str | None
    observed_model: str | None
    exit_code: int | None
    timed_out: bool
    stdout_digest: str
    stderr_digest: str
    response_fingerprint: str | None
    started_at: str
    finished_at: str
    diagnostics: tuple[str, ...]
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    receipt_fingerprint: str

    @classmethod
    def from_result(cls, result: CliInvokeResult, *, source_fingerprint: str, command_shape_fingerprint: str) -> "CliTransportReceipt":
        _sha(source_fingerprint, "source fingerprint")
        _sha(command_shape_fingerprint, "command shape fingerprint")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        values: dict[str, object] = {
            "schemaVersion": CLI_RECEIPT_SCHEMA,
            "status": result.status,
            "runnerIdentityFingerprint": result.identity.identity_fingerprint,
            "sourceFingerprint": source_fingerprint,
            "commandShapeFingerprint": command_shape_fingerprint,
            "cliVersion": result.cli_version,
            "observedModel": result.observed_model,
            "exitCode": result.exit_code,
            "timedOut": result.timed_out,
            "stdoutDigest": result.stdout_digest,
            "stderrDigest": result.stderr_digest,
            "responseFingerprint": result.response_fingerprint,
            "startedAt": now,
            "finishedAt": now,
            "diagnostics": list(result.diagnostics) + ([result.reason] if result.reason else []),
            "stdoutBytes": result.stdout_bytes,
            "stderrBytes": result.stderr_bytes,
            "stdoutTruncated": result.reason in {"stdout_limit_exceeded", "response_limit_exceeded"},
            "stderrTruncated": result.reason == "stderr_limit_exceeded",
        }
        values["receiptFingerprint"] = _fingerprint(values)
        _validate_payload(values, expected_identity_fingerprint=result.identity.identity_fingerprint, expected_source_fingerprint=source_fingerprint)
        return cls(
            status=values["status"], runner_identity_fingerprint=values["runnerIdentityFingerprint"], source_fingerprint=values["sourceFingerprint"],
            command_shape_fingerprint=values["commandShapeFingerprint"], cli_version=values["cliVersion"], observed_model=values["observedModel"],
            exit_code=values["exitCode"], timed_out=values["timedOut"], stdout_digest=values["stdoutDigest"], stderr_digest=values["stderrDigest"],
            response_fingerprint=values["responseFingerprint"], started_at=values["startedAt"], finished_at=values["finishedAt"],
            diagnostics=tuple(values["diagnostics"]), stdout_bytes=values["stdoutBytes"], stderr_bytes=values["stderrBytes"],
            stdout_truncated=values["stdoutTruncated"], stderr_truncated=values["stderrTruncated"], receipt_fingerprint=values["receiptFingerprint"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": CLI_RECEIPT_SCHEMA, "status": self.status, "runnerIdentityFingerprint": self.runner_identity_fingerprint,
            "sourceFingerprint": self.source_fingerprint, "commandShapeFingerprint": self.command_shape_fingerprint, "cliVersion": self.cli_version,
            "observedModel": self.observed_model, "exitCode": self.exit_code, "timedOut": self.timed_out, "stdoutDigest": self.stdout_digest,
            "stderrDigest": self.stderr_digest, "responseFingerprint": self.response_fingerprint, "startedAt": self.started_at,
            "finishedAt": self.finished_at, "diagnostics": list(self.diagnostics), "stdoutBytes": self.stdout_bytes, "stderrBytes": self.stderr_bytes,
            "stdoutTruncated": self.stdout_truncated, "stderrTruncated": self.stderr_truncated, "receiptFingerprint": self.receipt_fingerprint,
        }


def validate_cli_transport_receipt(payload: Mapping[str, object], *, expected_identity_fingerprint: str, expected_source_fingerprint: str) -> CliTransportReceipt:
    _validate_payload(payload, expected_identity_fingerprint=expected_identity_fingerprint, expected_source_fingerprint=expected_source_fingerprint)
    return CliTransportReceipt(
        status=payload["status"], runner_identity_fingerprint=payload["runnerIdentityFingerprint"], source_fingerprint=payload["sourceFingerprint"],
        command_shape_fingerprint=payload["commandShapeFingerprint"], cli_version=payload["cliVersion"], observed_model=payload["observedModel"],
        exit_code=payload["exitCode"], timed_out=payload["timedOut"], stdout_digest=payload["stdoutDigest"], stderr_digest=payload["stderrDigest"],
        response_fingerprint=payload["responseFingerprint"], started_at=payload["startedAt"], finished_at=payload["finishedAt"],
        diagnostics=tuple(payload["diagnostics"]), stdout_bytes=payload["stdoutBytes"], stderr_bytes=payload["stderrBytes"],
        stdout_truncated=payload["stdoutTruncated"], stderr_truncated=payload["stderrTruncated"], receipt_fingerprint=payload["receiptFingerprint"],
    )


def write_cli_transport_receipt(path: Path, receipt: CliTransportReceipt) -> Path:
    _regular_file_or_new(path)
    _validate_payload(receipt.to_dict(), expected_identity_fingerprint=receipt.runner_identity_fingerprint, expected_source_fingerprint=receipt.source_fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt.to_dict(), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise CliTransportReceiptError(f"cannot write CLI receipt: {exc}") from exc
    return path
