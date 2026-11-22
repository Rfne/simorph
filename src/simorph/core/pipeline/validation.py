"""Валидатор конвейера: порты, циклы, параметры и бюджеты (T8-01, G8-01).

Проверки идут по одному графу и возвращают замечания, а не исключения:
редактор показывает все проблемы сразу, а CLI — код 1. Диагностика
несовместимого ребра называет оба порта и их типы, поэтому пользователь
видит не только «нельзя», но и что именно не сходится.

Классы проверок:

1. **ссылки и типы** — узел существует, тип известен;
2. **порты** — порт объявлен узлом, типы выхода и входа совместимы;
3. **граф** — нет циклов;
4. **параметры** — обязательные параметры типа заполнены;
5. **подключение** — у каждого входа есть ребро, у каждого выхода — потребитель
   (предупреждения, а не ошибки: недособранный вариант можно сохранить);
6. **бюджет** — план критического пути не выходит за целевой ориентир.
"""

from __future__ import annotations

from collections.abc import Sequence

from simorph.core.issues import Issue, IssueCode, Severity, issue
from simorph.core.models.pipeline import PipelineSection
from simorph.core.pipeline.graph import build_pipeline_graph
from simorph.core.pipeline.nodes import NODE_SPECS, node_input_kind, node_output_kind, node_spec
from simorph.core.pipeline.ports import accepts, parse_port_ref, port_title

__all__ = [
    "NEURAL_BUDGET_MS",
    "SPARSE_BUDGET_MS",
    "can_run",
    "pipeline_issues",
]

#: Целевой бюджет разреженного вычислительного контура на кадр, мс (G8-02).
SPARSE_BUDGET_MS: float = 5.0

#: Целевой p95 инференса на PC, мс (G8-03).
NEURAL_BUDGET_MS: float = 400.0

_KNOWN_TYPES = frozenset(spec.type for spec in NODE_SPECS)


def pipeline_issues(pipeline: PipelineSection, *, prefix: str) -> list[Issue]:
    """Все замечания по одному конвейеру."""
    issues: list[Issue] = []
    issues += _duplicates(
        [(f"{prefix}.nodes.{index}.id", node.id) for index, node in enumerate(pipeline.nodes)]
    )
    issues += _type_issues(pipeline, prefix)
    issues += _edge_issues(pipeline, prefix)
    issues += _param_issues(pipeline, prefix)
    issues += _connection_issues(pipeline, prefix)
    issues += _cycle_issues(pipeline, prefix)
    issues += _budget_issues(pipeline, prefix)
    return issues


def can_run(pipeline: PipelineSection) -> bool:
    """Конвейер можно исполнять, если среди замечаний нет ошибок."""
    return not any(
        item.severity is Severity.ERROR for item in pipeline_issues(pipeline, prefix="pipeline")
    )


def _duplicates(pairs: Sequence[tuple[str, str]]) -> list[Issue]:
    seen: set[str] = set()
    issues: list[Issue] = []
    for path, value in pairs:
        if value in seen:
            issues.append(
                issue(IssueCode.DUPLICATE_ID, path, f"идентификатор {value!r} уже используется")
            )
        seen.add(value)
    return issues


def _type_issues(pipeline: PipelineSection, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    for index, node in enumerate(pipeline.nodes):
        if node.type in _KNOWN_TYPES:
            continue
        issues.append(
            issue(
                IssueCode.INVALID_VALUE,
                f"{prefix}.nodes.{index}.type",
                f"неизвестный тип узла {node.type!r}: допустимы {', '.join(sorted(_KNOWN_TYPES))}",
            )
        )
    return issues


def _edge_issues(pipeline: PipelineSection, prefix: str) -> list[Issue]:
    """Битые ссылки, неизвестные порты и несовместимые типы."""
    issues: list[Issue] = []
    by_id = {node.id: node for node in pipeline.nodes}
    seen_edges: set[tuple[str, str]] = set()

    for index, edge in enumerate(pipeline.edges):
        base = f"{prefix}.edges.{index}"
        pair = (edge.src, edge.dst)
        if pair in seen_edges:
            issues.append(
                issue(
                    IssueCode.DUPLICATE_ID,
                    base,
                    f"ребро {edge.src} → {edge.dst} уже существует",
                )
            )
        seen_edges.add(pair)

        try:
            src = parse_port_ref(edge.src)
            dst = parse_port_ref(edge.dst)
        except ValueError as exc:
            issues.append(issue(IssueCode.INVALID_VALUE, f"{base}.from", str(exc)))
            continue

        for field_name, ref in (("from", src), ("to", dst)):
            if ref.node not in by_id:
                issues.append(
                    issue(
                        IssueCode.DANGLING_REFERENCE,
                        f"{base}.{field_name}",
                        f"узел {ref.node!r} не найден в nodes",
                    )
                )

        src_node = by_id.get(src.node)
        dst_node = by_id.get(dst.node)
        if src_node is None or dst_node is None:
            continue
        if src_node.type not in _KNOWN_TYPES or dst_node.type not in _KNOWN_TYPES:
            continue

        src_kind = node_output_kind(src_node.type, src.port)
        if src_kind is None:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{base}.from",
                    f"у узла {src.node!r} ({src_node.type}) нет выхода {src.port!r}",
                )
            )
        dst_kind = node_input_kind(dst_node.type, dst.port)
        if dst_kind is None:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{base}.to",
                    f"у узла {dst.node!r} ({dst_node.type}) нет входа {dst.port!r}",
                )
            )
        if src_kind is None or dst_kind is None:
            continue
        if not accepts(dst_kind, src_kind):
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{base}.to",
                    f"порт {edge.src} ({port_title(src_kind)}) несовместим"
                    f" с {edge.dst} ({port_title(dst_kind)})",
                )
            )
    return issues


