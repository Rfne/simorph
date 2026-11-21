"""Тесты вкладок редакторов (T2-04, G2-03)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget

from simorph.settings import ApplicationSettings
from simorph.ui.tabs import EditorTabs


@pytest.fixture
def tabs(qtbot: object, isolated_settings: ApplicationSettings) -> EditorTabs:
    created = EditorTabs(isolated_settings)
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    return created


def _widget() -> QWidget:
    return QWidget()


@pytest.mark.ui
def test_open_editor_activates_existing_tab(tabs: EditorTabs) -> None:
    assert tabs.open_editor("world", "Мир", _widget()) == 0
    assert tabs.open_editor("agents", "Агенты", _widget()) == 1

    assert tabs.open_editor("world", "Мир", _widget()) == 0
    assert tabs.keys() == ["world", "agents"]
    assert tabs.current_key() == "world"


@pytest.mark.ui
def test_close_editor_removes_tab_and_emits(tabs: EditorTabs) -> None:
    tabs.open_editor("world", "Мир", _widget())
    closed: list[str] = []
    tabs.editor_closed.connect(closed.append)

    assert tabs.close_editor("world", confirm=False) is True
    assert tabs.count() == 0
    assert closed == ["world"]


@pytest.mark.ui
def test_close_asks_before_discarding_changes(
    tabs: EditorTabs, monkeypatch: pytest.MonkeyPatch
) -> None:
    tabs.open_editor("world", "Мир", _widget())
    tabs.set_dirty_checker(lambda key: key == "world")

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    assert tabs.close_editor("world") is False
    assert tabs.count() == 1

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    assert tabs.close_editor("world") is True
    assert tabs.count() == 0


@pytest.mark.ui
def test_reindex_keeps_keys_after_middle_tab_closed(tabs: EditorTabs) -> None:
    tabs.open_editor("world", "Мир", _widget())
    tabs.open_editor("agents", "Агенты", _widget())
    tabs.open_editor("exercises", "Упражнения", _widget())

    assert tabs.close_editor("agents", confirm=False)

    assert tabs.keys() == ["world", "exercises"]
    assert tabs.index_of("exercises") == 1


@pytest.mark.ui
def test_layout_is_saved_and_restored(
    tabs: EditorTabs, isolated_settings: ApplicationSettings
) -> None:
    tabs.open_editor("world", "Мир", _widget())
    tabs.open_editor("agents", "Агенты", _widget())
    tabs.save_layout()

    assert isolated_settings.layout == ["world", "agents"]

    isolated_settings.layout = ["world", "unknown", "agents"]
    restored = EditorTabs(isolated_settings)
    try:
        factories = {
            "world": lambda: ("Мир", _widget()),
            "agents": lambda: ("Агенты", _widget()),
        }
        assert restored.restore_layout(factories) == 2
        assert restored.keys() == ["world", "agents"]
    finally:
        restored.deleteLater()
