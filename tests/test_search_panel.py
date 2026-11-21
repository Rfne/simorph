"""Тесты поиска по проекту (T2-08, G2-06)."""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt

from simorph.core.factory import build_agent, new_project
from simorph.core.models.project import ProjectDocument
from simorph.settings import ApplicationSettings
from simorph.ui.search_panel import SearchPanel, search_document


@pytest.fixture
def panel(qtbot: object, isolated_settings: ApplicationSettings) -> SearchPanel:
    created = SearchPanel(isolated_settings)
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    return created


@pytest.mark.ui
def test_search_finds_agent_by_id(project_document: ProjectDocument) -> None:
    hits = search_document(project_document, "NRTC-01")

    # Агент и миссия ссылаются на один идентификатор, поэтому находятся оба.
    assert [hit.path for hit in hits] == ["agents.0", "exercises.0.missions.0"]
    assert hits[0].title == "NRTC-01"


@pytest.mark.ui
def test_search_finds_nested_sensor(project_document: ProjectDocument) -> None:
    hits = search_document(project_document, "cam_stereo")

    assert [hit.path for hit in hits] == ["agents.0.sensors.0"]


@pytest.mark.ui
def test_search_finds_pipeline(project_document: ProjectDocument) -> None:
    hits = search_document(project_document, "PIPE-CMP-01")

    assert [hit.path for hit in hits] == ["pipelines.0"]


@pytest.mark.ui
def test_search_is_case_insensitive(project_document: ProjectDocument) -> None:
    hits = search_document(project_document, "nrtc-01")

    assert [hit.path for hit in hits] == ["agents.0", "exercises.0.missions.0"]


@pytest.mark.ui
def test_empty_query_returns_nothing(project_document: ProjectDocument) -> None:
    assert search_document(project_document, "   ") == []


@pytest.mark.ui
def test_panel_shows_results(panel: SearchPanel, project_document: ProjectDocument) -> None:
    panel.set_document(project_document)

    panel._query.setText("cam_stereo")

    assert [hit.path for hit in panel.hits()] == ["agents.0.sensors.0"]
    assert panel._results.count() == 1
    assert "Найдено: 1" in panel._summary.text()


@pytest.mark.ui
def test_submit_remembers_query(
    panel: SearchPanel,
    project_document: ProjectDocument,
    isolated_settings: ApplicationSettings,
    qtbot: object,
) -> None:
    panel.set_document(project_document)
    panel._query.setText("NRTC-01")

    qtbot.keyClick(panel._query, Qt.Key.Key_Return)  # type: ignore[attr-defined]

    assert isolated_settings.search_history == ["NRTC-01"]
    assert panel._history.count() == 2


@pytest.mark.ui
def test_history_selection_runs_search(
    panel: SearchPanel,
    project_document: ProjectDocument,
    isolated_settings: ApplicationSettings,
) -> None:
    isolated_settings.add_search_query("NRTC-01")
    panel._reload_history()
    panel.set_document(project_document)
    panel._history.setCurrentIndex(1)

    assert panel._query.text() == "NRTC-01"
    assert [hit.path for hit in panel.hits()] == ["agents.0", "exercises.0.missions.0"]


@pytest.mark.ui
def test_activation_emits_path(panel: SearchPanel, project_document: ProjectDocument) -> None:
    panel.set_document(project_document)
    panel._query.setText("NRTC-01")
    received: list[str] = []
    panel.navigate_requested.connect(received.append)

    item = panel._results.item(0)
    panel._results.itemActivated.emit(item)

    assert received == ["agents.0"]


@pytest.mark.ui
def test_search_over_thousand_objects_is_fast() -> None:
    document = new_project("scale")
    agents = [build_agent().model_copy(update={"agent_id": f"A-{index}"}) for index in range(120)]
    document = document.model_copy(update={"agents": agents})

    started = time.perf_counter()
    hits = search_document(document, "A-42")
    elapsed = time.perf_counter() - started

    assert [hit.path for hit in hits] == ["agents.42"]
    assert elapsed < 0.2
