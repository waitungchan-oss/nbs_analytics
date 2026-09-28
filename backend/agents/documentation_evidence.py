from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .workflow_models import canonical_sha256
from .workflow_store import WorkflowStore
from .memory_hub_integration_models import MemoryHubIntegrationEvidence
from .documentation_models import DocumentationEvidenceV2, DocumentationSchemaError
from .documentation_policy import load_documentation_target


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
    for key in ("overallStatus", "status", "result", "verdict"):
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
    memory_hub_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
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
        if self.memory_hub_summary is not None:
            payload["memoryHubSummary"] = dict(self.memory_hub_summary)
        return payload


class DocumentationEvidenceCollector:
    def __init__(self, project_root: Path, *, store: WorkflowStore | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = store or WorkflowStore(self.project_root)

    def collect_target(self, run_id: str, target_id: str) -> DocumentationEvidenceV2:
        """Collect source-bound gate evidence for exactly one fixed documentation target."""
        try:
            target = load_documentation_target(target_id)
        except DocumentationSchemaError as exc:
            raise DocumentationEvidenceError("target ID is not in the fixed documentation catalog") from exc

        try:
            manifest = self.store.load_manifest(run_id).to_dict()
            status = self.store.load_status(run_id).to_dict()
            approval = self.store.read_artifact(run_id, "approval.json")
        except (OSError, PermissionError, TypeError, ValueError) as exc:
            raise DocumentationEvidenceError("run manifest, status, or approval is unavailable") from exc
        if status.get("status") != "completed":
            raise DocumentationEvidenceError("run must be completed")
        if manifest.get("runId") != run_id or status.get("runId") != run_id or approval.get("runId") != run_id:
            raise DocumentationEvidenceError("run identity does not match its approval evidence")
        if approval.get("authorizationStatus") != "approved":
            raise DocumentationEvidenceError("approval gate must PASS")
        if approval.get("approvedBaseSha") != manifest.get("gitHead"):
            raise DocumentationEvidenceError("approval base does not match the run source commit")

        gate_names = ("review", "full-verification", "hermes")
        artifacts: dict[str, dict[str, Any]] = {}
        gate_source_fingerprints: dict[str, str] = {}
        for name in gate_names:
            artifact_name = f"{name}.json"
            try:
                artifact = self.store.read_artifact(run_id, artifact_name)
            except (OSError, PermissionError, TypeError, ValueError) as exc:
                raise DocumentationEvidenceError(f"{name} gate artifact is unavailable") from exc
            if not _gate_passes(name, artifact):
                raise DocumentationEvidenceError(f"{name} gate must PASS")
            source_fingerprint = artifact.get("sourceFingerprint")
            if not isinstance(source_fingerprint, str) or len(source_fingerprint) != 64 or any(
                char not in "0123456789abcdef" for char in source_fingerprint
            ):
                raise DocumentationEvidenceError(f"{name} gate is missing a valid source fingerprint")
            gate_commit = artifact.get("commitSha")
            if gate_commit != manifest.get("gitHead"):
                raise DocumentationEvidenceError(f"{name} gate commit does not match the run source")
            artifacts[name] = artifact
            gate_source_fingerprints[name] = source_fingerprint
        distinct_sources = set(gate_source_fingerprints.values())
        if len(distinct_sources) != 1:
            raise DocumentationEvidenceError("gate source fingerprint mismatch")

        try:
            _, target_text, section, _, _ = _read_target_section(self.project_root, target)
        except (OSError, UnicodeError, ValueError, PermissionError) as exc:
            raise DocumentationEvidenceError("selected documentation target is missing or unsafe") from exc
        try:
            committed_target = subprocess.run(
                ["git", "show", f"{manifest['gitHead']}:{target.repo_path}"],
                cwd=self.project_root, capture_output=True, check=False, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DocumentationEvidenceError("accepted target source could not be read from Git") from exc
        if committed_target.returncode != 0 or committed_target.stdout != target_text.encode("utf-8"):
            raise DocumentationEvidenceError("selected target differs from the accepted source commit")

        source_records = []
        for path, payload in (
            ("manifest.json", manifest), ("status.json", status),
            ("approval.json", approval),
            *((f"{name}.json", artifacts[name]) for name in gate_names),
        ):
            source_records.append({"path": f".nbs_agent_runtime/runs/{run_id}/{path}", "sha256": _payload_sha256(payload)})
        source_records.append({
            "path": target.repo_path,
            "sha256": sha256(target_text.encode("utf-8")).hexdigest(),
        })
        gate_results = tuple({
            "gate": name,
            "status": "pass",
            "sourceFingerprint": gate_source_fingerprints[name],
            "evidenceFingerprint": _payload_sha256(artifacts[name]),
        } for name in gate_names)
        unsigned = {
            "schemaVersion": "documentation-evidence-v2",
            "taskId": run_id,
            "generatedAt": status.get("completedAt") or status.get("updatedAt"),
            "runId": run_id,
            "commitSha": manifest["gitHead"],
            "sourceFingerprint": next(iter(distinct_sources)),
            "selectedTargetId": target.target_id,
            "sources": source_records,
            "gateResults": list(gate_results),
            "guardrails": {
                "revenueScope": "不含掛賬核銷與TT退款轉團款",
                "mayBaseline": "HKD 12,057,968",
            },
            "expectedSectionSha256": sha256(section.encode("utf-8")).hexdigest(),
        }
        payload = {**unsigned, "evidenceFingerprint": canonical_sha256(unsigned)}
        try:
            return DocumentationEvidenceV2.from_dict(payload)
        except DocumentationSchemaError as exc:
            raise DocumentationEvidenceError("collected target evidence is invalid") from exc

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
        memory_summary = _read_memory_summary(self.store, run_id)
        if memory_summary is not None:
            evidence["memoryHubSummary"] = memory_summary
        evidence["documentationFingerprint"] = canonical_sha256(evidence)
        return DocumentationEvidence(
            evidence["schemaVersion"], evidence["taskId"], evidence["generatedAt"],
            tuple(evidence["sources"]), artifact_hashes, changed_paths, command_results,
            coverage, summaries, evidence["gateResults"], evidence["guardrails"],
            evidence["documentationFingerprint"], memory_summary,
        )


def _read_memory_summary(store: WorkflowStore, run_id: str) -> dict[str, Any] | None:
    """Read only a precomputed artifact; never query or provision Memory Hub."""
    try:
        payload = _read_fixed_artifact(store, run_id, "memory-hub-integration.json")
        evidence = MemoryHubIntegrationEvidence.from_dict(payload)
    except (FileNotFoundError, DocumentationEvidenceError, PermissionError, TypeError, ValueError):
        return None
    if evidence.status != "ready" or evidence.consumer_id != "context-agent":
        return None
    return {
        "status": evidence.status,
        "consumerId": evidence.consumer_id,
        "integrationMode": evidence.integration_mode,
        "authority": "non_authoritative_memory",
        "evidenceFingerprint": evidence.evidence_fingerprint,
        "hintCount": evidence.hint_count,
    }


def _payload_sha256(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _gate_passes(name: str, payload: dict[str, Any]) -> bool:
    if _status(payload) in {"pass", "passed", "success", "ok"}:
        return True
    if name == "full-verification":
        full_pytest = payload.get("fullPytest")
        acceptance = payload.get("acceptance")
        return (
            isinstance(full_pytest, dict) and full_pytest.get("exitCode") == 0
            and isinstance(acceptance, dict)
            and str(acceptance.get("status", "")).lower() in {"pass", "passed"}
        )
    return False


def _read_target_section(project_root: Path, target) -> tuple[Path, str, str, int, int]:
    relative = PurePosixPath(target.repo_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("target path is not repo-relative")
    path = project_root.joinpath(*relative.parts)
    path.relative_to(project_root)
    current = project_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError("documentation target must not traverse symlinks")
    if not path.is_file():
        raise FileNotFoundError(target.repo_path)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    offsets = []
    cursor = 0
    for line in lines:
        offsets.append((cursor, cursor + len(line)))
        cursor += len(line)
    wanted_level = len(target.section_heading) - len(target.section_heading.lstrip("#"))
    wanted_title = target.section_heading[wanted_level:].strip()
    matches = []
    headings = []
    fence = None
    fence_re = re.compile(r"^ {0,3}(`{3,}|~{3,})")
    heading_re = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n)?$")
    for index, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        fence_match = fence_re.match(bare)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = None
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue
        match = heading_re.match(line)
        if not match:
            continue
        heading = (len(match.group(1)), match.group(2).strip())
        headings.append((index, *heading))
        if heading == (wanted_level, wanted_title):
            matches.append(index)
    if len(matches) != 1:
        raise ValueError("target section heading is missing or duplicated")
    start_index = matches[0]
    end_index = len(lines)
    for index, level, _ in headings:
        if index > start_index and level <= wanted_level:
            end_index = index
            break
    start = offsets[start_index][0]
    end = offsets[end_index][0] if end_index < len(offsets) else len(text)
    return path, text, text[start:end], start, end


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
