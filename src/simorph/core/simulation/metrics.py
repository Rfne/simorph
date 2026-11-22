"""Метрики план/факт и сводка прогона (T7-09, T7-10).

Каждый интервал наблюдения (по умолчанию 1 с времени сцены) даёт одну
строку: сколько времени PC потрачено, какой получился коэффициент
``k = T_факт / T_план``, сколько заняла каждая ступень и какая была
очередь. Из этих строк строится сводка: гистограмма ``k``, ``p50``/``p95``,
доля интервалов за планом и вклад ступеней.

``budget_ms`` ступени и ``fps`` источника — плановые ориентиры. Их
превышение не прерывает исполнение: оно попадает в ``stage_overrun_ms``
и в сводку (FR-PIP-28, FR-SIM-17…18).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_INTERVAL_S",
    "IntervalSample",
    "RunMetrics",
    "ScaleRecorder",
    "StageSample",
    "histogram_buckets",
]

#: Интервал наблюдения по умолчанию: 1 с времени сцены (TRD 6.5.1).
DEFAULT_INTERVAL_S: float = 1.0

#: Допуск сравнения моментов времени, с.
_EPS = 1e-9

#: Границы гистограммы коэффициента масштаба.
_HISTOGRAM_EDGES: tuple[tuple[float, float], ...] = (
    (0.0, 0.9),
    (0.9, 1.0),
    (1.0, 1.1),
    (1.1, 1.25),
    (1.25, 1.5),
    (1.5, 2.0),
    (2.0, math.inf),
)


@dataclass(slots=True)
class StageSample:
    """Вклад одной ступени за интервал."""

    plan_ms: float = 0.0
    actual_ms: float = 0.0

    @property
    def overrun_ms(self) -> float:
        return max(0.0, self.actual_ms - self.plan_ms)

    def to_dict(self) -> dict[str, float]:
        return {
            "plan_ms": round(self.plan_ms, 3),
            "actual_ms": round(self.actual_ms, 3),
            "overrun_ms": round(self.overrun_ms, 3),
        }


@dataclass(slots=True)
class IntervalSample:
    """Один интервал наблюдения."""

    index: int
    scene_time_s: float
    wall_time_s: float
    stages: dict[str, StageSample] = field(default_factory=dict)
    queue_depth: int = 0
    queue_wait_ms: float = 0.0

    @property
    def planned_s(self) -> float:
        """План интервала: сумма плановых бюджетов ступеней."""
        return sum(item.plan_ms for item in self.stages.values()) / 1000.0

    @property
    def k(self) -> float:
        """Коэффициент масштаба интервала."""
        planned = self.planned_s
        if planned <= _EPS:
            return 1.0
        return self.wall_time_s / planned

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "scene_time_s": round(self.scene_time_s, 6),
            "wall_time_s": round(self.wall_time_s, 6),
            "k": round(self.k, 4),
            "stages": {name: item.to_dict() for name, item in self.stages.items()},
            "queue_depth": self.queue_depth,
            "queue_wait_ms": round(self.queue_wait_ms, 3),
        }


class ScaleRecorder:
    """Накапливает план и факт по интервалам времени сцены."""

    def __init__(self, interval_s: float = DEFAULT_INTERVAL_S) -> None:
        if interval_s <= 0.0:
            raise ValueError("интервал наблюдения должен быть положительным")
        self.interval_s = float(interval_s)
        self._next_boundary = self.interval_s
        self._index = 0
        self._plan: dict[str, float] = {}
        self._actual: dict[str, float] = {}
        self._depth = 0
        self._wait_ms = 0.0

    def observe_tick(
        self,
        stages: dict[str, tuple[float, float]],
        *,
        queue_depth: int = 0,
        queue_wait_ms: float = 0.0,
    ) -> None:
        """Добавляет такт: имя ступени → (план мс, факт мс)."""
        for name, (plan_ms, actual_ms) in stages.items():
            self._plan[name] = self._plan.get(name, 0.0) + max(0.0, float(plan_ms))
            self._actual[name] = self._actual.get(name, 0.0) + max(0.0, float(actual_ms))
        self._depth = max(self._depth, int(queue_depth))
        self._wait_ms = max(self._wait_ms, float(queue_wait_ms))

    def maybe_close(self, scene_time_s: float, wall_time_s: float) -> IntervalSample | None:
        """Закрывает интервал, если время сцены дошло до границы."""
        if scene_time_s + _EPS < self._next_boundary:
            return None
        sample = self._close(self._next_boundary, wall_time_s)
        self._next_boundary += self.interval_s
        return sample

    def flush(self, scene_time_s: float, wall_time_s: float) -> IntervalSample | None:
        """Закрывает незавершённый интервал в конце прогона."""
        if not self._plan:
            return None
        return self._close(max(0.0, scene_time_s), wall_time_s)

    def _close(self, scene_time_s: float, wall_time_s: float) -> IntervalSample:
        sample = IntervalSample(
            index=self._index,
            scene_time_s=scene_time_s,
            wall_time_s=wall_time_s,
            stages={
                name: StageSample(plan_ms=plan, actual_ms=self._actual.get(name, 0.0))
                for name, plan in self._plan.items()
            },
            queue_depth=self._depth,
            queue_wait_ms=self._wait_ms,
        )
        self._index += 1
        self._plan.clear()
        self._actual.clear()
        self._depth = 0
        self._wait_ms = 0.0
        return sample


@dataclass(slots=True)
class RunMetrics:
    """Метрики и сводка прогона."""

    intervals: list[IntervalSample] = field(default_factory=list)
    pc_time_s: float = 0.0
    scene_time_s: float = 0.0
    ticks: int = 0
    frames_pushed: int = 0
    frames_consumed: int = 0
    frames_dropped: int = 0
    realtime_drop: bool = False
    degradation: dict[str, Any] = field(default_factory=dict)
    queue: dict[str, Any] = field(default_factory=dict)

    # --- выборки --------------------------------------------------------

    @property
    def k_values(self) -> list[float]:
        return [sample.k for sample in self.intervals]

    def percentile(self, value: float) -> float:
        """Перцентиль коэффициента масштаба (ближайший ранг)."""
        values = sorted(self.k_values)
        if not values:
            return 1.0
        rank = max(1, math.ceil(value / 100.0 * len(values)))
        return values[min(len(values) - 1, rank - 1)]

    @property
    def share_over_plan(self) -> float:
        """Доля интервалов, где ``k > 1``."""
        values = self.k_values
        if not values:
            return 0.0
        return sum(1 for item in values if item > 1.0 + _EPS) / len(values)

    def histogram(self) -> dict[str, int]:
        """Гистограмма ``k`` по фиксированным интервалам."""
        return histogram_buckets(self.k_values)

    def stage_totals(self) -> dict[str, dict[str, float]]:
        """Суммарный вклад ступеней по всем интервалам."""
        totals: dict[str, StageSample] = {}
        for sample in self.intervals:
            for name, item in sample.stages.items():
                total = totals.setdefault(name, StageSample())
                total.plan_ms += item.plan_ms
                total.actual_ms += item.actual_ms
        return {name: item.to_dict() for name, item in totals.items()}

    # --- отчёт ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "intervals": len(self.intervals),
            "interval_s": DEFAULT_INTERVAL_S if not self.intervals else None,
            "scene_time_s": round(self.scene_time_s, 6),
            "pc_time_s": round(self.pc_time_s, 6),
            "ticks": self.ticks,
            "k": {
                "p50": round(self.percentile(50.0), 4),
                "p95": round(self.percentile(95.0), 4),
                "max": round(max(self.k_values, default=1.0), 4),
                "share_over_plan": round(self.share_over_plan, 4),
                "histogram": self.histogram(),
            },
            "stages": self.stage_totals(),
            "queue": dict(self.queue),
            "frames": {
                "pushed": self.frames_pushed,
                "consumed": self.frames_consumed,
                "dropped": self.frames_dropped,
            },
            "realtime_drop": self.realtime_drop,
            "degradation": dict(self.degradation),
            "samples": [sample.to_dict() for sample in self.intervals],
        }

    def summary(self) -> str:
        """Короткая сводка для CLI и панели прогона."""
        if not self.intervals:
            return (
                f"сцена {self.scene_time_s:.1f} с · PC {self.pc_time_s:.1f} с"
                f" · тактов {self.ticks} · кадров {self.frames_pushed}"
            )
        return (
            f"сцена {self.scene_time_s:.1f} с · PC {self.pc_time_s:.1f} с"
            f" · k p50 {self.percentile(50.0):.2f}, p95 {self.percentile(95.0):.2f}"
            f" · за планом {self.share_over_plan * 100:.0f}%"
            f" · тактов {self.ticks}"
            f" · кадров {self.frames_pushed}/{self.frames_consumed}"
            f" · потерь {self.frames_dropped}"
        )


def histogram_buckets(values: list[float]) -> dict[str, int]:
    """Гистограмма коэффициента масштаба с подписями границ."""
    result: dict[str, int] = {}
    for low, high in _HISTOGRAM_EDGES:
        label = f"{low:g}–{high:g}" if math.isfinite(high) else f"{low:g}+"
        result[label] = sum(1 for value in values if low <= value < high)
    return result
