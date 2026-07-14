from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.agents.evidence_models import (
    CommandEvidence,
    EvidenceBundle,
    EvidenceItem,
    load_json_config,
)


@dataclass(frozen=True)
class EvidencePolicy:
    project_root: Path
    read_roots: tuple[str, ...]
    root_files: tuple[str, ...]
    default_context_files: tuple[str, ...]
    extensions: tuple[str, ...]
    deny_patterns: tuple[str, ...]
    max_file_lines: int
    max_command_characters: int
    agent_executables: tuple[str, ...]

    @classmethod
    def from_project(cls, project_root: Path) -> "EvidencePolicy":
        root = project_root.resolve()
        allow = load_json_config(root, "agent_config/evidence_allowlist.json")
        budgets = load_json_config(root, "agent_config/token_budgets.json")
        excerpt = budgets["excerpt"]
        return cls(
            project_root=root,
            read_roots=tuple(allow["readRoots"]),
            root_files=tuple(allow["rootFiles"]),
            default_context_files=tuple(allow.get("defaultContextFiles") or ()),
            extensions=tuple(allow["extensions"]),
            deny_patterns=tuple(allow["denyPatterns"]),
            max_file_lines=int(excerpt["maxFileLines"]),
            max_command_characters=int(excerpt["maxCommandCharacters"]),
            agent_executables=tuple(allow["agentExecutables"]),
        )

    def resolve_read_path(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise PermissionError(f"Path is outside project root: {path}") from exc

        relative_text = relative.as_posix()
        if any(
            fnmatch.fnmatch(relative_text, pattern)
            or fnmatch.fnmatch(resolved.name, pattern)
            for pattern in self.deny_patterns
        ):
            raise PermissionError(f"Denied evidence path: {relative_text}")

        top = relative.parts[0] if relative.parts else ""
        allowed = relative_text in self.root_files or top in self.read_roots
        if not allowed or resolved.suffix not in self.extensions:
            raise PermissionError(f"Path is not allowlisted: {relative_text}")
        return resolved


class EvidenceCollector:
    _COMMAND_EXECUTABLES = frozenset({"git", "rg"})

    def __init__(self, project_root: Path, *, policy: EvidencePolicy | None = None) -> None:
        self.project_root = project_root.resolve()
        self.policy = policy or EvidencePolicy.from_project(self.project_root)

    def _run(self, label: str, argv: list[str]) -> CommandEvidence:
        if not argv or argv[0] not in self._COMMAND_EXECUTABLES:
            raise PermissionError(f"Command is not allowlisted: {argv[0] if argv else '<empty>'}")
        completed = subprocess.run(
            argv,
            cwd=self.project_root,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            shell=False,
        )
        limit = self.policy.max_command_characters
        return CommandEvidence(
            label=label,
            argv=tuple(argv),
            exit_code=completed.returncode,
            stdout=completed.stdout[:limit],
            stderr=completed.stderr[:limit],
            truncated=len(completed.stdout) > limit or len(completed.stderr) > limit,
        )

    def _document(self, path: Path) -> EvidenceItem:
        resolved = self.policy.resolve_read_path(path)
        relative = resolved.relative_to(self.project_root).as_posix()
        lines = resolved.read_text(encoding="utf-8").splitlines()
        selected = lines[: self.policy.max_file_lines]
        return EvidenceItem(
            kind="document",
            source=relative,
            content="\n".join(selected),
            metadata={"lineCount": len(lines), "truncated": len(lines) > len(selected)},
        )

    def _query_paths(
        self, queries: tuple[str, ...]
    ) -> tuple[tuple[Path, ...], tuple[CommandEvidence, ...]]:
        found: list[Path] = []
        commands: list[CommandEvidence] = []
        roots = [root for root in self.policy.read_roots if (self.project_root / root).exists()]
        for index, query in enumerate(queries[:8]):
            result = self._run(
                f"rg-query-{index}",
                ["rg", "--files-with-matches", "--fixed-strings", "--", query, *roots],
            )
            commands.append(result)
            for line in result.stdout.splitlines():
                candidate = self.project_root / line
                try:
                    resolved = self.policy.resolve_read_path(candidate)
                except PermissionError:
                    continue
                if resolved not in found:
                    found.append(resolved)
                if len(found) >= 12:
                    break
        return tuple(found), tuple(commands)

    def _repository(self) -> tuple[dict, tuple[CommandEvidence, ...]]:
        head = self._run("git-head", ["git", "rev-parse", "HEAD"])
        branch = self._run("git-branch", ["git", "branch", "--show-current"])
        status = self._run("git-status", ["git", "status", "--porcelain"])
        recent = self._run("git-log", ["git", "log", "-5", "--oneline"])
        dirty = sorted(line[3:] for line in status.stdout.splitlines() if len(line) > 3)
        return {
            "branch": branch.stdout.strip(),
            "head": head.stdout.strip(),
            "dirtyFiles": dirty,
        }, (head, branch, status, recent)

    def collect_context(
        self,
        brief_path: Path,
        base_ref: str = "main",
        *,
        include_paths: tuple[Path, ...] = (),
        queries: tuple[str, ...] = (),
    ) -> EvidenceBundle:
        repository, commands = self._repository()
        base = self._run("git-base", ["git", "rev-parse", base_ref])
        query_paths, query_commands = self._query_paths(queries)
        default_paths = [
            self.project_root / relative
            for relative in self.policy.default_context_files
            if (self.project_root / relative).is_file()
        ]
        selected_paths = [brief_path, *default_paths, *include_paths, *query_paths]
        unique_paths = tuple(dict.fromkeys(path.resolve() for path in selected_paths))
        evidence = tuple(self._document(path) for path in unique_paths)
        task = {
            "id": brief_path.stem,
            "objective": evidence[0].content,
            "scope": [],
            "forbidden": [],
        }
        return EvidenceBundle(
            schema_version="context-evidence-v1",
            task=task,
            repository=repository,
            guardrails={
                "revenueScope": "不含掛賬核銷與TT退款轉團款",
                "mayBaseline": "HKD 12,057,968",
            },
            evidence=evidence,
            commands=commands + (base,) + query_commands,
        )

    def collect_review(
        self, brief_path: Path, base_ref: str = "main", head_ref: str = "WORKTREE"
    ) -> EvidenceBundle:
        repository, commands = self._repository()
        diff_range = base_ref if head_ref == "WORKTREE" else f"{base_ref}...{head_ref}"
        changed = self._run("git-diff-name-only", ["git", "diff", "--name-only", diff_range])
        patches: list[EvidenceItem] = []
        patch_commands: list[CommandEvidence] = []
        for index, relative in enumerate(changed.stdout.splitlines()[:50]):
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                raise PermissionError(f"Unsafe changed path: {relative}")
            self.policy.resolve_read_path(self.project_root / relative)
            patch = self._run(
                f"git-diff-file-{index}",
                ["git", "diff", diff_range, "--", relative],
            )
            patch_commands.append(patch)
            patches.append(
                EvidenceItem(
                    kind="diff",
                    source=relative,
                    content=patch.stdout,
                    metadata={"truncated": patch.truncated},
                )
            )
        return EvidenceBundle(
            schema_version="review-evidence-v1",
            task={
                "id": brief_path.stem,
                "objective": self._document(brief_path).content,
                "scope": [],
                "forbidden": [],
            },
            repository={
                **repository,
                "base": base_ref,
                "headRef": head_ref,
                "diffFileLimitExceeded": changed.truncated
                or len(changed.stdout.splitlines()) > 50,
            },
            guardrails={
                "revenueScope": "不含掛賬核銷與TT退款轉團款",
                "mayBaseline": "HKD 12,057,968",
            },
            evidence=tuple(patches),
            commands=commands + (changed, *patch_commands),
        )
