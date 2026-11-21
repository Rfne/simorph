"""Граф миссии: порядок обхода, слои, циклы и достижимость (T6-02, T6-06).

Граф строится по списку элементов и никогда не бросает исключений: битые
ссылки и циклы — это данные для валидатора, а не повод уронить редактор.
Цикл допускается только через элемент ``loop``: обратные рёбра не мешают
топологическому порядку и раскладке слоёв, но остаются в списке ``cycles``.

Слои нужны панели графа: элемент попадает в слой на единицу глубже самого
дальнего предшественника. Так ветви ``parallel`` расходятся в стороны,
а не накладываются друг на друга.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simorph.core.models.exercise import MissionElement

__all__ = ["MissionGraph", "build_graph"]

#: Ограничение на число проходов релаксации слоёв: защита от цикла.
_LAYER_PASSES = 4


@dataclass(slots=True)
class MissionGraph:
    """Результат сборки графа миссии."""

    elements: list[MissionElement] = field(default_factory=list)
    by_id: dict[str, MissionElement] = field(default_factory=dict)
    successors: dict[str, list[str]] = field(default_factory=dict)
    parents: dict[str, list[str]] = field(default_factory=dict)
    start: str | None = None
    starts: list[str] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    layers: dict[str, int] = field(default_factory=dict)
    reachable: set[str] = field(default_factory=set)
    cycles: set[str] = field(default_factory=set)
    illegal_cycles: set[str] = field(default_factory=set)
    dangling: list[tuple[str, str]] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """Ровно один старт, уникальные id и нет циклов вне ``loop``."""
        return not self.duplicates and len(self.starts) == 1 and not self.illegal_cycles

    @property
    def layer_count(self) -> int:
        return (max(self.layers.values()) + 1) if self.layers else 0

    @property
    def terminal_ids(self) -> list[str]:
        """Элементы без исходящих переходов в порядке графа."""
        return [name for name in self.order if not self.successors.get(name)]

    def path_to(self, element_id: str) -> list[str]:
        """Один из путей от старта до элемента (для подсказки в панели)."""
        if self.start is None:
            return []
        queue: list[list[str]] = [[self.start]]
        seen: set[str] = set()
        while queue:
            path = queue.pop(0)
            node = path[-1]
            if node == element_id:
                return path
            if node in seen:
                continue
            seen.add(node)
            for target in self.successors.get(node, ()):
                queue.append([*path, target])
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "elements": len(self.elements),
            "order": list(self.order),
            "layers": dict(self.layers),
            "layer_count": self.layer_count,
            "terminal": self.terminal_ids,
            "cycles": sorted(self.cycles),
            "illegal_cycles": sorted(self.illegal_cycles),
            "dangling": [list(pair) for pair in self.dangling],
            "duplicates": list(self.duplicates),
            "valid": self.valid,
        }


def build_graph(elements: list[MissionElement]) -> MissionGraph:
    """Собирает граф по элементам миссии."""
    graph = MissionGraph(elements=list(elements))
    for element in elements:
        if element.id in graph.by_id:
            graph.duplicates.append(element.id)
            continue
        graph.by_id[element.id] = element

    for element in elements:
        known: list[str] = []
        for target in element.next:
            if target in graph.by_id:
                known.append(target)
            else:
                graph.dangling.append((element.id, target))
        graph.successors[element.id] = known
        for target in known:
            graph.parents.setdefault(target, []).append(element.id)

    graph.starts = [element.id for element in elements if element.type == "start"]
    graph.start = graph.starts[0] if len(graph.starts) == 1 else None
    graph.cycles, graph.illegal_cycles = _cycle_nodes(graph)
    graph.order = _topological_order(graph)
    graph.layers = _assign_layers(graph)
    graph.reachable = _reachable(graph)
    return graph


def _cycle_nodes(graph: MissionGraph) -> tuple[set[str], set[str]]:
    """Элементы на цикле: все и только те, где цикл закрыт не через ``loop``."""
    state: dict[str, int] = {}
    nodes: set[str] = set()
    illegal: set[str] = set()

    def visit(node_id: str, path: list[str]) -> None:
        state[node_id] = 1
        path.append(node_id)
        node = graph.by_id.get(node_id)
        for target in graph.successors.get(node_id, ()):
            if state.get(target) == 1:
                if target in path:
                    segment = path[path.index(target) :]
                    nodes.update(segment)
                    if node is not None and node.type != "loop":
                        illegal.update(segment)
            elif state.get(target, 0) == 0:
                visit(target, path)
        path.pop()
        state[node_id] = 2

    for element in graph.elements:
        if state.get(element.id, 0) == 0:
            visit(element.id, [])
    return nodes, illegal


def _topological_order(graph: MissionGraph) -> list[str]:
    """Порядок обхода: предшественники раньше потомков, циклы не блокируют."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        for target in graph.successors.get(node_id, ()):
            visit(target)
        order.append(node_id)

    for element in graph.elements:
        visit(element.id)
    order.reverse()
    return order


def _assign_layers(graph: MissionGraph) -> dict[str, int]:
    """Слой элемента: на единицу глубже самого дальнего предшественника."""
    layers = {element.id: 0 for element in graph.elements}
    if graph.start is not None:
        layers[graph.start] = 0
    for _ in range(max(1, len(graph.elements) * _LAYER_PASSES)):
        changed = False
        for element in graph.elements:
            parents = graph.parents.get(element.id, [])
            if not parents:
                continue
            candidate = max(layers.get(name, 0) for name in parents) + 1
            if candidate > layers[element.id] and candidate < len(graph.elements):
                layers[element.id] = candidate
                changed = True
        if not changed:
            break
    return layers


def _reachable(graph: MissionGraph) -> set[str]:
    """Элементы, достижимые из старта по переходам."""
    if graph.start is None:
        return set()
    reached: set[str] = set()
    stack = [graph.start]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        stack.extend(graph.successors.get(node_id, ()))
    return reached
