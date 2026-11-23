"""Источник датасета: детерминированная сцена и её кадры (T10-01).

Источник строит раскладку из seed сцены и рендерит кадры через ту же сцену
PyBullet, что и остальное приложение. Разметка берётся из маски кадра:
рамка объекта — это охватывающий прямоугольник его пикселей, поэтому
«разметка совпадает с кадром» выполняется по построению, а не проверкой
после факта.

Ключевые точки — восемь углов габарита, спроецированные камерой. Они
хранятся для полноты разметки (WP T10-01) и не участвуют в обучении
головы детекции.

Сцена пересобирается на каждый seed заново: два вызова с одним seed дают
побайтово те же кадры, и это проверяется тестом воспроизводимости.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Final

import numpy as np

from simorph.core.dataset.schema import (
    CLASS_NAMES,
    Box,
    DatasetConfig,
    DatasetFrame,
    mask_class_indices,
)
from simorph.core.factory import new_project
from simorph.core.models.world import WorldSection
from simorph.core.world.camera import CameraPose, CameraSpec
from simorph.core.world.ground_truth import bbox_corners, project_point
from simorph.core.world.layout import SceneLayout, SceneObject
from simorph.core.world.primitives import Color, PrimitiveShape
from simorph.core.world.renderer import Frame
from simorph.core.world.scene import Scene

__all__ = [
    "CLASS_STYLES",
    "FRAME_STEP_NS",
    "SCENE_SLOTS",
    "SyntheticSceneSource",
    "scene_seed",
]

#: Шаг метки времени кадра: 30 кадров в секунду.
FRAME_STEP_NS: Final = 33_333_333

#: Решётка позиций объектов: шаг 6 м, область 5x5.
SCENE_SLOTS: Final[tuple[tuple[float, float], ...]] = tuple(
    (column * 6.0, row * 6.0) for column in range(-2, 3) for row in range(-2, 3)
)

#: Оформление класса: форма, габариты и цвет. Цвета различимы намеренно:
#: закрытый набор классов должен быть отделим по внешнему виду, иначе
#: метрика гейта измеряет шум рендера, а не качество головы.
CLASS_STYLES: Final[dict[str, tuple[PrimitiveShape, tuple[float, float, float], Color]]] = {
    "tank": ("cylinder", (2.4, 2.4, 4.0), (0.88, 0.74, 0.16, 1.0)),
    "pump": ("box", (2.2, 1.8, 1.4), (0.78, 0.20, 0.16, 1.0)),
    "column": ("cylinder", (1.2, 1.2, 6.0), (0.16, 0.32, 0.80, 1.0)),
}

#: Цвет грунта: фон, на котором объекты отделимы по цвету.
TERRAIN_COLOR: Final[Color] = (0.34, 0.42, 0.28, 1.0)


def scene_seed(base_seed: int, scene_index: int) -> int:
    """Seed сцены: разные сцены не совпадают и не зависят от порядка кадров."""
    return base_seed * 1000 + scene_index


def build_scene_layout(seed: int) -> SceneLayout:
    """Раскладка сцены по seed: грунт и 6…9 объектов закрытых классов."""
    rng = random.Random(seed)
    objects: list[SceneObject] = [
        SceneObject(
            id="terrain",
            category="terrain",
            shape="box",
            pose=(0.0, 0.0, -0.5),
            size=(300.0, 300.0, 1.0),
            color=TERRAIN_COLOR,
        )
    ]
    slots = list(SCENE_SLOTS)
    rng.shuffle(slots)
    count = rng.randint(6, 9)
    for index, (x, y) in enumerate(slots[:count]):
        # Классы чередуются по кругу: иначе редкий и мелкий класс почти
        # не попадает в кадры, и метрика гейта измеряет перекос выборки.
        label = CLASS_NAMES[index % len(CLASS_NAMES)]
        shape, size, color = CLASS_STYLES[label]
        objects.append(
            SceneObject(
                id=f"{label.upper()}-{index:02d}",
                category="equipment",
                shape=shape,
                pose=(
                    x + rng.uniform(-1.0, 1.0),
                    y + rng.uniform(-1.0, 1.0),
                    size[2] / 2.0,
                ),
                size=size,
                color=color,
            )
        )
    return SceneLayout(seed=seed, objects=objects)


@dataclass(slots=True)
class SyntheticSceneSource:
    """Сцена-источник: рендерит кадры и разметку по seed и номеру кадра."""

    config: DatasetConfig
    world: WorldSection | None = None
    _cached_world: WorldSection | None = field(default=None, init=False, repr=False)

    def _world(self) -> WorldSection:
        if self.world is not None:
            return self.world
        if self._cached_world is None:
            self._cached_world = new_project("dataset", seed=self.config.seed).world
        return self._cached_world

    def render_scene(self, scene_index: int) -> list[DatasetFrame]:
        """Кадры одной сцены: раскладка, рендер, маски и разметка."""
        seed = scene_seed(self.config.seed, scene_index)
        layout = build_scene_layout(seed)
        by_id = {item.id: item for item in layout.objects}
        spec = CameraSpec(
            width=self.config.width,
            height=self.config.height,
            fov_deg=self.config.fov_deg,
        )
        rng = random.Random(seed * 31 + 7)
        frames: list[DatasetFrame] = []
        scene = Scene(self._world(), layout=layout)
        try:
            scene.build()
            for index in range(self.config.frames_per_scene):
                pose = self._pose(rng)
                frame = scene.render_pose(pose, spec)
                boxes, keypoints = self._annotate(frame, by_id, pose, spec)
                if len(boxes) < 2:
                    continue
                frames.append(
                    DatasetFrame(
                        frame_id=0,
                        scene_id=seed,
                        t_ns=index * FRAME_STEP_NS,
                        image=frame.rgb.copy(),
                        boxes=boxes,
                        mask=self._class_mask(frame.mask, frame.body_labels),
                        keypoints=keypoints,
                        pose=pose.to_dict(),
                        spec=spec.to_dict(),
                    )
                )
        finally:
            scene.close()
        return frames

    def _pose(self, rng: random.Random) -> CameraPose:
        """Поза сенсора: облёт сцены на высоте 3…6,5 м, взгляд в центр."""
        azimuth = math.radians(rng.uniform(0.0, 360.0))
        radius = rng.uniform(9.0, 13.0)
        return CameraPose(
            eye=(
                radius * math.cos(azimuth),
                radius * math.sin(azimuth),
                rng.uniform(3.0, 6.5),
            ),
            target=(rng.uniform(-2.0, 2.0), rng.uniform(-2.0, 2.0), 1.2),
        )

    def _annotate(
        self,
        frame: Frame,
        by_id: dict[str, SceneObject],
        pose: CameraPose,
        spec: CameraSpec,
    ) -> tuple[list[Box], dict[str, list[tuple[float, float, float]]]]:
        """Рамки по маске и ключевые точки по геометрии."""
        boxes: list[Box] = []
        keypoints: dict[str, list[tuple[float, float, float]]] = {}
        for body, label in sorted(frame.body_labels.items()):
            item = by_id.get(label)
            if item is None or item.category == "terrain":
                continue
            rows, columns = np.where(frame.mask == body)
            if rows.size < self.config.min_mask_px:
                continue
            x1 = float(columns.min())
            x2 = float(columns.max())
            y1 = float(rows.min())
            y2 = float(rows.max())
            if x2 - x1 < self.config.min_box_px or y2 - y1 < self.config.min_box_px:
                continue
            class_name = label.split("-")[0].lower()
            if class_name not in CLASS_NAMES:
                continue
            points: list[tuple[float, float, float]] = []
            projected_box = self._projected_box(item, pose, spec)
            for corner in bbox_corners(item):
                projected = project_point(corner, pose, spec)
                if projected is None:
                    continue
                u, v, depth = projected
                if 0.0 <= u < spec.width and 0.0 <= v < spec.height:
                    points.append((u, v, depth))
            if not self._is_well_visible(projected_box, (x1, y1, x2, y2), spec):
                # Обрезанный рамкой или перекрытый объект даёт рамку «видимой
                # части»: детектор, нашедший объект целиком, не совпадёт с ней
                # по IoU. Такие экземпляры в разметку не попадают.
                continue
            boxes.append(Box(label=class_name, x1=x1, y1=y1, x2=x2, y2=y2))
            if points:
                keypoints[label] = points
        return boxes, keypoints

    @staticmethod
    def _projected_box(
        item: SceneObject,
        pose: CameraPose,
        spec: CameraSpec,
    ) -> tuple[float, float, float, float] | None:
        """Габаритный прямоугольник объекта в пикселях по геометрии."""
        projected = [project_point(corner, pose, spec) for corner in bbox_corners(item)]
        visible = [point for point in projected if point is not None]
        if len(visible) < 4:
            return None
        us = [point[0] for point in visible]
        vs = [point[1] for point in visible]
        return (min(us), min(vs), max(us), max(vs))

    def _is_well_visible(
        self,
        projected: tuple[float, float, float, float] | None,
        mask_box: tuple[float, float, float, float],
        spec: CameraSpec,
    ) -> bool:
        """Объект виден не меньше порога от своей проекции в кадре.

        Проекция обрезается рамкой кадра до сравнения: объект на краю
        кадра виден не целиком, но это не перекрытие — его рамка в кадре
        и разметка совпадают. Отбрасываются именно перекрытые объекты,
        у которых маска заметно меньше проекции.
        """
        if projected is None:
            return False
        px1 = max(projected[0], 0.0)
        py1 = max(projected[1], 0.0)
        px2 = min(projected[2], float(spec.width))
        py2 = min(projected[3], float(spec.height))
        projected_area = (px2 - px1) * (py2 - py1)
        if projected_area <= 0.0:
            return False
        mask_area = (mask_box[2] - mask_box[0]) * (mask_box[3] - mask_box[1])
        return mask_area / projected_area >= self.config.min_visibility

    @staticmethod
    def _class_mask(mask: np.ndarray, labels: dict[int, str]) -> np.ndarray:
        """Маска в индексах классов: 0 — фон, 1…N — класс."""
        result = np.zeros(mask.shape, dtype=np.uint8)
        for body, index in mask_class_indices(labels).items():
            result[mask == body] = index
        return result
