from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import os

from .short_term_offload_models import ShortTermOffloadArtifact
from .short_term_offload_policy import ShortTermOffloadPolicy


class ShortTermOffloadStore:
    """A bounded JSON store under the project-owned isolated runtime root."""

    def __init__(self, project_root: Path, *, policy: ShortTermOffloadPolicy) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".nbs_agent_runtime" / "short-term-offload"
        self.policy = policy

    def _safe_id(self, value: str) -> str:
        self.policy.validate_ref_id(value)
        return value

    def _run_dir(self, run_id: str, session_id: str) -> Path:
        run, session = self._safe_id(run_id), self._safe_id(session_id)
        runtime = self.project_root / ".nbs_agent_runtime"
        if runtime.exists() and runtime.is_symlink():
            raise ValueError("runtime root symlink")
        if self.root.exists() and self.root.is_symlink():
            raise ValueError("offload root symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("offload root symlink")
        run_dir = self.root / run
        session_dir = run_dir / session
        if run_dir.exists() and run_dir.is_symlink() or session_dir.exists() and session_dir.is_symlink():
            raise ValueError("offload path symlink")
        run_dir.mkdir(exist_ok=True)
        session_dir.mkdir(exist_ok=True)
        return session_dir

    def _path(self, run_id: str, session_id: str, ref_id: str) -> Path:
        ref = self._safe_id(ref_id)
        path = self._run_dir(run_id, session_id) / f"{ref}.json"
        if path.parent.resolve() != (self.root / run_id / session_id).resolve() or path.is_symlink():
            raise ValueError("unsafe offload path")
        return path

    def _existing_path(self, run_id: str, session_id: str, ref_id: str) -> Path:
        self._safe_id(run_id)
        self._safe_id(session_id)
        self._safe_id(ref_id)
        runtime = self.project_root / ".nbs_agent_runtime"
        for component in (runtime, self.root):
            if component.exists() and component.is_symlink():
                raise ValueError("unsafe offload root symlink")
        run_dir = self.root / run_id
        session_dir = run_dir / session_id
        path = session_dir / f"{ref_id}.json"
        if any(component.is_symlink() for component in (run_dir, session_dir, path)):
            raise ValueError("unsafe offload path")
        return path

    def _run_artifacts(self, run_id: str) -> list[ShortTermOffloadArtifact]:
        run_dir = self.root / self._safe_id(run_id)
        if not run_dir.exists() or run_dir.is_symlink():
            return []
        artifacts: list[ShortTermOffloadArtifact] = []
        for path in run_dir.glob("*/[A-Za-z0-9]*.json"):
            if path.is_symlink() or not path.is_file():
                raise ValueError("unsafe offload artifact")
            artifacts.append(ShortTermOffloadArtifact.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        return artifacts

    def write(
        self,
        artifact: ShortTermOffloadArtifact,
        *,
        allow_expired: bool = False,
        now: datetime | None = None,
    ) -> None:
        comparison_now = now or datetime.now(timezone.utc)
        if artifact.status not in {"ready", "blocked"} or (not allow_expired and artifact.expires_at <= comparison_now):
            raise ValueError("artifact is not writable")
        # Re-parse the exact envelope before any filesystem write.
        validated = ShortTermOffloadArtifact.from_dict(artifact.to_dict())
        path = self._path(artifact.run_id, artifact.session_id, artifact.ref_id)
        artifacts = self._run_artifacts(artifact.run_id)
        existing = next((a for a in artifacts if a.ref_id == artifact.ref_id), None)
        if existing is None and len(artifacts) >= self.policy.max_artifacts_per_run:
            raise ValueError("artifact count cap")
        existing_bytes = 0
        run_dir = self.root / artifact.run_id
        if run_dir.exists():
            for existing_path in run_dir.glob("*/*.json"):
                if existing_path.is_symlink() or not existing_path.is_file():
                    raise ValueError("unsafe offload artifact")
                if existing_path != path:
                    existing_bytes += existing_path.stat().st_size
        total = existing_bytes + len(json.dumps(validated.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        if total > self.policy.max_total_bytes_per_run:
            raise ValueError("artifact byte cap")
        payload = json.dumps(validated.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        temp.write_text(payload, encoding="utf-8")
        os.replace(temp, path)

    def read(self, run_id: str, session_id: str, ref_id: str, *, now: datetime | None = None) -> ShortTermOffloadArtifact | None:
        path = self._existing_path(run_id, session_id, ref_id)
        if not path.exists():
            return None
        artifact = ShortTermOffloadArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if artifact.expires_at <= (now or datetime.now(timezone.utc)):
            return None
        return artifact

    def cleanup_expired(self, *, now: datetime) -> tuple[str, ...]:
        removed: list[str] = []
        runtime = self.project_root / ".nbs_agent_runtime"
        if runtime.exists() and runtime.is_symlink():
            raise ValueError("runtime root symlink")
        if self.root.exists() and self.root.is_symlink():
            raise ValueError("offload root symlink")
        if not self.root.exists() or self.root.is_symlink():
            return ()
        for path in self.root.glob("*/[A-Za-z0-9]*/*.json"):
            if path.is_symlink() or not path.is_file():
                raise ValueError("unsafe offload artifact")
            artifact = ShortTermOffloadArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if artifact.expires_at <= now:
                path.unlink()
                removed.append(artifact.ref_id)
        return tuple(sorted(removed))
