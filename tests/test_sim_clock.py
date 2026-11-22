"""Тесты часов симуляции: фиксированный шаг и мягкий масштаб (T7-01, T7-09)."""

from __future__ import annotations

import pytest

from simorph.core.simulation.clock import MAX_SCALE, MIN_SCALE, SimClock


def test_clock_starts_at_zero() -> None:
    clock = SimClock(step_s=0.01, scale=1.0)

    assert clock.ticks == 0
    assert clock.sim_time_s == 0.0
    assert clock.wall_time_s == 0.0
    assert clock.running is False
    assert clock.scale_factor() == pytest.approx(1.0)


def test_tick_advances_scene_by_exact_step() -> None:
    clock = SimClock(step_s=0.01)
    clock.start()

    assert clock.tick(0.004) is True
    assert clock.tick(0.006) is True

    assert clock.ticks == 2
    assert clock.sim_time_s == pytest.approx(0.02)
    assert clock.wall_time_s == pytest.approx(0.01)
    assert clock.scale_factor() == pytest.approx(0.5)


def test_soft_scale_stretches_instead_of_skipping() -> None:
    clock = SimClock(step_s=0.01, scale=2.0)
    clock.start()

    assert clock.target_interval_s == pytest.approx(0.005)
    assert clock.tick(0.02) is True

    assert clock.ticks == 1
    assert clock.sim_time_s == pytest.approx(0.01)
    assert clock.scale_factor() == pytest.approx(4.0)


def test_planned_time_and_ticks_for_wall() -> None:
    clock = SimClock(step_s=0.01, scale=0.5)
    clock.start()
    clock.tick()
    clock.tick()

    assert clock.planned_time_s == pytest.approx(0.04)
    assert clock.wall_time_s == pytest.approx(0.04)
    assert clock.ticks_for_wall(0.1) == 5


def test_pause_resume_and_stop() -> None:
    clock = SimClock()
    clock.start()
    assert clock.pause() is True
    assert clock.tick() is False
    assert clock.resume() is True
    assert clock.tick() is True
    clock.stop()
    assert clock.tick() is False
    assert clock.running is False


def test_start_after_stop_restarts() -> None:
    clock = SimClock()
    clock.start()
    clock.tick()
    clock.stop()
    clock.start()

    assert clock.ticks == 0
    assert clock.sim_time_s == 0.0
    assert clock.stopped is False


def test_scale_is_validated() -> None:
    with pytest.raises(ValueError):
        SimClock(scale=MIN_SCALE / 2)
    with pytest.raises(ValueError):
        SimClock(scale=MAX_SCALE * 2)
    with pytest.raises(ValueError):
        SimClock(step_s=0.0)

    clock = SimClock()
    with pytest.raises(ValueError):
        clock.set_scale(0.0)
    clock.set_scale(2.0)
    assert clock.scale == 2.0


def test_to_dict_reports_soft_scale() -> None:
    clock = SimClock(step_s=0.01, scale=2.0)
    clock.start()
    clock.tick(0.02)
    payload = clock.to_dict()

    assert payload["ticks"] == 1
    assert payload["sim_time_s"] == pytest.approx(0.01)
    assert payload["wall_time_s"] == pytest.approx(0.02)
    assert payload["target_interval_s"] == pytest.approx(0.005)
    assert payload["k"] == pytest.approx(4.0)
