"""Тесты метрик план/факт и сводки прогона (T7-09, T7-10, G7-09)."""

from __future__ import annotations

import pytest

from simorph.core.simulation.metrics import (
    IntervalSample,
    RunMetrics,
    ScaleRecorder,
    StageSample,
    histogram_buckets,
)


def test_recorder_closes_interval_at_boundary() -> None:
    recorder = ScaleRecorder(interval_s=1.0)
    recorder.observe_tick({"sensor": (8.0, 8.0)}, queue_depth=1, queue_wait_ms=0.5)

    assert recorder.maybe_close(0.5, 0.5) is None
    sample = recorder.maybe_close(1.0, 0.008)

    assert sample is not None
    assert sample.index == 0
    assert sample.scene_time_s == pytest.approx(1.0)
    assert sample.wall_time_s == pytest.approx(0.008)
    assert sample.stages["sensor"].plan_ms == pytest.approx(8.0)
    assert sample.stages["sensor"].actual_ms == pytest.approx(8.0)
    assert sample.k == pytest.approx(1.0)
    assert sample.queue_depth == 1
    assert sample.queue_wait_ms == pytest.approx(0.5)


def test_recorder_accumulates_and_flushes() -> None:
    recorder = ScaleRecorder(interval_s=1.0)
    recorder.observe_tick({"a": (2.0, 3.0)})
    recorder.observe_tick({"a": (2.0, 1.0)})

    sample = recorder.flush(0.4, 0.4)
    assert sample is not None
    assert sample.stages["a"].plan_ms == pytest.approx(4.0)
    assert sample.stages["a"].actual_ms == pytest.approx(4.0)
    assert sample.stages["a"].overrun_ms == pytest.approx(0.0)
    assert recorder.flush(0.4, 0.4) is None


def test_interval_k_is_wall_over_plan() -> None:
    sample = IntervalSample(
        index=0,
        scene_time_s=1.0,
        wall_time_s=3.0,
        stages={"sensor": StageSample(plan_ms=10.0, actual_ms=30.0)},
    )

    assert sample.planned_s == pytest.approx(0.01)
    assert sample.k == pytest.approx(300.0)
    assert sample.to_dict()["stages"]["sensor"]["overrun_ms"] == pytest.approx(20.0)


def _metrics() -> RunMetrics:
    return RunMetrics(
        intervals=[
            IntervalSample(0, 1.0, 0.005, {"s": StageSample(10.0, 5.0)}),
            IntervalSample(1, 2.0, 1.0, {"s": StageSample(10.0, 10.0)}),
            IntervalSample(2, 3.0, 2.0, {"s": StageSample(10.0, 20.0)}),
            IntervalSample(3, 4.0, 4.0, {"s": StageSample(10.0, 40.0)}),
        ],
        scene_time_s=4.0,
        pc_time_s=7.5,
        ticks=400,
        frames_pushed=400,
        frames_consumed=398,
        frames_dropped=2,
    )


def test_summary_statistics() -> None:
    metrics = _metrics()

    assert metrics.k_values == pytest.approx([0.5, 100.0, 200.0, 400.0])
    assert metrics.percentile(50.0) == pytest.approx(100.0)
    assert metrics.percentile(95.0) == pytest.approx(400.0)
    assert metrics.share_over_plan == pytest.approx(0.75)
    assert metrics.histogram()["0–0.9"] == 1


def test_stage_totals_and_payload() -> None:
    metrics = _metrics()
    totals = metrics.stage_totals()
    payload = metrics.to_dict()

    assert totals["s"]["plan_ms"] == pytest.approx(40.0)
    assert totals["s"]["actual_ms"] == pytest.approx(75.0)
    assert totals["s"]["overrun_ms"] == pytest.approx(35.0)
    assert payload["intervals"] == 4
    assert payload["k"]["p50"] == pytest.approx(100.0)
    assert payload["frames"] == {"pushed": 400, "consumed": 398, "dropped": 2}
    assert len(payload["samples"]) == 4


def test_empty_metrics_are_safe() -> None:
    metrics = RunMetrics()
    payload = metrics.to_dict()

    assert metrics.percentile(95.0) == pytest.approx(1.0)
    assert metrics.share_over_plan == 0.0
    assert payload["k"]["histogram"]["1–1.1"] == 0
    assert "сцена" in metrics.summary()


def test_histogram_buckets_cover_all_values() -> None:
    buckets = histogram_buckets([0.5, 0.95, 1.05, 1.2, 1.4, 1.8, 3.0])

    assert buckets["0–0.9"] == 1
    assert buckets["0.9–1"] == 1
    assert buckets["1–1.1"] == 1
    assert buckets["1.1–1.25"] == 1
    assert buckets["1.25–1.5"] == 1
    assert buckets["1.5–2"] == 1
    assert buckets["2+"] == 1


def test_recorder_validates_interval() -> None:
    with pytest.raises(ValueError):
        ScaleRecorder(0.0)
