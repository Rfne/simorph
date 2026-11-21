"""Тесты 3D-предпросмотра агента (T5-06, FR-AGT-10, FR-AGT-11)."""

from __future__ import annotations

import pytest

from simorph.core.agent import (
    AgentPreview,
    build_drone,
    build_rover,
    build_tree,
    collisions,
    initial_camera,
    preview_layout,
    service_zones,
)
from simorph.core.models.agent import AgentSection, Link
from simorph.core.schema_version import CURRENT_VERSION
from simorph.core.world import CameraSpec


class _BrokenRenderer:
    """Рендер, который падает при добавлении тела."""

    body_count = 0

    def clear(self) -> None:
        return None

    def add_object(self, _item: object) -> None:
        raise RuntimeError("boom")

    def set_lighting(self, *_args: object, **_kwargs: object) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.mark.unit
def test_preview_layout_has_links_and_zones() -> None:
    rover = build_rover()
    layout, pairs = preview_layout(rover)

    assert pairs == []
    links = [item for item in layout.objects if (item.asset or "").startswith("link:")]
    zones = [item for item in layout.objects if item.asset == "service_zone"]
    assert len(links) == len(rover.links) == 7
    assert len(zones) == 3  # стереокамера, камера и дальномер


@pytest.mark.unit
def test_zones_can_be_disabled() -> None:
    layout, _ = preview_layout(build_rover(), zones=False)

    assert all(item.asset != "service_zone" for item in layout.objects)


@pytest.mark.unit
def test_collisions_detect_overlapping_boxes() -> None:
    agent = AgentSection(
        schema_version=CURRENT_VERSION,
        agent_id="overlap",
        links=[
            Link(id="a", type="box", size_m=(1.0, 1.0, 1.0), mass_kg=1.0),
            Link(id="b", type="box", size_m=(1.0, 1.0, 1.0), mass_kg=1.0, pose=(0.2, 0.0, 0.0)),
        ],
    )

    assert collisions(build_tree(agent)) == [("a", "b")]


@pytest.mark.unit
def test_service_zones_follow_sensor_range() -> None:
    rover = build_rover()
    zones = service_zones(build_tree(rover), rover)

    assert len(zones) == 3
    lidar = next(item for item in zones if item.id == "ZONE-lidar")
    # Радиус зоны ограничен пятью габаритами агента: 30 м дальномера не влезают.
    assert lidar.size[0] == pytest.approx(16.0)


@pytest.mark.integration
def test_preview_builds_reference_agents() -> None:
    for agent in (build_rover(), build_drone()):
        with AgentPreview(agent) as preview:
            stats = preview.rebuild()
            assert stats.status == "ready"
            assert stats.error is None
            assert stats.links == len(agent.links)
            assert stats.zones > 0
            assert stats.collisions == []
            assert stats.bodies >= len(agent.links)
            assert stats.build_ms >= 0.0


@pytest.mark.integration
def test_preview_renders_frame_and_radius() -> None:
    preview = AgentPreview(build_rover())
    try:
        preview.rebuild()
        frame = preview.render(initial_camera(build_rover()), CameraSpec(width=64, height=48))
    finally:
        preview.close()

    assert frame.rgb.shape == (48, 64, 3)
    assert preview.bounds_radius() > 0.0


@pytest.mark.integration
def test_zones_toggle_rebuilds_layout() -> None:
    preview = AgentPreview(build_rover())
    try:
        preview.rebuild()
        assert preview.zones_enabled is True
        preview.set_zones(False)
        assert preview.zones_enabled is False
        assert preview.stats.zones == 0
        assert preview.stats.links == 7
    finally:
        preview.close()


@pytest.mark.integration
def test_renderer_failure_is_reported_not_raised() -> None:
    preview = AgentPreview(build_rover(), renderer=_BrokenRenderer())  # type: ignore[arg-type]

    stats = preview.rebuild()

    assert stats.status == "interrupted"
    assert "boom" in (stats.error or "")
    assert stats.to_dict()["status"] == "interrupted"


@pytest.mark.unit
def test_initial_camera_covers_agent() -> None:
    rover = build_rover()
    camera = initial_camera(rover)
    tree = build_tree(rover)

    assert camera.distance >= 2.0
    assert camera.target == pytest.approx(tree.center_of_mass)
    assert camera.yaw_deg == pytest.approx(35.0)
