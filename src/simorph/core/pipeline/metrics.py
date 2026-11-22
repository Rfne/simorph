"""Метрики конвейера: задержки узлов, очереди и отброшенные кадры (T8-06).

Метрика узла — это набор фактических задержек по кадрам. Сводка даёт
среднее, p50, p95 и максимум: p95 показывает хвост, а не только среднее,
поэтому по ней проверяются бюджеты инференса и разреженного контура.

Очередь измеряется в кадрах, ожидающих обработки, и в миллисекундах
ожидания. Отброшенные кадры считаются отдельно и не смешиваются с
потерями транспорта: это решение самого конвейера при явном
``realtime_drop``.

Каждое событие несёт обе метки времени — ``t_fsin_ns`` и
``t_monotonic_ns``, поэтому журнал конвейера стыкуется с журналом
симуляции и replay.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "NodeLatency",
    "PipelineMetrics",
    "PipelineRunReport",
    "percentile",
]


def percentile(values: list[float], p: float) -> float:
    """Перцентиль по методу ближайшего ранга; пустой список — 0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(p / 100.0 * len(ordered)) - 1))
    return ordered[rank]


@dataclass(slots=True)
class NodeLatency:
    """Задержки одного узла по кадрам."""

    node_id: str
    type: str
    plan_ms: float
    samples: list[float] = field(default_factory=list)

    def add(self, value_ms: float) -> None:
        self.samples.append(max(0.0, float(value_ms)))

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def mean_ms(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.samples) if self.samples else 0.0

    @property
    def p50_ms(self) -> float:
        return percentile(self.samples, 50.0)

    @property
    def p95_ms(self) -> float:
        return percentile(self.samples, 95.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "plan_ms": round(self.plan_ms, 3),
            "samples": self.count,
            "mean_ms": round(self.mean_ms, 3),
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "max_ms": round(self.max_ms, 3),
        }


@dataclass(slots=True)
class PipelineMetrics:
    """Сводка прогона конвейера."""

    pipeline_id: str = ""
    kind: str = "computational"
    frames_attempted: int = 0
    frames_completed: int = 0
    frames_dropped: int = 0
    queue_max_depth: int = 0
    queue_wait_ms: float = 0.0
    monotonic: bool = True
    critical_path: list[str] = field(default_factory=list)
    critical_path_ms: float = 0.0
    nodes: dict[str, NodeLatency] = field(default_factory=dict)
    roi: dict[str, int] = field(default_factory=dict)

    @property
    def loss_ratio(self) -> float:
        if not self.frames_attempted:
            return 0.0
        return self.frames_dropped / self.frames_attempted

    @property
    def total_work_ms(self) -> float:
        """Сумма фактических средних задержек всех узлов."""
        return sum(item.mean_ms for item in self.nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": {
                "attempted": self.frames_attempted,
                "completed": self.frames_completed,
                "dropped": self.frames_dropped,
                "loss_ratio": round(self.loss_ratio, 6),
            },
            "queue": {
                "max_depth": self.queue_max_depth,
                "wait_ms": round(self.queue_wait_ms, 3),
            },
            "nodes": {name: item.to_dict() for name, item in self.nodes.items()},
            "critical_path": list(self.critical_path),
            "critical_path_ms": round(self.critical_path_ms, 3),
            "total_work_ms": round(self.total_work_ms, 3),
            "monotonic": self.monotonic,
            "roi": dict(self.roi),
        }


@dataclass(slots=True)
class PipelineRunReport:
    """Итог прогона конвейера."""

    pipeline_id: str
    kind: str
    state: str
    reason: str | None
    config: dict[str, Any]
    metrics: PipelineMetrics
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state == "finished"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "state": self.state,
            "reason": self.reason,
            "pipeline_id": self.pipeline_id,
            "kind": self.kind,
            "config": dict(self.config),
            "metrics": self.metrics.to_dict(),
            "events": list(self.events),
        }

    def summary(self) -> str:
        metrics = self.metrics
        return (
            f"конвейер {self.pipeline_id} ({self.kind}): {self.state}"
            f" · кадров {metrics.frames_completed}/{metrics.frames_attempted}"
            f" · отброшено {metrics.frames_dropped}"
            f" ({metrics.loss_ratio * 100:.2f}%)"
            f" · план {metrics.critical_path_ms:.1f} мс"
            f" · факт {metrics.total_work_ms:.1f} мс"
            f" · очередь до {metrics.queue_max_depth}"
        )
