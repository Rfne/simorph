"""Тесты read-only исходников и статуса stale (T8-05, G8-05)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simorph.core.errors import ProjectError
from simorph.core.pipeline import (
    SOURCE_SNAPSHOT_VERSION,
    SourceTree,
    check_stale,
    language_for,
    load_snapshot,
    save_snapshot,
    scan_sources,
    snapshot_path,
    snapshot_payload,
)


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "pkg" / "cfg.json").write_text('{"a": 1}\n', encoding="utf-8")
    (root / "notes.txt").write_text("заметки\n", encoding="utf-8")
    (root / "image.bin").write_bytes(b"\x00\x01")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("hidden\n", encoding="utf-8")
    return root


def test_scan_collects_supported_files_and_skips_others(tmp_path: Path) -> None:
    tree = scan_sources(_tree(tmp_path))

    assert [item.path for item in tree.files] == ["main.py", "notes.txt", "pkg/cfg.json"]
    assert tree.truncated is False
    assert all(item.size > 0 for item in tree.files)
    assert tree.file("pkg/cfg.json") is not None
    assert tree.file("pkg/missing.json") is None


def test_digest_is_order_independent_and_changes_on_edit(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    first = scan_sources(root)
    reversed_tree = SourceTree(root=first.root, files=list(reversed(first.files)))

    assert first.digest == reversed_tree.digest

    (root / "main.py").write_text("print('changed')\n", encoding="utf-8")
    second = scan_sources(root)

    assert second.digest != first.digest


def test_read_returns_text_without_writing(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    tree = scan_sources(root)

    text = tree.read("main.py")

    assert text.splitlines() == ["print('ok')"]
    assert (root / "main.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_read_rejects_path_outside_root(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    tree = scan_sources(root)

    with pytest.raises(ProjectError, match="выходит за корень"):
        tree.read("../secret.txt")


def test_read_reports_missing_file(tmp_path: Path) -> None:
    tree = scan_sources(_tree(tmp_path))

    with pytest.raises(ProjectError, match="не найден"):
        tree.read("absent.py")


def test_scan_rejects_missing_or_file_root(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="не найден"):
        scan_sources(tmp_path / "absent")

    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(ProjectError, match="не является каталогом"):
        scan_sources(file)


def test_scan_truncates_at_limit(tmp_path: Path) -> None:
    tree = scan_sources(_tree(tmp_path), max_files=1)

    assert len(tree.files) == 1
    assert tree.truncated is True


def test_language_by_suffix() -> None:
    assert language_for("main.py") == "python"
    assert language_for("cfg.YAML") == "yaml"
    assert language_for("model.urdf") == "xml"
    assert language_for("unknown.zzz") == "text"


def test_snapshot_path_sits_next_to_project(tmp_path: Path) -> None:
    project = tmp_path / "project.json"

    assert snapshot_path(project) == tmp_path / "project.json.sources.json"
    assert snapshot_path(str(project)).name == "project.json.sources.json"


def test_snapshot_round_trip(tmp_path: Path) -> None:
    tree = scan_sources(_tree(tmp_path))
    target = tmp_path / "project.json.sources.json"

    save_snapshot(tree, target)

    payload = load_snapshot(target)
    assert payload["schema_version"] == SOURCE_SNAPSHOT_VERSION
    assert payload["digest"] == tree.digest
    assert payload["truncated"] is False
    assert len(payload["files"]) == 3
    assert snapshot_payload(tree)["digest"] == tree.digest


def test_load_snapshot_tolerates_missing_or_broken_file(tmp_path: Path) -> None:
    assert load_snapshot(tmp_path / "absent.json") == {}

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert load_snapshot(broken) == {}

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    assert load_snapshot(array) == {}


def test_check_stale_without_snapshot_is_missing(tmp_path: Path) -> None:
    tree = scan_sources(_tree(tmp_path))

    status = check_stale(tree, {})

    assert status.state == "missing"
    assert status.expected is None
    assert status.digest == tree.digest


def test_check_stale_reports_fresh_then_stale(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    tree = scan_sources(root)
    target = tmp_path / "project.json.sources.json"
    save_snapshot(tree, target)

    fresh = check_stale(tree, load_snapshot(target))
    assert fresh.state == "fresh"
    assert fresh.stale is False

    (root / "main.py").write_text("print('edited')\n", encoding="utf-8")
    edited = scan_sources(root)
    status = check_stale(edited, load_snapshot(target))

    assert status.state == "stale"
    assert status.stale is True
    assert status.changed == ["main.py"]
    assert status.added == []
    assert status.removed == []


def test_check_stale_lists_added_and_removed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    tree = scan_sources(root)
    target = tmp_path / "project.json.sources.json"
    save_snapshot(tree, target)

    (root / "notes.txt").unlink()
    (root / "new.md").write_text("# new\n", encoding="utf-8")
    updated = scan_sources(root)
    status = check_stale(updated, load_snapshot(target))

    assert status.state == "stale"
    assert status.removed == ["notes.txt"]
    assert status.added == ["new.md"]
    assert status.to_dict()["state"] == "stale"
