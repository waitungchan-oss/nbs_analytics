from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_MAX_ARTIFACT_BYTES = 512 * 1024
_NO_DOC_STATUSES = frozenset({"no_doc"})
_STALE_MARKERS = frozenset({"stale", "stale_artifact", "stale_target", "stale_upstream", "stale_source", "stale_input"})
_BLOCKED_MARKERS = frozenset({"blocked", "blocked_missing_runner", "failed", "changes_required"})
_PROTECTED_MARKERS = frozenset({"protected_incident", "protected-incident.json"})


def _read_snapshot(path: Path) -> tuple[dict[str, Any], str] | None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_ARTIFACT_BYTES:
        return None
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_ARTIFACT_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return (value, hashlib.sha256(raw).hexdigest()) if isinstance(value, dict) else None


def _read_json(path: Path) -> dict[str, Any] | None:
    snapshot = _read_snapshot(path)
    return snapshot[0] if snapshot else None


def _contains_marker(value: Any, markers: frozenset[str]) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_marker(k, markers) or _contains_marker(v, markers) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_marker(item, markers) for item in value)
    return isinstance(value, str) and value in markers


@dataclass(frozen=True)
class CompletedRunGate:
    run_id: str
    run_root: Path
    workflow_status: str
    review_verdict: str | None
    verification_status: str | None
    hermes_status: str | None
    documentation_status: str
    stale_upstream: bool
    protected_incident: bool
    blocked_upstream: bool
    gate_status: str
    artifact_fingerprints: tuple[tuple[str, str], ...] = ()
    artifact_payloads: tuple[tuple[str, dict[str, Any]], ...] = ()

    @classmethod
    def from_run(cls, root: Path, run_id: str) -> "CompletedRunGate":
        run_root = Path(root) / run_id
        if not run_root.is_dir() or run_root.is_symlink():
            return cls(run_id, run_root, "missing", None, None, None, "missing", True, False, True, "missing")
        required_names = ("manifest.json", "status.json", "review.json", "full-verification.json", "hermes.json")
        snapshots: dict[str, tuple[dict[str, Any], str]] = {}
        for name in required_names:
            snapshot = _read_snapshot(run_root / name)
            if snapshot is None:
                return cls(run_id, run_root, "missing", None, None, None, "missing", True, False, True, "blocked")
            snapshots[name] = snapshot
        status = snapshots["status.json"][0]; manifest = snapshots["manifest.json"][0]
        review = snapshots["review.json"][0]; verification = snapshots["full-verification.json"][0]
        hermes = snapshots["hermes.json"][0]
        implementation_snapshot = _read_snapshot(run_root / "implementation.json") if (run_root / "implementation.json").exists() else None
        implementation = implementation_snapshot[0] if implementation_snapshot else None
        doc_path = run_root / "documentation-evidence.json"
        documentation_snapshot = _read_snapshot(doc_path) if doc_path.exists() else None
        if doc_path.exists() and documentation_snapshot is None:
            return cls(run_id, run_root, "missing", None, None, None, "invalid", True, False, True, "blocked")
        documentation = documentation_snapshot[0] if documentation_snapshot else None
        workflow_status = status.get("status") if isinstance(status.get("status"), str) else "missing"
        review_verdict = review.get("verdict") if isinstance(review.get("verdict"), str) else None
        acceptance = verification.get("acceptance") if isinstance(verification.get("acceptance"), Mapping) else {}
        pytest_result = verification.get("fullPytest") if isinstance(verification.get("fullPytest"), Mapping) else {}
        verification_status = "passed" if pytest_result.get("exitCode") == 0 and acceptance.get("status") == "passed" else "failed"
        hermes_status = hermes.get("overallStatus") if isinstance(hermes.get("overallStatus"), str) else None
        documentation_status = "missing" if documentation is None else (documentation.get("status") if isinstance(documentation.get("status"), str) else "invalid")
        required = {"manifest.json": manifest, "status.json": status, "review.json": review, "full-verification.json": verification, "hermes.json": hermes}
        if implementation is not None:
            required["implementation.json"] = implementation
        if documentation is not None:
            required["documentation-evidence.json"] = documentation
        manifest_head = manifest.get("gitHead") if isinstance(manifest.get("gitHead"), str) else None
        identities_bound = all(item.get("runId") == run_id and item.get("gitHead") == manifest_head for item in required.values())
        artifacts = [status, manifest, review, verification, hermes]
        if implementation is not None:
            artifacts.append(implementation)
        if documentation is not None:
            artifacts.append(documentation)
        stale = any(_contains_marker(item, _STALE_MARKERS) for item in artifacts)
        protected = (run_root / "protected-incident.json").exists() or any(_contains_marker(item, _PROTECTED_MARKERS) for item in artifacts)
        blocked = any(_contains_marker(item, _BLOCKED_MARKERS) for item in artifacts)
        gate_status = "ready" if manifest.get("runId") == run_id and manifest_head and identities_bound and workflow_status == "completed" else "blocked"
        fingerprints = tuple(sorted((name, digest) for name, (_, digest) in snapshots.items()))
        if implementation_snapshot:
            fingerprints += (("implementation.json", implementation_snapshot[1]),)
            snapshots["implementation.json"] = implementation_snapshot
        if documentation_snapshot:
            fingerprints += (("documentation-evidence.json", documentation_snapshot[1]),)
            snapshots["documentation-evidence.json"] = documentation_snapshot
        fingerprints = tuple(sorted(fingerprints))
        payloads = tuple(sorted((name, payload) for name, (payload, _) in snapshots.items()))
        return cls(run_id, run_root, workflow_status, review_verdict, verification_status, hermes_status, documentation_status, stale, protected, blocked, gate_status, fingerprints, payloads)

    def is_memory_eligible(self) -> bool:
        return self.gate_status == "ready" and self.review_verdict == "pass" and self.verification_status == "passed" and self.hermes_status == "pass" and self.documentation_status in _NO_DOC_STATUSES and not self.stale_upstream and not self.protected_incident and not self.blocked_upstream

    def snapshot_matches(self) -> bool:
        current = tuple(sorted((name, snapshot[1]) for name, _ in self.artifact_fingerprints if (snapshot := _read_snapshot(self.run_root / name))))
        return current == self.artifact_fingerprints

    def artifact_payload(self, name: str) -> dict[str, Any] | None:
        return dict(dict(self.artifact_payloads).get(name, {})) or None
