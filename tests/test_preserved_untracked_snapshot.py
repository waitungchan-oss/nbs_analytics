from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.agents.preserved_untracked_snapshot import snapshot_preserved_untracked


ROOTS = (
    "Summay/AGENT_MEMORY_MAP_2026-09-05",
    "Summay/agent-town-demo",
    "Summay/秋季旅遊展_2026-09-25至27_銷售數據整理.docx",
)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_root(project: Path, root: str, files: dict[str, bytes]) -> None:
    root_path = project / root
    root_path.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = root_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def test_snapshot_sorts_exact_roots_and_redacts_descendant_names_and_content(tmp_path):
    first = "Summay/AGENT_MEMORY_MAP_2026-09-05"
    second = "Summay/agent-town-demo"
    _write_root(tmp_path, first, {"nested/private note.txt": b"confidential body"})
    _write_root(tmp_path, second, {"readme.md": b"public-ish body"})

    result = snapshot_preserved_untracked(tmp_path, [second, first])
    payload = result.to_dict()

    assert payload["schemaVersion"] == "preserved-untracked-inventory-v1"
    assert [entry["path"] for entry in payload["entries"]] == [first, second]
    assert payload["entries"][0]["regularFileCount"] == 1
    assert payload["entries"][0]["totalBytes"] == len(b"confidential body")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private note.txt" not in serialized
    assert "confidential body" not in serialized
    assert "public-ish body" not in serialized
    assert payload["inventoryFingerprint"] == _canonical_sha256(
        {"schemaVersion": payload["schemaVersion"], "entries": payload["entries"]}
    )


def test_snapshot_tree_digest_binds_utf8_path_and_file_bytes(tmp_path):
    root = "Summay/agent-town-demo"
    content = "hello 世界".encode()
    relative = "資料 note.txt"
    _write_root(tmp_path, root, {relative: content})

    entry = snapshot_preserved_untracked(tmp_path, [root]).to_dict()["entries"][0]
    file_hash = hashlib.sha256(content).hexdigest()
    tree_hash = _canonical_sha256([
        {"path": ".", "kind": "directory", "bytes": 0},
        {"path": relative, "kind": "file", "bytes": len(content), "sha256": file_hash},
    ])

    assert entry["treeSha256"] == tree_hash
    assert entry["maxDepth"] == 1


def test_snapshot_is_stable_across_calls_and_accepts_an_empty_directory(tmp_path):
    root = "Summay/AGENT_MEMORY_MAP_2026-09-05"
    (tmp_path / root).mkdir(parents=True)

    first = snapshot_preserved_untracked(tmp_path, [root]).to_dict()
    second = snapshot_preserved_untracked(tmp_path, [root]).to_dict()

    assert first == second
    assert first["entries"][0]["regularFileCount"] == 0
    assert first["entries"][0]["totalBytes"] == 0


def test_snapshot_handles_an_approved_single_file_root(tmp_path):
    root = ROOTS[2]
    content = b"docx placeholder bytes"
    target = tmp_path / root
    target.parent.mkdir(parents=True)
    target.write_bytes(content)

    entry = snapshot_preserved_untracked(tmp_path, [root]).to_dict()["entries"][0]

    assert entry["kind"] == "file"
    assert entry["regularFileCount"] == 1
    assert entry["totalBytes"] == len(content)
    assert entry["maxDepth"] == 0


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["Summay"],
        ["Summay/unknown"],
        ["../Summay/agent-town-demo"],
        ["Summay/agent-town-demo/../AGENT_MEMORY_MAP_2026-09-05"],
        ["Summay/agent-town-demo", "Summay/agent-town-demo"],
        ["Summay/AGENT_MEMORY_MAP_2026-09-05", "Summay/AGENT_MEMORY_MAP_2026-09-05/nested"],
    ],
)
def test_snapshot_rejects_non_allowlisted_duplicate_or_traversing_roots(tmp_path, paths):
    with pytest.raises(ValueError):
        snapshot_preserved_untracked(tmp_path, paths)


def test_snapshot_rejects_missing_approved_root(tmp_path):
    with pytest.raises((OSError, ValueError)):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


def test_snapshot_rejects_symlink_descendant(tmp_path):
    root = tmp_path / ROOTS[0]
    root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not follow", encoding="utf-8")
    (root / "link").symlink_to(outside)

    with pytest.raises((OSError, ValueError)):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


@pytest.mark.parametrize("link_component", ["Summay", ROOTS[0].split("/")[-1]])
def test_snapshot_rejects_symlink_in_approved_root_path(tmp_path, link_component):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.txt").write_text("not in root", encoding="utf-8")
    if link_component == "Summay":
        (tmp_path / "Summay").symlink_to(outside, target_is_directory=True)
    else:
        summary = tmp_path / "Summay"
        summary.mkdir()
        (summary / link_component).symlink_to(outside, target_is_directory=True)

    with pytest.raises((OSError, ValueError)):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


def test_snapshot_rejects_hardlinked_regular_file(tmp_path):
    root = tmp_path / ROOTS[0]
    root.mkdir(parents=True)
    original = root / "original.txt"
    original.write_text("linked", encoding="utf-8")
    try:
        os.link(original, root / "alias.txt")
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises((OSError, ValueError)):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


