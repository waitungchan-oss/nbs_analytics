from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .documentation_models import DocumentationProposal
from .documentation_targets import ObsidianTargetResolver


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


class DocumentationProposalValidator:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

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
                vault_relative = obsidian.resolve_info(kind, Path(repo_identity).name).vault_relative_path
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
    "DocumentationPreview", "DocumentationPreviewItem", "DocumentationProposalValidator",
    "DocumentationValidationError",
]
