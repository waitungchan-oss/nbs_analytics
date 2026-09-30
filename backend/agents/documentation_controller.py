from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PureWindowsPath

from .documentation_evidence import _read_target_section
from .documentation_models import (
    DocumentationApplication,
    DocumentationApplicationV2,
    DocumentationSchemaError,
    DOCUMENTATION_APPLICATION_SCHEMA,
)
from .documentation_policy import load_documentation_target
from .documentation_validator import (
    DocumentationPreview,
    DocumentationPreviewItem,
    DocumentationPreviewV2,
    DocumentationProposalValidator,
)
from .verification_chain import git_source_probe
from .verification_session import VerificationSession
from .workflow_models import canonical_sha256


_TARGET_ARTIFACTS = frozenset({
    "documentation-evidence-v2.json",
    "documentation-proposal-v2.json",
    "documentation-preview-v2.json",
    "documentation-application-v2.json",
})
_TARGET_ARTIFACT_MAX_BYTES = 5 * 1024 * 1024
_TARGET_BACKUP_MAX_BYTES = 2 * 1024 * 1024
_APPLY_RECEIPT_MAX_BYTES = 16 * 1024
_APPLY_RECEIPT_SCHEMA = "documentation-apply-receipt-v1"
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class DocumentationController:
    """Apply validated documentation previews inside the trusted write boundary."""

    def __init__(
        self,
        project_root: Path,
        *,
        runtime_root: Path | None = None,
        obsidian_vault: Path | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.runtime_root = Path(runtime_root or self.project_root / ".nbs_agent_runtime").resolve()
        self.obsidian_vault = Path(obsidian_vault).resolve() if obsidian_vault else None
        if self.obsidian_vault and self.obsidian_vault.is_symlink():
            raise PermissionError("symlink Obsidian vault root is not allowed")

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

    def target_artifact_path(self, run_id: str, target_id: str, artifact_name: str) -> Path:
        """Return a fixed, symlink-free v2 artifact path for one catalog target."""
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
            raise ValueError("unsafe documentation run ID")
        if artifact_name not in _TARGET_ARTIFACTS:
            raise ValueError("documentation v2 artifact name is not allowlisted")
        target = load_documentation_target(target_id)
        runtime_root = self.runtime_root
        candidate = runtime_root / "runs" / run_id / "documentation" / "targets" / target.target_id / artifact_name
        self._assert_no_symlink(candidate)
        resolved_root = runtime_root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("documentation artifact escapes runtime root") from exc
        return candidate

    def write_target_artifact(
        self, run_id: str, target_id: str, artifact_name: str, payload: dict,
    ) -> Path:
        path = self.target_artifact_path(run_id, target_id, artifact_name)
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if len(encoded) > _TARGET_ARTIFACT_MAX_BYTES:
            raise ValueError("documentation v2 artifact exceeds its size limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_no_symlink(path)
        if path.exists():
            if path.is_file() and path.stat().st_size <= _TARGET_ARTIFACT_MAX_BYTES and path.read_bytes() == encoded:
                return path
            if artifact_name == "documentation-application-v2.json" and path.is_file():
                existing = DocumentationApplicationV2.from_dict(
                    json.loads(path.read_text(encoding="utf-8")),
                )
                incoming = DocumentationApplicationV2.from_dict(payload)
                stable_existing = {
                    key: value for key, value in existing.to_dict().items()
                    if key not in {"generatedAt", "applicationFingerprint"}
                }
                stable_incoming = {
                    key: value for key, value in payload.items()
                    if key not in {"generatedAt", "applicationFingerprint"}
                }
                if stable_existing == stable_incoming:
                    return path
                identity_fields = (
                    "taskId", "runId", "sourceFingerprint", "proposalFingerprint",
                    "targetId", "repoPath", "approvalId", "beforeSectionSha256",
                    "afterSectionSha256",
                )
                old_payload = existing.to_dict()
                if (
                    all(old_payload[key] == payload.get(key) for key in identity_fields)
                    and existing.status in {"blocked", "awaiting_target_approval"}
                    and incoming.status == "applied"
                ):
                    self._atomic_replace(path, encoded, existing_mode=path.stat().st_mode)
                    return path
            raise FileExistsError("documentation v2 artifact already exists with different content")
        self._atomic_replace(path, encoded, existing_mode=None)
        return path

    def read_target_artifact(self, run_id: str, target_id: str, artifact_name: str) -> dict:
        path = self.target_artifact_path(run_id, target_id, artifact_name)
        self._assert_no_symlink(path)
        if not path.is_file() or path.stat().st_size > _TARGET_ARTIFACT_MAX_BYTES:
            raise ValueError("documentation v2 artifact is missing or exceeds its size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("documentation v2 artifact must contain a JSON object")
        return payload

    def apply_target(
        self,
        preview: DocumentationPreviewV2,
        *,
        approved_target_ids: frozenset[str],
    ) -> DocumentationApplicationV2:
        """Apply a single fixed section only after exact target approval and fresh checks."""
        if not isinstance(preview, DocumentationPreviewV2):
            raise ValueError("preview must be DocumentationPreviewV2")
        try:
            normalized = DocumentationPreviewV2.from_dict(preview.to_dict())
            target = load_documentation_target(normalized.target_id)
        except (DocumentationSchemaError, TypeError, ValueError) as exc:
            target_id = getattr(preview, "target_id", "")
            target = load_documentation_target(target_id)
            return self._target_application(preview, target, "blocked")

        if normalized.status != "preview_ready":
            return self._target_application(normalized, target, "blocked")
        if target.required_approval_id not in approved_target_ids:
            return self._target_application(normalized, target, "awaiting_target_approval")
        if approved_target_ids != frozenset({target.required_approval_id}):
            return self._target_application(normalized, target, "blocked")

        try:
            current_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.project_root,
                check=True, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if current_head != normalized.proposal.evidence.commit_sha:
                return self._target_application(normalized, target, "blocked")

            path, before_text, before_section, start, end = _read_target_section(self.project_root, target)
            before_bytes = before_text.encode("utf-8")
            section_hash = sha256(before_section.encode("utf-8")).hexdigest()
            if section_hash == normalized.after_section_sha256:
                if before_section != normalized.proposal.content:
                    return self._target_application(normalized, target, "blocked")
                self._assert_applied_source_current(normalized)
                return self._target_application(normalized, target, "applied")
            if (
                section_hash != normalized.before_section_sha256
                or section_hash != normalized.proposal.expected_section_sha256
            ):
                return self._target_application(normalized, target, "blocked")
            matched_session = self._assert_source_fingerprint_current(normalized)
            if len(before_bytes) > _TARGET_BACKUP_MAX_BYTES:
                return self._target_application(normalized, target, "blocked")

            fresh_preview = DocumentationProposalValidator(self.project_root).build_target_preview(
                normalized.proposal,
            )
            if fresh_preview.to_dict() != normalized.to_dict():
                return self._target_application(normalized, target, "blocked")
            self._preflight_applied_source_receipt(normalized, matched_session)
            after_text = before_text[:start] + normalized.proposal.content + before_text[end:]
            after_bytes = after_text.encode("utf-8")

            mode = path.stat().st_mode
            backup = self._target_backup_path(normalized.run_id, target.target_id, before_bytes)
            self._ensure_target_backup(backup, before_bytes)

            latest_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.project_root,
                check=True, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            matched_session = self._assert_source_fingerprint_current(normalized)
            self._assert_no_symlink(path)
            if latest_head != normalized.proposal.evidence.commit_sha or path.read_bytes() != before_bytes:
                return self._target_application(normalized, target, "blocked")
            self._atomic_replace(path, after_bytes, existing_mode=mode)
            if path.read_bytes() != after_bytes:
                return self._target_application(normalized, target, "blocked")
            _, _, after_section, _, _ = _read_target_section(self.project_root, target)
            if sha256(after_section.encode("utf-8")).hexdigest() != normalized.after_section_sha256:
                return self._target_application(normalized, target, "blocked")
            self._write_applied_source_receipt(normalized, matched_session, backup)
            return self._target_application(normalized, target, "applied")
        except (OSError, UnicodeError, subprocess.SubprocessError, DocumentationSchemaError, ValueError):
            return self._target_application(normalized, target, "blocked")

    def _matching_source_session(self, preview: DocumentationPreviewV2) -> VerificationSession:
        """Load a bounded retained session matching the preview's original seal."""
        sessions_root = self.project_root / ".nbs_agent_runtime" / "verification_sessions"
        if sessions_root.is_symlink() or not sessions_root.is_dir():
            raise ValueError("source-bound verification sessions are unavailable")
        session_dirs = sorted(sessions_root.iterdir(), key=lambda item: item.name)
        if len(session_dirs) > 512:
            raise ValueError("source-bound verification session set exceeds its bound")
        matched = None
        for session_dir in session_dirs:
            if session_dir.is_symlink() or not session_dir.is_dir():
                continue
            session_path = session_dir / "session.json"
            if session_path.is_symlink() or not session_path.is_file():
                continue
            try:
                session = VerificationSession.from_dict(
                    json.loads(session_path.read_text(encoding="utf-8")),
                )
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if session.source_fingerprint == preview.source_fingerprint:
                matched = session
                break
        if matched is None:
            raise ValueError("matching source-bound verification session is unavailable")
        return matched

    def _current_source_fingerprint(self, session: VerificationSession) -> str:
        """Recompute the canonical source identity from the session's sealed inputs."""
        current = git_source_probe(
            self.project_root,
            brief_path=session.brief_path,
            base_sha=session.base_sha,
            head_ref="WORKTREE",
            contract_path="docs/agents/REVIEW_AGENT_CONTRACT.md",
            policy_path="agent_config/token_budgets.json",
        )
        fresh = replace(
            session,
            head_sha=current["head_sha"],
            brief_fingerprint=current["brief_fingerprint"],
            worktree_fingerprint=current["worktree_fingerprint"],
            diff_fingerprint=current["diff_fingerprint"],
            contract_fingerprint=current["contract_fingerprint"],
            policy_fingerprint=current["policy_fingerprint"],
            gates={**session.gates, "sourceProbeVersion": current["source_probe_version"]},
        )
        return fresh.source_fingerprint

    def _assert_source_fingerprint_current(
        self, preview: DocumentationPreviewV2,
    ) -> VerificationSession:
        """Fail closed unless the worktree still matches the preview's source seal."""
        session = self._matching_source_session(preview)
        if self._current_source_fingerprint(session) != preview.source_fingerprint:
            raise ValueError("documentation source has drifted from its sealed preview")
        return session

    def _target_apply_receipt_path(
        self, preview: DocumentationPreviewV2, session: VerificationSession,
    ) -> Path:
        target = load_documentation_target(preview.target_id)
        if not _SAFE_RUN_ID.fullmatch(session.session_id):
            raise ValueError("unsafe source session ID")
        sessions_root = self.project_root / ".nbs_agent_runtime" / "verification_sessions"
        path = (
            sessions_root / session.session_id / "documentation-apply-receipts"
            / f"{target.target_id}-{preview.preview_fingerprint}.json"
        )
        self._assert_no_symlink(path)
        try:
            path.resolve(strict=False).relative_to(sessions_root.resolve(strict=False))
        except ValueError as exc:
            raise ValueError("documentation apply receipt escapes the verification runtime") from exc
        return path

    def _preflight_applied_source_receipt(
        self, preview: DocumentationPreviewV2, session: VerificationSession,
    ) -> None:
        """Prove the bounded receipt directory is writable before target mutation."""
        path = self._target_apply_receipt_path(preview, session)
        if path.exists():
            raise FileExistsError("documentation apply receipt already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_no_symlink(path)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.preflight.", suffix=".tmp", dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(b"documentation apply receipt preflight\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _ensure_target_backup(self, backup: Path, content: bytes) -> None:
        """Create a bounded backup or safely reuse an identical prior backup."""
        self._assert_no_symlink(backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        self._assert_no_symlink(backup)
        if backup.exists():
            if (
                not backup.is_file()
                or backup.stat().st_size > _TARGET_BACKUP_MAX_BYTES
                or backup.read_bytes() != content
            ):
                raise ValueError("existing documentation backup differs from expected source bytes")
            return
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._assert_no_symlink(backup)
            if (
                not backup.is_file()
                or backup.stat().st_size > _TARGET_BACKUP_MAX_BYTES
                or backup.read_bytes() != content
            ):
                raise ValueError("racing documentation backup differs from expected source bytes")
            return
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    def _write_applied_source_receipt(
        self,
        preview: DocumentationPreviewV2,
        session: VerificationSession,
        backup: Path,
    ) -> None:
        target = load_documentation_target(preview.target_id)
        backup_root = self.runtime_root / "documentation-backups" / preview.run_id
        self._assert_no_symlink(backup_root)
        self._assert_no_symlink(backup)
        if backup.is_symlink() or not backup.is_file():
            raise ValueError("documentation backup is unavailable")
        resolved_backup_root = backup_root.resolve(strict=True)
        resolved_backup = backup.resolve(strict=True)
        try:
            resolved_backup.relative_to(resolved_backup_root)
        except ValueError as exc:
            raise ValueError("documentation backup escapes its run directory") from exc
        if backup.stat().st_size > _TARGET_BACKUP_MAX_BYTES:
            raise ValueError("documentation backup is unavailable or exceeds its size limit")
        receipt = {
            "schemaVersion": _APPLY_RECEIPT_SCHEMA,
            "runId": preview.run_id,
            "targetId": target.target_id,
            "previewFingerprint": preview.preview_fingerprint,
            "sourceFingerprint": preview.source_fingerprint,
            "postApplySourceFingerprint": self._current_source_fingerprint(session),
            "proposalFingerprint": preview.proposal_fingerprint,
            "beforeSectionSha256": preview.before_section_sha256,
            "afterSectionSha256": preview.after_section_sha256,
            "backupName": backup.name,
            "backupSha256": sha256(backup.read_bytes()).hexdigest(),
        }
        encoded = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if len(encoded) > _APPLY_RECEIPT_MAX_BYTES:
            raise ValueError("documentation apply receipt exceeds its size limit")
        path = self._target_apply_receipt_path(preview, session)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_no_symlink(path)
        if path.exists():
            if path.is_file() and path.stat().st_size <= _APPLY_RECEIPT_MAX_BYTES and path.read_bytes() == encoded:
                return
            raise FileExistsError("documentation apply receipt already exists with different content")
        self._atomic_create(path, encoded)

    def _assert_applied_source_current(self, preview: DocumentationPreviewV2) -> None:
        """Require an intact apply receipt and unchanged post-apply source identity."""
        target = load_documentation_target(preview.target_id)
        session = self._matching_source_session(preview)
        path = self._target_apply_receipt_path(preview, session)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _APPLY_RECEIPT_MAX_BYTES:
            raise ValueError("documentation apply receipt is unavailable")
        payload = json.loads(path.read_text(encoding="utf-8"))
        fields = {
            "schemaVersion", "runId", "targetId", "previewFingerprint", "sourceFingerprint",
            "postApplySourceFingerprint", "proposalFingerprint", "beforeSectionSha256",
            "afterSectionSha256", "backupName", "backupSha256",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("documentation apply receipt schema is invalid")
        expected = {
            "schemaVersion": _APPLY_RECEIPT_SCHEMA,
            "runId": preview.run_id,
            "targetId": target.target_id,
            "previewFingerprint": preview.preview_fingerprint,
            "sourceFingerprint": preview.source_fingerprint,
            "proposalFingerprint": preview.proposal_fingerprint,
            "beforeSectionSha256": preview.before_section_sha256,
            "afterSectionSha256": preview.after_section_sha256,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError("documentation apply receipt identity differs from preview")
        backup_name = payload.get("backupName")
        if not isinstance(backup_name, str) or not re.fullmatch(
            rf"{re.escape(target.target_id)}-[0-9a-f]{{64}}\.md", backup_name,
        ):
            raise ValueError("documentation apply receipt backup identity is invalid")
        backup = self.runtime_root / "documentation-backups" / preview.run_id / backup_name
        self._assert_no_symlink(backup)
        if not backup.is_file() or backup.stat().st_size > _TARGET_BACKUP_MAX_BYTES:
            raise ValueError("documentation apply backup is unavailable")
        backup_digest = sha256(backup.read_bytes()).hexdigest()
        if payload.get("backupSha256") != backup_digest:
            raise ValueError("documentation apply backup fingerprint differs from receipt")
        post_fingerprint = payload.get("postApplySourceFingerprint")
        if not isinstance(post_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", post_fingerprint):
            raise ValueError("documentation apply receipt source fingerprint is invalid")
        if self._current_source_fingerprint(session) != post_fingerprint:
            raise ValueError("worktree has drifted since the approved documentation apply")

    def _target_backup_path(self, run_id: str, target_id: str, content: bytes) -> Path:
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("unsafe documentation run ID")
        target = load_documentation_target(target_id)
        digest = sha256(content).hexdigest()
        return self.runtime_root / "documentation-backups" / run_id / f"{target.target_id}-{digest}.md"

    @staticmethod
    def _target_application(
        preview: DocumentationPreviewV2,
        target,
        status: str,
    ) -> DocumentationApplicationV2:
        unsigned = {
            "schemaVersion": "documentation-application-v2",
            "taskId": preview.task_id,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "runId": preview.run_id,
            "sourceFingerprint": preview.source_fingerprint,
            "proposalFingerprint": preview.proposal_fingerprint,
            "status": status,
            "targetId": target.target_id,
            "repoPath": target.repo_path,
            "approvalId": target.required_approval_id,
            "beforeSectionSha256": preview.before_section_sha256,
            "afterSectionSha256": preview.after_section_sha256,
            "result": "applied" if status == "applied" else "preview" if status == "awaiting_target_approval" else "blocked",
        }
        return DocumentationApplicationV2.from_dict({
            **unsigned,
            "applicationFingerprint": canonical_sha256(unsigned),
        })

    def _apply_item(self, item: DocumentationPreviewItem) -> tuple[dict[str, object], bool]:
        try:
            path = self._target_path(item)
            path = self._assign_adr_path(item, path)
            self._assert_no_symlink(path)
            current_exists = path.exists()
            current = path.read_bytes() if current_exists else b""
            current_hash = sha256(current).hexdigest() if current_exists else None
            if current_hash == item.after_sha256:
                return self._record(item, f"already_applied;beforeSha256={current_hash};afterSha256={current_hash}", current_hash), True
            if current_hash != item.before_sha256:
                actual = current_hash or "missing"
                if item.target_kind == "adr" and current_exists:
                    return self._record(item, f"adr_target_exists;actual={actual}", None), False
                return self._record(item, f"stale_target: expected beforeSha256={item.before_sha256 or 'missing'};actual={actual}", None), False
            after = self._after_bytes(item, current)
            if sha256(after).hexdigest() != item.after_sha256:
                return self._record(item, "blocked: preview after hash does not match expected bytes", None), False
            if item.target_kind == "adr":
                self._atomic_create(path, after)
                applied_hash = sha256(path.read_bytes()).hexdigest()
                if applied_hash != item.after_sha256:
                    return self._record(item, "blocked: post-write hash verification failed", None), False
                return self._record(item, f"applied;beforeSha256=missing;afterSha256={applied_hash}", applied_hash), True
            existing_mode = path.stat().st_mode if current_exists else None
            if current_exists:
                self._backup(path, item.path_identity, current)
                latest_hash = sha256(path.read_bytes()).hexdigest()
                if latest_hash != current_hash:
                    return self._record(item, f"stale_target: expected beforeSha256={current_hash};actual={latest_hash}", None), False
            self._atomic_replace(path, after, existing_mode=existing_mode)
            applied_hash = sha256(path.read_bytes()).hexdigest()
            if applied_hash != item.after_sha256:
                return self._record(item, "blocked: post-write hash verification failed", None), False
            return self._record(item, f"applied;beforeSha256={current_hash or 'missing'};afterSha256={applied_hash}", applied_hash), True
        except (OSError, ValueError) as exc:
            return self._record(item, f"write_failed: {type(exc).__name__}", None), False

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

    def _target_path(self, item: DocumentationPreviewItem) -> Path:
        if item.target_kind == "brief_backfill" and item.vault_relative_path and self.obsidian_vault:
            relative = Path(item.vault_relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe Obsidian target path")
            resolved = (self.obsidian_vault / relative).resolve(strict=False)
            try:
                resolved.relative_to(self.obsidian_vault)
            except ValueError as exc:
                raise ValueError("Obsidian target escapes vault") from exc
            return resolved
        return self._repo_path(item.path_identity)

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
            if candidate.name.endswith(f"-{suffix}.md"):
                try:
                    if sha256(candidate.read_bytes()).hexdigest() == item.after_sha256:
                        return candidate
                except OSError:
                    continue
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

    @staticmethod
    def _atomic_create(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink()
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
    def _display_identity(value: str) -> str:
        is_absolute = Path(value).is_absolute() or PureWindowsPath(value).is_absolute()
        return "<absolute-target>" if is_absolute else value

    def _record(self, item: DocumentationPreviewItem, result: str, applied_hash: str | None) -> dict[str, object]:
        identity = item.vault_relative_path or item.path_identity
        return {
            "targetKind": item.target_kind,
            "targetIdentity": self._display_identity(identity),
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
