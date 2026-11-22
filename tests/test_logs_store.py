"""Тесты журнала прогонов: схема, запись батчами, чтение и восстановление (T9-01…T9-05)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from simorph.core.logs import (
    AnnotationRow,
    ArtifactRow,
    EventRow,
    MetricRow,
    RunBundle,
    RunRecord,
    RunStore,
    open_store,
    parse_moment,
)

pytestmark = pytest.mark.integration

#: Объём ряда для проверки записи «10^5 без потерь» (G9-02).
BULK_ROWS = 100_000


def _run(run_id: str = "run-1", **overrides: object) -> RunRecord:
    payload: dict[str, object] = {"run_id": run_id, "kind": "simulation"}
    payload.update(overrides)
    return RunRecord(**payload)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    """Пустой журнал во временном каталоге."""
    with RunStore.open(tmp_path / "run.sqlite") as opened:
        yield opened


def test_schema_tables_and_pragmas(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    with RunStore.open(path) as store:
        tables = set(store.tables())
        stats = store.stats()

    assert {"runs", "run_events", "metrics", "artifacts", "annotations", "settings"} <= tables
    assert stats["schema_version"] == 1
    assert stats["journal_mode"].lower() == "wal"
    assert stats["foreign_keys"] == 1
    assert stats["counts"]["runs"] == 0


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    with RunStore.open(path) as first:
        first.start_run(_run("keep"))
    with RunStore.open(path) as second:
        second.start_run(_run("other"))
        run_ids = [item["run_id"] for item in second.list_runs()]
        version = second.schema_version

    assert set(run_ids) == {"keep", "other"}
    assert version == 1


def test_record_writes_four_tables(store: RunStore) -> None:
    bundle = RunBundle(
        run=_run("full", status="finished", mission_id="M-01", seed=42),
        events=[EventRow(t_ns=1_000, source="simulation", type="frame_pushed")],
        metrics=[MetricRow("k", 1.0, 1_000, "ratio", {"interval": 0})],
        artifacts=[ArtifactRow(kind="stream", path="frames.bin", hash="sha256:abc")],
        annotations=[AnnotationRow(frame_id=1, class_id="person", bbox=(0, 0, 1, 1))],
    )

    written = store.record(bundle)
    counts = store.counts("full")

    assert written == {"events": 1, "metrics": 1, "artifacts": 1, "annotations": 1}
    assert counts["run_events"] == 1
    assert counts["metrics"] == 1
    assert counts["artifacts"] == 1
    assert counts["annotations"] == 1
    assert store.get_run("full")["seed"] == 42
    assert store.metrics("full")[0]["tags"] == {"interval": 0}
    assert store.annotations("full")[0]["bbox"] == [0.0, 0.0, 1.0, 1.0]


def test_artifact_duplicates_are_ignored(store: RunStore) -> None:
    store.start_run(_run("dup"))
    artifact = ArtifactRow(kind="report", path="report.json", hash="sha256:x")

    assert store.add_artifacts("dup", [artifact, artifact]) == 1
    assert len(store.artifacts("dup")) == 1


def test_write_bulk_metrics_without_loss(store: RunStore) -> None:
    """10^5 рядов батчами: все строки на месте (G9-02)."""
    store.start_run(_run("bulk"))
    rows = [
        MetricRow("node.src.p95_ms", float(index % 17), index, "ms") for index in range(BULK_ROWS)
    ]

    started = time.perf_counter()
    written = store.write_metrics("bulk", rows, batch_size=5000)
    elapsed = time.perf_counter() - started

    assert written == BULK_ROWS
    assert store.counts("bulk")["metrics"] == BULK_ROWS
    assert elapsed < 30.0
    assert store.integrity_check() == "ok"


def test_filtered_query_of_bulk_is_fast(store: RunStore) -> None:
    """Выборка из 10^5 по имени и интервалу — меньше секунды (G9-03)."""
    store.start_run(_run("fast"))
    rows = [
        MetricRow("noise" if index % 2 else "signal", float(index), index, "ms")
        for index in range(BULK_ROWS)
    ]
    store.write_metrics("fast", rows, batch_size=10_000)

    started = time.perf_counter()
    selected = store.metrics("fast", name="signal", since_ns=50_000, until_ns=60_000, limit=1000)
    elapsed = time.perf_counter() - started

    assert selected
    assert all(item["name"] == "signal" for item in selected)
    assert all(50_000 <= item["t_ns"] <= 60_000 for item in selected)
    assert elapsed < 1.0


def test_metric_filters_and_names(store: RunStore) -> None:
    store.start_run(_run("filters"))
    store.write_metrics(
        "filters",
        [
            MetricRow("stage.sensor.plan_ms", 8.0, 10, "ms"),
            MetricRow("stage.sensor.actual_ms", 9.0, 20, "ms"),
            MetricRow("queue_depth", 3.0, 30, "frames"),
        ],
    )

    assert store.metric_names("filters") == [
        "queue_depth",
        "stage.sensor.actual_ms",
        "stage.sensor.plan_ms",
    ]
    assert len(store.metrics("filters", prefix="stage.sensor.")) == 2
    assert [item["name"] for item in store.metrics("filters", name="queue_depth")] == [
        "queue_depth"
    ]
    assert [item["t_ns"] for item in store.metrics("filters", since_ns=15, until_ns=25)] == [20]


def test_event_filters(store: RunStore) -> None:
    store.start_run(_run("events"))
    store.write_events(
        "events",
        [
            EventRow(1, "simulation", "run_started"),
            EventRow(2, "pipeline", "frame_done", {"frame_id": 1}),
            EventRow(3, "pipeline", "frame_dropped"),
        ],
    )

    assert len(store.events("events", event_type="frame_dropped")) == 1
    assert len(store.events("events", source="pipeline")) == 2
    assert len(store.events("events", since_ns=2, until_ns=3)) == 2
    assert store.events("events")[1]["payload"] == {"frame_id": 1}


def test_list_runs_and_count_filters(store: RunStore) -> None:
    store.start_run(_run("a", started_at="2026-11-01T10:00:00Z"))
    store.start_run(_run("b", started_at="2026-11-02T10:00:00Z", pipeline_id="P-1"))
    store.start_run(
        _run("c", kind="pipeline", started_at="2026-11-03T10:00:00Z", pipeline_id="P-1")
    )
    store.finish_run("a")

    assert [item["run_id"] for item in store.list_runs()] == ["c", "b", "a"]
    assert [item["run_id"] for item in store.list_runs(kind="pipeline")] == ["c"]
    assert [item["run_id"] for item in store.list_runs(status="finished")] == ["a"]
    assert [item["run_id"] for item in store.list_runs(pipeline_id="P-1")] == ["c", "b"]
    assert store.count_runs() == 3
    assert store.count_runs(kind="pipeline") == 1
    assert store.count_runs(status="running") == 2
    assert len(store.list_runs(limit=1, offset=1)) == 1


def test_recover_marks_running_as_interrupted(tmp_path: Path) -> None:
    """После падения процесса прогон не остаётся «вечно идущим»."""
    path = tmp_path / "run.sqlite"
    crashed = RunStore.open(path)
    crashed.start_run(_run("crashed"))
    crashed.start_run(_run("done", status="finished"))
    crashed.close()

    with open_store(path) as store:
        recovered = store.get_run("crashed")
        finished = store.get_run("done")

    assert recovered is not None
    assert recovered["status"] == "interrupted"
    assert "прерван" in str(recovered["reason"])
    assert finished is not None
    assert finished["status"] == "finished"


def test_prune_keep_last_and_dry_run(store: RunStore) -> None:
    for index in range(4):
        store.start_run(_run(f"r{index}", started_at=f"2026-11-0{index + 1}T10:00:00Z"))
        store.write_metrics(f"r{index}", [MetricRow("m", 1.0, 0)])

    planned = store.prune(keep_last=2, dry_run=True)
    assert planned["removed"] == 0
    assert set(planned["candidates"]) == {"r0", "r1"}
    assert store.count_runs() == 4

    removed = store.prune(keep_last=2)
    assert removed["removed"] == 2
    assert [item["run_id"] for item in store.list_runs()] == ["r3", "r2"]
    assert store.counts()["metrics"] == 2


def test_prune_before_moment(store: RunStore) -> None:
    store.start_run(_run("old", started_at="2026-10-01T00:00:00Z"))
    store.start_run(_run("new", started_at="2026-11-01T00:00:00Z"))

    removed = store.prune(before=parse_moment("2026-10-15"))

    assert removed["candidates"] == ["old"]
    assert [item["run_id"] for item in store.list_runs()] == ["new"]


def test_parse_moment_normalises_dates() -> None:
    assert parse_moment(None) is None
    assert parse_moment("  ") is None
    assert parse_moment("2026-11-01") == "2026-11-01T00:00:00Z"
    assert parse_moment("2026-11-01T10:00:00Z") == "2026-11-01T10:00:00Z"


def test_foreign_key_rejects_orphan_event(store: RunStore) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.write_events("missing", [EventRow(0, "simulation", "orphan")])
    assert store.counts()["run_events"] == 0
