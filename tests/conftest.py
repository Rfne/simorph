"""Общие фикстуры тестов simorph."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from simorph.core.factory import new_project
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import save_project

# Qt должен узнать про offscreen до первого импорта виджетов.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Момент времени, зафиксированный в тестах: без него хэш не воспроизводим.
FIXED_MOMENT = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)

#: Идентификатор проекта из эталонного файла.
FIXED_PROJECT_ID = UUID("3f1c2a9e-7b1d-4c2a-9e6f-1a2b3c4d5e6f")


@pytest.fixture
def fixed_moment() -> datetime:
    """Фиксированное время создания проекта."""
    return FIXED_MOMENT


@pytest.fixture
def project_document(fixed_moment: datetime) -> ProjectDocument:
    """Новый проект со всеми семью разделами и предсказуемым временем."""
    return new_project(
        "test",
        seed=42,
        project_id=FIXED_PROJECT_ID,
        moment=fixed_moment,
    )


@pytest.fixture
def project_dir(tmp_path: Path, project_document: ProjectDocument) -> Iterator[Path]:
    """Временный каталог с сохранённым ``project.json``."""
    save_project(project_document, tmp_path / "project.json", make_backup=False)
    yield tmp_path


@pytest.fixture
def project_path(project_dir: Path) -> Path:
    """Путь к сохранённому проекту."""
    return project_dir / "project.json"
