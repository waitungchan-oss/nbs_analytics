from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_SUBDIRECTORIES = {
    "brief_backfill": "70_Codex_Briefs",
    "system_map": "10_System",
    "adr": "20_Decisions",
}


@dataclass(frozen=True)
class ResolvedTarget:
    target_kind: str
    vault_relative_path: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "targetKind": self.target_kind,
            "vaultRelativePath": self.vault_relative_path,
        }


class ObsidianTargetResolver:
    def __init__(self, vault_root: Path):
        self.vault_root = Path(vault_root)
        if self.vault_root.is_symlink():
            raise PermissionError("symlink vault root is not allowed")

    @classmethod
    def from_sources(
        cls,
        project_root: Path,
        *,
        cli_root: Path | None,
        environ: Mapping[str, str],
    ) -> "ObsidianTargetResolver | None":
        selected = str(cli_root) if cli_root is not None else environ.get("NBS_OBSIDIAN_VAULT")
        if not selected:
            config_path = Path(project_root) / ".nbs_agent_runtime" / "documentation.local.json"
            if config_path.exists():
                try:
                    payload = json.loads(config_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ValueError("invalid documentation.local.json") from exc
                selected = payload.get("obsidianVault") or payload.get("vaultRoot")
                if selected is not None and not isinstance(selected, str):
                    raise ValueError("local Obsidian vault must be a string")
        return cls(Path(selected)) if selected else None

    def _relative_target(self, target_kind: str, relative_name: str) -> tuple[Path, str]:
        if target_kind not in _SUBDIRECTORIES:
            raise PermissionError("unknown target kind")
        candidate = Path(relative_name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise PermissionError("path traversal is not allowed")
        subdirectory = _SUBDIRECTORIES[target_kind]
        parts = candidate.parts
        if parts and parts[0] == subdirectory:
            relative = candidate
        elif parts and parts[0] in _SUBDIRECTORIES.values():
            raise PermissionError("unknown target subdirectory")
        else:
            relative = Path(subdirectory) / candidate
        if not relative.name or any(part in ("", ".") for part in relative.parts):
            raise PermissionError("invalid target path")
        return relative, relative.as_posix()

    def resolve_info(self, target_kind: str, relative_name: str) -> ResolvedTarget:
        relative, identity = self._relative_target(target_kind, relative_name)
        root = self.vault_root
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise FileNotFoundError("Obsidian vault is missing or invalid")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("symlink target is not allowed")
        root_resolved = root.resolve(strict=True)
        target = (root / relative).resolve(strict=False)
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise PermissionError("target escapes Obsidian vault") from exc
        return ResolvedTarget(target_kind, identity, target)

    def resolve(self, target_kind: str, relative_name: str) -> Path:
        return self.resolve_info(target_kind, relative_name).path


__all__ = ["ObsidianTargetResolver", "ResolvedTarget"]
