from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.agent_runtime import _decode_json_or_codex_event_stream
from backend.agents.runner_identity import RunnerIdentity, RunnerIdentityError


class RunnerProfileError(ValueError):
    """Raised when a Review runner profile is invalid or unavailable."""


@dataclass(frozen=True)
class RunnerProfile:
    executable: str
    model: str
    cache_path: Path
    cli_version_floor: str = "0.142.5"

    def to_runner_identity(
        self, *, profile_name: str, execution_environment: str, provider: str
    ) -> RunnerIdentity:
        """Adapt an explicitly named Local CLI profile to the shared identity contract."""
        return RunnerIdentity.from_legacy_local_cli(
            runner_id=profile_name,
            provider=provider,
            model=self.model,
            profile=profile_name,
            execution_environment=execution_environment,
        )

    @classmethod
    def from_dict(cls, payload: dict, *, base_dir: Path) -> "RunnerProfile":
        if not isinstance(payload, dict):
            raise RunnerProfileError("runner profile must be an object")
        executable = payload.get("executable")
        model = payload.get("model")
        cache_path = payload.get("cachePath")
        if not all(isinstance(value, str) and value.strip() for value in (executable, model, cache_path)):
            raise RunnerProfileError("runner profile requires executable, model, and cachePath")
        return cls(
            executable=executable,
            model=model,
            cache_path=(base_dir / cache_path).resolve() if not Path(cache_path).is_absolute() else Path(cache_path),
            cli_version_floor=str(payload.get("cliVersionFloor", "0.142.5")),
        )


@dataclass(frozen=True)
class RunnerPreflightResult:
    status: str
    executable: str
    cli_version: str | None
    model: str
    cache_schema_status: str
    diagnostics: tuple[str, ...] = ()
    recovery: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "executable": self.executable,
            "cliVersion": self.cli_version,
            "model": self.model,
            "cacheSchemaStatus": self.cache_schema_status,
            "diagnostics": list(self.diagnostics),
            "recovery": list(self.recovery),
        }


def load_runner_profile(path: Path, *, base_dir: Path | None = None) -> RunnerProfile:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RunnerProfile.from_dict(payload, base_dir=base_dir or path.parent)


def preflight_runner(profile: RunnerProfile) -> RunnerPreflightResult:
    diagnostics: list[str] = []
    recovery: list[str] = []
    executable = Path(profile.executable)
    if not executable.is_absolute():
        diagnostics.append("runner executable must be absolute")
    elif not executable.is_file():
        diagnostics.append("runner executable is unavailable")

    cli_version: str | None = None
    if not diagnostics:
        try:
            result = subprocess.run(
                [str(executable), "--version"], capture_output=True, text=True,
                timeout=5, check=False,
            )
            cli_version = (result.stdout or result.stderr).strip()[:128]
            if result.returncode != 0:
                diagnostics.append("runner version probe failed")
        except (OSError, subprocess.TimeoutExpired):
            diagnostics.append("runner version probe unavailable")
    if not diagnostics and not _version_at_least(cli_version, profile.cli_version_floor):
        diagnostics.append("installed CLI is below the configured version floor")

    cache_status = "compatible"
    try:
        cache = json.loads(profile.cache_path.read_text(encoding="utf-8"))
        models = cache.get("models") if isinstance(cache, dict) else None
        if not isinstance(models, list):
            raise ValueError
        selected = next((item for item in models if isinstance(item, dict) and item.get("slug") == profile.model), None)
        if selected is None:
            cache_status = "incompatible"
            diagnostics.append("requested model is missing from cache")
        elif not _cache_entry_is_compatible(selected, cli_version):
            cache_status = "incompatible"
            diagnostics.append("models cache entry is incompatible with the installed CLI")
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        cache_status = "unavailable"
        diagnostics.append("models cache is invalid or unavailable")
    if cache_status != "compatible":
        recovery.append("repair or replace the cache with a CLI-compatible backup, then rerun preflight")
    return RunnerPreflightResult(
        status="ready" if not diagnostics else "blocked_runtime",
        executable=str(executable), cli_version=cli_version, model=profile.model,
        cache_schema_status=cache_status, diagnostics=tuple(diagnostics), recovery=tuple(recovery),
    )


