"""Репозиторий проекта: чтение, атомарная запись, резервная копия (T1-04).

Запись идёт по схеме «временный файл → fsync → резервная копия → замена»,
поэтому сбой на любом шаге оставляет исходный файл целым. Переводы строк
всегда ``\\n``, кодировка — UTF-8 без BOM: иначе golden-тесты разойдутся
между Windows и Linux.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simorph.core.canonical import dump_document
from simorph.core.errors import ProjectFormatError, ProjectNotFoundError
from simorph.core.migrations import migrate_payload
from simorph.core.models.project import ProjectDocument
from simorph.core.validation import parse_document

__all__ = [
    "BACKUP_SUFFIX",
    "TMP_SUFFIX",
    "LoadedProject",
    "backup_path_for",
    "load_project",
    "parse_document",
    "read_json",
    "save_project",
    "write_text_atomic",
]

#: Суффикс временного файла при атомарной записи.
TMP_SUFFIX = ".tmp"

#: Суффикс резервной копии.
BACKUP_SUFFIX = ".bak"


@dataclass(frozen=True, slots=True)
class LoadedProject:
    """Результат чтения: документ и сведения о миграции."""

    document: ProjectDocument
    payload: dict[str, Any] = field(default_factory=dict)
    applied_migrations: list[str] = field(default_factory=list)

    @property
    def migrated(self) -> bool:
        """Была ли применена хотя бы одна миграция."""
        return bool(self.applied_migrations)


def read_json(path: Path) -> dict[str, Any]:
    """Читает JSON-объект из файла.

    Raises:
        ProjectNotFoundError: файла нет.
        ProjectFormatError: файл не читается или это не JSON-объект.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProjectNotFoundError(f"файл проекта не найден: {path}") from exc
    except OSError as exc:
        raise ProjectFormatError(f"не удалось прочитать {path}: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectFormatError(
            f"{path}: некорректный JSON, строка {exc.lineno}, позиция {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(payload, dict):
        raise ProjectFormatError(f"{path}: ожидался JSON-объект")
    return payload


def load_project(path: Path, *, migrate: bool = True) -> LoadedProject:
    """Читает проект, при необходимости применяя миграции.

    Args:
        path: путь к ``project.json``.
        migrate: применять ли цепочку миграций к старым версиям.
    """
    payload = read_json(path)
    applied: list[str] = []
    if migrate:
        result = migrate_payload(payload, section="project")
        payload = result.payload
        applied = result.applied
    document = parse_document(payload)
    return LoadedProject(document=document, payload=payload, applied_migrations=applied)


def write_text_atomic(path: Path, text: str, *, make_backup: bool = True) -> Path | None:
    """Записывает текст атомарно, сохраняя предыдущую версию в ``.bak``.

    Returns:
        Путь к резервной копии или ``None``, если копировать было нечего.

    Raises:
        OSError: запись невозможна; исходный файл при этом не изменён.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + TMP_SUFFIX)
    backup_path = path.with_name(path.name + BACKUP_SUFFIX)

    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    created_backup: Path | None = None
    try:
        if make_backup and path.exists():
            shutil.copyfile(path, backup_path)
            created_backup = backup_path
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    return created_backup


def save_project(
    document: ProjectDocument,
    path: Path,
    *,
    make_backup: bool = True,
) -> Path | None:
    """Сохраняет документ в каноническом читаемом виде.

    Returns:
        Путь к резервной копии или ``None``.
    """
    return write_text_atomic(path, dump_document(document), make_backup=make_backup)


def backup_path_for(path: Path) -> Path:
    """Путь резервной копии для проекта."""
    return path.with_name(path.name + BACKUP_SUFFIX)
