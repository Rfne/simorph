"""Тесты настроек приложения (T1-06, G1-08)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simorph.core.canonical import compute_hash
from simorph.core.models.project import ProjectDocument
from simorph.settings import ApplicationSettings


@pytest.fixture
def settings(tmp_path: Path) -> ApplicationSettings:
    """Изолированный ``QSettings`` в ini-файле временного каталога."""
    pytest.importorskip("PySide6.QtCore")
    from PySide6.QtCore import QSettings

    return ApplicationSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )


@pytest.mark.unit
def test_defaults_are_sane(settings: ApplicationSettings) -> None:
    assert settings.theme == "dark"
    assert settings.language == "ru"
    assert settings.recent_projects == []
    assert settings.editor_path is None


@pytest.mark.unit
def test_values_are_persisted(settings: ApplicationSettings, tmp_path: Path) -> None:
    settings.theme = "light"
    settings.language = "en"
    settings.editor_path = tmp_path / "code.exe"
    settings.sync()

    assert settings.theme == "light"
    assert settings.language == "en"
    assert settings.editor_path == tmp_path / "code.exe"


@pytest.mark.unit
def test_invalid_theme_is_rejected(settings: ApplicationSettings) -> None:
    with pytest.raises(ValueError, match="тема"):
        settings.theme = "neon"


@pytest.mark.unit
def test_invalid_language_is_rejected(settings: ApplicationSettings) -> None:
    with pytest.raises(ValueError, match="язык"):
        settings.language = "fr"


@pytest.mark.unit
def test_recent_is_deduplicated(settings: ApplicationSettings) -> None:
    settings.add_recent("/a/project.json")
    settings.add_recent("/b/project.json")
    settings.add_recent("/a/project.json")

    assert [str(item) for item in settings.recent_projects] == [
        str(Path("/a/project.json")),
        str(Path("/b/project.json")),
    ]


@pytest.mark.unit
def test_recent_is_limited(settings: ApplicationSettings) -> None:
    for index in range(15):
        settings.add_recent(f"/p{index}/project.json")
    assert len(settings.recent_projects) == 10


@pytest.mark.unit
def test_clear_recent(settings: ApplicationSettings) -> None:
    settings.add_recent("/a/project.json")
    settings.clear_recent()
    assert settings.recent_projects == []


@pytest.mark.unit
def test_settings_do_not_touch_project(
    settings: ApplicationSettings,
    project_document: ProjectDocument,
) -> None:
    """G1-08: настройки приложения не влияют на содержимое проекта."""
    before = compute_hash(project_document)
    settings.theme = "light"
    settings.language = "en"
    settings.editor_path = "C:/tools/editor.exe"
    settings.add_recent("C:/projects/other/project.json")
    settings.sync()

    assert compute_hash(project_document) == before
    dumped = project_document.model_dump(mode="json", by_alias=True)
    assert "editor" not in str(dumped)
    assert dumped["settings"] == {"theme": "dark", "language": "ru", "offline": True}


@pytest.mark.unit
def test_snapshot_contains_all_keys(settings: ApplicationSettings) -> None:
    snapshot = settings.to_dict()
    assert set(snapshot) == {
        "theme",
        "language",
        "editor_path",
        "last_dir",
        "recent",
        "search_history",
        "layout",
    }


@pytest.mark.unit
def test_last_dir_roundtrip(settings: ApplicationSettings, tmp_path: Path) -> None:
    settings.last_dir = tmp_path
    assert settings.last_dir == tmp_path
    settings.last_dir = None
    assert settings.last_dir is None