def _cache_entry_is_compatible(entry: dict, cli_version: str | None) -> bool:
    """Accept the schema emitted by the matching CLI generation only."""
    match = re.search(r"(\d+)\.(\d+)", cli_version or "")
    modern_cli = match is not None and (int(match.group(1)), int(match.group(2))) >= (0, 150)
    if not modern_cli and isinstance(entry.get("base_instructions"), str):
        return True
    messages = entry.get("model_messages")
    return modern_cli and isinstance(messages, dict) and isinstance(messages.get("instructions_template"), str)


def _version_at_least(actual: str | None, required: str) -> bool:
    actual_match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", actual or "")
    required_match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", required)
    if not actual_match or not required_match:
        return False
    actual_value = tuple(int(actual_match.group(index) or 0) for index in (1, 2, 3))
    required_value = tuple(int(required_match.group(index) or 0) for index in (1, 2, 3))
    return actual_value >= required_value


# ---------------------------------------------------------------------------
# Task 3: live runner capability probe / RunnerCapabilityReceipt
# ---------------------------------------------------------------------------

_RECEIPT_SCHEMA = "runner-capability-v1"
_RECEIPT_KEYS = {
    "schemaVersion", "status", "executable", "cliVersion", "model",
    "cacheFingerprint", "environmentFingerprint", "diagnostics",
    "expiresByFingerprint",
}
ALLOWED_RECEIPT_STATUSES = {
    "static_ready", "turn_ready", "blocked_runner_capability",
    "blocked_runner_transport",
}
_PROBE_TIMEOUT_SECONDS = 15
_PROBE_MAX_OUTPUT_BYTES = 8 * 1024
# Fixed, short, read-only probe command shape (model and prompt are filled in).
_PROBE_ARGV_TEMPLATE = ("exec", "--ephemeral", "--json", "--model", "<model>", "<prompt>")
_PROBE_PROMPT = (
    'Reply with only the JSON object {"status":"ok","model":"<your-model-name>"}.'
)
# Codex 0.150.x reports the selected gpt-5.4 slug as this stable display name
# in a model-authored probe response.  Keep this allowlist narrow: an unknown
# model name must still fail closed.
_MODEL_DISPLAY_ALIASES = {
    "gpt-5.4": frozenset({"gpt-5.4", "gpt-5", "gpt-5 codex"}),
}
_ENV_IDENTITY_KEYS = ("CODEX_HOME", "HOME")


@dataclass(frozen=True)
class RunnerCapabilityReceipt:
    """Bounded receipt for static/live Review runner capability."""

    schema_version: str
    status: str
    executable: str
    cli_version: str | None
    model: str
    cache_fingerprint: str
    environment_fingerprint: str
    diagnostics: tuple[str, ...] = ()
    expires_by_fingerprint: str = ""
    runner_identity: RunnerIdentity | None = None

    def __post_init__(self) -> None:
        if self.schema_version != _RECEIPT_SCHEMA:
            raise RunnerProfileError("schemaVersion must be runner-capability-v1")
        if self.status not in ALLOWED_RECEIPT_STATUSES:
            raise RunnerProfileError(f"runner capability status is invalid: {self.status}")
        for key, value in (
            ("executable", self.executable), ("model", self.model),
            ("cacheFingerprint", self.cache_fingerprint),
            ("environmentFingerprint", self.environment_fingerprint),
        ):
            if not isinstance(value, str) or not value:
                raise RunnerProfileError(f"{key} must be a non-empty string")
        if self.cli_version is not None and not isinstance(self.cli_version, str):
            raise RunnerProfileError("cliVersion must be a string or null")
        if not all(isinstance(item, str) for item in self.diagnostics):
            raise RunnerProfileError("diagnostics must be a list of strings")
        if not isinstance(self.expires_by_fingerprint, str):
            raise RunnerProfileError("expiresByFingerprint must be a string")
        if self.runner_identity is not None:
            if not isinstance(self.runner_identity, RunnerIdentity):
                raise RunnerProfileError("runnerIdentity must be a RunnerIdentity")
            if self.runner_identity.model != self.model:
                raise RunnerProfileError("runnerIdentity model must match receipt model")

    @classmethod
    def from_dict(cls, payload: dict) -> "RunnerCapabilityReceipt":
        if not isinstance(payload, dict):
            raise RunnerProfileError("runner capability receipt must be an object")
        if set(payload) - (_RECEIPT_KEYS | {"runnerIdentity"}) or not _RECEIPT_KEYS <= set(payload):
            raise RunnerProfileError("runner capability receipt schema keys are invalid")
        try:
            runner_identity = (
                RunnerIdentity.from_dict(payload["runnerIdentity"])
                if "runnerIdentity" in payload else None
            )
        except RunnerIdentityError as exc:
            raise RunnerProfileError(f"runnerIdentity is invalid: {exc}") from exc
        return cls(
            schema_version=payload["schemaVersion"], status=payload["status"],
            executable=payload["executable"], cli_version=payload["cliVersion"],
            model=payload["model"], cache_fingerprint=payload["cacheFingerprint"],
            environment_fingerprint=payload["environmentFingerprint"],
            diagnostics=tuple(payload["diagnostics"]),
            expires_by_fingerprint=payload["expiresByFingerprint"],
            runner_identity=runner_identity,
        )

    def to_dict(self) -> dict:
        value = {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "executable": self.executable,
            "cliVersion": self.cli_version,
            "model": self.model,
            "cacheFingerprint": self.cache_fingerprint,
            "environmentFingerprint": self.environment_fingerprint,
            "diagnostics": list(self.diagnostics),
            "expiresByFingerprint": self.expires_by_fingerprint,
        }
        if self.runner_identity is not None:
            value["runnerIdentity"] = self.runner_identity.to_dict()
        return value


