"""Граф конвейера: порядок, слои, циклы и бюджет критического пути (T8-01).

Граф строится по узлам и рёбрам раздела ``pipeline`` и никогда не бросает
исключений: битые ссылки, неизвестные типы и циклы — это данные для
валидатора, а не повод уронить редактор. Порядок обхода детерминирован:
при равной готовности узлы идут в порядке объявления, поэтому раскладка
слоёв и отчёт воспроизводимы.

Критический путь считается по бюджетам узлов: это план, а не факт. Он
нужен редактору и отчёту, чтобы показать, какая ветвь задаёт стоимость
кадра и укладывается ли конвейер в целевой бюджет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.pipeline.nodes import NODE_SPECS, node_budget_ms
from simorph.core.pipeline.ports import parse_port_ref

__all__ = ["PipelineGraph", "build_pipeline_graph"]

_KNOWN_TYPES = frozenset(spec.type for spec in NODE_SPECS)


@dataclass(slots=True)
class PipelineGraph:
    """Результат сборки графа конвейера."""

    pipeline_id: str = ""
    kind: str = "computational"
    nodes: list[PipelineNode] = field(default_factory=list)
    edges: list[PipelineEdge] = field(default_factory=list)
    by_id: dict[str, PipelineNode] = field(default_factory=dict)
    successors: dict[str, list[str]] = field(default_factory=dict)
    predecessors: dict[str, list[str]] = field(default_factory=dict)
    outgoing: dict[str, list[PipelineEdge]] = field(default_factory=dict)
    incoming: dict[str, list[PipelineEdge]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    layers: dict[str, int] = field(default_factory=dict)
    cycles: set[str] = field(default_factory=set)
    dangling: list[tuple[str, str]] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    unknown_types: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    sinks: list[str] = field(default_factory=list)
    critical_path: list[str] = field(default_factory=list)
    critical_path_ms: float = 0.0

    @property
    def valid(self) -> bool:
        """Нет дублей, циклов, битых ссылок и неизвестных типов."""
        return not (self.duplicates or self.cycles or self.dangling or self.unknown_types)

    @property
    def layer_count(self) -> int:
        return (max(self.layers.values()) + 1) if self.layers else 0

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def budget_of(self, node_id: str) -> float:
        node = self.by_id.get(node_id)
        return node_budget_ms(node) if node is not None else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "kind": self.kind,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "order": list(self.order),
            "layers": dict(self.layers),
            "layer_count": self.layer_count,
            "sources": list(self.sources),
            "sinks": list(self.sinks),
            "cycles": sorted(self.cycles),
            "dangling": [list(pair) for pair in self.dangling],
            "duplicates": list(self.duplicates),
            "unknown_types": list(self.unknown_types),
            "critical_path": list(self.critical_path),
            "critical_path_ms": round(self.critical_path_ms, 3),
            "valid": self.valid,
        }


def build_pipeline_graph(pipeline: PipelineSection) -> PipelineGraph:
    """Собирает граф по разделу конвейера."""
    graph = PipelineGraph(
        pipeline_id=pipeline.pipeline_id,
        kind=pipeline.kind,
        nodes=list(pipeline.nodes),
        edges=list(pipeline.edges),
    )
    for node in pipeline.nodes:
        if node.id in graph.by_id:
            graph.duplicates.append(node.id)
            continue
        graph.by_id[node.id] = node
        if node.type not in _KNOWN_TYPES:
            graph.unknown_types.append(node.id)

    node_order = [node.id for node in pipeline.nodes]
    for node_id in graph.by_id:
        graph.successors.setdefault(node_id, [])
        graph.predecessors.setdefault(node_id, [])
        graph.outgoing.setdefault(node_id, [])
        graph.incoming.setdefault(node_id, [])

    for edge in pipeline.edges:
        try:
            src = parse_port_ref(edge.src)
            dst = parse_port_ref(edge.dst)
        except ValueError:
            graph.dangling.append((edge.src, edge.dst))
            continue
        if src.node not in graph.by_id or dst.node not in graph.by_id:
            missing = src.node if src.node not in graph.by_id else dst.node
            graph.dangling.append((missing, edge.src if missing == src.node else edge.dst))
            continue
        if dst.node not in graph.successors[src.node]:
            graph.successors[src.node].append(dst.node)
        if src.node not in graph.predecessors[dst.node]:
            graph.predecessors[dst.node].append(src.node)
        graph.outgoing[src.node].append(edge)
        graph.incoming[dst.node].append(edge)

    graph.cycles = _cycle_nodes(graph, node_order)
    graph.order = _topological_order(graph, node_order)
    graph.layers = _assign_layers(graph)
    graph.sources = _endpoints(graph, incoming=True)
    graph.sinks = _endpoints(graph, incoming=False)
    graph.critical_path, graph.critical_path_ms = _critical_path(graph)
    return graph


def _cycle_nodes(graph: PipelineGraph, node_order: list[str]) -> set[str]:
    """Узлы, входящие хотя бы в один цикл."""
    state: dict[str, int] = {}
    nodes: set[str] = set()

    def visit(node_id: str, path: list[str]) -> None:
        state[node_id] = 1
        path.append(node_id)
        for target in graph.successors.get(node_id, ()):
            if state.get(target) == 1:
                if target in path:
                    nodes.update(path[path.index(target) :])
            elif state.get(target, 0) == 0:
                visit(target, path)
        path.pop()
        state[node_id] = 2

    for node_id in node_order:
        if node_id in graph.by_id and state.get(node_id, 0) == 0:
            visit(node_id, [])
    return nodes


def _topological_order(graph: PipelineGraph, node_order: list[str]) -> list[str]:
    """Порядок обхода: предшественники раньше потомков, циклы не блокируют."""
    in_degree = {
        node_id: len(graph.predecessors.get(node_id, []))
        for node_id in node_order
        if node_id in graph.by_id
    }
    queue = [node_id for node_id in node_order if in_degree.get(node_id, 0) == 0]
    order: list[str] = []
    while queue:
        node_id = queue.pop(0)
        if node_id not in in_degree:
            continue
        order.append(node_id)
        in_degree.pop(node_id, None)
        for target in graph.successors.get(node_id, ()):
            if target not in in_degree:
                continue
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)
    # Узлы внутри цикла не попадают в порядок: добавляем их в конце,
    # чтобы раскладка и отчёт всё равно показали каждый узел.
    for node_id in node_order:
        if node_id in graph.by_id and node_id not in order:
            order.append(node_id)
    return order


def _assign_layers(graph: PipelineGraph) -> dict[str, int]:
    """Слой узла: на единицу глубже самого дальнего предшественника."""
    layers = dict.fromkeys(graph.order, 0)
    for node_id in graph.order:
        parents = [name for name in graph.predecessors.get(node_id, []) if name in layers]
        if parents:
            layers[node_id] = max(layers[name] for name in parents) + 1
    return layers


def _endpoints(graph: PipelineGraph, *, incoming: bool) -> list[str]:
    """Источники (без входов) или приёмники (без выходов)."""
    result: list[str] = []
    for node_id in graph.order:
        edges = graph.incoming.get(node_id, []) if incoming else graph.outgoing.get(node_id, [])
        if not edges:
            result.append(node_id)
    return result


def _critical_path(graph: PipelineGraph) -> tuple[list[str], float]:
    """Самый дорогой путь по бюджетам узлов."""
    if not graph.order:
        return [], 0.0
    position = {node_id: index for index, node_id in enumerate(graph.order)}
    best: dict[str, float] = {}
    previous: dict[str, str | None] = {}
    for node_id in graph.order:
        candidates = [
            (best[parent], position.get(parent, -1), parent)
            for parent in graph.predecessors.get(node_id, [])
            if parent in best
        ]
        if candidates:
            value, _index, parent = max(candidates)
            best[node_id] = value + graph.budget_of(node_id)
            previous[node_id] = parent
        else:
            best[node_id] = graph.budget_of(node_id)
            previous[node_id] = None
    last = max(
        graph.order,
        key=lambda name: (best.get(name, 0.0), position.get(name, -1)),
    )
    path: list[str] = []
    current: str | None = last
    seen: set[str] = set()
    while current is not None and current not in seen:
        path.append(current)
        seen.add(current)
        current = previous.get(current)
    path.reverse()
    return path, best.get(last, 0.0)
