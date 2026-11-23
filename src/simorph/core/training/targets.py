"""Кодирование разметки в сетку и декодирование предсказаний (T10-04).

Таргеты повторяют устройство головы: центр объекта попадает в одну ячейку
сетки, вокруг него рисуется гауссов «плюс» в тепловой карте класса, а в
этой же ячейке хранятся логарифмы размера и смещение центра внутри ячейки.

Логарифм размера, а не размер в ячейках: относительная ошибка одинакова
для маленького и большого объекта, и голова не «съезжает» на средний
размер, как это бывает при L1 по абсолютным пикселям.

Декодирование симметрично: пики карты → рамки → NMS по классам.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import torch
from torch import Tensor

from simorph.core.dataset.schema import Box, box_iou
from simorph.core.training.model import ModelSpec

__all__ = [
    "DEFAULT_SCORE_THRESHOLD",
    "Targets",
    "decode_predictions",
    "encode_targets",
    "focal_loss",
    "gaussian_kernel",
    "nms",
]

#: Порог уверенности при декодировании.
DEFAULT_SCORE_THRESHOLD: Final = 0.05

#: Предел числа детекций на кадр до NMS.
DEFAULT_TOPK: Final = 40


@dataclass(frozen=True, slots=True)
class Targets:
    """Таргеты одного кадра в форме выходов головы."""

    heatmap: np.ndarray
    size: np.ndarray
    offset: np.ndarray
    mask: np.ndarray

    @property
    def positives(self) -> int:
        return int(self.mask.sum())


def gaussian_kernel(radius: int) -> np.ndarray:
    """Нормированное гауссово пятно ``(2r+1, 2r+1)``."""
    sigma = max(radius / 2.0, 1.0)
    axis = np.arange(-radius, radius + 1, dtype=np.float32)
    xx, yy = np.meshgrid(axis, axis)
    return np.exp(-(xx * xx + yy * yy) / (2.0 * sigma * sigma)).astype(np.float32)


def encode_targets(boxes: list[Box], spec: ModelSpec) -> Targets:
    """Раскладывает рамки кадра по сетке."""
    grid_width, grid_height = spec.grid
    heatmap = np.zeros((spec.num_classes, grid_height, grid_width), dtype=np.float32)
    size = np.zeros((2, grid_height, grid_width), dtype=np.float32)
    offset = np.zeros((2, grid_height, grid_width), dtype=np.float32)
    mask = np.zeros((grid_height, grid_width), dtype=np.float32)
    stride = spec.stride

    for box in boxes:
        center_x = (box.x1 + box.x2) / 2.0 / stride
        center_y = (box.y1 + box.y2) / 2.0 / stride
        cell_x, cell_y = int(center_x), int(center_y)
        if not (0 <= cell_x < grid_width and 0 <= cell_y < grid_height):
            continue
        if mask[cell_y, cell_x] > 0.0:
            continue
        width_cells = max((box.x2 - box.x1) / stride, 1.0)
        height_cells = max((box.y2 - box.y1) / stride, 1.0)
        radius = max(1, int(min(width_cells, height_cells) / 4.0))
        kernel = gaussian_kernel(radius)
        left = max(0, cell_x - radius)
        right = min(grid_width, cell_x + radius + 1)
        top = max(0, cell_y - radius)
        bottom = min(grid_height, cell_y + radius + 1)
        patch = kernel[
            top - (cell_y - radius) : bottom - (cell_y - radius),
            left - (cell_x - radius) : right - (cell_x - radius),
        ]
        class_index = spec.classes.index(box.label)
        heatmap[class_index, top:bottom, left:right] = np.maximum(
            heatmap[class_index, top:bottom, left:right],
            patch,
        )
        # Регрессия размера и смещения учится не только в пиковой ячейке,
        # но и в соседних, где тепловая карта ещё уверена: иначе ошибка
        # центра в одну ячейку не компенсируется, и IoU падает.
        for row_offset, column_offset in zip(*np.where(patch >= 0.5), strict=True):
            cell_y = top + int(row_offset)
            cell_x = left + int(column_offset)
            if mask[cell_y, cell_x] > 0.0:
                continue
            size[0, cell_y, cell_x] = float(np.log(width_cells))
            size[1, cell_y, cell_x] = float(np.log(height_cells))
            offset[0, cell_y, cell_x] = center_x - cell_x
            offset[1, cell_y, cell_x] = center_y - cell_y
            mask[cell_y, cell_x] = 1.0
    return Targets(heatmap=heatmap, size=size, offset=offset, mask=mask)


def focal_loss(prediction: Tensor, target: Tensor) -> Tensor:
    """Penalty-reduced focal loss из CenterNet: гасит простые негативы."""
    probability = torch.sigmoid(prediction).clamp(1e-4, 1.0 - 1e-4)
    positive = target.eq(1.0).float()
    negative = 1.0 - positive
    positive_loss = -((1.0 - probability) ** 2) * torch.log(probability) * positive
    negative_loss = (
        -((1.0 - target) ** 4) * (probability**2) * torch.log(1.0 - probability) * negative
    )
    return (positive_loss.sum() + negative_loss.sum()) / positive.sum().clamp(min=1.0)


def nms(boxes: list[Box], *, iou_threshold: float = 0.5) -> list[Box]:
    """Подавление пересечений внутри каждого класса."""
    kept: list[Box] = []
    for label in {box.label for box in boxes}:
        candidates = sorted((box for box in boxes if box.label == label), key=lambda b: -b.score)
        chosen: list[Box] = []
        for candidate in candidates:
            if all(box_iou(candidate, item) < iou_threshold for item in chosen):
                chosen.append(candidate)
        kept.extend(chosen)
    return kept


def decode_predictions(
    heatmap: Tensor,
    size: Tensor,
    offset: Tensor,
    spec: ModelSpec,
    *,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    topk: int = DEFAULT_TOPK,
    iou_threshold: float = 0.5,
) -> list[Box]:
    """Предсказания одного кадра: пики карты → рамки → NMS."""
    scores = heatmap[0]
    pooled = torch.nn.functional.max_pool2d(scores.unsqueeze(0), 3, 1, 1)[0]
    peaks = (scores == pooled) & (scores > score_threshold)
    class_indices, rows, columns = torch.where(peaks)
    stride = spec.stride
    candidates: list[Box] = []
    for class_index, row, column in zip(
        class_indices.tolist(), rows.tolist(), columns.tolist(), strict=True
    ):
        center_x = (column + float(offset[0, 0, row, column])) * stride
        center_y = (row + float(offset[0, 1, row, column])) * stride
        width = float(np.exp(float(size[0, 0, row, column]))) * stride
        height = float(np.exp(float(size[0, 1, row, column]))) * stride
        candidates.append(
            Box(
                label=spec.classes[class_index],
                x1=center_x - width / 2.0,
                y1=center_y - height / 2.0,
                x2=center_x + width / 2.0,
                y2=center_y + height / 2.0,
                score=float(scores[class_index, row, column]),
            )
        )
    candidates.sort(key=lambda box: -box.score)
    return nms(candidates[:topk], iou_threshold=iou_threshold)
