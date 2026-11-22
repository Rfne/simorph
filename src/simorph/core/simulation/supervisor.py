"""Супервизор миссии: состояния, таймеры транзакций и очередь (T7-03).

Супервизор — единственное место, где меняется состояние прогона. Переходы
проверяются таблицей: из ``idle`` можно только запустить, из ``running`` —
поставить на паузу, завершить или прервать, из ``paused`` — продолжить или
прервать. Незаконный переход — это ошибка программы, а не данные, поэтому
он бросает исключение.

Элементы миссии исполняются по расписанию бюджета (этап 6): у каждого
элемента есть окно ``[start_s, end_s)``, и когда время сцены входит
в окно, супервизор публикует смену элемента. Ветви ``parallel`` входят
одновременно — расписание уже учитывает критический путь.

Таймеры NPU-транзакций и очередь фоновых задач живут здесь же: они дают
``actual_ms`` и ``overrun_ms`` для метрик, не вмешиваясь в физику.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from simorph.core.mission.timings import mission_budget
from simorph.core.models.exercise import Mission, MissionElement
from simorph.core.simulation.events import EventKind, EventStream

__all__ = [
    "ALLOWED_TRANSITIONS",
    "BackgroundQueue",
    "ElementWindow",
    "MissionSupervisor",
    "RunState",
    "TransactionTimer",
]

#: Допуск сравнения моментов времени, с.
_EPS = 1e-9


class RunState(StrEnum):
    """Состояние прогона."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    INTERRUPTED = "interrupted"


#: Разрешённые переходы состояний.
ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.IDLE: frozenset({RunState.RUNNING}),
    RunState.RUNNING: frozenset({RunState.PAUSED, RunState.FINISHED, RunState.INTERRUPTED}),
    RunState.PAUSED: frozenset({RunState.RUNNING, RunState.INTERRUPTED}),
    RunState.FINISHED: frozenset(),
    RunState.INTERRUPTED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ElementWindow:
    """Окно исполнения элемента в времени сцены."""

    element_id: str
    element_type: str
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def contains(self, t_s: float) -> bool:
        return self.start_s - _EPS <= t_s < self.end_s - _EPS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.element_id,
            "type": self.element_type,
            "start_s": round(self.start_s, 6),
            "end_s": round(self.end_s, 6),
        }


@dataclass(slots=True)
class TransactionTimer:
    """Таймер NPU-транзакции: план против факта."""

    name: str
    budget_ms: float
    started_ns: int | None = None
    finished_ns: int | None = None

    @property
    def actual_ms(self) -> float | None:
        if self.started_ns is None or self.finished_ns is None:
            return None
        return max(0.0, (self.finished_ns - self.started_ns) / 1e6)

    @property
    def overrun_ms(self) -> float:
        actual = self.actual_ms
        if actual is None:
            return 0.0
        return max(0.0, actual - self.budget_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "budget_ms": round(self.budget_ms, 3),
            "actual_ms": None if self.actual_ms is None else round(self.actual_ms, 3),
            "overrun_ms": round(self.overrun_ms, 3),
        }


