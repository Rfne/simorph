"""Ground truth сцены: глубина, маски, позы и ключевые точки (T4-05, FR-WLD-06).

Ground truth считается из двух независимых источников и сверяется:

- глубина и маска приходят из рендера (``renderer``);
- поза, габариты и ключевые точки — из раскладки, через проекцию камеры
  (``project_point``).

Именно поэтому проверка «глубина совпадает с геометрией» осмысленна:
ожидаемое значение считается по геометрии, а не берётся из того же буфера.

Маски подписаны идентификаторами объектов, а не внутренними номерами тел
PyBullet: пользователю нужен ``B-Z-01-02``, а не ``17``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

from simorph.core.world.camera import CameraPose, CameraSpec, Vec3
from simorph.core.world.layout import SceneObject

__all__ = [
    "GroundTruth",
    "bbox_corners",
    "project_point",
    "projection_matrix",
    "surface_depth",
    "view_matrix",
]

#: Допуск согласованности глубины и геометрии (G4-03).
DEPTH_TOLERANCE: Final = 0.01


@dataclass(slots=True)
class GroundTruth:
    """Снимок истины: глубина, маски, позы и ключевые точки."""

    depth: np.ndarray
    mask: np.ndarray
    labels: dict[int, str]
    poses: dict[str, Vec3] = field(default_factory=dict)
    keypoints: dict[str, list[Vec3]] = field(default_factory=dict)
    spec: CameraSpec | None = None

    @property
    def width(self) -> int:
        return int(self.depth.shape[1])

    @property
    def height(self) -> int:
        return int(self.depth.shape[0])

    def depth_at(self, u: int, v: int) -> float:
        """Глубина в пикселе, м."""
        return float(self.depth[int(v), int(u)])

    def label_at(self, u: int, v: int) -> str | None:
        """Идентификатор объекта в пикселе или ``None`` для фона."""
        body = int(self.mask[int(v), int(u)])
        return self.labels.get(body)

    def visible_labels(self) -> list[str]:
        """Идентификаторы объектов, видимых хотя бы одним пикселем."""
        found = {self.labels.get(int(value)) for value in np.unique(self.mask)}
        return sorted(label for label in found if label is not None)

    def mask_of(self, label: str) -> np.ndarray:
        """Булева маска объекта по идентификатору."""
        bodies = [body for body, name in self.labels.items() if name == label]
        if not bodies:
            return np.zeros_like(self.mask, dtype=bool)
        return np.isin(self.mask, bodies)

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "depth_min": round(float(np.min(self.depth)), 3),
            "depth_max": round(float(np.max(self.depth)), 3),
            "visible": self.visible_labels(),
            "objects": len(self.poses),
            "keypoints": {name: len(points) for name, points in self.keypoints.items()},
        }


def view_matrix(pose: CameraPose) -> np.ndarray:
    """Матрица перехода «мир → камера» (камера смотрит вдоль ``-z``)."""
    forward = np.array(pose.forward, dtype=np.float64)
    right = np.array(pose.right, dtype=np.float64)
    up = np.cross(right, forward)
    eye = np.array(pose.eye, dtype=np.float64)
    matrix = np.eye(4, dtype=np.float64)
    matrix[0, :3] = right
    matrix[1, :3] = up
    matrix[2, :3] = -forward
    matrix[0, 3] = -float(right @ eye)
    matrix[1, 3] = -float(up @ eye)
    matrix[2, 3] = float(forward @ eye)
    return matrix


def projection_matrix(spec: CameraSpec) -> np.ndarray:
    """Перспективная матрица проекции OpenGL-совместимого вида."""
    aspect = spec.aspect
    f = 1.0 / math.tan(math.radians(spec.fov_deg) / 2.0)
    matrix = np.zeros((4, 4), dtype=np.float64)
    matrix[0, 0] = f / aspect
    matrix[1, 1] = f
    matrix[2, 2] = (spec.far + spec.near) / (spec.near - spec.far)
    matrix[2, 3] = (2.0 * spec.far * spec.near) / (spec.near - spec.far)
    matrix[3, 2] = -1.0
    return matrix


def project_point(
    point: Vec3,
    pose: CameraPose,
    spec: CameraSpec,
) -> tuple[float, float, float] | None:
    """Пиксель и глубина точки; ``None``, если точка за камерой или вне кадра."""
    view = view_matrix(pose)
    projection = projection_matrix(spec)
    vector = np.array([point[0], point[1], point[2], 1.0], dtype=np.float64)
    camera = view @ vector
    clip = projection @ camera
    if abs(clip[3]) < 1e-9 or clip[3] <= 0.0:
        return None
    ndc_x = clip[0] / clip[3]
    ndc_y = clip[1] / clip[3]
    depth = -float(camera[2])
    if depth <= 0.0:
        return None
    u = (ndc_x + 1.0) * 0.5 * spec.width
    v = (1.0 - ndc_y) * 0.5 * spec.height
    return u, v, depth


def bbox_corners(item: SceneObject) -> list[Vec3]:
    """Восемь углов габаритного параллелепипеда объекта."""
    half_x = item.size[0] / 2.0
    half_y = item.size[1] / 2.0
    half_z = item.size[2] / 2.0
    center_x, center_y, center_z = item.pose
    return [
        (center_x + sign_x * half_x, center_y + sign_y * half_y, center_z + sign_z * half_z)
        for sign_x in (-1.0, 1.0)
        for sign_y in (-1.0, 1.0)
        for sign_z in (-1.0, 1.0)
    ]


def surface_depth(item: SceneObject, pose: CameraPose, spec: CameraSpec) -> float | None:
    """Ожидаемая глубина ближайшей грани объекта вдоль луча камеры.

    Глубина центра меньше глубины видимой поверхности на проекцию
    полугабаритов на направление взгляда — именно это значение сравнивается
    с буфером глубины (G4-03).
    """
    projected = project_point(item.pose, pose, spec)
    if projected is None:
        return None
    forward = pose.forward
    half_x = item.size[0] / 2.0
    half_y = item.size[1] / 2.0
    half_z = item.size[2] / 2.0
    support = abs(forward[0]) * half_x + abs(forward[1]) * half_y + abs(forward[2]) * half_z
    return projected[2] - support
