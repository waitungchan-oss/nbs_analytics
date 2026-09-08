from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_conftest():
    path = Path(__file__).with_name("conftest.py")
    spec = importlib.util.spec_from_file_location("nbs_test_conftest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_db_resolves_git_worktree_common_root(tmp_path: Path) -> None:
    conftest = _load_conftest()
    common_root = tmp_path / "main"
    worktree = tmp_path / "worktree" / "nbs_analytics"
    (common_root / ".git" / "worktrees" / "nbs_analytics").mkdir(parents=True)
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(
        f"gitdir: {common_root / '.git' / 'worktrees' / 'nbs_analytics'}\n",
        encoding="utf-8",
    )
    expected = common_root / "nbs_marketing_data.db"
    expected.write_bytes(b"canonical-db")

    assert conftest._git_common_root(worktree) == common_root
    assert conftest._source_db(worktree, minimum_bytes=1) == expected