@dataclass(slots=True)
class BackgroundQueue:
    """Очередь фоновых задач: глубина и ожидание."""

    max_depth: int = 8
    _tasks: list[tuple[str, int]] = field(default_factory=list)
    _peak: int = 0
    _total_wait_ms: float = 0.0
    _max_wait_ms: float = 0.0
    _pushed: int = 0
    _popped: int = 0

    @property
    def depth(self) -> int:
        return len(self._tasks)

    @property
    def peak(self) -> int:
        return self._peak

    @property
    def pushed(self) -> int:
        return self._pushed

    @property
    def popped(self) -> int:
        return self._popped

    @property
    def total_wait_ms(self) -> float:
        return self._total_wait_ms

    @property
    def max_wait_ms(self) -> float:
        return self._max_wait_ms

    def push(self, task: str, t_ns: int) -> bool:
        """Ставит задачу в очередь; ``False`` — очередь заполнена."""
        if len(self._tasks) >= self.max_depth:
            return False
        self._tasks.append((task, int(t_ns)))
        self._pushed += 1
        self._peak = max(self._peak, len(self._tasks))
        return True

    def pop(self, t_ns: int) -> tuple[str, float] | None:
        """Забирает самую старую задачу и её ожидание в очереди, мс."""
        if not self._tasks:
            return None
        name, started = self._tasks.pop(0)
        wait_ms = max(0.0, (int(t_ns) - started) / 1e6)
        self._popped += 1
        self._total_wait_ms += wait_ms
        self._max_wait_ms = max(self._max_wait_ms, wait_ms)
        return name, wait_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "peak": self._peak,
            "pushed": self._pushed,
            "popped": self._popped,
            "total_wait_ms": round(self._total_wait_ms, 3),
            "max_wait_ms": round(self._max_wait_ms, 3),
        }


