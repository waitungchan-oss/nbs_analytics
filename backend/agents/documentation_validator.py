from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .documentation_evidence import _read_target_section
from .documentation_models import (
    DocumentationProposal,
    DocumentationProposalV2,
    DocumentationSchemaError,
)
from .documentation_policy import load_documentation_target
from .documentation_targets import ObsidianTargetResolver
from .workflow_models import canonical_sha256


class DocumentationValidationError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentationPreviewItem:
    target_kind: str
    path_identity: str
    vault_relative_path: str | None
    before_sha256: str | None
    after_sha256: str
    unified_diff: str
    risk_tier: str
    required_approval: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "targetKind": self.target_kind,
            "pathIdentity": self.path_identity,
            "vaultRelativePath": self.vault_relative_path,
            "beforeSha256": self.before_sha256,
            "afterSha256": self.after_sha256,
            "unifiedDiff": self.unified_diff,
            "riskTier": self.risk_tier,
            "requiredApproval": self.required_approval,
        }


@dataclass(frozen=True)
class DocumentationPreview:
    status: str
    items: tuple[DocumentationPreviewItem, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "items": [item.to_dict() for item in self.items],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DocumentationPreviewV2:
    schema_version: str
    proposal: DocumentationProposalV2
    status: str
    task_id: str
    run_id: str
    source_fingerprint: str
    evidence_fingerprint: str
    proposal_fingerprint: str
    target_id: str
    target_kind: str
    repo_path: str
    section_heading: str
    risk_tier: str
    required_approval_id: str
    before_section_sha256: str
    after_section_sha256: str
    unified_diff: str
    preview_fingerprint: str

    @property
    def canonical_fingerprint(self) -> str:
        return canonical_sha256({
            key: value for key, value in self.to_dict().items()
            if key != "previewFingerprint"
        })

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DocumentationPreviewV2":
        fields = {
            "schemaVersion", "proposal", "status", "taskId", "runId",
            "sourceFingerprint", "evidenceFingerprint", "proposalFingerprint",
            "targetId", "targetKind", "repoPath", "sectionHeading", "riskTier",
            "requiredApprovalId", "beforeSectionSha256", "afterSectionSha256",
            "unifiedDiff", "previewFingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != fields:
            raise DocumentationSchemaError("v2 preview fields are invalid")
        if payload["schemaVersion"] != "documentation-preview-v2":
            raise DocumentationSchemaError("v2 preview schemaVersion is invalid")
        try:
            proposal = DocumentationProposalV2.from_dict(payload["proposal"])
            target = load_documentation_target(proposal.target_id)
        except (DocumentationSchemaError, TypeError) as exc:
            raise DocumentationSchemaError("v2 preview proposal or target is invalid") from exc
        expected = {
            "status": "preview_ready",
            "taskId": proposal.task_id,
            "runId": proposal.evidence.run_id,
            "sourceFingerprint": proposal.evidence.source_fingerprint,
            "evidenceFingerprint": proposal.evidence_fingerprint,
            "proposalFingerprint": proposal.proposal_fingerprint,
            "targetId": target.target_id,
            "targetKind": target.target_kind,
            "repoPath": target.repo_path,
            "sectionHeading": target.section_heading,
            "riskTier": target.risk_tier,
            "requiredApprovalId": target.required_approval_id,
        }
        if any(payload[key] != value for key, value in expected.items()):
            raise DocumentationSchemaError("v2 preview identity differs from its proposal or policy")
        if proposal.status != "ready":
            raise DocumentationSchemaError("v2 preview proposal is not ready")
        for key in ("beforeSectionSha256", "afterSectionSha256", "previewFingerprint"):
            value = payload[key]
            if not isinstance(value, str) or len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise DocumentationSchemaError(f"{key} must be a lowercase SHA-256 digest")
        if payload["beforeSectionSha256"] != proposal.expected_section_sha256:
            raise DocumentationSchemaError("preview before hash differs from the proposal's expected section")
        if payload["afterSectionSha256"] != _digest(proposal.content):
            raise DocumentationSchemaError("preview after hash differs from proposal content")
        diff = payload["unifiedDiff"]
        if not isinstance(diff, str) or len(diff.encode("utf-8")) > _V2_DIFF_MAX_BYTES:
            raise DocumentationSchemaError("v2 preview diff is invalid or exceeds its byte limit")
        model = cls(
            "documentation-preview-v2", proposal, "preview_ready", proposal.task_id,
            proposal.evidence.run_id, proposal.evidence.source_fingerprint,
            proposal.evidence_fingerprint, proposal.proposal_fingerprint,
            target.target_id, target.target_kind, target.repo_path,
            target.section_heading, target.risk_tier, target.required_approval_id,
            payload["beforeSectionSha256"], payload["afterSectionSha256"], diff,
            payload["previewFingerprint"],
        )
        if model.canonical_fingerprint != model.preview_fingerprint:
            raise DocumentationSchemaError("preview fingerprint does not match canonical payload")
        return model

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "proposal": self.proposal.to_dict(),
            "status": self.status,
            "taskId": self.task_id,
            "runId": self.run_id,
            "sourceFingerprint": self.source_fingerprint,
            "evidenceFingerprint": self.evidence_fingerprint,
            "proposalFingerprint": self.proposal_fingerprint,
            "targetId": self.target_id,
            "targetKind": self.target_kind,
            "repoPath": self.repo_path,
            "sectionHeading": self.section_heading,
            "riskTier": self.risk_tier,
            "requiredApprovalId": self.required_approval_id,
            "beforeSectionSha256": self.before_section_sha256,
            "afterSectionSha256": self.after_section_sha256,
            "unifiedDiff": self.unified_diff,
            "previewFingerprint": self.preview_fingerprint,
        }


_START = "<!-- documentation-agent:implementation-evidence:start -->"
_END = "<!-- documentation-agent:implementation-evidence:end -->"
_PROTECTED = ("不含掛賬核銷與TT退款轉團款", "HKD 12,057,968")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"(?:password|passwd|secret|api[_ -]?key|access[_ -]?token)\s*[:=]", re.I),
    re.compile(r"\b(?:Bearer\s+|AKIA[0-9A-Z]{16}\b)", re.I),
    re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\)[^\s`]+", re.I),
)
_RAW_PATTERNS = (
    re.compile(r"\b(?:transaction[_ -]?rows?|raw[_ -]?rows?|source[_ -]?document[_ -]?no)\b", re.I),
    re.compile(r"(?:收款時間|來源單據號|交易號碼)"),
    re.compile(r"^\s*transaction[_ -]?id\s*,\s*(?:amount|value)\s*$", re.I | re.M),
)
_V2_DIFF_MAX_BYTES = 32 * 1024
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _safe_repo_path(root: Path, identity: str) -> Path:
    candidate = Path(identity.split("#", 1)[0].split("|", 1)[0])
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DocumentationValidationError("unsafe target path")
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise DocumentationValidationError("unsafe target path") from exc
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise DocumentationValidationError("symlink target is not allowed")
    return resolved


def _diff(path_identity: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=path_identity, tofile=path_identity,
    ))


def _markdown_headings(text: str) -> list[tuple[int, str, int]]:
    headings = []
    fence = None
    cursor = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        fence_match = _FENCE.match(bare)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = None
            cursor += len(line)
            continue
        if fence_match:
            fence = fence_match.group(1)
            cursor += len(line)
            continue
        match = _ATX_HEADING.match(bare)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip(), cursor))
        cursor += len(line)
    return headings


class DocumentationProposalValidator:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def build_target_preview(self, proposal: DocumentationProposalV2) -> DocumentationPreviewV2:
        """Build a policy-bound, read-only preview for exactly one v2 target section."""
        if not isinstance(proposal, DocumentationProposalV2):
            raise DocumentationValidationError("proposal must be DocumentationProposalV2")
        try:
            proposal = DocumentationProposalV2.from_dict(proposal.to_dict())
            target = load_documentation_target(proposal.target_id)
        except (DocumentationSchemaError, TypeError, ValueError) as exc:
            raise DocumentationValidationError("invalid v2 proposal or unknown target") from exc
        if proposal.status != "ready":
            raise DocumentationValidationError("v2 proposal is not ready")
        if proposal.operation != "replace_section":
            raise DocumentationValidationError("v2 target operation is not section replacement")
        if (
            proposal.target_kind != target.target_kind
            or proposal.repo_path != target.repo_path
            or proposal.section_heading != target.section_heading
        ):
            raise DocumentationValidationError("v2 proposal target differs from the fixed catalog")
        required_gates = {"review", "full-verification", "hermes"}
        gate_results = proposal.evidence.gate_results
        if (
            {item["gate"] for item in gate_results} != required_gates
            or any(
                item["status"] not in {"pass", "passed", "success", "ok"}
                or item["sourceFingerprint"] != proposal.evidence.source_fingerprint
                for item in gate_results
            )
        ):
            raise DocumentationValidationError("v2 proposal evidence gates are incomplete or stale")
        self._check_content(proposal.content)
        try:
            _, before_text, before_section, start, end = _read_target_section(
                self.project_root.resolve(), target,
            )
        except (OSError, UnicodeError, ValueError, PermissionError) as exc:
            raise DocumentationValidationError("v2 target section is missing or unsafe") from exc

        before_section_hash = _digest(before_section)
        if before_section_hash != proposal.expected_section_sha256:
            raise DocumentationValidationError("stale_target: section hash changed")

        target_level = len(target.section_heading) - len(target.section_heading.lstrip("#"))
        target_title = target.section_heading[target_level:].strip()
        headings = _markdown_headings(proposal.content)
        first_line = proposal.content.splitlines()[0] if proposal.content else ""
        if (
            first_line != target.section_heading
            or not proposal.content.endswith("\n")
            or not headings
            or headings[0] != (target_level, target_title, 0)
            or any(level <= target_level for level, _, _ in headings[1:])
        ):
            raise DocumentationValidationError("v2 content must replace only the selected Markdown section")

        after_text = before_text[:start] + proposal.content + before_text[end:]
        self._check_protected(before_text, after_text)
        unified_diff = _diff(target.repo_path, before_text, after_text)
        if len(unified_diff.encode("utf-8")) > _V2_DIFF_MAX_BYTES:
            raise DocumentationValidationError("v2 preview diff exceeds the 32 KiB limit")
        unsigned = {
            "schemaVersion": "documentation-preview-v2",
            "proposal": proposal.to_dict(),
            "status": "preview_ready",
            "taskId": proposal.task_id,
            "runId": proposal.evidence.run_id,
            "sourceFingerprint": proposal.evidence.source_fingerprint,
            "evidenceFingerprint": proposal.evidence_fingerprint,
            "proposalFingerprint": proposal.proposal_fingerprint,
            "targetId": target.target_id,
            "targetKind": target.target_kind,
            "repoPath": target.repo_path,
            "sectionHeading": target.section_heading,
            "riskTier": target.risk_tier,
            "requiredApprovalId": target.required_approval_id,
            "beforeSectionSha256": before_section_hash,
            "afterSectionSha256": _digest(proposal.content),
            "unifiedDiff": unified_diff,
        }
        preview = DocumentationPreviewV2.from_dict({
            **unsigned,
            "previewFingerprint": canonical_sha256(unsigned),
        })
        return preview

    def build_preview(
        self,
        proposal: DocumentationProposal,
        *,
        obsidian: ObsidianTargetResolver | None = None,
    ) -> DocumentationPreview:
        if proposal.status != "ready":
            raise DocumentationValidationError("proposal is not ready")
        items = []
        for raw in proposal.proposals:
            kind = raw["targetKind"]
            identity = raw["targetIdentity"]
            content = raw["content"]
            self._check_content(content)
            path = _safe_repo_path(self.project_root, identity)
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            after = self._render(kind, raw["operation"], identity, before, content, path.exists())
            self._check_protected(before, after)
            vault_relative = None
            if obsidian is not None:
                repo_identity = identity.split("#", 1)[0].split("::", 1)[0].split("|", 1)[0]
                resolved_vault = obsidian.resolve_info(kind, Path(repo_identity).name)
                if resolved_vault.path.exists():
                    vault_relative = resolved_vault.vault_relative_path
            items.append(DocumentationPreviewItem(
                kind, identity, vault_relative, _digest(before) if path.exists() else None,
                _digest(after), _diff(identity, before, after),
                "low" if kind == "brief_backfill" else "high",
                None if kind == "brief_backfill" else kind,
            ))
        return DocumentationPreview("preview_ready", tuple(items))

    def _render(self, kind: str, operation: str, identity: str, before: str, content: str, exists: bool) -> str:
        if kind == "brief_backfill":
            if operation != "update_managed_block" or not identity.startswith("docs/briefs/") or not identity.endswith(".md"):
                raise DocumentationValidationError("invalid Brief target")
            has_start = _START in content
            has_end = _END in content
            if has_start != has_end or content.count(_START) > 1 or content.count(_END) > 1:
                raise DocumentationValidationError("malformed managed block")
            block = content if has_start else f"{_START}\n{content.rstrip()}\n{_END}"
            pattern = re.compile(re.escape(_START) + r".*?" + re.escape(_END), re.S)
            if len(pattern.findall(before)) > 1:
                raise DocumentationValidationError("duplicate managed blocks")
            return pattern.sub(block, before, count=1) if pattern.search(before) else before.rstrip() + "\n\n" + block + "\n"
        if kind == "system_map":
            if operation != "replace_section" or identity.split("#", 1)[0].split("::", 1)[0].split("|", 1)[0] != "NBS_ANALYTICS_SYSTEM_MAP.md":
                raise DocumentationValidationError("invalid system map target")
            heading, expected = self._section_spec(identity, content)
            if heading.startswith("#") and " " not in heading:
                heading_re = re.compile(r"^#{1,6}[ \t]+" + re.escape(heading.lstrip("#")) + r"[ \t]*$", re.M)
            else:
                heading_re = re.compile(r"^" + re.escape(heading) + r"[ \t]*$", re.M)
            matches = list(heading_re.finditer(before))
            if len(matches) != 1:
                raise DocumentationValidationError("duplicate or missing section heading")
            start = matches[0].start()
            matched_heading = matches[0].group(0)
            level = len(matched_heading) - len(matched_heading.lstrip("#"))
            next_heading = re.compile(r"^#{1," + str(level) + r"}[ \t]+", re.M).search(before, matches[0].end())
            end = next_heading.start() if next_heading else len(before)
            section = before[start:end]
            if expected and _digest(section) != expected:
                raise DocumentationValidationError("stale_target: section hash changed")
            replacement = content if content.lstrip().startswith("#") else heading + "\n" + content
            if len(re.findall(r"^#{1," + str(level) + r"}[ \t]+", replacement, re.M)) > 1:
                raise DocumentationValidationError("replacement touches multiple sections")
            return before[:start] + replacement.rstrip() + "\n\n" + before[end:].lstrip("\n")
        if kind == "adr":
            if operation != "create_file" or not re.fullmatch(r"Summay/ADR-[^/]+\.md", identity) or exists:
                raise DocumentationValidationError("ADR is create-only and target must be new")
            return content
        raise DocumentationValidationError("unknown documentation target")

    @staticmethod
    def _section_spec(identity: str, content: str) -> tuple[str, str | None]:
        separator = "#" if "#" in identity else "::" if "::" in identity else None
        parts = identity.split(separator, 1) if separator else [identity]
        heading = parts[1].split("|", 1)[0].replace("%20", " ").strip() if len(parts) == 2 else ""
        expected = None
        suffix = parts[1] if len(parts) == 2 else ""
        for marker in ("|sha256=", "|baseSha256=", "|expectedSectionSha256="):
            if marker in suffix:
                expected = suffix.split(marker, 1)[1].split("|", 1)[0]
                break
        metadata = re.search(r"expectedSectionSha256\s*[:=]\s*([0-9a-f]{64})", content, re.I)
        if expected is None and metadata:
            expected = metadata.group(1).lower()
        if not heading:
            match = re.search(r"^#{1,6} .+$", content, re.M)
            heading = match.group(0) if match else ""
        if not heading:
            raise DocumentationValidationError("system map section heading is required")
        return heading, expected

    @staticmethod
    def _check_content(content: str) -> None:
        for pattern in (*_SECRET_PATTERNS, *_RAW_PATTERNS):
            if pattern.search(content):
                raise DocumentationValidationError("unsafe documentation content")

    @staticmethod
    def _check_protected(before: str, after: str) -> None:
        for protected in _PROTECTED:
            if protected in before and protected not in after:
                raise DocumentationValidationError("protected governance text was removed")
        if re.search(r"HKD\s+(?!12,057,968\b)\d[\d,]*", after) and not re.search(r"HKD\s+(?!12,057,968\b)\d[\d,]*", before):
            raise DocumentationValidationError("protected baseline mutation")


__all__ = [
    "DocumentationPreview", "DocumentationPreviewItem", "DocumentationPreviewV2", "DocumentationProposalValidator",
    "DocumentationValidationError",
]
