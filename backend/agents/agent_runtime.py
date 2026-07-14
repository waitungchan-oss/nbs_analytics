from __future__ import annotations

import json
import fcntl
from contextlib import contextmanager
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Callable, Protocol
from uuid import uuid4

from backend.agents.evidence_models import (
    ALLOWED_CONTEXT_STATUSES,
    ALLOWED_REVIEW_STATUSES,
    EvidenceBundle,
    canonical_fingerprint,
    estimate_tokens,
)


DEFAULT_INPUT_TOKEN_LIMIT = 12_000
DEFAULT_OUTPUT_TOKEN_LIMIT = 1_500
_SAFE_AGENT_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_ALLOWED_TELEMETRY_RESULTS = ALLOWED_CONTEXT_STATUSES | ALLOWED_REVIEW_STATUSES
_TELEMETRY_MAX_BYTES = 1024 * 1024
_TELEMETRY_MAX_LINE_BYTES = 4096


class AgentRunner(Protocol):
    def run(self, payload: dict) -> dict: ...


def resolve_runtime_output_path(project_root: Path, raw_path: str) -> Path:
    project_lexical = Path(os.path.abspath(os.fspath(project_root)))
    root_lexical = project_lexical / ".nbs_agent_runtime"
    candidate = Path(raw_path)
    candidate_lexical = Path(os.path.abspath(
        os.fspath(project_lexical / candidate if not candidate.is_absolute() else candidate)
    ))
    try:
        relative = candidate_lexical.relative_to(root_lexical)
    except ValueError as exc:
        raise PermissionError(f"Agent output must stay under {root_lexical}") from exc
    if relative == Path("."):
        raise PermissionError(f"Agent output must be a file below {root_lexical}")
    current = root_lexical
    for part in relative.parts[:-1]:
        if current.is_symlink():
            raise PermissionError(f"Agent output parent cannot be a symlink: {current}")
        current = current / part
    if current.is_symlink():
        raise PermissionError(f"Agent output parent cannot be a symlink: {current}")
    root = root_lexical.resolve()
    resolved = candidate_lexical.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Agent output must stay under {root_lexical}") from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_implementation_runtime_path(project_root: Path, raw_path: str) -> Path:
    project_lexical = Path(os.path.abspath(os.fspath(project_root)))
    runtime_root = project_lexical / ".nbs_agent_runtime"
    implementation_root = runtime_root / "implementation"
    candidate = Path(raw_path)
    raw_candidate = implementation_root / candidate if not candidate.is_absolute() else candidate
    candidate_lexical = Path(os.path.abspath(os.fspath(raw_candidate)))
    try:
        relative = candidate_lexical.relative_to(implementation_root)
    except ValueError as exc:
        raise PermissionError(
            f"Implementation runtime output must stay under {implementation_root}"
        ) from exc
    if relative == Path("."):
        raise PermissionError("Implementation runtime output must be a file below implementation")
    current = runtime_root
    for part in ("implementation", *relative.parts[:-1]):
        if current.is_symlink():
            raise PermissionError(f"Implementation runtime parent cannot be a symlink: {current}")
        current = current / part
    if current.is_symlink():
        raise PermissionError(f"Implementation runtime parent cannot be a symlink: {current}")
    resolved = candidate_lexical.resolve()
    try:
        resolved.relative_to(implementation_root.resolve())
    except ValueError as exc:
        raise PermissionError(
            f"Implementation runtime output must stay under {implementation_root}"
        ) from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def agent_request_fingerprint(
    bundle: EvidenceBundle,
    instructions: str,
    output_schema: str,
    evidence_payload: dict | None = None,
) -> str:
    public_evidence = bundle.to_dict() if evidence_payload is None else evidence_payload
    return canonical_fingerprint(
        {
            "sourceBundleFingerprint": bundle.fingerprint,
            "publicEvidence": public_evidence,
            "instructions": instructions,
            "outputSchema": output_schema,
        }
    )