def test_snapshot_rejects_special_files(tmp_path):
    root = tmp_path / ROOTS[0]
    root.mkdir(parents=True)
    try:
        os.mkfifo(root / "pipe")
    except (AttributeError, OSError) as exc:
        pytest.skip(f"FIFO creation is unavailable: {exc}")

    with pytest.raises((OSError, ValueError)):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


@pytest.mark.parametrize(
    ("limit_name", "limit", "files", "roots", "expected_code"),
    [
        ("MAX_DEPTH", 2, {"a/b/c.txt": b"x"}, [ROOTS[0]], "preserved_untracked_limit_exceeded"),
        ("MAX_FILES_PER_ROOT", 2, {f"f{i}.txt": b"x" for i in range(3)}, [ROOTS[0]], "preserved_untracked_limit_exceeded"),
        ("MAX_TOTAL_FILES", 1, {"a.txt": b"x"}, [ROOTS[0], ROOTS[1]], "preserved_untracked_limit_exceeded"),
        ("MAX_FILE_BYTES", 3, {"large.txt": b"1234"}, [ROOTS[0]], "preserved_untracked_limit_exceeded"),
        ("MAX_TOTAL_BYTES", 3, {"a.txt": b"12", "b.txt": b"34"}, [ROOTS[0]], "preserved_untracked_limit_exceeded"),
    ],
)
def test_snapshot_enforces_each_configured_cap(tmp_path, monkeypatch, limit_name, limit, files, roots, expected_code):
    from backend.agents import preserved_untracked_snapshot as snapshot_module

    _write_root(tmp_path, roots[0], files)
    for root in roots[1:]:
        _write_root(tmp_path, root, {"extra.txt": b"x"})
    monkeypatch.setattr(snapshot_module, limit_name, limit)

    with pytest.raises(ValueError, match=expected_code):
        snapshot_preserved_untracked(tmp_path, roots)


def test_snapshot_bounds_total_inventory_entries_in_empty_directory_tree(tmp_path, monkeypatch):
    from backend.agents import preserved_untracked_snapshot as snapshot_module

    root = tmp_path / ROOTS[0]
    for name in ("empty-a", "empty-b", "empty-c"):
        (root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(snapshot_module, "MAX_TOTAL_ENTRIES", 3)

    with pytest.raises(ValueError, match="preserved_untracked_limit_exceeded"):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


@pytest.mark.parametrize("capability", ["_OPEN_NOFOLLOW", "_OPEN_DIRECTORY"])
def test_snapshot_fails_closed_when_secure_open_capability_is_unavailable(tmp_path, monkeypatch, capability):
    from backend.agents import preserved_untracked_snapshot as snapshot_module

    (tmp_path / ROOTS[0]).mkdir(parents=True)
    monkeypatch.setattr(snapshot_module, capability, 0)

    def unexpected_open(*args, **kwargs):
        raise AssertionError("project root must not be opened without secure traversal flags")

    monkeypatch.setattr(snapshot_module.os, "open", unexpected_open)
    with pytest.raises(ValueError, match="preserved_untracked_unsafe_entry"):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


def test_snapshot_detects_file_change_during_hash(tmp_path, monkeypatch):
    from backend.agents import preserved_untracked_snapshot as snapshot_module

    root = tmp_path / ROOTS[0]
    root.mkdir(parents=True)
    target = root / "moving.txt"
    target.write_text("before", encoding="utf-8")
    read_regular_file = snapshot_module._read_regular_file

    def mutate_after_read(*args, **kwargs):
        result = read_regular_file(*args, **kwargs)
        target.write_text("after with different length", encoding="utf-8")
        return result

    monkeypatch.setattr(snapshot_module, "_read_regular_file", mutate_after_read)
    with pytest.raises((OSError, ValueError), match="changed|race|drift"):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


def test_snapshot_detects_directory_change_during_traversal(tmp_path, monkeypatch):
    from backend.agents import preserved_untracked_snapshot as snapshot_module

    root = tmp_path / ROOTS[0]
    root.mkdir(parents=True)
    (root / "first.txt").write_text("first", encoding="utf-8")
    list_directory_names = snapshot_module._list_directory_names
    changed = False

    def add_during_listing(directory_fd, budget):
        nonlocal changed
        names = list_directory_names(directory_fd, budget)
        if not changed:
            (root / "late.txt").write_text("late", encoding="utf-8")
            changed = True
        return names

    monkeypatch.setattr(snapshot_module, "_list_directory_names", add_during_listing)
    with pytest.raises((OSError, ValueError), match="changed|drift|consistent"):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])


def test_snapshot_detects_ancestor_replaced_by_symlink_during_hash(tmp_path, monkeypatch):
    from backend.agents import preserved_untracked_snapshot as snapshot_module

    root = tmp_path / ROOTS[0]
    root.mkdir(parents=True)
    target = root / "moving.txt"
    target.write_text("before", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    real_read_regular_file = snapshot_module._read_regular_file
    replaced = False

    def replace_ancestor_after_read(*args, **kwargs):
        nonlocal replaced
        result = real_read_regular_file(*args, **kwargs)
        if not replaced:
            (tmp_path / "Summay").rename(tmp_path / "Summay.original")
            (tmp_path / "Summay").symlink_to(outside, target_is_directory=True)
            replaced = True
        return result

    monkeypatch.setattr(snapshot_module, "_read_regular_file", replace_ancestor_after_read)
    with pytest.raises((OSError, ValueError)):
        snapshot_preserved_untracked(tmp_path, [ROOTS[0]])
