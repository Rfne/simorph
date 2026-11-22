"""Тесты супервизора: состояния, окна, таймеры и очередь (T7-03, G7-04)."""

from __future__ import annotations

import pytest

from simorph.core.models.exercise import Mission, MissionElement
from simorph.core.simulation.events import EventKind
from simorph.core.simulation.supervisor import (
    ALLOWED_TRANSITIONS,
    BackgroundQueue,
    MissionSupervisor,
    RunState,
    TransactionTimer,
)


def _mission() -> Mission:
    return Mission(
        mission_id="M-01",
        agent_id="NRTC-01",
        elements=[
            MissionElement(id="start", type="start", next=["wait"]),
            MissionElement(id="wait", type="dwell", duration_s=10.0, next=["ret"]),
            MissionElement(id="ret", type="return_to_base"),
        ],
    )


def test_all_states_are_reachable() -> None:
    supervisor = MissionSupervisor(_mission())
    assert supervisor.state is RunState.IDLE

    supervisor.start()
    assert supervisor.state is RunState.RUNNING
    assert supervisor.pause() is True
    assert supervisor.state is RunState.PAUSED
    assert supervisor.resume() is True
    supervisor.finish()
    assert supervisor.state is RunState.FINISHED

    interrupted = MissionSupervisor(_mission())
    interrupted.start()
    interrupted.interrupt("сбой")
    assert interrupted.state is RunState.INTERRUPTED
    assert interrupted.reason == "сбой"


def test_illegal_transitions_are_rejected() -> None:
    supervisor = MissionSupervisor(_mission())
    with pytest.raises(ValueError):
        supervisor.transition(RunState.FINISHED)
    supervisor.start()
    with pytest.raises(ValueError):
        supervisor.transition(RunState.IDLE)
    assert ALLOWED_TRANSITIONS[RunState.FINISHED] == frozenset()


def test_windows_follow_budget_and_advance_once() -> None:
    supervisor = MissionSupervisor(_mission())

    assert [window.element_id for window in supervisor.windows] == ["start", "wait", "ret"]
    assert supervisor.total_s == pytest.approx(10.0)
    assert supervisor.active(0.0) == ["wait"]

    assert supervisor.advance(0.0, 0.1) == []
    supervisor.start()
    entered = supervisor.advance(0.0, 0.1)
    assert entered == ["start", "wait"]
    assert supervisor.advance(0.1, 0.2) == []
    assert supervisor.entered == ["start", "wait"]
    assert supervisor.complete(10.0) is True
    assert supervisor.complete(9.0) is False


def test_transactions_measure_plan_and_overrun() -> None:
    supervisor = MissionSupervisor(_mission())
    supervisor.begin_transaction("npu", 4.0, 1_000_000)
    timer = supervisor.finish_transaction("npu", 7_500_000)

    assert timer is not None
    assert timer.actual_ms == pytest.approx(6.5)
    assert timer.overrun_ms == pytest.approx(2.5)
    assert supervisor.finish_transaction("npu", 9_000_000) is None
    assert supervisor.transactions["npu"].to_dict()["overrun_ms"] == pytest.approx(2.5)


def test_background_queue_tracks_wait() -> None:
    queue = BackgroundQueue(max_depth=2)
    assert queue.push("a", 0) is True
    assert queue.push("b", 0) is True
    assert queue.push("c", 0) is False
    assert queue.depth == 2
    assert queue.peak == 2

    item = queue.pop(2_000_000)
    assert item == ("a", pytest.approx(2.0))
    assert queue.popped == 1
    assert queue.max_wait_ms == pytest.approx(2.0)
    assert queue.to_dict()["pushed"] == 2


def test_reset_returns_to_idle() -> None:
    supervisor = MissionSupervisor(_mission())
    supervisor.start()
    supervisor.advance(0.0, 0.1)
    supervisor.reset()

    assert supervisor.state is RunState.IDLE
    assert supervisor.entered == []
    assert supervisor.reason is None


def test_to_dict_contains_schedule() -> None:
    supervisor = MissionSupervisor(_mission())
    supervisor.start()
    payload = supervisor.to_dict()

    assert payload["state"] == "running"
    assert payload["mission_id"] == "M-01"
    assert payload["elements"] == 3
    assert len(payload["windows"]) == 3


def test_transition_events_are_ordered() -> None:
    supervisor = MissionSupervisor(_mission())
    supervisor.start()
    supervisor.pause()
    supervisor.resume()
    supervisor.finish()

    kinds = [item.kind for item in supervisor.events.events]
    assert kinds == [
        EventKind.RUN_STARTED,
        EventKind.STATE_CHANGED,
        EventKind.STATE_CHANGED,
        EventKind.RUN_FINISHED,
    ]
    assert supervisor.events.ordered() is True


def test_transaction_timer_rounds_values() -> None:
    timer = TransactionTimer(name="npu", budget_ms=4.0, started_ns=0, finished_ns=5_000_000)
    payload = timer.to_dict()

    assert payload["actual_ms"] == pytest.approx(5.0)
    assert payload["overrun_ms"] == pytest.approx(1.0)