def _resolve_executable(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        if not path.is_file():
            raise FileNotFoundError(value)
        return path.resolve()
    found = shutil.which(value)
    if not found:
        raise FileNotFoundError(value)
    return Path(found).resolve()


class SubprocessAgentRunner:
    def __init__(
        self,
        argv: list[str],
        allowed_executables: tuple[str, ...],
        timeout_seconds: int = 120,
    ) -> None:
        if not argv:
            raise ValueError("Agent command cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("Agent timeout must be positive")

        executable = _resolve_executable(argv[0])
        allowed: set[Path] = set()
        for value in allowed_executables:
            try:
                allowed.add(_resolve_executable(value))
            except FileNotFoundError:
                continue
        if executable not in allowed:
            raise PermissionError(f"Agent executable is not allowlisted: {executable}")
        self.argv = (str(executable), *argv[1:])
        self.timeout_seconds = timeout_seconds

    def run(self, payload: dict) -> dict:
        completed = subprocess.run(
            list(self.argv),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Agent command failed with exit {completed.returncode}: {completed.stderr[:1000]}"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Agent output is not valid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("Agent output must be a JSON object")
        return result


class AgentRuntime:
    def __init__(
        self,
        runtime_root: Path,
        input_token_limit: int | None = None,
        output_token_limit: int | None = None,
    ) -> None:
        self.runtime_root = runtime_root.resolve()
        if self.runtime_root.name != ".nbs_agent_runtime":
            raise PermissionError(
                f"Agent runtime root must be named .nbs_agent_runtime: {self.runtime_root}"
            )
        configured = self._load_configured_budgets()
        self.input_token_limit = input_token_limit or configured[0]
        self.output_token_limit = output_token_limit or configured[1]
        if self.input_token_limit <= 0 or self.output_token_limit <= 0:
            raise ValueError("Agent token budgets must be positive")

    def _load_configured_budgets(self) -> tuple[int, int]:
        config_path = self.runtime_root.parent / "agent_config" / "token_budgets.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            context = config["context"]
            return int(context["inputTokens"]), int(context["outputTokens"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return DEFAULT_INPUT_TOKEN_LIMIT, DEFAULT_OUTPUT_TOKEN_LIMIT

    def _paths(self, agent_name: str, fingerprint: str) -> tuple[Path, Path]:
        safe_name = _SAFE_AGENT_NAME.sub("-", agent_name).strip(".-") or "agent"
        report = self.runtime_root / "reports" / f"{safe_name}-{fingerprint}.json"
        telemetry = self.runtime_root / "telemetry" / "agent_runs.jsonl"
        report.parent.mkdir(parents=True, exist_ok=True)
        telemetry.parent.mkdir(parents=True, exist_ok=True)
        (self.runtime_root / "locks").mkdir(parents=True, exist_ok=True)
        return report, telemetry

    def _lock_path(self, fingerprint: str) -> Path:
        return self.runtime_root / "locks" / f"{fingerprint}.lock"

    @staticmethod
    @contextmanager
    def _locked(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _write_json_atomic(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _schema_check(result: object, output_schema: str) -> dict:
        if not isinstance(result, dict):
            raise ValueError("Agent output must be a JSON object")
        if result.get("schemaVersion") != output_schema:
            raise ValueError(f"Unexpected agent schema: {result.get('schemaVersion')}")
        return result

    def _telemetry(
        self,
        path: Path,
        *,
        agent_name: str,
        bundle: EvidenceBundle,
        request_fingerprint: str,
        input_text: str,
        result: dict | None,
        cache_hit: bool,
        started: float,
    ) -> None:
        safe_agent_name = _SAFE_AGENT_NAME.sub("-", agent_name).strip(".-")[:64] or "agent"
        telemetry_result = "unknown"
        if result:
            for key in ("status", "verdict"):
                candidate_result = result.get(key)
                if isinstance(candidate_result, str) and candidate_result in _ALLOWED_TELEMETRY_RESULTS:
                    telemetry_result = candidate_result
                    break
        record = {
            "runId": uuid4().hex,
            "agent": safe_agent_name,
            "bundleFingerprint": bundle.fingerprint,
            "requestFingerprint": request_fingerprint,
            "inputCharacters": len(input_text),
            "estimatedInputTokens": estimate_tokens(input_text),
            "outputTokens": estimate_tokens(json.dumps(result, ensure_ascii=False)) if result else 0,
            "filesConsidered": len(bundle.evidence),
            "filesIncluded": len(bundle.evidence),
            "cacheHit": cache_hit,
            "durationMs": round((perf_counter() - started) * 1000, 3),
            "result": telemetry_result if result else "context_overflow",
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(line.encode("utf-8")) > _TELEMETRY_MAX_LINE_BYTES:
            raise ValueError("Agent telemetry record exceeds size limit")
        lock_path = self.runtime_root / "locks" / "telemetry.lock"
        with self._locked(lock_path):
            if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > _TELEMETRY_MAX_BYTES:
                rotated = path.with_name(f"{path.name}.1")
                if rotated.exists():
                    rotated.unlink()
                os.replace(path, rotated)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def run(
        self,
        agent_name: str,
        bundle: EvidenceBundle,
        runner: AgentRunner,
        output_schema: str,
        instructions: str,
        evidence_payload: dict | None = None,
        output_validator: Callable[[object], dict] | None = None,
    ) -> dict:
        public_evidence = bundle.to_dict() if evidence_payload is None else evidence_payload
        request_fingerprint = agent_request_fingerprint(
            bundle,
            instructions=instructions,
            output_schema=output_schema,
            evidence_payload=public_evidence,
        )
        payload = {
            "contractVersion": output_schema,
            "instructions": instructions,
            "evidence": public_evidence,
            "sourceBundleFingerprint": bundle.fingerprint,
            "bundleFingerprint": request_fingerprint,
        }
        input_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        report_path, telemetry_path = self._paths(agent_name, request_fingerprint)
        started = perf_counter()
        input_tokens = estimate_tokens(input_text)
        if input_tokens > self.input_token_limit:
            result = {
                "schemaVersion": output_schema,
                "status": "context_overflow",
                "requestFingerprint": request_fingerprint,
            }
            self._telemetry(
                telemetry_path,
                agent_name=agent_name,
                bundle=bundle,
                request_fingerprint=request_fingerprint,
                input_text=input_text,
                result=result,
                cache_hit=False,
                started=started,
            )
            return result

        result: dict
        cache_hit = False
        with self._locked(self._lock_path(request_fingerprint)):
            if report_path.exists():
                try:
                    result = self._schema_check(
                        json.loads(report_path.read_text(encoding="utf-8")), output_schema
                    )
                    if output_validator is not None:
                        result = self._schema_check(output_validator(result), output_schema)
                    if estimate_tokens(json.dumps(result, ensure_ascii=False)) > self.output_token_limit:
                        raise ValueError("Cached agent output exceeds output token budget")
                    cache_hit = True
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    cache_hit = False
                    report_path.unlink(missing_ok=True)

            if not cache_hit:
                result = self._schema_check(runner.run(payload), output_schema)
                if output_validator is not None:
                    result = self._schema_check(output_validator(result), output_schema)
                if estimate_tokens(json.dumps(result, ensure_ascii=False)) > self.output_token_limit:
                    raise ValueError("Agent output exceeds output token budget")
                self._write_json_atomic(report_path, result)

        self._telemetry(
            telemetry_path,
            agent_name=agent_name,
            bundle=bundle,
            request_fingerprint=request_fingerprint,
            input_text=input_text,
            result=result,
            cache_hit=cache_hit,
            started=started,
        )
        return result
