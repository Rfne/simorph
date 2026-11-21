"""Тесты вкладки «Миссия»: три панели и компактный режим (T6-03, T6-04)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from simorph.core.factory import new_project
from simorph.core.issues import IssueCode
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.project import ProjectDocument
from simorph.settings import ApplicationSettings
from simorph.ui.main_window import MISSION_TITLE, MainWindow
from simorph.ui.mission_view import MissionView


def _two_missions() -> ExerciseSection:
    """Упражнение с двумя миссиями: вторая — короткая."""
    return ExerciseSection(
        schema_version="1.0",
        exercise_id="EX-01",
        seed=1,
        world_id="W-01",
        agents=["NRTC-01"],
        missions=[
            Mission(
                mission_id="M-01",
                agent_id="NRTC-01",
                elements=[
                    MissionElement(id="start", type="start", next=["wait"]),
                    MissionElement(id="wait", type="dwell", duration_s=10.0, next=["ret"]),
                    MissionElement(id="ret", type="return_to_base"),
                ],
            ),
            Mission(
                mission_id="M-02",
                agent_id="NRTC-01",
                elements=[
                    MissionElement(id="start", type="start", next=["ret"]),
                    MissionElement(id="ret", type="return_to_base", duration_s=3.0),
                ],
            ),
        ],
    )


def _document() -> ProjectDocument:
    document = new_project("mission", seed=1)
    return document.model_copy(update={"exercises": [_two_missions()]})


@pytest.fixture
def window(qtbot: object, isolated_settings: ApplicationSettings) -> MainWindow:
    created = MainWindow(isolated_settings)
    created.confirmations_enabled = False
    created.store.apply(_document())
    qtbot.addWidget(created)
    return created


@pytest.fixture
def mission_view(window: MainWindow) -> Iterator[MissionView]:
    window.open_mission()
    view = window.mission_view
    assert view is not None
    yield view
    view.dispose()


@pytest.mark.ui
def test_mission_tab_opens(window: MainWindow) -> None:
    index = window.open_mission()
    view = window.mission_view

    assert index >= 0
    assert view is not None
    assert window.tabs.tabText(index) == MISSION_TITLE
    assert window.action_mission.shortcut().toString() == "F8"
    assert view.mission is not None
    assert view.mission.mission_id == "M-01"
    assert view.graph.valid is True
    assert view.budget.total_s == pytest.approx(10.0)


@pytest.mark.ui
def test_mission_tab_reuses_existing(window: MainWindow) -> None:
    assert window.open_mission() == window.open_mission()


@pytest.mark.ui
def test_graph_shows_layers(mission_view: MissionView) -> None:
    rows = mission_view.graph_rows()

    assert [name for _layer, name in rows] == ["start", "wait", "ret"]
    assert [layer for layer, _name in rows] == [0, 1, 2]


@pytest.mark.ui
def test_timeline_matches_budget(mission_view: MissionView) -> None:
    rows = mission_view.timeline_rows()

    assert rows == [("start", 0.0, 0.0), ("wait", 0.0, 10.0), ("ret", 10.0, 10.0)]
    assert mission_view.budget.critical_path == ["start", "wait", "ret"]


@pytest.mark.ui
def test_selection_syncs_element_panel(mission_view: MissionView) -> None:
    assert mission_view.select_element("wait") is True

    assert mission_view.selected_id == "wait"
    assert "Зависание" in mission_view.element_title
    assert mission_view.field_text("duration_s") == "10"


@pytest.mark.ui
def test_selection_of_unknown_element_is_rejected(mission_view: MissionView) -> None:
    assert mission_view.select_element("ghost") is False
    assert mission_view.selected_id != "ghost"


@pytest.mark.ui
def test_edit_duration_updates_budget_and_timeline(mission_view: MissionView) -> None:
    mission_view.select_element("wait")

    assert mission_view.submit_field("duration_s", "25") is True

    assert mission_view.budget.total_s == pytest.approx(25.0)
    assert mission_view.timeline_rows()[1] == ("wait", 0.0, 25.0)
    assert "бюджет 25.0" in mission_view.summary_text


@pytest.mark.ui
def test_negative_duration_is_rejected(mission_view: MissionView) -> None:
    mission_view.select_element("wait")

    assert mission_view.submit_field("duration_s", "-5") is False
    assert "ошибка" in mission_view.status_text.lower() or "0" in mission_view.status_text
    assert mission_view.mission is not None
    wait = next(item for item in mission_view.mission.elements if item.id == "wait")
    assert wait.duration_s == pytest.approx(10.0)


@pytest.mark.ui
def test_invalid_transition_creates_graph_issue(mission_view: MissionView) -> None:
    mission_view.select_element("ret")

    assert mission_view.submit_field("next", "start") is True

    assert mission_view.graph.illegal_cycles
    assert any(item.code is IssueCode.INVALID_GRAPH for item in mission_view.issues)
    assert "ошибок 3" in mission_view.summary_text


@pytest.mark.ui
def test_store_edit_refreshes_view(window: MainWindow, mission_view: MissionView) -> None:
    window.store.set_value("exercises.0.missions.0.elements.1.duration_s", 7.0)

    assert mission_view.budget.total_s == pytest.approx(7.0)
    assert mission_view.timeline_rows()[1] == ("wait", 0.0, 7.0)


@pytest.mark.ui
def test_mission_switch_keeps_panels_in_sync(mission_view: MissionView) -> None:
    assert mission_view.set_selection(0, 1) is True

    assert mission_view.mission is not None
    assert mission_view.mission.mission_id == "M-02"
    assert mission_view.budget.total_s == pytest.approx(3.0)
    assert [name for _layer, name in mission_view.graph_rows()] == ["start", "ret"]


@pytest.mark.ui
def test_compact_mode_hides_graph_and_keeps_selection(mission_view: MissionView) -> None:
    mission_view.select_element("wait")
    assert mission_view.graph_panel_visible is True

    mission_view.set_compact(True)

    assert mission_view.compact is True
    assert mission_view.graph_panel_visible is False
    assert mission_view.selected_id == "wait"
    assert mission_view.element_title.startswith("Зависание")

    mission_view.set_compact(False)

    assert mission_view.compact is False
    assert mission_view.graph_panel_visible is True
    assert mission_view.selected_id == "wait"
    assert mission_view.mission_index == 0


@pytest.mark.ui
def test_add_and_remove_element(mission_view: MissionView) -> None:
    before = len(mission_view.mission.elements) if mission_view.mission else 0

    assert mission_view.add_element("dwell") is True
    assert mission_view.mission is not None
    assert len(mission_view.mission.elements) == before + 1
    assert mission_view.selected_id == "dwell-1"
    assert mission_view.budget.total_s == pytest.approx(10.0)
    assert mission_view.budget.parallel_width == 2

    assert mission_view.remove_element("dwell-1") is True
    assert len(mission_view.mission.elements) == before
    assert all("dwell-1" not in item.next for item in mission_view.mission.elements)


@pytest.mark.ui
def test_export_and_read_only_import(mission_view: MissionView, tmp_path: Path) -> None:
    target = tmp_path / "mission.yaml"

    assert mission_view.save_export(target) is True
    assert target.exists()
    before = target.read_bytes()

    assert mission_view.import_preview(target) is True

    assert target.read_bytes() == before
    assert "только чтение" in mission_view.status_text


@pytest.mark.ui
def test_import_of_broken_file_reports_error(mission_view: MissionView, tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{", encoding="utf-8")

    assert mission_view.import_preview(target) is False
    assert "не выполнен" in mission_view.status_text


@pytest.mark.ui
def test_closing_tab_disposes_view(window: MainWindow) -> None:
    window.open_mission()
    view = window.mission_view
    assert view is not None

    assert window.tabs.close_editor("mission") is True
    assert window.mission_view is None
