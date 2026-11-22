"""Кадр конвейера: идентификатор, метки времени и полезная нагрузка.

Кадр неизменяем: узлы передают его дальше, не меняя метки. Метка
``t_fsin_ns`` — время сцены, ``t_monotonic_ns`` — монотонное время PC.
Обе метки идут сквозь весь конвейер, поэтому задержку узла можно измерить
без внешних часов.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FramePayload"]


@dataclass(frozen=True, slots=True)
class FramePayload:
    """Один кадр на входе конвейера."""

    frame_id: int
    t_fsin_ns: int
    t_monotonic_ns: int
    camera_id: str = "sim.camera"
    width: int = 640
    height: int = 480
    data: bytes = b""

    @property
    def size_bytes(self) -> int:
        return len(self.data)
