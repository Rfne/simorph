"""3D-предпросмотр агента без фотореализма (T5-06, FR-AGT-10, FR-AGT-11).

Предпросмотр переиспользует рендер мира: те же тела, камера и кадр.
Отличий два: цвета плоские (реализм выключен) и, кроме звеньев, рисуются
зоны обслуживания сенсоров и подсветка коллизий.

Коллизия здесь — пересечение габаритных параллелепипедов звеньев в мировых
координатах. Это дешёвая проверка, которая ловит грубые ошибки сборки
(колесо внутри корпуса, мачта сквозь камеру) до запуска физики.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from simorph.core.agent.kinematics import KinematicTree, Vec3, build_tree
from simorph.core.world.camera import CameraPose, CameraSpec, OrbitCamera
from simorph.core.world.layout import SceneLayout, SceneObject
from simorph.core.world.lighting import SunState
from simorph.core.world.primitives import Color, PrimitiveShape
from simorph.core.world.renderer import BulletRenderer, Frame

__all__ = [
    "AgentPreview",
    "PreviewStats",
    "collisions",
    "initial_camera",
    "preview_layout",
    "service_zones",
]

#: Цвета звеньев по типу: плоские, без материалов.
_LINK_COLORS: dict[str, Color] = {
    "box": (0.62, 0.64, 0.68, 1.0),
    "cylinder": (0.35, 0.55, 0.78, 1.0),
    "sphere": (0.85, 0.60, 0.30, 1.0),
    "mesh": (0.55, 0.45, 0.75, 1.0),
}

#: Форма примитива для каждого типа звена.
_LINK_SHAPES: dict[str, PrimitiveShape] = {
    "box": "box",
    "cylinder": "cylinder",
    "sphere": "sphere",
    "mesh": "box",
}

#: Цвет звена, участвующего в коллизии.
_COLLISION_COLOR: Color = (0.90, 0.25, 0.22, 1.0)

#: Цвет зоны обслуживания камеры и дальномера.
_CAMERA_ZONE_COLOR: Color = (0.30, 0.70, 0.45, 1.0)
_DISTANCE_ZONE_COLOR: Color = (0.25, 0.55, 0.85, 1.0)

#: Во сколько габаритов агента ограничивается радиус зоны в предпросмотре.
ZONE_EXTENT_FACTOR: float = 5.0

#: Минимальный радиус зоны, м.
MIN_ZONE_RADIUS: float = 1.0


@dataclass(slots=True)
class PreviewStats:
    """Статистика предпросмотра."""

    links: int = 0
    zones: int = 0
    collisions: list[tuple[str, str]] = field(default_factory=list)
    build_ms: float = 0.0
    bodies: int = 0
    status: str = "ready"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "links": self.links,
            "zones": self.zones,
            "collisions": [list(pair) for pair in self.collisions],
            "build_ms": round(self.build_ms, 3),
            "bodies": self.bodies,
            "status": self.status,
            "error": self.error,
        }


def _aabb(tree: KinematicTree, link_id: str) -> tuple[Vec3, Vec3]:
    link = tree.links[link_id]
    pose = tree.world_pose(link_id)
    half = tuple(max(0.0, value) / 2.0 for value in link.size_m)
    low = (pose[0] - half[0], pose[1] - half[1], pose[2] - half[2])
    high = (pose[0] + half[0], pose[1] + half[1], pose[2] + half[2])
    return low, high


def _overlap(left: tuple[Vec3, Vec3], right: tuple[Vec3, Vec3]) -> bool:
    """Пересечение двух AABB с допуском: касание гранью не считается."""
    tolerance = 1e-6
    return all(
        left[0][index] < right[1][index] - tolerance
        and right[0][index] < left[1][index] - tolerance
        for index in range(3)
    )


def collisions(tree: KinematicTree) -> list[tuple[str, str]]:
    """Пары звеньев, чьи габариты пересекаются."""
    pairs: list[tuple[str, str]] = []
    names = [name for name in tree.order if name in tree.links] or sorted(tree.links)
    boxes = {name: _aabb(tree, name) for name in names}
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            if _overlap(boxes[left], boxes[right]):
                pairs.append((left, right))
    return pairs


def service_zones(tree: KinematicTree, agent: Any) -> list[SceneObject]:
    """Плоские диски зон обслуживания сенсоров."""
    extent = max(tree.extent_m) if tree.links else 1.0
    limit = max(MIN_ZONE_RADIUS, extent * ZONE_EXTENT_FACTOR)
    zones: list[SceneObject] = []
    for sensor in agent.sensors:
        if sensor.type not in {"camera", "stereo_camera", "distance"}:
            continue
        if sensor.link is None or sensor.link not in tree.links:
            continue
        radius = min(float(sensor.range_m or agent.hw.tc_sen.detection_range_m), limit)
        radius = max(MIN_ZONE_RADIUS, radius)
        pose = tree.world_pose(sensor.link)
        color = _DISTANCE_ZONE_COLOR if sensor.type == "distance" else _CAMERA_ZONE_COLOR
        zones.append(
            SceneObject(
                id=f"ZONE-{sensor.id}",
                category="zone",
                shape="cylinder",
                pose=(pose[0], pose[1], max(0.01, pose[2] - 0.02)),
                size=(radius * 2.0, radius * 2.0, 0.04),
                color=color,
                asset="service_zone",
            )
        )
    return zones


def preview_layout(
    agent: Any,
    *,
    tree: KinematicTree | None = None,
    zones: bool = True,
    highlight: bool = True,
) -> tuple[SceneLayout, list[tuple[str, str]]]:
    """Раскладка предпросмотра: звенья, зоны и пары коллизий."""
    built = tree if tree is not None else build_tree(agent)
    pairs = collisions(built) if highlight else []
    colliding = {name for pair in pairs for name in pair}
    objects: list[SceneObject] = []
    for link_id in built.order or sorted(built.links):
        link = built.links[link_id]
        color = (
            _COLLISION_COLOR
            if link_id in colliding
            else _LINK_COLORS.get(link.type, _LINK_COLORS["box"])
        )
        objects.append(
            SceneObject(
                id=link_id,
                category="equipment",
                shape=_LINK_SHAPES.get(link.type, "box"),
                pose=built.world_pose(link_id),
                size=link.size_m,
                color=color,
                asset=f"link:{link.type}",
            )
        )
    if zones:
        objects.extend(service_zones(built, agent))
    return SceneLayout(objects=objects, seed=0), pairs


def initial_camera(agent: Any) -> OrbitCamera:
    """Камера, охватывающая агента целиком."""
    tree = build_tree(agent)
    extent = max(tree.extent_m) if tree.links else 1.0
    center = tree.center_of_mass
    return OrbitCamera(
        target=center,
        distance=max(2.0, extent * 3.0),
        yaw_deg=35.0,
        pitch_deg=-25.0,
    )


class AgentPreview:
    """Предпросмотр агента: тела, зоны и кадры."""

    def __init__(
        self,
        agent: Any,
        *,
        seed: int = 0,
        renderer: BulletRenderer | None = None,
    ) -> None:
        self._agent = agent
        self._seed = int(seed)
        self._renderer = renderer if renderer is not None else BulletRenderer()
        self._owns_renderer = renderer is None
        self._layout = SceneLayout(seed=self._seed)
        self._stats = PreviewStats()
        self._zones = True
        self._highlight = True

    # --- сборка ---------------------------------------------------------

    def rebuild(self, agent: Any | None = None, *, zones: bool | None = None) -> PreviewStats:
        """Пересобирает предпросмотр; сбой не выходит наружу."""
        if agent is not None:
            self._agent = agent
        if zones is not None:
            self._zones = bool(zones)
        started = time.perf_counter()
        try:
            layout, pairs = preview_layout(
                self._agent,
                zones=self._zones,
                highlight=self._highlight,
            )
            self._renderer.clear()
            for item in layout.objects:
                self._renderer.add_object(item)
            self._renderer.set_lighting(SunState(45.0, 60.0, 0.1), realism=False)
            self._layout = layout
            links = sum(1 for item in layout.objects if (item.asset or "").startswith("link:"))
            self._stats = PreviewStats(
                links=links,
                zones=sum(1 for item in layout.objects if item.asset == "service_zone"),
                collisions=pairs,
                bodies=self._renderer.body_count,
                status="ready",
            )
        except Exception as exc:  # предпросмотр не должен ронять окно
            self._stats = PreviewStats(status="interrupted", error=str(exc))
        self._stats.build_ms = (time.perf_counter() - started) * 1000.0
        return self._stats

    # --- состояние ------------------------------------------------------

    @property
    def agent(self) -> Any:
        return self._agent

    @property
    def layout(self) -> SceneLayout:
        return self._layout

    @property
    def stats(self) -> PreviewStats:
        return self._stats

    @property
    def zones_enabled(self) -> bool:
        return self._zones

    def set_zones(self, enabled: bool) -> None:
        self.rebuild(zones=bool(enabled))

    # --- кадры ----------------------------------------------------------

    def render(
        self,
        camera: OrbitCamera | CameraPose,
        spec: CameraSpec | None = None,
    ) -> Frame:
        """Кадр предпросмотра: плоские цвета, без реализма."""
        pose = camera.pose() if isinstance(camera, OrbitCamera) else camera
        target_spec = spec if spec is not None else CameraSpec(640, 480)
        return self._renderer.render(pose, target_spec)

    def bounds_radius(self) -> float:
        """Радиус, в который вписан агент с зонами: для камеры и тестов."""
        tree = build_tree(self._agent)
        extent = max(tree.extent_m) if tree.links else 1.0
        return math.hypot(extent, extent) / 2.0

    def close(self) -> None:
        if self._owns_renderer:
            self._renderer.close()

    def __enter__(self) -> AgentPreview:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
