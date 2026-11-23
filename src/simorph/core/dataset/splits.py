"""Сплиты по сценам без утечки кадров (T10-02, G10-02).

Сплит строится по сценам, а не по кадрам: все кадры сцены попадают ровно
в один сплит. Поэтому вопрос «есть ли пересечение train и val» сводится
к вопросу «пересекаются ли множества сцен», и ответ проверяется напрямую.

Сцены перемешиваются seed'ом сплита: при смене seed меняется состав
валидации, но не появляется утечка. Итоговый набор сцен фиксируется
в манифесте, поэтому датасет воспроизводим и без пересчёта.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Final, Literal

__all__ = ["DEFAULT_VAL_FRACTION", "SplitPlan", "plan_splits", "split_of"]

#: Доля сцен валидации, если явное число не задано.
DEFAULT_VAL_FRACTION: Final = 0.25

#: Имя сплита.
SplitName = Literal["train", "val"]


@dataclass(frozen=True, slots=True)
class SplitPlan:
    """Раскладка сцен по сплитам."""

    train: tuple[int, ...]
    val: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.train:
            raise ValueError("train не может быть пустым")
        if not self.val:
            raise ValueError("val не может быть пустым")
        overlap = set(self.train) & set(self.val)
        if overlap:
            raise ValueError(f"сцены попали в оба сплита: {sorted(overlap)}")

    @property
    def scenes(self) -> tuple[int, ...]:
        return self.train + self.val

    def split_of(self, scene_id: int) -> SplitName:
        if scene_id in self.train:
            return "train"
        if scene_id in self.val:
            return "val"
        raise KeyError(f"сцена {scene_id} не входит в план сплитов")

    def to_dict(self) -> dict[str, list[int]]:
        return {"train": list(self.train), "val": list(self.val)}


def plan_splits(scene_ids: list[int], *, seed: int, val_scenes: int | None = None) -> SplitPlan:
    """Делит сцены на train и val, перемешивая их seed'ом."""
    unique = sorted(set(scene_ids))
    if len(unique) < 2:
        raise ValueError("для сплита нужно минимум две сцены")
    if val_scenes is None:
        val_count = max(1, round(len(unique) * DEFAULT_VAL_FRACTION))
    else:
        val_count = int(val_scenes)
    if not 1 <= val_count < len(unique):
        raise ValueError(f"число валидационных сцен вне диапазона 1…{len(unique) - 1}")

    shuffled = list(unique)
    random.Random(seed).shuffle(shuffled)
    val = tuple(sorted(shuffled[:val_count]))
    train = tuple(sorted(shuffled[val_count:]))
    return SplitPlan(train=train, val=val)


def split_of(plan: SplitPlan, scene_id: int) -> SplitName:
    """Имя сплита для сцены (короткая форма для циклов сборки)."""
    return plan.split_of(scene_id)
