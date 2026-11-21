"""Тесты главного окна: запуск, навигация, правка, темы (T2-01, G2-01, G2-02, G2-05)."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from simorph.core.locking import lock_path_for
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import load_project
from simorph.settings import ApplicationSettings
from simorph.ui.main_window import MainWindow
from simorph.ui.session import autosave_path_for


@pytest.fixture
def window(qtbot: object, isolated_settings: ApplicationSettings) -> MainWindow:
    """Окно с изолированными настройками и без модальных вопросов.

    Модальные диалоги в offscreen-режиме некому закрыть, поэтому вопросы
    выключены: их проверяет отдельный тест с подменённым ``QMessageBox``.
    """
    created = MainWindow(isolated_settings)
    created.confirmations_enabled = False
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    return created


@pytest.mark.ui
def test_window_starts_with_six_groups(window: MainWindow) -> None:
    assert window.tree_model.rowCount() == 6
    assert window.issues == []
    assert "simorph" in window.windowTitle()


@pytest.mark.ui
def test_theme_switches_without_restart(window: MainWindow) -> None:
    assert window.switch_theme("light") == "light"
    assert window.current_theme == "light"
    assert window.switch_theme("dark") == "dark"
    assert window.current_theme == "dark"


@pytest.mark.ui
def test_navigation_selects_node_and_shows_properties(window: MainWindow) -> None:
    window.navigate_to("agents.0.sensors.0.id")

    assert window.inspector.path == "agents.0.sensors.0.id"
    index = window.tree_view.currentIndex()
    assert index.isValid()


@pytest.mark.ui
def test_edit_is_saved_and_visible_after_reopen(
    window: MainWindow, project_path: Path, tmp_path: Path
) -> None:
    assert window.open_project(project_path, confirm=False)

    assert window.inspector.commit("world.seed", 777)
    assert window.store.document.world.seed == 777

    target = tmp_path / "saved" / "project.json"
    assert window._save_to(target)
    assert load_project(target).document.world.seed == 777


@pytest.mark.ui
def test_invalid_value_is_rejected(window: MainWindow) -> None:
    before = window.store.document.world.seed

    assert window.inspector.commit("world.seed", -5) is False
    assert window.store.document.world.seed == before


@pytest.mark.ui
def test_validation_error_raises_badge(
    window: MainWindow, project_document: ProjectDocument
) -> None:
    window.store.reset(project_document)
    window.store.set_value("exercises.0.world_id", "W-99")

    issues = window.validate_project()

    assert issues
    group = window.tree_model.index(3, 0)
    assert "⚠" in str(group.data(int(Qt.ItemDataRole.DisplayRole)))


@pytest.mark.ui
def test_error_navigation_opens_field(
    window: MainWindow, project_document: ProjectDocument
) -> None:
    window.store.reset(project_document)
    window.store.set_value("exercises.0.world_id", "W-99")
    issues = window.validate_project()
    target = next(item for item in issues if item.path.startswith("exercises.0"))

    window.navigate_to(target.path)

    assert window.inspector.path.startswith("exercises.0")
    assert window.tree_view.currentIndex().isValid()


@pytest.mark.ui
def test_tabs_open_and_restore_layout(
    window: MainWindow, isolated_settings: ApplicationSettings
) -> None:
    assert window.open_section("world") == 0
    assert window.open_section("agents") == 1
    assert window.tabs.current_key() == "agents"

    window.tabs.save_layout()
    assert isolated_settings.layout == ["world", "agents"]

    restored = MainWindow(isolated_settings)
    try:
        assert restored.tabs.keys() == ["world", "agents"]
    finally:
        restored.deleteLater()


@pytest.mark.ui
def test_autosave_writes_separate_file(window: MainWindow, tmp_path: Path) -> None:
    target = tmp_path / "project.json"
    window._save_to(target)
    window.store.set_value("world.seed", 11)

    window.autosave()

    autosave = target.with_name(target.name + ".autosave")
    assert autosave.exists()
    assert load_project(target).document.world.seed == 42
    assert load_project(autosave).document.world.seed == 11


@pytest.mark.ui
def test_recovery_restores_autosaved_changes(window: MainWindow, tmp_path: Path) -> None:
    target = tmp_path / "project.json"
    window._save_to(target)
    window.store.set_value("world.seed", 99)
    window.autosave()

    assert window.session.has_recovery()
    recovered = window.session.recover()

    assert recovered is not None
    assert recovered.world.seed == 99


@pytest.mark.ui
def test_window_starts_fast(qtbot: object, isolated_settings: ApplicationSettings) -> None:
    started = time.perf_counter()
    created = MainWindow(isolated_settings)
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0


@pytest.mark.ui
def test_second_window_opens_read_only(
    window: MainWindow,
    isolated_settings: ApplicationSettings,
    project_path: Path,
    qtbot: object,
) -> None:
    assert window.open_project(project_path, confirm=False)

    other = MainWindow(isolated_settings)
    other.confirmations_enabled = False
    qtbot.addWidget(other)  # type: ignore[attr-defined]
    assert other.open_project(project_path, confirm=False)

    assert other.session.read_only is True
    assert other.inspector.read_only is True
    assert other.action_save.isEnabled() is False
    assert "только чтение" in other.windowTitle()
    assert other.inspector.commit("world.seed", 5) is False


@pytest.mark.ui
def test_close_releases_lock(window: MainWindow, project_path: Path) -> None:
    assert window.open_project(project_path, confirm=False)
    lock = lock_path_for(project_path)
    assert lock.exists()

    window.close()

    assert not lock.exists()


@pytest.mark.ui
def test_kill_recovery_restores_autosave(
    window: MainWindow,
    isolated_settings: ApplicationSettings,
    project_path: Path,
    qtbot: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert window.open_project(project_path, confirm=False)
    window.store.set_value("world.seed", 99)
    window.autosave()
    # Имитация kill: блокировка отпускается, автосохранение остаётся,
    # а на диске остаётся устаревшая блокировка завершившегося процесса.
    window.session.close()
    lock_path_for(project_path).write_text(
        json.dumps({"pid": 0, "host": socket.gethostname(), "user": "", "created_at": ""}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    restored = MainWindow(isolated_settings)
    restored.confirmations_enabled = False
    qtbot.addWidget(restored)  # type: ignore[attr-defined]

    assert restored.open_project(project_path, confirm=False)
    assert restored.store.document.world.seed == 99
    assert not autosave_path_for(project_path).exists()


@pytest.mark.ui
def test_close_asks_confirmation_for_unsaved_changes(
    window: MainWindow,
    isolated_settings: ApplicationSettings,
    qtbot: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window.store.set_value("world.seed", 5)
    asked: list[str] = []

    def fake_question(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]) if len(args) > 2 else "")
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    window.confirmations_enabled = True

    assert window.close() is False
    assert asked
    assert window.isVisible() is False or window.store.is_modified is True


@pytest.mark.ui
def test_new_project_resets_history(window: MainWindow) -> None:
    window.store.set_value("world.seed", 5)
    assert window.store.can_undo()

    assert window.new_project(confirm=False)
    assert window.store.can_undo() is False
