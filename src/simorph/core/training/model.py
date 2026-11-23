"""Голова детекции: закрытый набор классов, одна сетка (T10-04).

Модель намеренно маленькая: кадр 96x72, шаг сетки 4 пикселя, три свёрточных
блока. Этого достаточно, чтобы дообучить голову на синтетическом датасете
за секунды на CPU, и при этом архитектура остаётся той же, что у реальной
головы: тепловая карта классов, размеры и смещение центра.

Веса инициализируются от seed внутри ``fork_rng``: глобальное состояние
генератора не меняется, а два запуска с одним seed дают одинаковую модель.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import torch
from torch import Tensor, nn

__all__ = ["DEFAULT_STRIDE", "DetectionHead", "ModelSpec"]

#: Шаг выходной сетки относительно пикселей кадра.
DEFAULT_STRIDE: Final = 4


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Форма входа и закрытый набор классов."""

    classes: tuple[str, ...]
    width: int
    height: int
    stride: int = DEFAULT_STRIDE

    def __post_init__(self) -> None:
        if not self.classes:
            raise ValueError("нужен хотя бы один класс")
        if len(set(self.classes)) != len(self.classes):
            raise ValueError("классы не должны повторяться")
        if self.width % self.stride or self.height % self.stride:
            raise ValueError("размер кадра должен делиться на шаг сетки")

    @property
    def grid(self) -> tuple[int, int]:
        """Ширина и высота выходной сетки в ячейках."""
        return (self.width // self.stride, self.height // self.stride)

    @property
    def num_classes(self) -> int:
        return len(self.classes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classes": list(self.classes),
            "width": self.width,
            "height": self.height,
            "stride": self.stride,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelSpec:
        return cls(
            classes=tuple(str(item) for item in payload["classes"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            stride=int(payload.get("stride", DEFAULT_STRIDE)),
        )


class DetectionHead(nn.Module):
    """Свёрточная голова: тепловая карта, размер и смещение центра."""

    def __init__(self, spec: ModelSpec, *, seed: int = 0) -> None:
        super().__init__()
        self.spec = spec
        channels = 96
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            self.body = nn.Sequential(
                _block(3, 24),
                _block(24, 48),
                _block(48, 64, stride=1),
                _block(64, channels, stride=1),
            )
            self.head = nn.Conv2d(channels, spec.num_classes + 4, 1)

    def forward(self, images: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        """Возвращает логиты карты, логарифмы размеров и смещения."""
        raw = self.head(self.body(images))
        heatmap, size, offset = raw.split([self.spec.num_classes, 2, 2], dim=1)
        return heatmap, size, offset

    def save(self, path: Path | str, **extra: Any) -> Path:
        """Чекпойнт: веса, форма входа и произвольные поля."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {"spec": self.spec.to_dict(), "state": self.state_dict(), **extra},
            target,
        )
        return target

    @classmethod
    def load(cls, path: Path | str) -> tuple[DetectionHead, dict[str, Any]]:
        """Читает чекпойнт и возвращает модель вместе с полями чекпойнта."""
        payload = torch.load(Path(path), map_location="cpu", weights_only=False)
        spec = ModelSpec.from_dict(payload["spec"])
        model = cls(spec)
        model.load_state_dict(payload["state"])
        model.eval()
        extra = {key: value for key, value in payload.items() if key not in {"spec", "state"}}
        return model, extra


def _block(in_channels: int, out_channels: int, *, stride: int = 2) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_channels, out_channels, 3, stride, 1),
        nn.GroupNorm(8, out_channels),
        nn.ReLU(inplace=True),
    )
