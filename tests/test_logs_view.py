"""Тесты вкладки «Журналы»: прогоны, фильтры, ряды и экспорт (T9-03, G9-03)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from simorph.core.logs import (
    EventRow,
    MetricRow,
    RunRecord,
    default_log_path,
    open_store,
)
from simorph.settings import ApplicationSettings
from simorph.ui.logs_view import RUN_COLUMNS, LogsView
from simorph.ui.main_window import LOGS_TITLE, MainWindow


def _seed(database: Path) -> None:
    with open_store(database) as store:
        store.start_run(
            RunRecord(
                run_id="sim-1",
                kind="simulation",
                status="finished",
                started_at="2026-11-01T10:00:00Z",
                finished_at="2026-11-01T10:01:00Z",
                mission_id="M-01",
            )
        )
        store.write_metrics(
            "sim-1",
            [
                MetricRow("k", 0.75, 1_000, "ratio"),
                MetricRow("stage.sensor.plan_ms", 8.0, 2_000, "ms"),
            ],
        )
        store.write_events("sim-1", [EventRow(1_000, "simulation", "run_started")])
        store.start_run(
            RunRecord(
                run_id="pipe-1",
                kind="pipeline",
                status="finished",
                started_at="2026-11-02T10:00:00Z",
                finished_at="2026-11-02T10:01:00Z",
                pipeline_id="PIPE-CMP-01",
            )
        )
        store.write_metrics("pipe-1", [MetricRow("node.src.p95_ms", 0.3, 1_000, "ms")])


@pytest.fixture
def window(qtbot: object, isolated_settings: ApplicationSettings) -> MainWindow:
    created = MainWindow(isolated_settings)
    created.confirmations_enabled = False
    qtbot.addWidget(created)
    return created


@pytest.fixture
def view(window: MainWindow) -> Iterator[LogsView]:
    window.open_logs()
    created = window.logs_view
    assert created is not None
    yield created
    created.dispose()


@pytest.mark.ui
def test_tab_opens_from_action(window: MainWindow) -> None:
    assert window.action_logs.shortcut().toString() == "F11"

    index = window.open_logs()
    created = window.logs_view

    assert created is not None
    assert window.tabs.widget(index) is created
    assert window.tabs.tabText(index) == LOGS_TITLE


@pytest.mark.ui
def test_missing_database_is_reported(view: LogsView, tmp_path: Path) -> None:
    assert view.set_database(tmp_path / "absent.sqlite") is False

    assert view.store is None
    assert view.database is None
    assert "первого прогона" in view.status_text
    assert view.runs == []


@pytest.mark.ui
def test_runs_and_rows_are_shown(view: LogsView, tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite"
    _seed(database)

    assert view.set_database(database) is True
    assert view.run_ids() == ["pipe-1", "sim-1"]
    assert view.summary_text == "прогонов 2"
    assert len(view.runs) == 2
    assert view.selected_run_id == "pipe-1"
    assert view.row_count() == 1
    assert view.row_values(0)[1] == "node.src.p95_ms"


@pytest.mark.ui
def test_kind_and_status_filters(view: LogsView, tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite"
    _seed(database)
    view.set_database(database)

    assert view.set_kind_filter("simulation") == 1
    assert view.run_ids() == ["sim-1"]

    assert view.set_kind_filter(None) == 2
    assert view.set_status_filter("finished") == 2
    assert view.run_ids() == ["pipe-1", "sim-1"]


@pytest.mark.ui
def test_text_filter_hides_foreign_runs(view: LogsView, tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite"
    _seed(database)
    view.set_database(database)

    view.set_search("sim-1")

    assert "фильтр: sim-1" in view.summary_text


@pytest.mark.ui
def test_metric_filter_and_events_switch(view: LogsView, tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite"
    _seed(database)
    view.set_database(database)
    assert view.select_run("sim-1") is True

    view.set_metric("stage.")
    assert view.refresh_rows() == 1
    assert view.row_values(0)[1] == "stage.sensor.plan_ms"

    assert view.set_table("events") == 1
    assert view.table_name() == "events"
    assert view.row_count() == 1
    assert view.row_values(0)[1] == "run_started"


@pytest.mark.ui
def test_interval_filter_limits_rows(view: LogsView, tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite"
    _seed(database)
    view.set_database(database)
    view.select_run("sim-1")

    assert view.set_interval(1500, None) == 1
    assert view.row_values(0)[0] == "2000"

    assert view.set_interval(1500, 1500) == 0


@pytest.mark.ui
def test_export_selected_rows(view: LogsView, tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite"
    _seed(database)
    view.set_database(database)
    target = tmp_path / "rows.csv"

    assert view.export_to(target) is True
    assert target.exists()
    assert "выгружено 1 строк" in view.status_text


@pytest.mark.ui
def test_async_rows_arrive(view: LogsView, tmp_path: Path) -> None:
    database = tmp_path / "run.sqlite"
    _seed(database)
    view.set_database(database)
    view.select_run("sim-1")
    view.set_metric("k")
    assert view.refresh_rows() == 1

    view.set_metric("")
    view.refresh_rows_async()

    assert view.wait_for_reads(20_000) is True
    assert view.row_count() == 2


@pytest.mark.ui
def test_project_path_uses_run_sqlite(view: LogsView, tmp_path: Path) -> None:
    database = default_log_path(tmp_path / "project.json")
    _seed(database)

    view.set_project_path(tmp_path / "project.json")

    assert view.database == database
    assert view.run_ids() == ["pipe-1", "sim-1"]
    assert view.row_count() >= 1
    assert len(RUN_COLUMNS) == 6
