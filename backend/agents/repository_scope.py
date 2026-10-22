"""Shared Git path scope for repository evidence that excludes user summaries."""

from __future__ import annotations


SUMMARY_EXCLUDE_PATHSPEC = ":(exclude,icase,glob)**/Summay/**"
SUMMARY_LEAF_EXCLUDE_PATHSPEC = ":(exclude,icase,glob)**/Summay"
AGENT_RUNTIME_EXCLUDE_PATHSPEC = ":(exclude).nbs_agent_runtime"


def repository_git_pathspecs(*additional_exclusions: str) -> tuple[str, ...]:
    """Return include-all plus exclusions for bounded repository Git commands."""
    return (".", SUMMARY_EXCLUDE_PATHSPEC, SUMMARY_LEAF_EXCLUDE_PATHSPEC, *additional_exclusions)


def verification_source_git_pathspecs(*additional_exclusions: str) -> tuple[str, ...]:
    """Exclude generated Agent runtime evidence from source worktree fingerprints."""
    return repository_git_pathspecs(AGENT_RUNTIME_EXCLUDE_PATHSPEC, *additional_exclusions)


def is_excluded_repository_path(relative_path: str) -> bool:
    """Lexically reject every path containing a case-insensitive Summay component."""
    if not isinstance(relative_path, str):
        return False
    return any(
        component.casefold() == "summay"
        for component in relative_path.replace("\\", "/").split("/")
    )
