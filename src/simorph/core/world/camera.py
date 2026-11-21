"""Камеры сцены: сенсор и орбитальный обзор (T4-05, T4-07, FR-WLD-02).

``CameraSpec`` описывает сенсор: разрешение, поле зрения, ближнюю и дальнюю
плоскости, режим стереопары. ``CameraPose`` — положение и направление
взгляда. ``OrbitCamera`` — модель управления обзором: орбита, панорамирование
и зум вокруг цели.

Математика камеры не зависит от PyBullet: те же функции считают проекцию
ключевых точек для ground truth, поэтому проверка глубины не требует
второго источника истины.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

__all__ = [
    "CameraPose",
    "CameraSpec",
    "OrbitCamera",
    "Vec3",
    "stereo_offset",
]

Vec3 = tuple[float, float, float]

#: Режим камеры.
CameraMode = Literal["mono", "stereo"]


@dataclass(frozen=True, slots=True)
class CameraSpec:
    """Параметры сенсора."""

    width: int = 640
    height: int = 480
    fov_deg: float = 60.0
    near: float = 0.1
    far: float = 1000.0
    mode: CameraMode = "mono"
    baseline_m: float = 0.12

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)

    def scaled(self, width: int, height: int) -> CameraSpec:
        """Тот же сенсор с другим разрешением (для быстрого предпросмотра)."""
        return CameraSpec(
            width=max(16, int(width)),
            height=max(16, int(height)),
            fov_deg=self.fov_deg,
            near=self.near,
            far=self.far,
            mode=self.mode,
            baseline_m=self.baseline_m,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "fov_deg": self.fov_deg,
            "near": self.near,
            "far": self.far,
            "mode": self.mode,
            "baseline_m": self.baseline_m,
        }


@dataclass(frozen=True, slots=True)
class CameraPose:
    """Положение камеры: точка, цель и направление «вверх»."""

    eye: Vec3
    target: Vec3
    up: Vec3 = (0.0, 0.0, 1.0)

    @property
    def forward(self) -> Vec3:
        return _normalize(_sub(self.target, self.eye))

    @property
    def right(self) -> Vec3:
        return _normalize(_cross(self.forward, self.up))

    @property
    def up_vector(self) -> Vec3:
        return _cross(self.right, self.forward)

    def translated(self, offset: Vec3) -> CameraPose:
        return CameraPose(
            eye=_add(self.eye, offset),
            target=_add(self.target, offset),
            up=self.up,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"eye": list(self.eye), "target": list(self.target), "up": list(self.up)}


@dataclass(slots=True)
class OrbitCamera:
    """Орбитальный обзор сцены."""

    target: Vec3 = (0.0, 0.0, 0.0)
    distance: float = 220.0
    yaw_deg: float = 45.0
    pitch_deg: float = -35.0
    fov_deg: float = 60.0

    #: Минимальная и максимальная дистанция, м.
    MIN_DISTANCE: ClassVar[float] = 2.0
    MAX_DISTANCE: ClassVar[float] = 5000.0

    def pose(self) -> CameraPose:
        """Положение камеры по текущим углам."""
        yaw = math.radians(self.yaw_deg)
        pitch = math.radians(self.pitch_deg)
        forward = (
            math.cos(pitch) * math.cos(yaw),
            math.cos(pitch) * math.sin(yaw),
            math.sin(pitch),
        )
        eye = _sub(self.target, _scale(forward, self.distance))
        return CameraPose(eye=eye, target=self.target)

    def orbit(self, delta_yaw_deg: float, delta_pitch_deg: float) -> None:
        """Поворот вокруг цели."""
        self.yaw_deg = (self.yaw_deg + delta_yaw_deg) % 360.0
        self.pitch_deg = min(85.0, max(-85.0, self.pitch_deg + delta_pitch_deg))

    def pan(self, delta_right: float, delta_up: float) -> None:
        """Сдвиг цели в плоскости кадра."""
        pose = self.pose()
        step = self.distance / 500.0
        offset = _add(
            _scale(pose.right, delta_right * step),
            _scale(pose.up_vector, -delta_up * step),
        )
        self.target = _add(self.target, offset)

    def zoom(self, factor: float) -> None:
        """Приближение или удаление камеры."""
        if factor <= 0.0:
            return
        self.distance = min(self.MAX_DISTANCE, max(self.MIN_DISTANCE, self.distance * factor))

    def spec(self, width: int = 640, height: int = 480) -> CameraSpec:
        return CameraSpec(width=width, height=height, fov_deg=self.fov_deg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": list(self.target),
            "distance": self.distance,
            "yaw_deg": self.yaw_deg,
            "pitch_deg": self.pitch_deg,
            "fov_deg": self.fov_deg,
        }


def stereo_offset(pose: CameraPose, baseline_m: float) -> tuple[CameraPose, CameraPose]:
    """Левая и правая камеры стереопары вокруг исходной позы."""
    half = baseline_m / 2.0
    right = _scale(pose.right, half)
    left = CameraPose(eye=_sub(pose.eye, right), target=_sub(pose.target, right), up=pose.up)
    right_pose = CameraPose(eye=_add(pose.eye, right), target=_add(pose.target, right), up=pose.up)
    return left, right_pose


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, factor: float) -> Vec3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _normalize(a: Vec3) -> Vec3:
    length = _length(a)
    if length <= 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)
