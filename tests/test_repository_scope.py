from __future__ import annotations

import subprocess
from pathlib import Path

from backend.agents.repository_scope import (
    SUMMARY_EXCLUDE_PATHSPEC,
    is_excluded_repository_path,
    repository_git_pathspecs,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout


def test_summary_path_guard_is_case_insensitive_and_component_based():
    assert is_excluded_repository_path("Summay/a.md")
    assert is_excluded_repository_path("docs/summay/a.md")
    assert not is_excluded_repository_path("docs/summary/a.md")
    assert not is_excluded_repository_path("docs/SummayNotes/a.md")


def test_git_pathspec_excludes_root_and_nested_case_variants(tmp_path: Path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "tests@example.com")
    _git(tmp_path, "config", "user.name", "Acceptance Tests")
    fixtures = {
        "Summay/root.md": "tracked root summary\n",
        "docs/Summay/nested.md": "tracked nested summary\n",
        "docs/summay/lower.md": "tracked lower summary\n",
        "src/allowed.py": "tracked allowed file\n",
    }
    for relative, content in fixtures.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(tmp_path, "add", "--all")
    _git(tmp_path, "commit", "-qm", "fixture")

    (tmp_path / "Summay/root.md").write_text("changed root summary\n", encoding="utf-8")
    (tmp_path / "docs/Summay/nested.md").write_text("changed nested summary\n", encoding="utf-8")
    (tmp_path / "docs/summay/lower.md").write_text("changed lower summary\n", encoding="utf-8")
    (tmp_path / "Summay/untracked.md").write_text("untracked summary\n", encoding="utf-8")
    (tmp_path / "src/allowed.py").write_text("changed allowed file\n", encoding="utf-8")
    pathspec = repository_git_pathspecs()

    status = _git(
        tmp_path, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames",
        "--", *pathspec,
    )
    diff = _git(
        tmp_path, "diff", "--no-ext-diff", "--no-renames", "HEAD", "--", *pathspec,
    )
    untracked = _git(
        tmp_path, "ls-files", "--others", "--exclude-standard", "--", *pathspec,
    )

    assert "src/allowed.py" in status
    assert "src/allowed.py" in diff
    assert "Summay" not in status + diff + untracked
    assert "summay" not in status + diff + untracked
    assert "Summay/untracked.md" not in untracked
    assert SUMMARY_EXCLUDE_PATHSPEC in pathspec
