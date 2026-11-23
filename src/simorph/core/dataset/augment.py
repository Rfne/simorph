"""Аугментация кадров с детерминизмом по seed (T10-03, G10-01).

Аугментация воспроизводит деградации бортового канала: шум сенсора,
сдвиг экспозиции, размытие, рассинхронизацию метки времени и потерю
кадров. Параметры каждой аугментации выводятся из ``(seed, номер кадра)``,
поэтому один и тот же датасет с одним seed даёт один и тот же набор
преобразований — это и есть проверяемое свойство детерминизма.

Потеря кадра — единственная аугментация, которая меняет число кадров.
Она возвращает ``None`` вместе с параметрами, чтобы вызывающий код мог
учесть пропуск в статистике, а не молча потерять его.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Any, Final

import numpy as np

from simorph.core.dataset.schema import Box, DatasetFrame

__all__ = ["AugmentConfig", "AugmentParams", "augment_frame", "blur_image"]

#: Множитель для независимого потока случайных чисел на кадр.
_FRAME_STRIDE: Final = 1_000_003


@dataclass(frozen=True, slots=True)
class AugmentConfig:
    """Диапазоны аугментации; ``seed`` делает их воспроизводимыми."""

    seed: int = 0
    noise_sigma: float = 4.0
    gain_range: tuple[float, float] = (0.85, 1.15)
    bias_range: tuple[float, float] = (-12.0, 12.0)
    blur_range: tuple[float, float] = (0.0, 0.8)
    drop_probability: float = 0.0
    desync_ns: int = 5_000_000
    flip_probability: float = 0.5

    def __post_init__(self) -> None:
        if self.noise_sigma < 0.0:
            raise ValueError("шум не может быть отрицательным")
        if not 0.0 <= self.drop_probability <= 1.0:
            raise ValueError("вероятность потери кадра вне диапазона 0…1")
        if not 0.0 <= self.flip_probability <= 1.0:
            raise ValueError("вероятность отражения вне диапазона 0…1")
        if self.desync_ns < 0:
            raise ValueError("рассинхронизация не может быть отрицательной")
        for name, (low, high) in (
            ("экспозиция", self.gain_range),
            ("яркость", self.bias_range),
            ("размытие", self.blur_range),
        ):
            if high < low:
                raise ValueError(f"диапазон {name} вывернут")

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "noise_sigma": self.noise_sigma,
            "gain_range": list(self.gain_range),
            "bias_range": list(self.bias_range),
            "blur_range": list(self.blur_range),
            "drop_probability": self.drop_probability,
            "desync_ns": self.desync_ns,
            "flip_probability": self.flip_probability,
        }


@dataclass(frozen=True, slots=True)
class AugmentParams:
    """Фактические параметры одного кадра: их можно записать и сравнить."""

    gain: float
    bias: float
    noise_sigma: float
    blur_sigma: float
    flipped: bool
    dropped: bool
    desync_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "gain": round(self.gain, 6),
            "bias": round(self.bias, 6),
            "noise_sigma": round(self.noise_sigma, 6),
            "blur_sigma": round(self.blur_sigma, 6),
            "flipped": self.flipped,
            "dropped": self.dropped,
            "desync_ns": self.desync_ns,
        }


def blur_image(image: np.ndarray, sigma: float) -> np.ndarray:
    """Размытие 3-тапным гауссовым ядром по осям: без внешних зависимостей."""
    if sigma <= 0.05:
        return image
    radius = 1
    weights = np.array(
        [np.exp(-0.5 * (offset / sigma) ** 2) for offset in range(-radius, radius + 1)],
        dtype=np.float32,
    )
    weights /= weights.sum()
    work = image.astype(np.float32)
    padded = np.pad(work, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    vertical = np.zeros_like(work)
    for index in range(3):
        vertical += weights[index] * padded[index : index + work.shape[0]]
    padded = np.pad(vertical, ((0, 0), (radius, radius), (0, 0)), mode="edge")
    horizontal = np.zeros_like(work)
    for index in range(3):
        horizontal += weights[index] * padded[:, index : index + work.shape[1]]
    return np.clip(horizontal, 0.0, 255.0).astype(np.uint8)


def _params(config: AugmentConfig, index: int) -> tuple[AugmentParams, np.random.Generator]:
    """Параметры кадра из ``(seed, номер)``: одинаковые при повторном вызове."""
    rng = random.Random(config.seed * _FRAME_STRIDE + index)
    generator = np.random.default_rng([config.seed, index])
    gain = rng.uniform(*config.gain_range)
    bias = rng.uniform(*config.bias_range)
    blur = rng.uniform(*config.blur_range)
    flipped = rng.random() < config.flip_probability
    dropped = rng.random() < config.drop_probability
    desync = rng.randint(-config.desync_ns, config.desync_ns) if config.desync_ns else 0
    params = AugmentParams(
        gain=gain,
        bias=bias,
        noise_sigma=config.noise_sigma,
        blur_sigma=blur,
        flipped=flipped,
        dropped=dropped,
        desync_ns=desync,
    )
    return params, generator


def augment_frame(
    frame: DatasetFrame,
    config: AugmentConfig,
) -> tuple[DatasetFrame | None, AugmentParams]:
    """Аугментирует кадр; ``None`` означает потерянный кадр."""
    params, generator = _params(config, frame.frame_id)
    if params.dropped:
        return None, params

    image = frame.image.astype(np.float32) * params.gain + params.bias
    if params.noise_sigma > 0.0:
        image += generator.normal(0.0, params.noise_sigma, image.shape)
    augmented = np.clip(image, 0.0, 255.0).astype(np.uint8)
    augmented = blur_image(augmented, params.blur_sigma)

    boxes = list(frame.boxes)
    mask = frame.mask
    keypoints = dict(frame.keypoints)
    if params.flipped:
        width = frame.width
        augmented = augmented[:, ::-1].copy()
        boxes = [
            Box(
                label=box.label,
                x1=width - 1 - box.x2,
                y1=box.y1,
                x2=width - 1 - box.x1,
                y2=box.y2,
                score=box.score,
            )
            for box in boxes
        ]
        if mask is not None:
            mask = mask[:, ::-1].copy()
        keypoints = {
            name: [(width - 1 - u, v, depth) for u, v, depth in points]
            for name, points in keypoints.items()
        }

    return (
        replace(
            frame,
            image=augmented,
            boxes=boxes,
            mask=mask,
            keypoints=keypoints,
            t_ns=frame.t_ns + params.desync_ns,
        ),
        params,
    )
