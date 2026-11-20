"""Тесты репозитория: атомарность, резервные копии, стабильность (T1-04)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from simorph.core.canonical import dump_document
from simorph.core.errors import ProjectFormatError, ProjectNotFoundError
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import (
    backup_path_for,
    load_project,
    read_json,
    save_project,
    write_text_atomic,
)


@pytest.mark.unit
def test_save_creates_file_with_lf(tmp_path: Path, project_document: ProjectDocument) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)

    raw = path.read_bytes()
    assert raw.startswith(b"{")
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert raw.decode("utf-8")


@pytest.mark.unit
def test_open_save_open_save_is_byte_identical(
    tmp_path: Path, project_document: ProjectDocument
) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)
    first = path.read_bytes()

    loaded = load_project(path)
    save_project(loaded.document, path, make_backup=False)
    second = path.read_bytes()

    assert first == second


@pytest.mark.unit
def test_save_creates_backup(tmp_path: Path, project_document: ProjectDocument) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)
    original = path.read_text(encoding="utf-8")

    changed = project_document.model_copy(update={"name": "изменённый"})
    backup = save_project(changed, path)

    assert backup == backup_path_for(path)
    assert backup is not None
    assert backup.read_text(encoding="utf-8") == original


@pytest.mark.unit
def test_failed_write_keeps_original_intact(
    tmp_path: Path, project_document: ProjectDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)
    original = path.read_bytes()

    def boom(self: Path, target: Path) -> None:
        raise OSError("диск переполнен")

    monkeypatch.setattr(Path, "replace", boom)

    with pytest.raises(OSError):
        save_project(project_document.model_copy(update={"name": "новый"}), path)

    assert path.read_bytes() == original
    assert backup_path_for(path).exists()
    assert not (tmp_path / "project.json.tmp").exists()


@pytest.mark.unit
def test_failed_write_leaves_no_tmp(
    tmp_path: Path, project_document: ProjectDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "project.json"

    def boom(self: Path, target: Path) -> None:
        raise OSError("нет места")

    monkeypatch.setattr(Path, "replace", boom)

    with pytest.raises(OSError):
        save_project(project_document, path)
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.unit
def test_missing_file_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        read_json(tmp_path / "absent.json")


@pytest.mark.unit
def test_broken_json_reports_position(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"a": 1,}', encoding="utf-8")
    with pytest.raises(ProjectFormatError) as exc_info:
        read_json(path)
    assert "строка" in str(exc_info.value)


@pytest.mark.unit
def test_non_object_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ProjectFormatError):
        read_json(path)


@pytest.mark.unit
def test_utf8_content_roundtrip(tmp_path: Path, project_document: ProjectDocument) -> None:
    path = tmp_path / "проект.json"
    document = project_document.model_copy(update={"name": "Калибровка НРТК"})
    save_project(document, path, make_backup=False)

    loaded = load_project(path)
    assert loaded.document.name == "Калибровка НРТК"


@pytest.mark.unit
def test_load_reports_migrations(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(
        '{"version": "0.9", "project_id": "3f1c2a9e-7b1d-4c2a-9e6f-1a2b3c4d5e6f",'
        ' "name": "old", "profile": {"parameters": []},'
        ' "world": {"world_id": "W-01", "seed": 1, "terrain": {"size_m": [10, 10]}},'
        ' "agents": [], "exercises": [], "pipelines": [], "reports": []}',
        encoding="utf-8",
    )
    loaded = load_project(path)
    assert loaded.migrated
    assert loaded.document.schema_version == "1.0"


@pytest.mark.unit
def test_write_text_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "file.txt"
    write_text_atomic(target, "данные\n", make_backup=False)
    assert target.read_text(encoding="utf-8") == "данные\n"


@pytest.mark.unit
def test_dump_matches_saved_file(tmp_path: Path, project_document: ProjectDocument) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)
    assert path.read_text(encoding="utf-8") == dump_document(project_document)


@pytest.mark.unit
def test_no_backup_when_disabled(tmp_path: Path, project_document: ProjectDocument) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)
    assert save_project(project_document, path, make_backup=False) is None
    assert not backup_path_for(path).exists()


@pytest.mark.unit
@pytest.mark.skipif(os.name == "nt", reason="проверка прав файловой системы различается")
def test_readonly_directory_fails_cleanly(
    tmp_path: Path, project_document: ProjectDocument
) -> None:
    path = tmp_path / "project.json"
    save_project(project_document, path, make_backup=False)
    original = path.read_bytes()

    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError):
            save_project(project_document.model_copy(update={"name": "x"}), path)
    finally:
        tmp_path.chmod(0o700)

    assert path.read_bytes() == original
