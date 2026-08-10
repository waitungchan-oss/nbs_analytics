from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_hint_models import MemoryHints
from backend.agents.memory_sidecar_models import MemorySidecarSchemaError


class _MemoryProviderFallback:
    """Compatible no-op base when the external Hermes package is unavailable."""


try:  # Hermes is optional for local NBS tests and ordinary development.
    from agent.memory_provider import MemoryProvider as _MemoryProviderBase
except ImportError:  # pragma: no cover - exercised when Hermes is installed.
    _MemoryProviderBase = _MemoryProviderFallback

ACTIVATION_SCHEMA = "hermes-nbs-sidecar-activation-v1"
MAX_HINTS_BYTES = 6000
MAX_QUERY_CHARS = 512
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_ENVELOPE_FIELDS = frozenset({
    "schemaVersion", "manifestId", "activationId", "sessionId", "recallMode", "gitHead", "projectId", "workspaceKind", "workspaceFingerprint",
    "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider",
    "model", "reasoning", "hintsPath", "writerDisabled",
})


def activation_binding_fingerprint(envelope: Mapping[str, Any]) -> str:
    return canonical_fingerprint({key: envelope[key] for key in sorted(_ENVELOPE_FIELDS - {"activationId"})})


class NbsHermesSidecarProvider(_MemoryProviderBase):
    def __init__(self, project_root: str | Path, activation_envelope: Mapping[str, Any] | None = None) -> None:
        self.project_root = Path(project_root)
        self.activation_envelope = dict(activation_envelope) if isinstance(activation_envelope, Mapping) else None
        self.session_id = ""

    def _current_git_head(self) -> str:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project_root, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def _git_status_porcelain(self) -> str:
        result = subprocess.run(["git", "status", "--porcelain"], cwd=self.project_root, capture_output=True, text=True, check=False)
        return result.stdout if result.returncode == 0 else "!error"

    def _hints_path(self, envelope: Mapping[str, Any]) -> Path | None:
        raw = envelope["hintsPath"]
        if not isinstance(raw, str) or not raw or len(raw) > 256:
            return None
        candidate = Path(raw)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            return None
        root = self.project_root.resolve(strict=False) / ".nbs_agent_runtime"
        if root.is_symlink():
            return None
        path = root / candidate
        current = root
        for part in candidate.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                return None
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
        except ValueError:
            return None
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_HINTS_BYTES:
            return None
        return path

    def _workspace_fingerprint(self, envelope: Mapping[str, Any]) -> str:
        return canonical_fingerprint({
            "projectRoot": str(self.project_root.resolve()), "projectId": envelope["projectId"],
            "workspaceKind": envelope["workspaceKind"],
        })

    def _valid_envelope(self) -> Mapping[str, Any] | None:
        envelope = self.activation_envelope
        if envelope is None or set(envelope) != _ENVELOPE_FIELDS:
            return None
        if envelope.get("schemaVersion") != ACTIVATION_SCHEMA or envelope.get("recallMode") != "on" or envelope.get("provider") != "hermes" or envelope.get("model") != "deepseek-v4-flash" or envelope.get("reasoning") != "medium" or envelope.get("writerDisabled") is not True:
            return None
        if not isinstance(envelope.get("sessionId"), str) or not _IDENTIFIER.fullmatch(envelope["sessionId"]) or not isinstance(envelope.get("projectId"), str) or not _IDENTIFIER.fullmatch(envelope["projectId"]) or envelope.get("workspaceKind") not in {"repo", "isolated_worktree"} or not isinstance(envelope.get("hintsPath"), str) or not _SHA40.fullmatch(envelope.get("gitHead", "")):
            return None
        for key in ("manifestId", "activationId", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint"):
            if not isinstance(envelope.get(key), str) or not _SHA256.fullmatch(envelope[key]):
                return None
        if envelope["activationId"] != activation_binding_fingerprint(envelope):
            return None
        return envelope

    def _load_hints(self) -> MemoryHints | None:
        envelope = self._valid_envelope()
        if envelope is None:
            return None
        path = self._hints_path(envelope)
        if path is None:
            return None
        try:
            hints = MemoryHints.from_dict(json.loads(path.read_bytes()))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, MemorySidecarSchemaError):
            return None
        return hints if hints.status == "ready" else None

    def is_available(self) -> bool:
        envelope = self._valid_envelope()
        return bool(envelope and self.session_id == envelope["sessionId"] and self._workspace_fingerprint(envelope) == envelope["workspaceFingerprint"] and self._current_git_head() == envelope["gitHead"] and not self._git_status_porcelain().strip() and self._load_hints() is not None)

    def initialize(self, session_id: str, **_: Any) -> None:
        self.session_id = session_id if isinstance(session_id, str) else ""

    @property
    def name(self) -> str:
        return "nbs_sidecar"

    def get_tool_schemas(self) -> list[object]:
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if session_id and session_id != self.session_id:
            return ""
        if not isinstance(query, str) or not query or len(query) > MAX_QUERY_CHARS or not self.is_available():
            return ""
        hints = self._load_hints()
        if hints is None or hints.query_fingerprint != canonical_fingerprint({"query": query}):
            return ""
        result = "non_authoritative_memory\n" + "\n".join(f"- {hint.summary}" for hint in hints.hints)
        return result if len(result.encode("utf-8")) <= MAX_HINTS_BYTES else ""

    def sync_turn(self, *_: Any, **__: Any) -> None:
        return None