class MissionSupervisor:
    """Состояния прогона, окна элементов и таймеры."""

    def __init__(
        self,
        mission: Mission,
        events: EventStream | None = None,
        *,
        total_s: float | None = None,
    ) -> None:
        self._mission = mission
        self._events = events if events is not None else EventStream()
        self._state = RunState.IDLE
        self._windows = _element_windows(mission)
        computed = max((window.end_s for window in self._windows), default=0.0)
        self._total_s = max(0.0, float(total_s)) if total_s is not None else computed
        self._entered: set[str] = set()
        self._transactions: dict[str, TransactionTimer] = {}
        self._queue = BackgroundQueue()
        self._reason: str | None = None

    # --- состояние ------------------------------------------------------

    @property
    def state(self) -> RunState:
        return self._state

    @property
    def mission(self) -> Mission:
        return self._mission

    @property
    def events(self) -> EventStream:
        return self._events

    @property
    def windows(self) -> list[ElementWindow]:
        return list(self._windows)

    @property
    def total_s(self) -> float:
        return self._total_s

    @property
    def entered(self) -> list[str]:
        return [window.element_id for window in self._windows if window.element_id in self._entered]

    @property
    def reason(self) -> str | None:
        """Причина прерывания."""
        return self._reason

    @property
    def transactions(self) -> dict[str, TransactionTimer]:
        return dict(self._transactions)

    @property
    def queue(self) -> BackgroundQueue:
        return self._queue

    def active(self, t_s: float) -> list[str]:
        """Элементы, которые исполняются в момент времени сцены."""
        return [window.element_id for window in self._windows if window.contains(t_s)]

    # --- переходы -------------------------------------------------------

    def transition(self, state: RunState, *, reason: str | None = None) -> None:
        """Переводит прогон в новое состояние.

        Raises:
            ValueError: переход запрещён таблицей.
        """
        if state is self._state:
            return
        allowed = ALLOWED_TRANSITIONS[self._state]
        if state not in allowed:
            raise ValueError(f"переход {self._state} → {state} запрещён")
        previous = self._state
        self._state = state
        if reason is not None:
            self._reason = reason
        kind = {
            RunState.RUNNING: EventKind.RUN_STARTED,
            RunState.FINISHED: EventKind.RUN_FINISHED,
            RunState.INTERRUPTED: EventKind.RUN_INTERRUPTED,
        }.get(state, EventKind.STATE_CHANGED)
        if previous is RunState.PAUSED and state is RunState.RUNNING:
            kind = EventKind.STATE_CHANGED
        self._events.emit(
            kind,
            reason or f"{previous} → {state}",
            from_state=str(previous),
            to_state=str(state),
        )

    def start(self) -> None:
        self.transition(RunState.RUNNING, reason="прогон запущен")

    def pause(self) -> bool:
        """Пауза; ``False``, если прогон не идёт."""
        if self._state is not RunState.RUNNING:
            return False
        self.transition(RunState.PAUSED, reason="пауза")
        return True

    def resume(self) -> bool:
        """Продолжение после паузы."""
        if self._state is not RunState.PAUSED:
            return False
        self.transition(RunState.RUNNING, reason="продолжение")
        return True

    def finish(self) -> None:
        if self._state is RunState.RUNNING:
            self.transition(RunState.FINISHED, reason="миссия завершена")

    def interrupt(self, reason: str) -> None:
        """Прерывает прогон из любого незавершённого состояния."""
        if self._state in (RunState.FINISHED, RunState.INTERRUPTED):
            return
        if self._state is RunState.IDLE:
            self._state = RunState.RUNNING
        self.transition(RunState.INTERRUPTED, reason=reason)
        self._events.emit(EventKind.ERROR, reason)

    def reset(self) -> None:
        """Возвращает супервизор в исходное состояние (новый прогон)."""
        self._state = RunState.IDLE
        self._entered.clear()
        self._transactions.clear()
        self._queue = BackgroundQueue()
        self._reason = None

    # --- исполнение -----------------------------------------------------

    def advance(self, t_prev_s: float, t_now_s: float) -> list[str]:
        """Продвигает исполнение и возвращает вошедшие элементы."""
        if self._state is not RunState.RUNNING:
            return []
        entered: list[str] = []
        for window in self._windows:
            if window.element_id in self._entered:
                continue
            if window.start_s <= t_now_s + _EPS:
                self._entered.add(window.element_id)
                entered.append(window.element_id)
                self._events.emit(
                    EventKind.ELEMENT_CHANGED,
                    f"элемент {window.element_id}",
                    t_scene_ns=round(max(t_prev_s, window.start_s) * 1e9),
                    element_id=window.element_id,
                    element_type=window.element_type,
                    start_s=round(window.start_s, 6),
                )
        return entered

    def complete(self, t_now_s: float) -> bool:
        """Миссия закончилась: время сцены дошло до конца бюджета."""
        return t_now_s + _EPS >= self._total_s

    # --- таймеры и очередь ----------------------------------------------

    def begin_transaction(self, name: str, budget_ms: float, t_ns: int) -> TransactionTimer:
        timer = TransactionTimer(name=name, budget_ms=float(budget_ms), started_ns=int(t_ns))
        self._transactions[name] = timer
        self._events.emit(
            EventKind.TRANSACTION_STARTED,
            f"транзакция {name}",
            budget_ms=round(float(budget_ms), 3),
        )
        return timer

    def finish_transaction(self, name: str, t_ns: int) -> TransactionTimer | None:
        timer = self._transactions.get(name)
        if timer is None or timer.finished_ns is not None:
            return None
        timer.finished_ns = int(t_ns)
        self._events.emit(
            EventKind.TRANSACTION_FINISHED,
            f"транзакция {name}",
            actual_ms=round(timer.actual_ms or 0.0, 3),
            overrun_ms=round(timer.overrun_ms, 3),
        )
        return timer

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": str(self._state),
            "reason": self._reason,
            "mission_id": self._mission.mission_id,
            "agent_id": self._mission.agent_id,
            "total_s": round(self._total_s, 6),
            "elements": len(self._windows),
            "entered": self.entered,
            "windows": [window.to_dict() for window in self._windows],
            "queue": self._queue.to_dict(),
            "transactions": {name: timer.to_dict() for name, timer in self._transactions.items()},
        }


def _element_windows(mission: Mission) -> list[ElementWindow]:
    """Окна элементов по расписанию бюджета миссии."""
    budget = mission_budget(mission.elements)
    by_id: dict[str, MissionElement] = {item.id: item for item in mission.elements}
    windows: list[ElementWindow] = []
    for element_id in budget.starts:
        element = by_id.get(element_id)
        if element is None:
            continue
        windows.append(
            ElementWindow(
                element_id=element_id,
                element_type=element.type,
                start_s=budget.starts[element_id],
                end_s=budget.ends.get(element_id, budget.starts[element_id]),
            )
        )
    windows.sort(key=lambda window: (window.start_s, window.end_s, window.element_id))
    return windows
