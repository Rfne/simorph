"""Оценка головы: mAP@0.5, recall@FPPI и задержка инференса (T10-05).

Метрики считаются по тем же рамкам, что и обучение: предсказания
декодируются в пиксельные рамки и сопоставляются с разметкой по IoU.
AP берётся с 101 точкой интерполяции (как в COCO), recall@FPPI — по кривой
«доля ложных срабатываний на кадр», а задержка — по фактическим вызовам
модели на CPU, без прогрева.

Все три метрики воспроизводимы: декодирование детерминировано, а задержка
измеряется на тех же кадрах в том же порядке. Поэтому повторный ``eval``
даёт те же mAP и recall, а p95 может колебаться только из-за планировщика ОС.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import torch

from simorph.core.dataset.schema import Box, DatasetFrame, box_iou
from simorph.core.training.model import DetectionHead
from simorph.core.training.targets import DEFAULT_SCORE_THRESHOLD, decode_predictions

__all__ = [
    "FPPI_TARGETS",
    "EvalReport",
    "average_precision",
    "evaluate",
    "recall_at_fppi",
]

#: Целевые значения FPPI, для которых считается recall.
FPPI_TARGETS: Final[tuple[float, ...]] = (0.1, 0.5, 1.0)

#: Число прогревочных вызовов, не попадающих в замер задержки.
_WARMUP: Final = 3


@dataclass(slots=True)
class EvalReport:
    """Итог оценки: качество, задержка и объём выборки."""

    map50: float
    per_class: dict[str, float]
    recall_fppi: dict[str, float]
    p50_latency_ms: float
    p95_latency_ms: float
    frames: int
    detections: int
    ground_truth: int
    iou_threshold: float
    score_threshold: float
    latency_samples: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "map50": round(self.map50, 6),
            "per_class": {name: round(value, 6) for name, value in self.per_class.items()},
            "recall_fppi": {name: round(value, 6) for name, value in self.recall_fppi.items()},
            "p50_latency_ms": round(self.p50_latency_ms, 4),
            "p95_latency_ms": round(self.p95_latency_ms, 4),
            "frames": self.frames,
            "detections": self.detections,
            "ground_truth": self.ground_truth,
            "iou_threshold": self.iou_threshold,
            "score_threshold": self.score_threshold,
        }

    def summary(self) -> str:
        classes = ", ".join(f"{name} {value:.2f}" for name, value in self.per_class.items())
        recall = ", ".join(f"{name} {value:.2f}" for name, value in self.recall_fppi.items())
        return (
            f"оценка: mAP@0.5 {self.map50:.3f} ({classes})"
            f" · recall@FPPI {recall}"
            f" · p95 задержки {self.p95_latency_ms:.1f} мс"
            f" · кадров {self.frames}, детекций {self.detections}"
        )


def average_precision(matches: list[int], scores: list[float], positives: int) -> float:
    """AP по 101 точке интерполяции для одного класса."""
    if positives == 0 or not scores:
        return 0.0
    order = np.argsort(-np.asarray(scores, dtype=np.float64), kind="stable")
    ordered = np.asarray(matches, dtype=np.float64)[order]
    true_positive = np.cumsum(ordered)
    false_positive = np.cumsum(1.0 - ordered)
    recall = true_positive / positives
    precision = true_positive / np.maximum(true_positive + false_positive, 1.0)
    points = np.linspace(0.0, 1.0, 101)
    return float(
        sum(
            float(precision[recall >= point].max()) if bool((recall >= point).any()) else 0.0
            for point in points
        )
        / len(points)
    )


def recall_at_fppi(
    matches: list[int],
    scores: list[float],
    positives: int,
    frames: int,
) -> dict[str, float]:
    """Recall при фиксированных FPPI: кривая по порогам уверенности."""
    result = {f"{target:g}": 0.0 for target in FPPI_TARGETS}
    if positives == 0 or not scores or frames == 0:
        return result
    order = np.argsort(-np.asarray(scores, dtype=np.float64), kind="stable")
    ordered = np.asarray(matches, dtype=np.float64)[order]
    recall = np.cumsum(ordered) / positives
    false_positive = np.cumsum(1.0 - ordered) / frames
    for target in FPPI_TARGETS:
        allowed = recall[false_positive <= target]
        result[f"{target:g}"] = float(allowed.max()) if allowed.size else 0.0
    return result


def _match(
    predictions: list[Box],
    truth: list[Box],
    *,
    iou_threshold: float,
) -> tuple[list[int], list[float], int]:
    """Сопоставление предсказаний одного класса с разметкой кадра."""
    matches: list[int] = []
    scores: list[float] = []
    used = [False] * len(truth)
    for prediction in sorted(predictions, key=lambda box: -box.score):
        best_iou = 0.0
        best_index = -1
        for index, box in enumerate(truth):
            if used[index]:
                continue
            value = box_iou(prediction, box)
            if value > best_iou:
                best_iou = value
                best_index = index
        if best_iou >= iou_threshold:
            used[best_index] = True
            matches.append(1)
        else:
            matches.append(0)
        scores.append(prediction.score)
    return matches, scores, len(truth)


def evaluate(
    model: DetectionHead,
    frames: list[DatasetFrame],
    *,
    iou_threshold: float = 0.5,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> EvalReport:
    """Считает mAP@0.5, recall@FPPI и p95 задержки на наборе кадров."""
    if not frames:
        raise ValueError("пустой набор кадров: оценивать нечего")
    spec = model.spec
    model.eval()

    per_class_matches: dict[str, list[int]] = {name: [] for name in spec.classes}
    per_class_scores: dict[str, list[float]] = {name: [] for name in spec.classes}
    positives: dict[str, int] = dict.fromkeys(spec.classes, 0)
    detections = 0
    latencies: list[float] = []

    with torch.no_grad():
        for index, frame in enumerate(frames):
            tensor = _frame_tensor(frame)
            started = time.perf_counter()
            outputs = model(tensor)
            elapsed = (time.perf_counter() - started) * 1000.0
            if index >= _WARMUP:
                latencies.append(elapsed)
            predictions = decode_predictions(
                outputs[0],
                outputs[1],
                outputs[2],
                spec,
                score_threshold=score_threshold,
            )
            detections += len(predictions)
            for name in spec.classes:
                class_predictions = [box for box in predictions if box.label == name]
                class_truth = [box for box in frame.boxes if box.label == name]
                matches, scores, count = _match(
                    class_predictions, class_truth, iou_threshold=iou_threshold
                )
                per_class_matches[name].extend(matches)
                per_class_scores[name].extend(scores)
                positives[name] += count

    per_class = {
        name: average_precision(per_class_matches[name], per_class_scores[name], positives[name])
        for name in spec.classes
    }
    mean_ap = float(np.mean(list(per_class.values()))) if per_class else 0.0

    all_matches = [item for name in spec.classes for item in per_class_matches[name]]
    all_scores = [item for name in spec.classes for item in per_class_scores[name]]
    total_positives = sum(positives.values())
    recall = recall_at_fppi(all_matches, all_scores, total_positives, len(frames))

    samples = latencies if latencies else [0.0]
    return EvalReport(
        map50=mean_ap,
        per_class=per_class,
        recall_fppi=recall,
        p50_latency_ms=float(np.median(samples)),
        p95_latency_ms=float(np.percentile(samples, 95)),
        frames=len(frames),
        detections=detections,
        ground_truth=total_positives,
        iou_threshold=iou_threshold,
        score_threshold=score_threshold,
        latency_samples=samples,
    )


def _frame_tensor(frame: DatasetFrame) -> torch.Tensor:
    array = np.ascontiguousarray(frame.image, dtype=np.float32) / 255.0
    return torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)
