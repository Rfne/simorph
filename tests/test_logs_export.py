"""Экспорт журнала в CSV и Parquet: колонки и типы (T9-04, G9-04)."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from simorph.core.logs import (
    EXPORT_COLUMNS,
    EventRow,
    MetricRow,
    RunRecord,
    RunStore,
    export_rows,
    export_table,
    rows_for_table,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def filled_store(tmp_path: Path) -> Iterator[RunStore]:
    """Журнал с одним прогоном: две метрики и одно событие."""
    with RunStore.open(tmp_path / "run.sqlite") as store:
        store.start_run(
            RunRecord(run_id="exp", kind="simulation", seed=7, started_at="2026-11-01T10:00:00Z")
        )
        store.write_metrics(
            "exp",
            [
                MetricRow("k", 0.75, 1_000, "ratio"),
                MetricRow("stage.sensor.plan_ms", 8.0, 2_000, "ms"),
            ],
        )
        store.write_events("exp", [EventRow(1_000, "simulation", "run_started", {"seq": 0})])
        store.finish_run("exp")
        yield store


def test_rows_for_table_filters(filled_store: RunStore) -> None:
    metrics = rows_for_table(filled_store, "metrics", run_id="exp", prefix="stage.")
    events = rows_for_table(filled_store, "events", run_id="exp", name="run_started")
    runs = rows_for_table(filled_store, "runs")

    assert [item["name"] for item in metrics] == ["stage.sensor.plan_ms"]
    assert len(events) == 1
    assert runs[0]["run_id"] == "exp"


def test_export_metrics_csv(filled_store: RunStore, tmp_path: Path) -> None:
    target = tmp_path / "metrics.csv"

    result = export_table(filled_store, "metrics", target, run_id="exp")

    assert result.rows == 2
    assert result.format == "csv"
    assert result.path == str(target)
    assert result.bytes > 0
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == EXPORT_COLUMNS["metrics"]
    assert rows[1][2] == "k"
    assert rows[1][5] == "1000"


def test_export_metrics_parquet_keeps_types(filled_store: RunStore, tmp_path: Path) -> None:
    """Parquet читается pandas без приведения типов (G9-04)."""
    target = tmp_path / "metrics.parquet"

    result = export_table(filled_store, "metrics", target, run_id="exp")
    frame = pd.read_parquet(target)

    assert result.format == "parquet"
    assert list(frame.columns) == list(EXPORT_COLUMNS["metrics"])
    assert frame["id"].dtype == "int64"
    assert frame["t_ns"].dtype == "int64"
    assert frame["value"].dtype == "float64"
    assert pd.api.types.is_string_dtype(frame["name"])
    assert frame["t_ns"].tolist() == [1_000, 2_000]
    assert frame["value"].tolist() == [0.75, 8.0]


def test_export_events_parquet(filled_store: RunStore, tmp_path: Path) -> None:
    target = tmp_path / "events.parquet"

    export_table(filled_store, "events", target, run_id="exp")
    frame = pd.read_parquet(target)

    assert frame["t_ns"].dtype == "int64"
    assert frame["type"].tolist() == ["run_started"]
    assert frame["payload_json"].iloc[0].startswith("{")


def test_export_runs_parquet_seed_is_int(filled_store: RunStore, tmp_path: Path) -> None:
    target = tmp_path / "runs.parquet"

    export_table(filled_store, "runs", target)
    frame = pd.read_parquet(target)

    assert frame["seed"].dtype == "int64"
    assert frame["run_id"].tolist() == ["exp"]


def test_format_follows_extension(filled_store: RunStore, tmp_path: Path) -> None:
    rows = rows_for_table(filled_store, "metrics", run_id="exp")

    csv_result = export_rows("metrics", rows, tmp_path / "plain.csv")
    parquet_result = export_rows("metrics", rows, tmp_path / "plain.parquet")

    assert csv_result.format == "csv"
    assert parquet_result.format == "parquet"
    assert not list(tmp_path.glob("*.tmp"))


def test_unknown_table_rejected(filled_store: RunStore, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="неизвестная таблица"):
        export_table(filled_store, "unknown", tmp_path / "x.csv")


def test_export_creates_parent_directory(filled_store: RunStore, tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deep" / "metrics.csv"

    result = export_table(filled_store, "metrics", target, run_id="exp")

    assert result.rows == 2
    assert target.exists()
