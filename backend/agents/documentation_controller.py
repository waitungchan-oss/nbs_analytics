from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from .documentation_models import DocumentationApplication, DOCUMENTATION_APPLICATION_SCHEMA
from .documentation_validator import DocumentationPreview, DocumentationPreviewItem


class DocumentationController:
    """Apply validated documentation previews inside the trusted write boundary."""

    def __init__(self, project_root: Path, *, runtime_root: Path | None = None):
        self.project_root = Path(project_root).resolve()
        self.runtime_root = Path(runtime_root or self.project_root / ".nbs_agent_runtime").resolve()

    def apply(
        self,
        preview: DocumentationPreview,
        *,
        apply_brief: bool,
        approved_targets: frozenset[str],
    ) -> DocumentationApplication:
        application_items = []
        if preview.status != "preview_ready":
            application = self._application(preview, "blocked", [])
            self._save_manifest(application)
            return application

        for item in preview.items:
            if item.target_kind == "brief_backfill" and not apply_brief:
                application_items.append(self._record(item, "brief_apply_not_enabled", None))
            elif item.target_kind != "brief_backfill" and not self._approved(item, approved_targets):
                application_items.append(self._record(item, "target_approval_required", None))
        if application_items:
            application = self._application(preview, "awaiting_target_approval", application_items)
            self._save_manifest(application)
            return application

        applied_count = 0
        failed_count = 0
        for item in preview.items:
            record, applied = self._apply_item(item)
            application_items.append(record)
            if applied:
                applied_count += 1
            elif not record["result"].startswith("already_applied"):
                failed_count += 1

        status = "applied" if failed_count == 0 else "partially_applied" if applied_count else "blocked"
        application = self._application(preview, status, application_items)
        self._save_manifest(application)
        return application

    def _apply_item(self, item: DocumentationPreviewItem) -> tuple[dict[str, object], bool]:
        try:
            path = self._repo_path(item.path_identity)
            path = self._assign_adr_path(item, path)
            self._assert_no_symlink(path)
            current_exists = path.exists()
            current = path.read_bytes() if current_exists else b""
            current_hash = sha256(current).hexdigest() if current_exists else None
            if current_hash == item.after_sha256:
                return self._record(item, f"already_applied;beforeSha256={current_hash};afterSha256={current_hash}", current_hash), True
            if current_hash != item.before_sha256:
                actual = current_hash or "missing"
                return self._record(item, f"stale_target: expected beforeSha256={item.before_sha256 or 'missing'};actual={actual}", None), False
            after = self._after_bytes(item, current)
            if sha256(after).hexdigest() != item.after_sha256:
                return self._record(item, "blocked: preview after hash does not match expected bytes", None), False
            if current_exists:
                self._backup(path, item.path_identity, current)
            self._atomic_replace(path, after, existing_mode=path.stat().st_mode if current_exists else None)
            applied_hash = sha256(path.read_bytes()).hexdigest()
            if applied_hash != item.after_sha256:
                return self._record(item, "blocked: post-write hash verification failed", None), False
            return self._record(item, f"applied;beforeSha256={current_hash or 'missing'};afterSha256={applied_hash}", applied_hash), True
        except (OSError, ValueError) as exc:
            return self._record(item, f"write_failed: {type(exc).__name__}: {exc}", None), False

    def _repo_path(self, identity: str) -> Path:
        relative = identity.split("#", 1)[0].split("::", 1)[0].split("|", 1)[0]
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
            raise ValueError("unsafe target path")
        resolved = (self.project_root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("target escapes project root") from exc
        return resolved

    @staticmethod
    def _assert_no_symlink(path: Path) -> None:
        current = Path(path.anchor) if path.anchor else Path()
        parts = path.parts[1:] if path.anchor else path.parts
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("symlink target is not allowed")

    @staticmethod
    def _approved(item: DocumentationPreviewItem, approvals: frozenset[str]) -> bool:
        return item.path_identity in approvals or bool(item.vault_relative_path and item.vault_relative_path in approvals)

    @staticmethod
    def _after_bytes(item: DocumentationPreviewItem, before: bytes) -> bytes:
        source = before.decode("utf-8")
        diff_lines = item.unified_diff.splitlines(keepends=True)
        hunks = [index for index, line in enumerate(diff_lines) if line.startswith("@@")]
        if not hunks:
            raise ValueError("preview unified diff has no hunk")
        source_lines = source.splitlines(keepends=True)
        output: list[str] = []
        source_index = 0
        for hunk_index, start in enumerate(hunks):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", diff_lines[start])
            if not match:
                raise ValueError("invalid preview unified diff")
            old_start = max(int(match.group(1)) - 1, 0)
            if old_start < source_index or old_start > len(source_lines):
                raise ValueError("preview diff does not match current target")
            output.extend(source_lines[source_index:old_start])
            source_index = old_start
            end = hunks[hunk_index + 1] if hunk_index + 1 < len(hunks) else len(diff_lines)
            for line in diff_lines[start + 1:end]:
                if line.startswith("\\"):
                    continue
                if not line:
                    raise ValueError("invalid empty diff line")
                prefix, content = line[0], line[1:]
                if prefix == " ":
                    if source_index >= len(source_lines) or source_lines[source_index] != content:
                        raise ValueError("preview context does not match current target")
                    output.append(content)
                    source_index += 1
                elif prefix == "-":
                    if source_index >= len(source_lines) or source_lines[source_index] != content:
                        raise ValueError("preview removal does not match current target")
                    source_index += 1
                elif prefix == "+":
                    output.append(content)
                else:
                    raise ValueError("invalid preview unified diff line")
        output.extend(source_lines[source_index:])
        return "".join(output).encode("utf-8")

    def _assign_adr_path(self, item: DocumentationPreviewItem, path: Path) -> Path:
        if item.target_kind != "adr":
            return path
        match = re.fullmatch(r"ADR-(.+)\.md", path.name)
        if not match:
            raise ValueError("invalid ADR target")
        suffix = match.group(1)
        suffix = suffix.split("-", 1)[1] if suffix[:1].isdigit() and "-" in suffix else suffix
        numbers = []
        for candidate in path.parent.glob("ADR-*.md"):
            found = re.match(r"ADR-(\d+)(?:-|\.md)", candidate.name)
            if found:
                numbers.append(int(found.group(1)))
        return path.with_name(f"ADR-{max(numbers, default=0) + 1:03d}-{suffix}.md")

    def _backup(self, path: Path, identity: str, content: bytes) -> None:
        backup_root = self.runtime_root / "documentation-backups" / self._run_id()
        backup_root.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", identity)[:120]
        safe_name = f"{safe_name}-{sha256(identity.encode()).hexdigest()[:12]}.md"
        backup = backup_root / safe_name
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    @staticmethod
    def _atomic_replace(path: Path, content: bytes, *, existing_mode: int | None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                if existing_mode is not None:
                    os.fchmod(handle.fileno(), existing_mode & 0o777)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    def _application(self, preview: DocumentationPreview, status: str, items: list[dict[str, object]]) -> DocumentationApplication:
        application = DocumentationApplication(
            schema_version=DOCUMENTATION_APPLICATION_SCHEMA,
            task_id="documentation-controller",
            generated_at=datetime.now(timezone.utc).isoformat(),
            proposal_fingerprint=sha256(json.dumps(preview.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
            status=status,
            applications=tuple(items),
        )
        return application

    @staticmethod
    def _record(item: DocumentationPreviewItem, result: str, applied_hash: str | None) -> dict[str, object]:
        return {
            "targetKind": item.target_kind,
            "targetIdentity": item.vault_relative_path or item.path_identity,
            "operation": "create_file" if item.target_kind == "adr" else "replace_section" if item.target_kind == "system_map" else "update_managed_block",
            "result": result,
            "appliedSha256": applied_hash,
        }

    def _save_manifest(self, application: DocumentationApplication) -> None:
        run_root = self.runtime_root / "runs" / self._run_id()
        run_root.mkdir(parents=True, exist_ok=True)
        destination = run_root / "documentation-application.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(application.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, destination)

    @staticmethod
    def _run_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


__all__ = ["DocumentationController"]
