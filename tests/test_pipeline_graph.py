"""Тесты графа конвейера: порядок, слои, циклы и критический путь (T8-01)."""

from __future__ import annotations

import pytest

from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.pipeline import PipelineGraph, build_pipeline_graph


def _edge(src: str, dst: str) -> PipelineEdge:
    return PipelineEdge.model_validate({"from": src, "to": dst})


def computational() -> PipelineSection:
    """Разреженный контур стерео: источник → … → приёмник событий."""
    return PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-CMP-01",
        kind="computational",
        nodes=[
            PipelineNode(id="src", type="source.stereo", params={"camera": "cam_stereo"}),
            PipelineNode(id="rect", type="rectify"),
            PipelineNode(id="det", type="sparse.detect"),
            PipelineNode(id="trk", type="sparse.track"),
            PipelineNode(id="sp", type="sparse.stereo"),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        edges=[
            _edge("src.stereo", "rect.stereo"),
            _edge("rect.stereo", "det.stereo"),
            _edge("det.features", "trk.features"),
            _edge("trk.tracks", "sp.tracks"),
            _edge("sp.points", "sink.events"),
        ],
    )


def neural() -> PipelineSection:
    return PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-NN-01",
        kind="neural",
        nodes=[
            PipelineNode(id="src", type="source.frame", params={"camera": "cam_stereo"}),
            PipelineNode(id="pre", type="preprocess"),
            PipelineNode(id="inf", type="inference"),
            PipelineNode(id="nms", type="postprocess.nms"),
            PipelineNode(id="sink", type="sink.events", params={"topic": "events"}),
        ],
        edges=[
            _edge("src.frame", "pre.frame"),
            _edge("pre.frame", "inf.frame"),
            _edge("inf.detections", "nms.detections"),
            _edge("nms.detections", "sink.events"),
        ],
    )


def test_computational_graph_order_layers_and_endpoints() -> None:
    graph = build_pipeline_graph(computational())

    assert graph.valid is True
    assert graph.order == ["src", "rect", "det", "trk", "sp", "sink"]
    assert graph.layers == {"src": 0, "rect": 1, "det": 2, "trk": 3, "sp": 4, "sink": 5}
    assert graph.layer_count == 6
    assert graph.sources == ["src"]
    assert graph.sinks == ["sink"]
    assert graph.edge_count == 5


def test_critical_path_uses_node_budgets() -> None:
    graph = build_pipeline_graph(computational())

    assert graph.critical_path == ["src", "rect", "det", "trk", "sp", "sink"]
    assert graph.critical_path_ms == pytest.approx(0.2 + 0.8 + 1.2 + 1.0 + 1.5 + 0.1)


def test_neural_plan_fits_neural_budget() -> None:
    graph = build_pipeline_graph(neural())

    assert graph.critical_path_ms == pytest.approx(0.2 + 2.0 + 380.0 + 5.0 + 0.1)
    assert graph.critical_path_ms <= 400.0


def test_to_dict_is_stable_and_rounded() -> None:
    graph = build_pipeline_graph(neural())
    payload = graph.to_dict()

    assert payload["pipeline_id"] == "PIPE-NN-01"
    assert payload["nodes"] == 5
    assert payload["edges"] == 4
    assert payload["valid"] is True
    assert payload["cycles"] == []
    assert payload["critical_path_ms"] == pytest.approx(387.3)
    assert payload["layers"]["sink"] == 4


def test_graph_is_deterministic() -> None:
    first = build_pipeline_graph(computational())
    second = build_pipeline_graph(computational())

    assert first.order == second.order
    assert first.layers == second.layers
    assert first.critical_path == second.critical_path


def test_cycle_is_detected_and_does_not_block_order() -> None:
    pipeline = PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-CYC",
        nodes=[
            PipelineNode(id="a", type="rectify"),
            PipelineNode(id="b", type="rectify"),
        ],
        edges=[_edge("a.stereo", "b.stereo"), _edge("b.stereo", "a.stereo")],
    )

    graph = build_pipeline_graph(pipeline)

    assert graph.cycles == {"a", "b"}
    assert graph.valid is False
    assert sorted(graph.order) == ["a", "b"]


def test_dangling_edge_is_reported() -> None:
    pipeline = PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-DAN",
        nodes=[PipelineNode(id="src", type="source.frame", params={"camera": "cam"})],
        edges=[_edge("src.frame", "ghost.events"), _edge("src.frame", "bad")],
    )

    graph = build_pipeline_graph(pipeline)

    assert graph.valid is False
    assert ("ghost", "ghost.events") in graph.dangling
    assert ("src.frame", "bad") in graph.dangling


def test_duplicates_and_unknown_types_are_reported() -> None:
    pipeline = PipelineSection(
        schema_version="1.0",
        pipeline_id="PIPE-DUP",
        nodes=[
            PipelineNode(id="a", type="source.frame", params={"camera": "cam"}),
            PipelineNode(id="a", type="source.frame", params={"camera": "cam"}),
            PipelineNode(id="b", type="mystery.node"),
        ],
    )

    graph = build_pipeline_graph(pipeline)

    assert graph.duplicates == ["a"]
    assert graph.unknown_types == ["b"]
    assert graph.valid is False
    assert "a" in graph.by_id


def test_empty_graph_has_zero_layers() -> None:
    graph = PipelineGraph()

    assert graph.order == []
    assert graph.layer_count == 0
    assert graph.valid is True
    assert graph.budget_of("нет") == 0.0
