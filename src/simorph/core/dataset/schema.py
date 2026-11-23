"""Структуры датасета: кадр, рамка, конфигурация и отчёт (T10-01).

Датасет — это набор кадров сцены с разметкой, снятой с той же маски, что
и кадр. Поэтому разметка не может разойтись с изображением: рамка объекта
считается по пикселям маски, а не по независимой аннотации.

Идентификаторы кадров сквозные по всему датасету, а сцена кадра хранится
рядом: по ней строятся сплиты, и утечка кадров одной сцены в train и val
становится проверяемым свойством, а не соглашением.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

__all__ = [
    "CLASS_NAMES",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "Box",
    "DatasetConfig",
    "DatasetFrame",
    "DatasetReport",
    "box_iou",
    "mask_class_indices",
]

#: Closed-set классы датасета: имя класса совпадает с префиксом id объекта.
CLASS_NAMES: Final[tuple[str, ...]] = ("tank", "pump", "column")

#: Размер кадра по умолчанию: компромисс между детализацией и временем CI.
DEFAULT_WIDTH: Final = 96
DEFAULT_HEIGHT: Final = 72


@dataclass(frozen=True, slots=True)
class Box:
    """Прямоугольная рамка объекта в пикселях кадра."""

    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0

    def __post_init__(self) -> None:
        if self.label not in CLASS_NAMES:
            raise ValueError(f"неизвестный класс {self.label!r}")
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError("рамка вывернута: ожидается x1<=x2 и y1<=y2")

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "x1": round(self.x1, 3),
            "y1": round(self.y1, 3),
            "x2": round(self.x2, 3),
            "y2": round(self.y2, 3),
            "score": round(self.score, 6),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Box:
        return cls(
            label=str(payload["label"]),
            x1=float(payload["x1"]),
            y1=float(payload["y1"]),
            x2=float(payload["x2"]),
            y2=float(payload["y2"]),
            score=float(payload.get("score", 1.0)),
        )

    def to_xyxy(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)


def box_iou(
    first: Box | tuple[float, float, float, float],
    second: Box | tuple[float, float, float, float],
) -> float:
    """Пересечение поверх объединения двух рамок."""
    a = first.to_xyxy() if isinstance(first, Box) else first
    b = second.to_xyxy() if isinstance(second, Box) else second
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


@dataclass(slots=True)
class DatasetFrame:
    """Один кадр датасета: изображение, разметка, маска и метка времени."""

    frame_id: int
    scene_id: int
    t_ns: int
    image: np.ndarray
    boxes: list[Box] = field(default_factory=list)
    mask: np.ndarray | None = None
    keypoints: dict[str, list[tuple[float, float, float]]] = field(default_factory=dict)
    pose: dict[str, Any] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    def to_meta(self) -> dict[str, Any]:
        """Разметка кадра без пикселей: то, что попадает в ``labels/*.json``."""
        return {
            "frame_id": self.frame_id,
            "scene_id": self.scene_id,
            "t_ns": self.t_ns,
            "width": self.width,
            "height": self.height,
            "boxes": [box.to_dict() for box in self.boxes],
            "keypoints": {
                name: [[round(u, 3), round(v, 3), round(d, 3)] for u, v, d in points]
                for name, points in sorted(self.keypoints.items())
            },
            "pose": dict(self.pose),
            "spec": dict(self.spec),
        }


@dataclass(slots=True)
class DatasetConfig:
    """Параметры сборки датасета."""

    scenes: int = 4
    frames_per_scene: int = 80
    val_scenes: int = 1
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    seed: int = 20261122
    fov_deg: float = 60.0
    min_mask_px: int = 60
    min_box_px: int = 6
    min_visibility: float = 0.2

    def __post_init__(self) -> None:
        if self.scenes < 1:
            raise ValueError("число сцен обучения должно быть положительным")
        if self.val_scenes < 1:
            raise ValueError("нужна хотя бы одна валидационная сцена")
        if self.frames_per_scene < 1:
            raise ValueError("число кадров на сцену должно быть положительным")
        if self.width < 16 or self.height < 16:
            raise ValueError("кадр слишком мал: минимум 16x16")
        if self.width % 4 or self.height % 4:
            raise ValueError("ширина и высота кадра должны делиться на 4")
        if not 10.0 <= self.fov_deg <= 170.0:
            raise ValueError("угол обзора вне диапазона 10…170°")
        if not 0.0 < self.min_visibility <= 1.0:
            raise ValueError("порог видимости вне диапазона 0…1")

    @property
    def scene_count(self) -> int:
        return self.scenes + self.val_scenes

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenes": self.scenes,
            "frames_per_scene": self.frames_per_scene,
            "val_scenes": self.val_scenes,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "fov_deg": self.fov_deg,
            "min_mask_px": self.min_mask_px,
            "min_box_px": self.min_box_px,
            "min_visibility": self.min_visibility,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DatasetConfig:
        return cls(
            scenes=int(payload["scenes"]),
            frames_per_scene=int(payload["frames_per_scene"]),
            val_scenes=int(payload["val_scenes"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            seed=int(payload["seed"]),
            fov_deg=float(payload["fov_deg"]),
            min_mask_px=int(payload["min_mask_px"]),
            min_box_px=int(payload["min_box_px"]),
            min_visibility=float(payload.get("min_visibility", 0.2)),
        )


@dataclass(slots=True)
class DatasetReport:
    """Итог сборки: где датасет, что в нём и как он воспроизводится."""

    path: str
    run_id: str | None
    manifest_hash: str
    config: dict[str, Any]
    frames: int
    boxes: int
    classes: dict[str, int]
    splits: dict[str, int]
    scenes: dict[str, list[int]]
    seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "path": self.path,
            "run_id": self.run_id,
            "manifest_hash": self.manifest_hash,
            "config": dict(self.config),
            "frames": self.frames,
            "boxes": self.boxes,
            "classes": dict(self.classes),
            "splits": dict(self.splits),
            "scenes": {name: list(items) for name, items in self.scenes.items()},
            "seconds": round(self.seconds, 3),
        }

    def summary(self) -> str:
        classes = ", ".join(f"{name} {count}" for name, count in self.classes.items())
        splits = ", ".join(f"{name} {count}" for name, count in self.splits.items())
        train_scenes = len(self.scenes.get("train", []))
        val_scenes = len(self.scenes.get("val", []))
        return (
            f"датасет {self.path}: кадров {self.frames} ({splits})"
            f" · рамок {self.boxes} ({classes})"
            f" · сцен {train_scenes}+{val_scenes}"
            f" · манифест {self.manifest_hash[:12]}"
        )


def mask_class_indices(labels: dict[int, str]) -> dict[int, int]:
    """Отображение «номер тела → индекс класса» для записи маски в PNG."""
    result: dict[int, int] = {}
    for body, label in labels.items():
        name = label.split("-")[0].lower()
        if name in CLASS_NAMES:
            result[int(body)] = CLASS_NAMES.index(name) + 1
    return result