def _cache_fingerprint(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _environment_fingerprint() -> str:
    return canonical_fingerprint({
        "platform": sys.platform,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        **{key: os.environ.get(key, "") for key in _ENV_IDENTITY_KEYS},
    })


def capability_fingerprint(
    profile: RunnerProfile,
    *,
    cli_version: str | None,
    cache_fingerprint: str,
) -> str:
    """Canonical identity of the capability a receipt expires with.

    Covers the executable path, CLI version, selected model, cache bytes,
    fixed probe command shape and relevant non-secret environment identity.
    """
    return canonical_fingerprint({
        "executable": str(Path(profile.executable).resolve()),
        "cliVersion": cli_version,
        "model": profile.model,
        "cacheFingerprint": cache_fingerprint,
        "probeCommandShape": list(_PROBE_ARGV_TEMPLATE),
        "environmentFingerprint": _environment_fingerprint(),
    })


def _build_receipt(
    profile: RunnerProfile,
    *,
    status: str,
    cli_version: str | None,
    cache_fingerprint: str,
    environment_fingerprint: str,
    current_expiry: str,
    diagnostics: tuple[str, ...],
) -> RunnerCapabilityReceipt:
    return RunnerCapabilityReceipt(
        schema_version=_RECEIPT_SCHEMA,
        status=status,
        executable=str(Path(profile.executable).resolve()),
        cli_version=cli_version,
        model=profile.model,
        cache_fingerprint=cache_fingerprint,
        environment_fingerprint=environment_fingerprint,
        diagnostics=diagnostics,
        expires_by_fingerprint=current_expiry,
    )


def _probe_argv(profile: RunnerProfile) -> list[str]:
    return [profile.executable] + [
        profile.model if part == "<model>" else _PROBE_PROMPT if part == "<prompt>" else part
        for part in _PROBE_ARGV_TEMPLATE
    ]


def _run_live_probe(
    profile: RunnerProfile,
    *,
    cli_version: str | None,
    cache_fingerprint: str,
    environment_fingerprint: str,
    current_expiry: str,
) -> RunnerCapabilityReceipt:
    """Run exactly one short, read-only live turn probe (no retry)."""
    argv = _probe_argv(profile)
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS,
            check=False, shell=False,
        )
    except subprocess.TimeoutExpired:
        return _build_receipt(
            profile, status="blocked_runner_transport", cli_version=cli_version,
            cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry, diagnostics=("live probe timed out",),
        )
    except OSError as exc:
        return _build_receipt(
            profile, status="blocked_runner_transport", cli_version=cli_version,
            cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry, diagnostics=(f"live probe could not start: {exc}",),
        )

    if completed.returncode != 0:
        diagnostics = (f"live probe exited nonzero ({completed.returncode})",)
        stderr_tail = completed.stderr.strip()[:200]
        if stderr_tail:
            diagnostics = (*diagnostics, stderr_tail)
        return _build_receipt(
            profile, status="blocked_runner_transport", cli_version=cli_version,
            cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry, diagnostics=diagnostics,
        )

    if len(completed.stdout.encode("utf-8")) > _PROBE_MAX_OUTPUT_BYTES:
        return _build_receipt(
            profile, status="blocked_runner_transport", cli_version=cli_version,
            cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry, diagnostics=("live probe output exceeds 8 KiB bound",),
        )

    try:
        response = _decode_json_or_codex_event_stream(completed.stdout)
    except json.JSONDecodeError:
        return _build_receipt(
            profile, status="blocked_runner_transport", cli_version=cli_version,
            cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry, diagnostics=("live probe response is not valid JSON",),
        )

    if not isinstance(response, dict) or response.get("status") != "ok":
        return _build_receipt(
            profile, status="blocked_runner_transport", cli_version=cli_version,
            cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry, diagnostics=("live probe response contract is invalid",),
        )

    reported_model = response.get("model")
    accepted_models = _MODEL_DISPLAY_ALIASES.get(
        profile.model, frozenset({profile.model.casefold()})
    )
    if not isinstance(reported_model, str) or reported_model.casefold() not in accepted_models:
        return _build_receipt(
            profile, status="blocked_runner_transport", cli_version=cli_version,
            cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry,
            diagnostics=(f"live probe model mismatch: {reported_model!r}",),
        )

    return _build_receipt(
        profile, status="turn_ready", cli_version=cli_version,
        cache_fingerprint=cache_fingerprint, environment_fingerprint=environment_fingerprint,
        current_expiry=current_expiry, diagnostics=(),
    )


