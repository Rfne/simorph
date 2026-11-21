"""Тесты вкладки «Агент» (T5-06, G5-05, G5-07)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from simorph.core.factory import new_project
from simorph.settings import ApplicationSettings
from simorph.ui.agent_view import AgentView
from simorph.ui.main_window import AGENT_TITLE, MainWindow


@pytest.fixture
def window(qtbot: object, isolated_settings: ApplicationSettings) -> MainWindow:
    created = MainWindow(isolated_settings)
    created.confirmations_enabled = False
    qtbot.addWidget(created)
    return created


@pytest.fixture
def agent_view(window: MainWindow) -> Iterator[AgentView]:
    window.open_agent()
    view = window.agent_view
    assert view is not None
    yield view
    view.dispose()


@pytest.mark.ui
def test_agent_tab_opens_and_builds(window: MainWindow) -> None:
    index = window.open_agent()
    view = window.agent_view

    assert index >= 0
    assert view is not None
    assert window.tabs.tabText(index) == AGENT_TITLE
    assert view.agent is not None
    assert view.preview is not None
    assert view.preview.stats.status == "ready"
    assert "агент" in view.status_text
    assert window.action_agent.shortcut().toString() == "F7"


@pytest.mark.ui
def test_agent_tab_reuses_existing(window: MainWindow) -> None:
    first = window.open_agent()
    second = window.open_agent()

    assert first == second


@pytest.mark.ui
def test_agent_renders_frame(agent_view: AgentView) -> None:
    image = agent_view.render_once(64, 48)

    assert image.width() == 64
    assert image.height() == 48
    assert not image.isNull()


@pytest.mark.ui
def test_zones_toggle_changes_preview(agent_view: AgentView) -> None:
    preview = agent_view.preview
    assert preview is not None
    before = preview.stats.zones

    agent_view.set_zones(False)

    assert preview.zones_enabled is False
    assert before > 0
    assert preview.stats.zones == 0


@pytest.mark.ui
def test_export_writes_urdf(agent_view: AgentView, tmp_path: Path) -> None:
    target = tmp_path / "agent.urdf"

    assert agent_view.save_export(target) is True
    assert target.exists()
    assert "экспорт" in agent_view.status_text


@pytest.mark.ui
def test_reset_view_restores_camera(agent_view: AgentView) -> None:
    agent_view.camera.orbit(40.0, 10.0)
    agent_view.camera.zoom(2.0)

    agent_view.reset_view()

    assert agent_view.camera.yaw_deg == pytest.approx(35.0)
    assert agent_view.camera.pitch_deg == pytest.approx(-25.0)


@pytest.mark.ui
def test_rebuild_picks_up_document_changes(window: MainWindow) -> None:
    window.open_agent()
    view = window.agent_view
    assert view is not None
    empty = window.store.document.model_copy(update={"agents": []})

    view.rebuild(empty)
    view._rebuild_now()  # проверяем отложенную пересборку без таймера

    assert view.agent is None
    assert view.preview is None
    assert "нет агентов" in view.status_text


@pytest.mark.ui
def test_empty_project_view(isolated_settings: ApplicationSettings, qtbot: object) -> None:
    document = new_project("empty").model_copy(update={"agents": []})
    view = AgentView(document, isolated_settings)
    qtbot.addWidget(view)

    assert view.agent is None
    assert view.render_once(32, 24).isNull()
    assert view.save_export(Path("unused.urdf")) is False

    view.dispose()


@pytest.mark.ui
def test_dispose_stops_timers(agent_view: AgentView) -> None:
    agent_view.dispose()
    agent_view.tick()

    assert agent_view.frame_ms == 0.0
