"""Тесты кинематического дерева агента (T5-01, FR-AGT-02, FR-AGT-05)."""

from __future__ import annotations

import math

import pytest

from simorph.core.agent import (
    GRAVITY,
    build_drone,
    build_rover,
    build_tree,
    link_inertia,
    link_volume,
)
from simorph.core.models.agent import AgentSection, Joint, Link
from simorph.core.schema_version import CURRENT_VERSION


def _agent(links: list[Link], joints: list[Joint]) -> AgentSection:
    return AgentSection(schema_version=CURRENT_VERSION, agent_id="tree", links=links, joints=joints)


def _link(link_id: str, pose: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Link:
    return Link(id=link_id, type="box", size_m=(0.1, 0.1, 0.1), mass_kg=1.0, pose=pose)


def _joint(joint_id: str, parent: str, child: str) -> Joint:
    return Joint(id=joint_id, parent=parent, child=child, type="fixed")


@pytest.mark.unit
def test_rover_tree_is_valid_and_ordered() -> None:
    rover = build_rover()
    tree = build_tree(rover)

    assert tree.valid
    assert tree.errors == []
    assert tree.root == "base"
    assert tree.order[0] == "base"
    assert len(tree.order) == len(rover.links) == 7
    assert tree.depth("wheel_fl") == 1
    assert tree.depth("base") == 0


@pytest.mark.unit
def test_drone_tree_is_valid() -> None:
    tree = build_tree(build_drone())

    assert tree.valid
    assert tree.root == "body"
    assert tree.children["cam_pan"] == ["cam_tilt"]
    assert tree.parents["cam_tilt"] == "cam_pan"


@pytest.mark.unit
def test_world_pose_sums_chain() -> None:
    tree = build_tree(build_rover())

    assert tree.world_pose("base") == pytest.approx((0.0, 0.0, 0.25))
    assert tree.world_pose("wheel_fl") == pytest.approx((0.7, 0.45, 0.10))
    assert tree.world_pose("mast") == pytest.approx((0.0, 0.0, 1.15))


@pytest.mark.unit
def test_mass_weight_and_inertia() -> None:
    tree = build_tree(build_rover())

    assert tree.total_mass_kg == pytest.approx(54.4)
    assert tree.weight_n == pytest.approx(54.4 * GRAVITY)
    inertia = tree.inertia_kg_m2
    assert all(value > 0.0 for value in inertia)
    assert inertia[0] < inertia[2]  # ровер длиннее, чем выше
    assert all(math.isfinite(value) for value in tree.center_of_mass)
    extent = tree.extent_m
    assert extent[0] == pytest.approx(1.6, abs=1e-6)
    assert extent[1] == pytest.approx(1.1, abs=1e-6)


@pytest.mark.unit
def test_single_link_center_of_mass_is_own_pose() -> None:
    link = _link("a", (1.0, 2.0, 3.0))
    tree = build_tree(_agent([link], []))

    assert tree.valid
    assert tree.center_of_mass == pytest.approx((1.0, 2.0, 3.0))
    assert tree.inertia_kg_m2 == pytest.approx(link_inertia(link))


@pytest.mark.unit
def test_two_roots_reported() -> None:
    tree = build_tree(_agent([_link("a"), _link("b")], []))

    assert not tree.valid
    assert tree.root is None
    assert any("несколько корней" in message for message in tree.errors)


@pytest.mark.unit
def test_cycle_reported() -> None:
    tree = build_tree(
        _agent(
            [_link("a"), _link("b")],
            [_joint("j1", "a", "b"), _joint("j2", "b", "a")],
        )
    )

    assert not tree.valid
    assert any("цикл" in message for message in tree.errors)


@pytest.mark.unit
def test_two_parents_reported() -> None:
    tree = build_tree(
        _agent(
            [_link("a"), _link("b"), _link("c")],
            [_joint("j1", "a", "b"), _joint("j2", "a", "c"), _joint("j3", "b", "c")],
        )
    )

    assert not tree.valid
    assert any("два родителя" in message for message in tree.errors)


@pytest.mark.unit
def test_missing_parent_and_unreachable_reported() -> None:
    missing = build_tree(_agent([_link("a")], [_joint("j", "ghost", "a")]))
    assert any("не найден" in message for message in missing.errors)

    unreachable = build_tree(
        _agent(
            [_link("a"), _link("b"), _link("c"), _link("d")],
            [_joint("j1", "a", "b"), _joint("j2", "c", "d"), _joint("j3", "d", "c")],
        )
    )
    assert any("недостижимы" in message for message in unreachable.errors)


@pytest.mark.unit
def test_empty_agent_reported() -> None:
    tree = build_tree(_agent([], []))

    assert not tree.valid
    assert "у агента нет звеньев" in tree.errors
    assert tree.total_mass_kg == 0.0
    assert tree.center_of_mass == (0.0, 0.0, 0.0)
    assert tree.inertia_kg_m2 == (0.0, 0.0, 0.0)


@pytest.mark.unit
def test_geometry_helpers() -> None:
    box = Link(id="box", type="box", size_m=(2.0, 4.0, 6.0), mass_kg=12.0)
    assert link_volume(box) == pytest.approx(48.0)
    assert link_inertia(box) == pytest.approx((52.0, 40.0, 20.0))

    sphere = Link(id="ball", type="sphere", size_m=(2.0, 2.0, 2.0), mass_kg=3.0)
    assert link_volume(sphere) == pytest.approx(4.0 / 3.0 * math.pi)
    assert link_inertia(sphere) == pytest.approx((1.2, 1.2, 1.2))

    cylinder = Link(id="drum", type="cylinder", size_m=(2.0, 2.0, 4.0), mass_kg=6.0)
    assert link_volume(cylinder) == pytest.approx(math.pi * 4.0)
    assert link_inertia(cylinder) == pytest.approx((9.5, 9.5, 3.0))


@pytest.mark.unit
def test_to_dict_is_json_ready() -> None:
    payload = build_tree(build_drone()).to_dict()

    assert payload["root"] == "body"
    assert payload["links"] == 7
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert len(payload["inertia_kg_m2"]) == 3
