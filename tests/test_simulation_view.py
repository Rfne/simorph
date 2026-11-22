"""Тесты вкладки «Симуляция»: прогон, пауза, метрики и события (T7-05, G7-01)."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from simorph.core.factory import new_project
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.project import ProjectDocument
from simorph.settings import ApplicationSettings
from simorph.ui.main_window import SIMULATION_TITLE, MainWindow
from simorph.ui.simulation_view import SimulationView


def _exercise() -> ExerciseSection:
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
                    MissionElement(id="wait", type="dwell", duration_s=1.0, next=["ret"]),
                    MissionElement(id="ret", type="return_to_base"),
                ],
            )
        ],
    )


def _document() -> ProjectDocument:
    document = new_project("simulation", seed=1)
    return document.model_copy(update={"exercises": [_exercise()]})


@pytest.fixture
def window(qtbot: object, isolated_settings: ApplicationSettings) -> MainWindow:
    created = MainWindow(isolated_settings)
    created.confirmations_enabled = False
    created.store.apply(_document())
    qtbot.addWidget(created)
    return created


@pytest.fixture
def view(window: MainWindow) -> Iterator[SimulationView]:
    window.open_simulation()
    created = window.simulation_view
    assert created is not None
    yield created
    created.dispose()


def test_tab_opens_with_f9(window: MainWindow) -> None:
    index = window.open_simulation()
    created = window.simulation_view

    assert created is not None
    assert window.tabs.widget(index) is created
    assert window.tabs.tabText(index) == SIMULATION_TITLE


def test_view_starts_on_first_mission(view: SimulationView) -> None:
    assert view.exercise_index == 0
    assert view.mission_index == 0
    assert view.mission is not None
    assert view.mission.mission_id == "M-01"
    assert view.state_text == "прогон не запущен"


def test_run_finishes_and_fills_metrics(view: SimulationView) -> None:
    assert view.start_run(0.2) is True
    assert view.wait(5.0) is True

    report = view.report
    assert report is not None
    assert report.ok is True
    assert "finished" in view.state_text
    assert "кадры:" in view.frames_text
    assert view.interval_rows()
    stages = view.stage_rows()
    assert stages and stages[0][0] == "sensor"
    assert view.event_rows()


def test_stop_interrupts_run(view: SimulationView) -> None:
    assert view.start_run(5.0) is True
    view.stop_run()
    assert view.wait(5.0) is True

    report = view.report
    assert report is not None
    assert report.state == "interrupted"
    assert "interrupted" in view.state_text


def test_pause_and_step(view: SimulationView) -> None:
    assert view.start_run(5.0) is True
    runner = view.runner
    assert runner is not None
    deadline = time.monotonic() + 2.0
    while runner.clock.ticks < 1 and time.monotonic() < deadline:
        time.sleep(0.002)
    assert view.toggle_pause() is True
    ticks = runner.clock.ticks
    assert view.step_once() is True
    assert runner.clock.ticks == ticks + 1
    assert view.toggle_pause() is True
    view.stop_run()
    assert view.wait(5.0) is True


def test_rebuild_keeps_selection(view: SimulationView) -> None:
    assert view.set_selection(0, 0) is True
    view.rebuild(_document())

    assert view.mission_index == 0
    assert view.mission is not None
    assert view.set_selection(0, 1) is False


def test_start_without_mission_reports_error(
    window: MainWindow, isolated_settings: ApplicationSettings
) -> None:
    document = _document().model_copy(
        update={"exercises": [_exercise().model_copy(update={"missions": []})]}
    )
    window.store.apply(document)
    window.open_simulation()
    created = window.simulation_view
    assert created is not None

    assert created.start_run(0.1) is False
    assert "нет миссий" in created.status_text
