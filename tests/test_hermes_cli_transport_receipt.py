from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.hermes_cli_transport import CliInvokeResult
from backend.agents.hermes_cli_transport_receipt import (
    CLI_RECEIPT_SCHEMA,
    CliTransportReceipt,
    CliTransportReceiptError,
    validate_cli_transport_receipt,
    write_cli_transport_receipt,
)
from backend.agents.runner_identity import RunnerIdentity


def _result(status: str = "ready") -> CliInvokeResult:
    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="hermes-cli", provider="hermes", model="deepseek-v4-flash", profile="max", execution_environment="hermes-local"
    )
    return CliInvokeResult(
        status=status,
        identity=identity,
        response={"model": "deepseek-v4-flash", "response": "ok"} if status == "ready" else {},
        response_fingerprint="e" * 64 if status == "ready" else None,
        exit_code=0 if status == "ready" else 7,
        stdout_bytes=42,
        stderr_bytes=0,
        stdout_digest="a" * 64,
        stderr_digest="b" * 64,
        cli_version="1.2.3",
        observed_model="deepseek-v4-flash",
        reason=None if status == "ready" else "non_zero_exit",
        diagnostics=(),
    )


def test_receipt_has_exact_bounded_schema_and_fingerprint() -> None:
    receipt = CliTransportReceipt.from_result(_result(), source_fingerprint="c" * 64, command_shape_fingerprint="d" * 64)
    payload = receipt.to_dict()
    assert payload["schemaVersion"] == CLI_RECEIPT_SCHEMA
    assert set(payload) == {
        "schemaVersion", "status", "runnerIdentityFingerprint", "sourceFingerprint", "commandShapeFingerprint",
        "cliVersion", "observedModel", "exitCode", "timedOut", "stdoutDigest", "stderrDigest",
        "responseFingerprint", "startedAt", "finishedAt", "diagnostics", "stdoutBytes", "stderrBytes",
        "stdoutTruncated", "stderrTruncated", "receiptFingerprint",
    }
    assert validate_cli_transport_receipt(payload, expected_identity_fingerprint=receipt.runner_identity_fingerprint, expected_source_fingerprint="c" * 64).to_dict() == payload


def test_receipt_rejects_binding_tamper_and_secret_capture() -> None:
    receipt = CliTransportReceipt.from_result(_result(), source_fingerprint="c" * 64, command_shape_fingerprint="d" * 64)
    payload = receipt.to_dict()
    payload["sourceFingerprint"] = "f" * 64
    with pytest.raises(CliTransportReceiptError, match="fingerprint"):
        validate_cli_transport_receipt(payload, expected_identity_fingerprint=receipt.runner_identity_fingerprint, expected_source_fingerprint="c" * 64)

    payload = receipt.to_dict()
    payload["diagnostics"] = ["api_key=secret"]
    payload["receiptFingerprint"] = "0" * 64
    with pytest.raises(CliTransportReceiptError, match="sensitive"):
        validate_cli_transport_receipt(payload, expected_identity_fingerprint=receipt.runner_identity_fingerprint, expected_source_fingerprint="c" * 64)


def test_blocked_receipt_cannot_be_ready_or_timeout_inconsistent() -> None:
    receipt = CliTransportReceipt.from_result(_result("blocked_runner_transport"), source_fingerprint="c" * 64, command_shape_fingerprint="d" * 64)
    assert receipt.status == "blocked_runner_transport"
    payload = receipt.to_dict()
    payload["status"] = "ready"
    with pytest.raises(CliTransportReceiptError):
        validate_cli_transport_receipt(payload, expected_identity_fingerprint=receipt.runner_identity_fingerprint, expected_source_fingerprint="c" * 64)


def test_receipt_writer_is_atomic_and_rejects_symlink(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    receipt = CliTransportReceipt.from_result(_result(), source_fingerprint="c" * 64, command_shape_fingerprint="d" * 64)
    assert write_cli_transport_receipt(path, receipt) == path
    assert json.loads(path.read_text(encoding="utf-8"))["receiptFingerprint"] == receipt.receipt_fingerprint
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(CliTransportReceiptError, match="regular file"):
        write_cli_transport_receipt(link, receipt)
