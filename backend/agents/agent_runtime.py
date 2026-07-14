from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from backend.agents.evidence_models import EvidenceBundle, canonical_fingerprint, estimate_tokens


DEFAULT_INPUT_TOKEN_LIMIT = 12_000
DEFAULT_OUTPUT_TOKEN_LIMIT = 1_500
_SAFE_AGENT_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class AgentRunner(Protocol):
    def run(self, payload: dict) -> dict: ...


def resolve_runtime_output_path(project_root: Path, raw_path: str) -> Path:
    root = (project_root / ".nbs_agent_runtime").resolve()
    candidate = Path(raw_path)
    resolved = (project_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Agent output must stay under {root}") from exc
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
        requested_name = Path(argv[0]).name
        allowed: set[Path] = set()
        allowed_names: set[str] = set()
        for value in allowed_executables:
            allowed_names.add(Path(value).name)
            try:
                allowed.add(_resolve_executable(value))
            except FileNotFoundError:
                continue
        if executable not in allowed and requested_name not in allowed_names:
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
        return report, telemetry

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
        record = {
            "runId": uuid4().hex,
            "agent": agent_name,
            "bundleFingerprint": bundle.fingerprint,
            "requestFingerprint": request_fingerprint,
            "inputCharacters": len(input_text),
            "estimatedInputTokens": estimate_tokens(input_text),
            "outputTokens": estimate_tokens(json.dumps(result, ensure_ascii=False)) if result else 0,
            "filesConsidered": len(bundle.evidence),
            "filesIncluded": len(bundle.evidence),
            "cacheHit": cache_hit,
            "durationMs": round((perf_counter() - started) * 1000, 3),
            "result": result.get("status") if result else "context_overflow",
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def run(
        self,
        agent_name: str,
        bundle: EvidenceBundle,
        runner: AgentRunner,
        output_schema: str,
        instructions: str,
        evidence_payload: dict | None = None,
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
        cache_hit = report_path.exists()
        if cache_hit:
            try:
                result = self._schema_check(
                    json.loads(report_path.read_text(encoding="utf-8")), output_schema
                )
                if estimate_tokens(json.dumps(result, ensure_ascii=False)) > self.output_token_limit:
                    raise ValueError("Cached agent output exceeds output token budget")
            except (OSError, json.JSONDecodeError, ValueError):
                cache_hit = False

        if not cache_hit:
            result = self._schema_check(runner.run(payload), output_schema)
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
