"""Швы переноса: InferenceBackend и RoiChannel (T8-07, G8-07).

Исполнитель конвейера не знает, чем считается модель: он вызывает
``InferenceBackend.infer()``. Поэтому замена заглушки на ONNX Runtime,
OpenVINO или NPU-бэкенд не меняет ни узлы, ни вызывающий код — меняется
только объект, переданный в раннер.

``RoiChannel`` — такой же шов для областей интереса: вычислительный
контур публикует ROI, нейросетевой забирает их без прямого импорта
модулей друг друга. Оба канала используют сквозной ``monotonic_ns``,
поэтому сообщения стыкуются с кадрами по времени.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from simorph.core.pipeline.frames import FramePayload

__all__ = [
    "DEFAULT_INFERENCE_MS",
    "InferenceBackend",
    "InferenceResult",
    "RoiChannel",
    "RoiMessage",
    "StubInferenceBackend",
    "make_backend",
]

#: Плановая задержка инференса на PC, мс (G8-03).
DEFAULT_INFERENCE_MS: float = 380.0


@dataclass(frozen=True, slots=True)
class InferenceResult:
    """Результат одного вызова бэкенда."""

    detections: int
    embeddings: int
    latency_ms: float
    backend: str


@runtime_checkable
class InferenceBackend(Protocol):
    """Контракт любого бэкенда инференса."""

    name: str

    def infer(self, frame: FramePayload) -> InferenceResult:
        """Выполняет инференс кадра."""
        ...


class StubInferenceBackend:
    """Детерминированная заглушка: нужна тестам и демо-прогону."""

    def __init__(
        self,
        *,
        name: str = "stub",
        latency_ms: float = DEFAULT_INFERENCE_MS,
        detections: int = 4,
        embeddings: int = 8,
    ) -> None:
        if latency_ms < 0.0:
            raise ValueError("задержка инференса не может быть отрицательной")
        if detections < 0 or embeddings < 0:
            raise ValueError("число детекций и эмбеддингов не может быть отрицательным")
        self.name = name
        self.latency_ms = float(latency_ms)
        self.detections = int(detections)
        self.embeddings = int(embeddings)

    def infer(self, frame: FramePayload) -> InferenceResult:
        # Число детекций зависит от кадра, но воспроизводимо: это не случайность,
        # а детерминированная функция идентификатора.
        extra = frame.frame_id % 3
        return InferenceResult(
            detections=self.detections + extra,
            embeddings=self.embeddings,
            latency_ms=self.latency_ms,
            backend=self.name,
        )


def make_backend(kind: str, **kwargs: Any) -> InferenceBackend:
    """Фабрика бэкенда по имени; неизвестное имя — заглушка.

    Шов намеренно простой: новые бэкенды регистрируются здесь, а раннер
    и узлы остаются без изменений.
    """
    if kind in {"stub", "default", "onnx", "openvino", "rknn", "nbg"}:
        return StubInferenceBackend(name=kind, **kwargs)
    return StubInferenceBackend(name=kind, **kwargs)


@dataclass(frozen=True, slots=True)
class RoiMessage:
    """Сообщение канала ROI."""

    frame_id: int
    t_monotonic_ns: int
    rois: tuple[tuple[float, float, float, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "t_monotonic_ns": self.t_monotonic_ns,
            "rois": [list(item) for item in self.rois],
        }


class RoiChannel:
    """Кольцевой канал ROI с ограниченной ёмкостью."""

    def __init__(self, capacity: int = 4) -> None:
        if capacity < 1:
            raise ValueError("ёмкость канала ROI должна быть положительной")
        self._capacity = capacity
        self._items: deque[RoiMessage] = deque(maxlen=capacity)
        self._published = 0
        self._dropped = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def publish(self, message: RoiMessage) -> bool:
        """Публикует сообщение; ``False``, если пришлось вытеснить старое."""
        with self._lock:
            overwritten = len(self._items) == self._capacity
            if overwritten:
                self._dropped += 1
            self._items.append(message)
            self._published += 1
            return not overwritten

    def drain(self) -> list[RoiMessage]:
        """Забирает все сообщения в порядке публикации."""
        with self._lock:
            items = list(self._items)
            self._items.clear()
            return items

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "capacity": self._capacity,
                "depth": len(self._items),
                "published": self._published,
                "dropped": self._dropped,
            }
