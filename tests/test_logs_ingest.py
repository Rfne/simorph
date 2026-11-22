"""Импорт отчётов прогонов в журнал: симуляция и конвейер (T9-02, G9-01)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from simorph.core.logs import (
    ArtifactRow,
    default_log_path,
    open_store,
    pipeline_bundle,
    record_to_file,
    simulation_bundle,
)
from simorph.core.pipeline.metrics import NodeLatency, PipelineMetrics, PipelineRunReport

pytestmark = pytest.mark.integration


def _simulation_report() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        mission_id="M-01",
        reason="миссия завершена",
        config={"seed": 42, "scale": 1.0},
        events=[
            {
                "seq": 0,
                "t_scene_ns": 1_000,
                "t_monotonic_ns": 5,
                "kind": "run_started",
                "message": "старт",
                "data": {"mission": "M-01"},
            }
        ],
        metrics={
            "samples": [
                {
                    "index": 0,
                    "scene_time_s": 0.5,
                    "k": 0.9,
                    "wall_time_s": 0.01,
                    "stages": {"sensor": {"plan_ms": 8.0, "actual_ms": 9.0, "overrun_ms": 1.0}},
                    "queue_depth": 2,
                    "queue_wait_ms": 1.5,
                }
            ],
            "k": {"p50": 0.9, "p95": 0.95, "max": 1.0, "share_over_plan": 0.1},
            "frames": {"dropped": 0, "pushed": 6, "consumed": 6},
        },
        record=None,
        status=None,
    )


def _pipeline_report() -> PipelineRunReport:
    metrics = PipelineMetrics(
        pipeline_id="P-1",
        frames_attempted=6,
        frames_completed=6,
        frames_dropped=0,
        queue_max_depth=2,
        queue_wait_ms=1.0,
        critical_path_ms=0.3,
        nodes={"src": NodeLatency("src", "source.frame", 0.1, [0.1, 0.2, 0.3])},
    )
    return PipelineRunReport(
        pipeline_id="P-1",
        kind="computational",
        state="finished",
        reason=None,
        config={"seed": 7},
        metrics=metrics,
        events=[
            {
                "kind": "frame_done",
                "t_fsin_ns": 2_000,
                "t_monotonic_ns": 9,
                "frame_id": 1,
                "node": "src",
                "node_type": "source.frame",
                "latency_ms": 0.2,
                "cause": None,
            }
        ],
    )


def test_simulation_bundle_layout() -> None:
    bundle = simulation_bundle(
        _simulation_report(),
        run_id="sim-1",
        project_hash="abc",
        exercise_id="NRTC-01",
        app_version="0.11.0",
    )

    assert bundle.run.run_id == "sim-1"
    assert bundle.run.kind == "simulation"
    assert bundle.run.status == "finished"
    assert bundle.run.seed == 42
    assert bundle.run.project_hash == "abc"
    assert bundle.run.app_version == "0.11.0"

    names = {item.name for item in bundle.metrics}
    assert {"k", "interval.wall_time_ms", "queue_depth", "queue_wait_ms"} <= names
    assert {"stage.sensor.plan_ms", "stage.sensor.actual_ms", "stage.sensor.overrun_ms"} <= names
    assert {"k.p50", "k.p95", "k.max", "k.share_over_plan"} <= names
    assert {"frames.dropped", "frames.pushed", "frames.consumed"} <= names

    sensor_plan = next(item for item in bundle.metrics if item.name == "stage.sensor.plan_ms")
    assert sensor_plan.value == 8.0
    assert sensor_plan.unit == "ms"
    assert sensor_plan.t_ns == 500_000_000
    assert sensor_plan.tags["stage"] == "sensor"

    assert bundle.events[0].type == "run_started"
    assert bundle.events[0].t_ns == 1_000
    assert bundle.events[0].payload["message"] == "старт"


def test_simulation_bundle_marks_failed_run() -> None:
    report = _simulation_report()
    report.ok = False
    report.reason = "ошибка миссии"

    bundle = simulation_bundle(report, run_id="failed")

    assert bundle.run.status == "interrupted"
    assert bundle.run.reason == "ошибка миссии"


def test_pipeline_bundle_layout() -> None:
    bundle = pipeline_bundle(_pipeline_report(), run_id="pipe-1", app_version="0.11.0")

    assert bundle.run.kind == "pipeline"
    assert bundle.run.pipeline_id == "P-1"
    assert bundle.run.seed == 7
    assert bundle.run.status == "finished"

    names = {item.name for item in bundle.metrics}
    assert {
        "node.src.plan_ms",
        "node.src.mean_ms",
        "node.src.p50_ms",
        "node.src.p95_ms",
        "node.src.max_ms",
        "node.src.samples",
        "pipeline.critical_path_ms",
        "pipeline.total_work_ms",
        "frames.attempted",
        "frames.completed",
        "frames.dropped",
        "queue.max_depth",
        "queue.wait_ms",
    } <= names

    p95 = next(item for item in bundle.metrics if item.name == "node.src.p95_ms")
    assert p95.value == pytest.approx(0.3)
    assert p95.unit == "ms"
    assert p95.tags == {"node": "src", "type": "source.frame"}

    critical = next(item for item in bundle.metrics if item.name == "pipeline.critical_path_ms")
    assert critical.value == pytest.approx(0.3)
    assert critical.tags == {"summary": True}

    assert bundle.events[0].t_ns == 2_000
    assert bundle.events[0].payload["node"] == "src"


def test_default_log_path_sits_next_to_project(tmp_path: Path) -> None:
    project = tmp_path / "nested" / "project.json"

    assert default_log_path(project) == tmp_path / "nested" / "run.sqlite"


def test_record_to_file_writes_run(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"

    summary = record_to_file(
        path,
        _simulation_report(),
        kind="simulation",
        project_hash="hash",
        exercise_id="NRTC-01",
        app_version="0.11.0",
    )

    assert path.exists()
    assert summary["kind"] == "simulation"
    assert summary["counts"]["run_events"] == 1
    assert summary["counts"]["metrics"] > 0

    with open_store(path) as store:
        run = store.get_run(summary["run_id"])

    assert run is not None
    assert run["exercise_id"] == "NRTC-01"
    assert run["project_hash"] == "hash"


def test_record_to_file_pipeline(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"

    summary = record_to_file(path, _pipeline_report(), kind="pipeline", app_version="0.11.0")

    with open_store(path) as store:
        run = store.get_run(summary["run_id"])
        names = store.metric_names(summary["run_id"])

    assert run is not None
    assert run["kind"] == "pipeline"
    assert "node.src.p95_ms" in names


def test_artifact_from_path_hashes_file(tmp_path: Path) -> None:
    target = tmp_path / "frames.bin"
    target.write_bytes(b"frames")

    artifact = ArtifactRow.from_path(target, kind="stream")

    assert artifact.kind == "stream"
    assert artifact.size_bytes == 6
    assert artifact.hash == f"sha256:{hashlib.sha256(b'frames').hexdigest()}"


def test_artifact_from_missing_path_keeps_name(tmp_path: Path) -> None:
    artifact = ArtifactRow.from_path(tmp_path / "absent.bin", kind="stream")

    assert artifact.path.endswith("absent.bin")
    assert artifact.hash is None
    assert artifact.size_bytes is None
