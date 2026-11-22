"""Тесты headless-раннера: такты, кадры, деградация и изоляция (T7-05…T7-10)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from simorph.core.factory import build_agent, build_exercise
from simorph.core.models.exercise import Mission
from simorph.core.simulation.degradation import Degradation
from simorph.core.simulation.events import EventKind
from simorph.core.simulation.replay import replay_stream
from simorph.core.simulation.runner import (
    RunReport,
    SimulationConfig,
    SimulationRunner,
    effective_state,
    read_status,
)
from simorph.core.simulation.supervisor import RunState


class _VirtualTime:
    """Виртуальные часы: прогон детерминирован и не ждёт реального времени."""

    def __init__(self) -> None:
        self.ns = 0

    def now(self) -> int:
        return self.ns

    def sleep(self, seconds: float) -> None:
        self.ns += max(0, round(seconds * 1e9))


def _mission() -> Mission:
    return build_exercise(42).missions[0]


def _runner(
    config: SimulationConfig | None = None,
    *,
    mission: Mission | None = None,
    frame_source: Callable[[int, int], bytes] | None = None,
    virtual: bool = True,
) -> tuple[SimulationRunner, _VirtualTime]:
    clock = _VirtualTime()
    target = mission if mission is not None else _mission()
    settings = config or SimulationConfig(duration_s=0.25)
    if virtual:
        runner = SimulationRunner(
            target,
            agent=build_agent(),
            config=settings,
            frame_source=frame_source,
            now_ns=clock.now,
            sleep=clock.sleep,
        )
    else:
        runner = SimulationRunner(
            target,
            agent=build_agent(),
            config=settings,
            frame_source=frame_source,
        )
    return runner, clock


def test_headless_run_completes_without_frame_loss() -> None:
    runner, _clock = _runner()
    try:
        report = runner.run()
    finally:
        runner.close()

    assert report.ok is True
    assert report.state == str(RunState.FINISHED)
    assert report.frames["attempted"] == 30
    assert report.frames["pushed"] == 30
    assert report.frames["consumed"] == 30
    assert report.frames["dropped"] == 0
    assert report.loss_ratio == 0.0
    assert report.clock["sim_time_s"] == pytest.approx(0.25)
    assert report.metrics["intervals"] >= 1
    assert report.metrics["stages"]["sensor"]["plan_ms"] > 0.0


def test_frames_equal_ticks_even_with_slowdown() -> None:
    config = SimulationConfig(duration_s=0.1, work_ms=1000.0 / 120.0, slowdown=3.0)
    runner, _clock = _runner(config)
    try:
        report = runner.run()
    finally:
        runner.close()

    assert report.ok is True
    assert report.frames["attempted"] == report.frames["pushed"]
    assert report.frames["pushed"] == 12
    assert report.metrics["k"]["p50"] == pytest.approx(3.0, abs=0.2)
    assert report.metrics["stages"]["sensor"]["overrun_ms"] > 0.0


def test_degradation_is_reflected_in_metrics() -> None:
    config = SimulationConfig(
        duration_s=0.5,
        degradation=Degradation(
            sensor_latency_ms=2.0,
            frame_loss_ratio=0.5,
            desync_ms=0.2,
            exposure_jitter_us=10.0,
        ),
    )
    runner, _clock = _runner(config)
    try:
        report = runner.run()
    finally:
        runner.close()

    assert report.ok is True
    assert report.frames["dropped"] > 0
    assert report.metrics["degradation"]["lost"] == report.frames["dropped"]
    assert report.metrics["degradation"]["applied"] > 0
    assert report.metrics["degradation"]["max_desync_ns"] > 0
    assert report.metrics["degradation"]["max_jitter_us"] > 0.0


def test_events_are_ordered_and_cover_run() -> None:
    runner, _clock = _runner(SimulationConfig(duration_s=0.1))
    try:
        report = runner.run()
    finally:
        runner.close()

    kinds = [item["kind"] for item in report.events]
    assert kinds[0] == str(EventKind.RUN_STARTED)
    assert kinds[-1] == str(EventKind.RUN_FINISHED)
    assert str(EventKind.FRAME_PUSHED) in kinds
    assert str(EventKind.FRAME_CONSUMED) in kinds
    stamps = [item["t_monotonic_ns"] for item in report.events]
    assert stamps == sorted(stamps)


def test_failure_isolated_as_interrupted(tmp_path: Path) -> None:
    stream = tmp_path / "stream.jsonl"
    report_path = tmp_path / "report.json"

    def failing_source(frame_id: int, _fsin: int) -> bytes:
        if frame_id >= 5:
            raise RuntimeError("камера отключилась")
        return b"frame"

    runner, _clock = _runner(
        SimulationConfig(duration_s=1.0, record_path=stream, report_path=report_path),
        frame_source=failing_source,
    )
    try:
        report = runner.run()
    finally:
        runner.close()

    assert report.state == str(RunState.INTERRUPTED)
    assert "камера отключилась" in (report.reason or "")
    assert report.ok is False
    assert any(item["kind"] == str(EventKind.ERROR) for item in report.events)
    assert report_path.exists()
    assert read_status(report_path.with_name("report.status.json"))["state"] == "interrupted"
    assert replay_stream(stream).ok is True


def test_record_and_replay_roundtrip(tmp_path: Path) -> None:
    stream = tmp_path / "stream.jsonl"
    runner, _clock = _runner(SimulationConfig(duration_s=0.2, record_path=stream))
    try:
        report = runner.run()
    finally:
        runner.close()

    replay = replay_stream(stream)
    assert replay.ok is True
    assert replay.frames == report.frames["pushed"]
    assert replay.meta["mission_id"] == "M-01"
    assert replay.meta["sensor_hz"] == pytest.approx(120.0)


def test_status_file_marks_running_as_interrupted_after_crash(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    runner, _clock = _runner(SimulationConfig(duration_s=0.1, report_path=report_path))
    try:
        report = runner.run()
    finally:
        runner.close()

    status = read_status(report_path.with_name("report.status.json"))
    assert status["state"] == "finished"
    assert effective_state(status, alive=False) == "finished"
    assert effective_state({"state": "running"}, alive=False) == "interrupted"
    assert report.status is not None


def test_pause_step_and_stop() -> None:
    runner, _clock = _runner(
        SimulationConfig(duration_s=10.0, pace=False, work_ms=5.0),
        virtual=False,
    )
    import threading

    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2.0
    while runner.clock.ticks < 2 and time.monotonic() < deadline:
        time.sleep(0.002)
    assert runner.pause() is True
    paused_ticks = runner.clock.ticks
    time.sleep(0.03)
    assert runner.clock.ticks == paused_ticks

    assert runner.step_once() is True
    assert runner.clock.ticks == paused_ticks + 1
    assert runner.resume() is True
    runner.request_stop()
    thread.join(2.0)

    assert thread.is_alive() is False
    report = runner.report
    assert isinstance(report, RunReport)
    assert report.state == str(RunState.INTERRUPTED)
    runner.close()


def test_runner_rejects_second_run() -> None:
    runner, _clock = _runner(SimulationConfig(duration_s=0.05))
    try:
        runner.run()
        with pytest.raises(RuntimeError):
            runner.run()
    finally:
        runner.close()


def test_config_is_validated() -> None:
    with pytest.raises(ValueError):
        SimulationConfig(sensor_hz=0.0)
    with pytest.raises(ValueError):
        SimulationConfig(slowdown=0.0)
    with pytest.raises(ValueError):
        SimulationConfig(frame_bytes=0)
    with pytest.raises(ValueError):
        SimulationConfig(duration_s=0.0)
