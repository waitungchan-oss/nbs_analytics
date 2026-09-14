from pathlib import Path

from backend.agents.acceptance_paths import is_temporary_path, temporary_roots


def test_temporary_roots_canonicalize_macos_tmp_symlink(monkeypatch, tmp_path):
    real_tmp = tmp_path / "real-tmp"
    real_tmp.mkdir()
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(real_tmp))

    roots = temporary_roots(environ={"TMPDIR": "/tmp/ignored"}, platform_name="darwin")

    assert real_tmp.resolve() in roots


def test_temporary_roots_deduplicate_canonical_aliases(tmp_path):
    alias = tmp_path / "alias"
    target = tmp_path / "target"
    target.mkdir()
    alias.symlink_to(target, target_is_directory=True)

    roots = temporary_roots(
        environ={"TMPDIR": str(alias)},
        platform_name="linux",
        system_temp=target,
    )

    assert roots.count(target.resolve()) == 1


def test_is_temporary_path_rejects_sibling(tmp_path):
    root = tmp_path / "fixture"

    assert not is_temporary_path(tmp_path / "fixture-sibling", [root])


def test_is_temporary_path_accepts_nested_fixture(tmp_path):
    root = tmp_path / "fixture"
    nested = root / "evidence.json"

    assert is_temporary_path(nested, [root])
