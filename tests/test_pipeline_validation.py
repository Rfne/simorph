"""Тесты валидатора конвейера: порты, параметры, циклы и бюджеты (G8-01)."""

from __future__ import annotations

from simorph.core.factory import build_pipeline
from simorph.core.issues import IssueCode, Severity
from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.pipeline import (
    NEURAL_BUDGET_MS,
    SPARSE_BUDGET_MS,
    can_run,
    pipeline_issues,
)


def _edge(src: str, dst: str) -> PipelineEdge:
    return PipelineEdge.model_validate({"from": src, "to": dst})


def _section(
    nodes: list[PipelineNode],
    edges: list[PipelineEdge] | None = None,
    *,
    kind: str = "computational",
) -> PipelineSection:
    return PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-T",
        kind=kind,  # type: ignore[arg-type]
        nodes=nodes,
        edges=edges or [],
    )


def _errors(pipeline: PipelineSection) -> list[object]:
    return [
        item
        for item in pipeline_issues(pipeline, prefix="pipelines.0")
        if item.severity is Severity.ERROR
    ]


def _warnings(pipeline: PipelineSection) -> list[object]:
    return [
        item
        for item in pipeline_issues(pipeline, prefix="pipelines.0")
        if item.severity is Severity.WARNING
    ]


def test_demo_pipeline_is_clean() -> None:
    issues = pipeline_issues(build_pipeline(), prefix="pipelines.0")

    assert issues == []
    assert can_run(build_pipeline()) is True


def test_incompatible_ports_are_errors() -> None:
    pipeline = _section(
        [
            PipelineNode(id="src", type="source.frame", params={"camera": "cam"}),
            PipelineNode(id="det", type="sparse.detect"),
        ],
        [_edge("src.frame", "det.stereo")],
    )

    errors = _errors(pipeline)

    assert errors
    assert any("несовместим" in item.message for item in errors)  # type: ignore[attr-defined]
    assert can_run(pipeline) is False


def test_unknown_port_is_reported() -> None:
    pipeline = _section(
        [
            PipelineNode(id="src", type="source.frame", params={"camera": "cam"}),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        [_edge("src.nope", "sink.events")],
    )

    errors = _errors(pipeline)

    assert any("нет выхода 'nope'" in item.message for item in errors)  # type: ignore[attr-defined]


def test_dangling_reference_names_missing_node() -> None:
    pipeline = _section(
        [PipelineNode(id="src", type="source.frame", params={"camera": "cam"})],
        [_edge("src.frame", "ghost.events")],
    )

    issues = pipeline_issues(pipeline, prefix="pipelines.0")

    assert any(item.code is IssueCode.DANGLING_REFERENCE for item in issues)
    assert any("'ghost'" in item.message for item in issues)


def test_duplicate_node_and_edge_are_errors() -> None:
    nodes = [
        PipelineNode(id="src", type="source.frame", params={"camera": "cam"}),
        PipelineNode(id="src", type="source.frame", params={"camera": "cam"}),
        PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
    ]
    edge = _edge("src.frame", "sink.events")
    pipeline = _section(nodes, [edge, edge])

    issues = pipeline_issues(pipeline, prefix="pipelines.0")

    assert any(
        item.code is IssueCode.DUPLICATE_ID and "идентификатор" in item.message for item in issues
    )
    assert any("ребро" in item.message and "уже существует" in item.message for item in issues)


def test_unknown_type_is_error() -> None:
    pipeline = _section([PipelineNode(id="a", type="mystery.node")])

    errors = _errors(pipeline)

    assert errors
    assert any("неизвестный тип узла" in item.message for item in errors)  # type: ignore[attr-defined]


def test_missing_required_param_is_error() -> None:
    pipeline = _section([PipelineNode(id="src", type="source.frame")])

    errors = _errors(pipeline)

    assert any("требует параметры: camera" in item.message for item in errors)  # type: ignore[attr-defined]


def test_unconnected_ports_are_warnings_only() -> None:
    pipeline = _section(
        [
            PipelineNode(id="src", type="source.frame", params={"camera": "cam"}),
            PipelineNode(id="rect", type="rectify"),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ]
    )

    issues = pipeline_issues(pipeline, prefix="pipelines.0")

    assert _errors(pipeline) == []
    assert _warnings(pipeline)
    assert can_run(pipeline) is True
    assert any("не подключён вход 'stereo'" in item.message for item in issues)


def test_cycle_is_error() -> None:
    pipeline = _section(
        [
            PipelineNode(id="a", type="rectify"),
            PipelineNode(id="b", type="rectify"),
        ],
        [_edge("a.stereo", "b.stereo"), _edge("b.stereo", "a.stereo")],
    )

    errors = _errors(pipeline)

    assert len([item for item in errors if item.code is IssueCode.INVALID_GRAPH]) == 2
    assert can_run(pipeline) is False


def test_sparse_plan_over_target_is_warning() -> None:
    pipeline = _section(
        [
            PipelineNode(id="src", type="source.stereo", params={"camera": "cam"}),
            PipelineNode(id="rect", type="rectify", budget_ms=SPARSE_BUDGET_MS + 1.0),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        [_edge("src.stereo", "rect.stereo"), _edge("rect.stereo", "sink.events")],
    )

    warnings = _warnings(pipeline)

    assert _errors(pipeline) == []
    assert any("превышает ориентир 5.0 мс" in item.message for item in warnings)  # type: ignore[attr-defined]


def test_neural_plan_over_target_is_warning() -> None:
    pipeline = _section(
        [
            PipelineNode(id="src", type="source.frame", params={"camera": "cam"}),
            PipelineNode(id="inf", type="inference", budget_ms=NEURAL_BUDGET_MS + 50.0),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        [_edge("src.frame", "inf.frame"), _edge("inf.detections", "sink.events")],
        kind="neural",
    )

    warnings = _warnings(pipeline)

    assert _errors(pipeline) == []
    assert any("превышает ориентир 400.0 мс" in item.message for item in warnings)  # type: ignore[attr-defined]


def test_malformed_reference_is_error() -> None:
    pipeline = _section(
        [PipelineNode(id="src", type="source.frame", params={"camera": "cam"})],
        [PipelineEdge.model_construct(src="src", dst="bad")],
    )

    errors = _errors(pipeline)

    assert any(item.code is IssueCode.INVALID_VALUE for item in errors)