def probe_runner(
    profile: RunnerProfile,
    *,
    receipt_path: Path | str | None = None,
    runner_identity: RunnerIdentity | None = None,
) -> RunnerCapabilityReceipt:
    """Probe a Review runner's capability and return a bounded receipt.

    Static failure is reported as ``blocked_runner_capability`` without a
    live probe. A stored ``turn_ready`` receipt whose
    ``expiresByFingerprint`` matches the current capability fingerprint is
    reused; a stale receipt triggers exactly one new probe. The probe is
    short, read-only, uses a fixed prompt, carries no business data and is
    never retried on failure.
    """
    static = preflight_runner(profile)
    cache_fingerprint = _cache_fingerprint(profile.cache_path)
    environment_fingerprint = _environment_fingerprint()
    current_expiry = capability_fingerprint(
        profile, cli_version=static.cli_version, cache_fingerprint=cache_fingerprint
    )
    if static.status != "ready":
        return _build_receipt(
            profile, status="blocked_runner_capability",
            cli_version=static.cli_version, cache_fingerprint=cache_fingerprint,
            environment_fingerprint=environment_fingerprint,
            current_expiry=current_expiry, diagnostics=static.diagnostics,
        )
    if receipt_path is not None:
        cached = load_capability_receipt(receipt_path)
        if (
            cached is not None
            and cached.status == "turn_ready"
            and cached.expires_by_fingerprint == current_expiry
        ):
            return cached
    receipt = _run_live_probe(
        profile, cli_version=static.cli_version, cache_fingerprint=cache_fingerprint,
        environment_fingerprint=environment_fingerprint, current_expiry=current_expiry,
    )
    if runner_identity is not None:
        receipt = RunnerCapabilityReceipt(**{**receipt.__dict__, "runner_identity": runner_identity})
    if receipt_path is not None and receipt.status == "turn_ready":
        write_capability_receipt(receipt_path, receipt)
    return receipt


def write_capability_receipt(path: Path | str, receipt: RunnerCapabilityReceipt) -> Path:
    if not isinstance(receipt, RunnerCapabilityReceipt):
        raise RunnerProfileError("write_capability_receipt requires a RunnerCapabilityReceipt")
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{resolved.name}.", dir=resolved.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(receipt.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return resolved


def load_capability_receipt(path: Path | str) -> RunnerCapabilityReceipt | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return RunnerCapabilityReceipt.from_dict(payload)
    except RunnerProfileError:
        return None
