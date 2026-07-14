from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.agents.evidence_models import (
    CommandEvidence,
    EvidenceBundle,
    EvidenceItem,
    estimate_tokens,
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
    context_input_tokens: int = 12000

    @classmethod
    def from_project(cls, project_root: Path) -> "EvidencePolicy":
        root = project_root.resolve()
        allow = load_json_config(root, "agent_config/evidence_allowlist.json")
        budgets = load_json_config(root, "agent_config/token_budgets.json")
        excerpt = budgets["excerpt"]
        context = budgets["context"]
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
            context_input_tokens=int(context["inputTokens"]),
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
    _QUERY_MAX_FILES_PER_BATCH = 64
    _QUERY_MAX_ARGUMENT_CHARACTERS = 6000

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

    def _candidate_paths(self) -> tuple[str, ...]:
        candidates: set[str] = set()
        roots = [self.project_root / root for root in self.policy.read_roots]
        paths = [
            self.project_root / relative
            for relative in self.policy.root_files
            if (self.project_root / relative).is_file()
        ]
        for root in roots:
            if root.exists():
                paths.extend(path for path in root.rglob("*") if path.is_file())
        for path in paths:
            try:
                resolved = self.policy.resolve_read_path(path)
            except PermissionError:
                continue
            candidates.add(resolved.relative_to(self.project_root).as_posix())
        return tuple(sorted(candidates))

    def _query_paths(
        self, queries: tuple[str, ...]
    ) -> tuple[tuple[Path, ...], tuple[CommandEvidence, ...]]:
        found: list[Path] = []
        commands: list[CommandEvidence] = []
        candidates = self._candidate_paths()
        for index, query in enumerate(queries[:8]):
            if len(found) >= 12:
                break
            if not candidates:
                commands.append(CommandEvidence(
                    label=f"rg-query-{index}",
                    argv=("rg", "--files-with-matches", "--fixed-strings", "--", query),
                    exit_code=1,
                    stdout="",
                    stderr="No policy-approved evidence files",
                ))
                continue
            prefix = ["rg", "--files-with-matches", "--fixed-strings", "--", query]
            prefix_characters = sum(len(argument) + 1 for argument in prefix)
            batches: list[tuple[str, ...]] = []
            batch: list[str] = []
            batch_characters = prefix_characters
            for candidate in candidates:
                candidate_characters = len(candidate) + 1
                if prefix_characters + candidate_characters > self._QUERY_MAX_ARGUMENT_CHARACTERS:
                    raise ValueError(f"Evidence candidate path is too long: {candidate}")
                if batch and (
                    len(batch) >= self._QUERY_MAX_FILES_PER_BATCH
                    or batch_characters + candidate_characters > self._QUERY_MAX_ARGUMENT_CHARACTERS
                ):
                    batches.append(tuple(batch))
                    batch = []
                    batch_characters = prefix_characters
                batch.append(candidate)
                batch_characters += candidate_characters
            if batch:
                batches.append(tuple(batch))

            for batch_index, candidate_batch in enumerate(batches):
                result = self._run(
                    f"rg-query-{index}-{batch_index}",
                    [*prefix, *candidate_batch],
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
                if len(found) >= 12:
                    break
        return tuple(found), tuple(commands)

    def _resolve_ref(self, label: str, ref: str) -> tuple[str, CommandEvidence]:
        if not ref or ref.startswith("-"):
            raise ValueError(f"Invalid ref: {ref!r}")
        command = self._run(
            label,
            ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        )
        sha = command.stdout.strip()
        if command.exit_code != 0 or len(sha) != 40 or any(
            character not in "0123456789abcdef" for character in sha.lower()
        ):
            raise ValueError(f"Unable to resolve immutable ref: {ref!r}")
        return sha, command

    @staticmethod
    def _estimate_input_tokens(
        task: dict,
        guardrails: dict,
        evidence: tuple[EvidenceItem, ...],
        commands: tuple[CommandEvidence, ...],
    ) -> int:
        payload = {
            "task": task,
            "guardrails": guardrails,
            "evidence": [item.to_dict() for item in evidence],
            "commands": [item.to_dict() for item in commands],
        }
        return estimate_tokens(json.dumps(payload, ensure_ascii=False, sort_keys=True))

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
        base_sha, base = self._resolve_ref("git-base", base_ref)
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
        guardrails = {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        }
        all_commands = commands + (base,) + query_commands
        estimated_input_tokens = self._estimate_input_tokens(
            task, guardrails, evidence, all_commands,
        )
        repository = {
            **repository,
            "base": base_ref,
            "baseSha": base_sha,
            "estimatedInputTokens": estimated_input_tokens,
            "contextOverflow": estimated_input_tokens > self.policy.context_input_tokens,
        }
        return EvidenceBundle(
            schema_version="context-evidence-v1",
            task=task,
            repository=repository,
            guardrails=guardrails,
            evidence=evidence,
            commands=all_commands,
        )

    def collect_review(
        self, brief_path: Path, base_ref: str = "main", head_ref: str = "WORKTREE"
    ) -> EvidenceBundle:
        repository, commands = self._repository()
        base_sha, base_command = self._resolve_ref("git-base", base_ref)
        head_command = None
        head_sha = None
        if head_ref == "WORKTREE":
            diff_range = base_sha
        else:
            head_sha, head_command = self._resolve_ref("git-head-ref", head_ref)
            diff_range = f"{base_sha}...{head_sha}"
        diff_options = ["--no-textconv", "--no-ext-diff"]
        changed = self._run(
            "git-diff-name-only",
            ["git", "diff", *diff_options, "--name-only", diff_range],
        )
        patches: list[EvidenceItem] = []
        patch_commands: list[CommandEvidence] = []
        for index, relative in enumerate(changed.stdout.splitlines()[:50]):
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                raise PermissionError(f"Unsafe changed path: {relative}")
            self.policy.resolve_read_path(self.project_root / relative)
            patch = self._run(
                f"git-diff-file-{index}",
                ["git", "diff", *diff_options, diff_range, "--", relative],
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
                "baseSha": base_sha,
                "headRef": head_ref,
                "headSha": head_sha,
                "diffFileLimitExceeded": changed.truncated
                or len(changed.stdout.splitlines()) > 50,
            },
            guardrails={
                "revenueScope": "不含掛賬核銷與TT退款轉團款",
                "mayBaseline": "HKD 12,057,968",
            },
            evidence=tuple(patches),
            commands=commands + (base_command, *(tuple() if head_command is None else (head_command,)), changed, *patch_commands),
        )
