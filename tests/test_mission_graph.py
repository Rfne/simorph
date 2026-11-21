"""Тесты графа миссии: порядок, слои, циклы, достижимость (T6-02, T6-06)."""

from __future__ import annotations

import pytest

from simorph.core.mission.graph import build_graph
from simorph.core.models.exercise import MissionElement


def _linear() -> list[MissionElement]:
    """Цепочка старт → точка → возврат."""
    return [
        MissionElement(id="start", type="start", next=["wp"]),
        MissionElement(id="wp", type="waypoint", pose=(10.0, 0.0, 5.0), next=["ret"]),
        MissionElement(id="ret", type="return_to_base"),
    ]


def _parallel() -> list[MissionElement]:
    """Старт → параллельно → две ветви → возврат."""
    return [
        MissionElement(id="start", type="start", next=["split"]),
        MissionElement(id="split", type="parallel", next=["a", "b"]),
        MissionElement(id="a", type="waypoint", pose=(10.0, 0.0, 5.0), next=["ret"]),
        MissionElement(id="b", type="waypoint", pose=(20.0, 0.0, 5.0), next=["ret"]),
        MissionElement(id="ret", type="return_to_base"),
    ]


@pytest.mark.unit
def test_linear_graph_is_valid() -> None:
    graph = build_graph(_linear())

    assert graph.valid is True
    assert graph.start == "start"
    assert graph.starts == ["start"]
    assert graph.order == ["start", "wp", "ret"]
    assert graph.dangling == []
    assert graph.duplicates == []
    assert graph.terminal_ids == ["ret"]


@pytest.mark.unit
def test_layers_grow_with_depth() -> None:
    graph = build_graph(_linear())

    assert graph.layers == {"start": 0, "wp": 1, "ret": 2}
    assert graph.layer_count == 3


@pytest.mark.unit
def test_parallel_branches_share_layer() -> None:
    graph = build_graph(_parallel())

    assert graph.layers["split"] == 1
    assert graph.layers["a"] == graph.layers["b"] == 2
    assert graph.layers["ret"] == 3
    assert graph.layer_count == 4


@pytest.mark.unit
def test_order_puts_predecessors_first() -> None:
    graph = build_graph(_parallel())
    position = {name: index for index, name in enumerate(graph.order)}

    assert position["start"] < position["split"]
    assert position["split"] < position["a"]
    assert position["split"] < position["b"]
    assert position["a"] < position["ret"]
    assert position["b"] < position["ret"]
    assert len(graph.order) == 5


@pytest.mark.unit
def test_cycle_via_loop_is_legal() -> None:
    graph = build_graph(
        [
            MissionElement(id="start", type="start", next=["loop"]),
            MissionElement(id="loop", type="loop", iterations=3, next=["start"]),
        ]
    )

    assert graph.cycles == {"start", "loop"}
    assert graph.illegal_cycles == set()
    assert graph.valid is True
    assert set(graph.order) == {"start", "loop"}


@pytest.mark.unit
def test_cycle_without_loop_is_illegal() -> None:
    graph = build_graph(
        [
            MissionElement(id="start", type="start", next=["wp"]),
            MissionElement(id="wp", type="waypoint", pose=(1.0, 2.0, 0.0), next=["start"]),
        ]
    )

    assert graph.cycles == {"start", "wp"}
    assert graph.illegal_cycles == {"start", "wp"}
    assert graph.valid is False


@pytest.mark.unit
def test_dangling_reference_is_recorded_not_raised() -> None:
    graph = build_graph(
        [
            MissionElement(id="start", type="start", next=["ghost"]),
        ]
    )

    assert graph.dangling == [("start", "ghost")]
    assert graph.successors["start"] == []
    assert graph.terminal_ids == ["start"]


@pytest.mark.unit
def test_duplicate_ids_are_recorded() -> None:
    graph = build_graph(
        [
            MissionElement(id="start", type="start", next=[]),
            MissionElement(id="start", type="return_to_base"),
        ]
    )

    assert graph.duplicates == ["start"]
    assert graph.valid is False
    assert len(graph.by_id) == 1


@pytest.mark.unit
def test_two_starts_are_not_valid() -> None:
    graph = build_graph(
        [
            MissionElement(id="s1", type="start", next=[]),
            MissionElement(id="s2", type="start", next=[]),
        ]
    )

    assert graph.starts == ["s1", "s2"]
    assert graph.start is None
    assert graph.valid is False
    assert graph.reachable == set()


@pytest.mark.unit
def test_unreachable_element_is_not_reachable() -> None:
    elements = [*_linear(), MissionElement(id="lost", type="return_to_base")]

    graph = build_graph(elements)

    assert graph.reachable == {"start", "wp", "ret"}
    assert "lost" not in graph.reachable
    assert "lost" in graph.order


@pytest.mark.unit
def test_path_to_returns_chain() -> None:
    graph = build_graph(_linear())

    assert graph.path_to("ret") == ["start", "wp", "ret"]
    assert graph.path_to("ghost") == []


@pytest.mark.unit
def test_path_to_without_start_is_empty() -> None:
    graph = build_graph([MissionElement(id="ret", type="return_to_base")])

    assert graph.path_to("ret") == []


@pytest.mark.unit
def test_empty_mission_graph() -> None:
    graph = build_graph([])

    assert graph.valid is False
    assert graph.layer_count == 0
    assert graph.order == []
    assert graph.terminal_ids == []


@pytest.mark.unit
def test_to_dict_contains_layout_data() -> None:
    payload = build_graph(_parallel()).to_dict()

    assert payload["start"] == "start"
    assert payload["elements"] == 5
    assert payload["layer_count"] == 4
    assert payload["terminal"] == ["ret"]
    assert payload["cycles"] == []
    assert payload["illegal_cycles"] == []
    assert payload["valid"] is True
    assert payload["layers"]["a"] == 2
