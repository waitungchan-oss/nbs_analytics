from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .workflow_models import canonical_sha256
from .workflow_store import WorkflowStore


REQUIRED_ARTIFACTS = (
    "manifest.json",
    "status.json",
    "approval.json",
    "implementation.json",
    "targeted-verification.json",
    "review.json",
    "full-verification.json",
    "hermes.json",
)
_GATE_ARTIFACTS = ("review.json", "full-verification.json", "hermes.json")
_MAX_ITEMS = 64
_MAX_TEXT = 512


class DocumentationEvidenceError(RuntimeError):
    """Raised when a workflow run is not safe to use as documentation evidence."""


def _bounded_text(value: Any) -> str:
    return str(value)[:_MAX_TEXT]


def _status(payload: dict[str, Any]) -> str:
    for key in ("overallStatus", "status", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return _bounded_text(value.lower())
    return ""


def _read_fixed_artifact(store: WorkflowStore, run_id: str, name: str) -> dict[str, Any]:
    # The name is selected only from REQUIRED_ARTIFACTS; WorkflowStore still owns path checks.
    return store._read_json(store._run_file(run_id, name))


@dataclass(frozen=True)
class DocumentationEvidence:
    schema_version: str
    task_id: str
    generated_at: str
    sources: tuple[dict[str, str], ...]
    artifact_hashes: dict[str, str]
    changed_paths: tuple[str, ...]
    command_results: tuple[dict[str, Any], ...]
    requirement_coverage: tuple[str, ...]
    summaries: dict[str, str]
    gate_results: dict[str, str]
    guardrails: dict[str, str]
    documentation_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "generatedAt": self.generated_at,
            "sources": [dict(item) for item in self.sources],
            "artifactHashes": dict(self.artifact_hashes),
            "changedPaths": list(self.changed_paths),
            "commandResults": [dict(item) for item in self.command_results],
            "requirementCoverage": list(self.requirement_coverage),
            "summaries": dict(self.summaries),
            "gateResults": dict(self.gate_results),
            "guardrails": dict(self.guardrails),
            "documentationFingerprint": self.documentation_fingerprint,
        }


class DocumentationEvidenceCollector:
    def __init__(self, project_root: Path, *, store: WorkflowStore | None = None) -> None:
        self.store = store or WorkflowStore(Path(project_root))

    def collect(self, run_id: str) -> DocumentationEvidence:
        manifest = self.store.load_manifest(run_id).to_dict()
        status = self.store.load_status(run_id).to_dict()
        artifacts = {name: _read_fixed_artifact(self.store, run_id, name)
                     for name in REQUIRED_ARTIFACTS[2:]}
        if status.get("status") != "completed":
            raise DocumentationEvidenceError("run must be completed")
        for name in _GATE_ARTIFACTS:
            if _status(artifacts[name]) not in {"pass", "passed", "success", "ok"}:
                label = "Hermes" if name == "hermes.json" else name[:-5]
                raise DocumentationEvidenceError(f"{label} gate must PASS")

        approval = _read_fixed_artifact(self.store, run_id, "approval.json")
        if approval.get("authorizationStatus") != "approved":
            raise DocumentationEvidenceError("approval gate must PASS")

        # artifactBytes is store bookkeeping and changes when this sidecar writes
        # its own bounded artifacts; it is not implementation evidence.
        stable_status = {key: value for key, value in status.items() if key != "artifactBytes"}
        artifact_hashes = {
            name: sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
            for name, payload in (("manifest.json", manifest), ("status.json", status),
                                  ("approval.json", approval), *artifacts.items())}
        artifact_hashes["status.json"] = sha256(
            json.dumps(stable_status, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        changed_paths = _collect_paths(artifacts)
        command_results = _collect_commands(artifacts)
        coverage = _collect_strings(artifacts, ("requirements", "requirementCoverage", "coveredRequirements"))
        summaries = {name[:-5]: _bounded_text(payload.get("summary", payload.get("message", "")))
                     for name, payload in artifacts.items() if payload.get("summary") or payload.get("message")}
        sources = [{"path": name, "sha256": digest} for name, digest in artifact_hashes.items()]
        brief_source = _manifest_brief_source(manifest)
        if brief_source is not None and brief_source not in sources:
            sources.append(brief_source)
        evidence = {
            "schemaVersion": "documentation-evidence-v1",
            "taskId": _bounded_text(run_id),
            "generatedAt": _bounded_text(status.get("completedAt") or status.get("updatedAt") or datetime.now(timezone.utc).isoformat()),
            "sources": sources,
            "artifactHashes": artifact_hashes,
            "changedPaths": list(changed_paths),
            "commandResults": list(command_results),
            "requirementCoverage": list(coverage),
            "summaries": summaries,
            "gateResults": {name[:-5]: _status(artifacts[name]) for name in _GATE_ARTIFACTS},
            "guardrails": {"revenueScope": "不含掛賬核銷與TT退款轉團款", "mayBaseline": "HKD 12,057,968"},
        }
        evidence["documentationFingerprint"] = canonical_sha256(evidence)
        return DocumentationEvidence(
            evidence["schemaVersion"], evidence["taskId"], evidence["generatedAt"],
            tuple(evidence["sources"]), artifact_hashes, changed_paths, command_results,
            coverage, summaries, evidence["gateResults"], evidence["guardrails"],
            evidence["documentationFingerprint"],
        )


def _collect_paths(artifacts: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    values: list[str] = []
    for payload in artifacts.values():
        for key in ("changedPaths", "files", "paths"):
            items = payload.get(key, [])
            if isinstance(items, list):
                for item in items[:_MAX_ITEMS]:
                    if isinstance(item, str):
                        if item:
                            values.append(_bounded_text(item))
                    elif isinstance(item, dict):
                        path = item.get("path")
                        if not isinstance(path, str) or not path:
                            raise DocumentationEvidenceError("changed path must be a non-empty string")
                        values.append(_bounded_text(path))
    return tuple(sorted({item for item in values if item})[:_MAX_ITEMS])


def _manifest_brief_source(manifest: dict[str, Any]) -> dict[str, str] | None:
    value = manifest.get("briefPath")
    digest = manifest.get("briefSha256")
    if not isinstance(value, str) or not isinstance(digest, str):
        return None
    normalized = value.replace("\\", "/").strip()
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value.strip())
    segments = normalized.split("/")
    if (
        not normalized
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(segment in {".", ".."} for segment in segments)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        return None
    return {"path": posix.as_posix(), "sha256": digest}


def _collect_commands(artifacts: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    for payload in artifacts.values():
        commands = payload.get("commands", [])
        if not isinstance(commands, list):
            continue
        for item in commands[:_MAX_ITEMS]:
            if isinstance(item, dict) and isinstance(item.get("command"), str):
                result = {
                    "commandId": sha256(item["command"].encode("utf-8")).hexdigest(),
                    "summary": _bounded_text(item.get("summary", item.get("message", ""))),
                }
                if isinstance(item.get("exitCode"), int):
                    result["exitCode"] = item["exitCode"]
                results.append(result)
    return tuple(results[:_MAX_ITEMS])


def _collect_strings(artifacts: dict[str, dict[str, Any]], keys: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for payload in artifacts.values():
        for key in keys:
            items = payload.get(key, [])
            if isinstance(items, list):
                values.extend(_bounded_text(item) for item in items[:_MAX_ITEMS] if isinstance(item, str))
    return tuple(sorted(set(values))[:_MAX_ITEMS])
