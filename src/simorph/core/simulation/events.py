"""Поток событий симуляции (T7-04, FR-SIM-11).

Одно событие — это вид, сообщение, время PC и время сцены. Время PC
берётся из монотонных часов и не убывает: даже если системные часы
переставят, порядок событий остаётся прежним. Время сцены нужно, чтобы
событие можно было привязать к кадру и к элементу миссии.

События копятся в памяти и сразу пишутся в артефакты прогона, поэтому
после аварийного завершения журнал остаётся читаемым.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = ["EventKind", "EventStream", "SimEvent"]


class EventKind(StrEnum):
    """Виды событий супервизора и симуляции."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_INTERRUPTED = "run_interrupted"
    STATE_CHANGED = "state_changed"
    ELEMENT_CHANGED = "element_changed"
    TRANSACTION_STARTED = "transaction_started"
    TRANSACTION_FINISHED = "transaction_finished"
    FRAME_PUSHED = "frame_pushed"
    FRAME_CONSUMED = "frame_consumed"
    FRAME_DROPPED = "frame_dropped"
    DEGRADATION = "degradation"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SimEvent:
    """Одно событие потока."""

    seq: int
    kind: EventKind
    t_monotonic_ns: int
    t_scene_ns: int
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": str(self.kind),
            "t_monotonic_ns": self.t_monotonic_ns,
            "t_scene_ns": self.t_scene_ns,
            "message": self.message,
            "data": dict(self.data),
        }


class EventStream:
    """Упорядоченный поток событий с монотонными метками времени."""

    def __init__(self, *, clock_ns: Any = None) -> None:
        self._events: list[SimEvent] = []
        self._clock_ns = clock_ns if clock_ns is not None else time.perf_counter_ns
        self._last_ns = 0

    # --- состояние ------------------------------------------------------

    @property
    def events(self) -> list[SimEvent]:
        return list(self._events)

    @property
    def last(self) -> SimEvent | None:
        return self._events[-1] if self._events else None

    def __len__(self) -> int:
        return len(self._events)

    def by_kind(self, kind: EventKind) -> list[SimEvent]:
        return [item for item in self._events if item.kind is kind]

    def kinds(self) -> set[EventKind]:
        return {item.kind for item in self._events}

    # --- запись ---------------------------------------------------------

    def emit(
        self,
        kind: EventKind,
        message: str = "",
        *,
        t_scene_ns: int = 0,
        **data: Any,
    ) -> SimEvent:
        """Добавляет событие; метка PC не убывает."""
        now = int(self._clock_ns())
        stamp = max(now, self._last_ns)
        self._last_ns = stamp
        event = SimEvent(
            seq=len(self._events),
            kind=kind,
            t_monotonic_ns=stamp,
            t_scene_ns=int(t_scene_ns),
            message=message,
            data=dict(data),
        )
        self._events.append(event)
        return event

    def clear(self) -> None:
        self._events.clear()
        self._last_ns = 0

    # --- отчёт ----------------------------------------------------------

    def to_payload(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._events]

    def ordered(self) -> bool:
        """Метки PC не убывают, метки сцены не убывают."""
        previous = (-1, -1)
        for item in self._events:
            current = (item.t_monotonic_ns, item.t_scene_ns)
            if current[0] < previous[0] or current[1] < previous[1]:
                return False
            previous = current
        return True