def _param_issues(pipeline: PipelineSection, prefix: str) -> list[Issue]:
    issues: list[Issue] = []
    for index, node in enumerate(pipeline.nodes):
        if node.type not in _KNOWN_TYPES:
            continue
        spec = node_spec(node.type)
        missing = [name for name in spec.required_params if name not in node.params]
        if missing:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"{prefix}.nodes.{index}.params",
                    f"узел {node.type!r} требует параметры: {', '.join(missing)}",
                )
            )
    return issues


def _connection_issues(pipeline: PipelineSection, prefix: str) -> list[Issue]:
    """Неподключённые входы и выходы — предупреждения."""
    issues: list[Issue] = []
    by_id = {node.id: node for node in pipeline.nodes}
    connected_inputs: dict[str, set[str]] = {node.id: set() for node in pipeline.nodes}
    connected_outputs: dict[str, set[str]] = {node.id: set() for node in pipeline.nodes}
    for edge in pipeline.edges:
        try:
            src = parse_port_ref(edge.src)
            dst = parse_port_ref(edge.dst)
        except ValueError:
            continue
        if src.node in by_id:
            connected_outputs[src.node].add(src.port)
        if dst.node in by_id:
            connected_inputs[dst.node].add(dst.port)

    for index, node in enumerate(pipeline.nodes):
        if node.type not in _KNOWN_TYPES:
            continue
        spec = node_spec(node.type)
        for port in spec.inputs:
            if port not in connected_inputs[node.id]:
                issues.append(
                    issue(
                        IssueCode.INVALID_VALUE,
                        f"{prefix}.nodes.{index}",
                        f"у узла {node.id!r} не подключён вход {port!r}",
                        severity=Severity.WARNING,
                    )
                )
        for port in spec.outputs:
            if port not in connected_outputs[node.id]:
                issues.append(
                    issue(
                        IssueCode.INVALID_VALUE,
                        f"{prefix}.nodes.{index}",
                        f"у узла {node.id!r} не подключён выход {port!r}",
                        severity=Severity.WARNING,
                    )
                )
    return issues


def _cycle_issues(pipeline: PipelineSection, prefix: str) -> list[Issue]:
    graph = build_pipeline_graph(pipeline)
    index_by_id = {node.id: index for index, node in enumerate(pipeline.nodes)}
    return [
        issue(
            IssueCode.INVALID_GRAPH,
            f"{prefix}.nodes.{index_by_id[name]}",
            "цикл в графе конвейера",
        )
        for name in sorted(graph.cycles, key=lambda value: index_by_id.get(value, 0))
    ]


def _budget_issues(pipeline: PipelineSection, prefix: str) -> list[Issue]:
    graph = build_pipeline_graph(pipeline)
    limit = SPARSE_BUDGET_MS if pipeline.kind == "computational" else NEURAL_BUDGET_MS
    if graph.critical_path_ms <= limit:
        return []
    return [
        issue(
            IssueCode.INVALID_VALUE,
            f"{prefix}.nodes",
            f"план критического пути {graph.critical_path_ms:.1f} мс"
            f" превышает ориентир {limit:.1f} мс",
            severity=Severity.WARNING,
        )
    ]
