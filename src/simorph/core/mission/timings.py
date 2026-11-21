"""Тайминги и бюджет миссии (T6-05, FR-MSN-07).

Бюджет миссии — это не сумма длительностей, а критический путь: ветви
``parallel`` идут одновременно, поэтому миссия заканчивается, когда
заканчивается самая долгая ветвь. Раннее расписание (ASAP) даёт каждому
элементу момент старта и окончания, а по ним считаются занятость
и ширина параллелизма.

Значения только читаются: тайминги хранятся в элементах, а бюджет —
расчётная величина для панели параметров и отчёта.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simorph.core.mission.graph import build_graph
from simorph.core.models.exercise import MissionElement

__all__ = ["MissionBudget", "element_duration", "mission_budget"]

#: Допуск сравнения моментов времени, с.
_EPS = 1e-9


def element_duration(element: MissionElement) -> float:
    """Длительность элемента, с; отсутствие означает мгновенный переход."""
    return float(element.duration_s or 0.0)


@dataclass(slots=True)
class MissionBudget:
    """Расчёт бюджета: расписание, критический путь и ширина параллелизма."""

    starts: dict[str, float] = field(default_factory=dict)
    ends: dict[str, float] = field(default_factory=dict)
    critical_path: list[str] = field(default_factory=list)
    total_s: float = 0.0
    sequential_s: float = 0.0
    parallel_width: int = 0
    ttl_s: float | None = None

    @property
    def over_ttl(self) -> bool:
        """Бюджет не укладывается в самый жёсткий TTL миссии."""
        return self.ttl_s is not None and self.total_s > self.ttl_s + _EPS

    def element_start(self, element_id: str) -> float | None:
        return self.starts.get(element_id)

    def element_end(self, element_id: str) -> float | None:
        return self.ends.get(element_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_s": round(self.total_s, 3),
            "sequential_s": round(self.sequential_s, 3),
            "parallel_width": self.parallel_width,
            "ttl_s": None if self.ttl_s is None else round(self.ttl_s, 3),
            "over_ttl": self.over_ttl,
            "critical_path": list(self.critical_path),
            "timeline": [
                {
                    "id": name,
                    "start_s": round(self.starts[name], 3),
                    "end_s": round(self.ends[name], 3),
                }
                for name in self.starts
            ],
        }


def mission_budget(elements: list[MissionElement]) -> MissionBudget:
    """Считает раннее расписание и критический путь миссии."""
    graph = build_graph(elements)
    budget = MissionBudget()
    if not elements:
        return budget

    for name in graph.order:
        element = graph.by_id.get(name)
        if element is None:
            continue
        parents = graph.parents.get(name, [])
        start = max((budget.ends.get(parent, 0.0) for parent in parents), default=0.0)
        duration = element_duration(element)
        budget.starts[name] = start
        budget.ends[name] = start + duration
        budget.sequential_s += duration

    budget.total_s = max(budget.ends.values(), default=0.0)
    budget.critical_path = _critical_path(graph, budget)
    budget.parallel_width = _parallel_width(budget)
    ttls = [float(element.ttl_s) for element in elements if element.ttl_s is not None]
    budget.ttl_s = min(ttls) if ttls else None
    return budget


def _critical_path(graph: Any, budget: MissionBudget) -> list[str]:
    """Цепочка от элемента с самым поздним окончанием назад к старту."""
    if not budget.ends:
        return []
    position = {name: index for index, name in enumerate(getattr(graph, "order", ()))}
    last = max(budget.ends, key=lambda name: (budget.ends[name], position.get(name, -1)))
    path = [last]
    seen = {last}
    current = last
    while True:
        parents = [name for name in graph.parents.get(current, []) if name in budget.ends]
        if not parents:
            break
        parent = max(parents, key=lambda name: (budget.ends[name], position.get(name, -1)))
        if parent in seen:
            break
        path.append(parent)
        seen.add(parent)
        current = parent
    path.reverse()
    return path


def _parallel_width(budget: MissionBudget) -> int:
    """Максимальное число одновременно выполняемых элементов."""
    events: list[tuple[float, int]] = []
    for name, start in budget.starts.items():
        end = budget.ends.get(name, start)
        if end - start <= _EPS:
            continue
        events.append((start, 1))
        events.append((end, -1))
    if not events:
        return 1 if budget.starts else 0
    events.sort(key=lambda item: (item[0], item[1]))
    current = 0
    peak = 0
    for _moment, delta in events:
        current += delta
        peak = max(peak, current)
    return peak
