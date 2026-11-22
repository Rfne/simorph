"""Инжекция деградации сенсорного потока (T7-06, FR-SIM-08).

Деградация — это управляемая порча кадров, а не сбой: задержка сенсора,
потеря части кадров, рассинхронизация меток и джиттер выдержки. Параметры
приходят из конфигурации прогона, применяются к каждому кадру и видны
в метриках, поэтому сравнение «чистый поток против деградированного»
не требует отдельного стенда.

Рассинхронизация ограничена ±0,5 мс: большее значение — уже не инжекция,
а ошибка конфигурации, и она отклоняется.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from simorph.core.simulation.transport import FramePacket

__all__ = ["DEFAULT_SEED", "MAX_DESYNC_MS", "Degradation", "DegradationInjector"]

#: Предел рассинхронизации меток, мс (T7-06).
MAX_DESYNC_MS: float = 0.5

#: Seed инжекции по умолчанию: результат воспроизводим.
DEFAULT_SEED: int = 20261121


@dataclass(frozen=True, slots=True)
class Degradation:
    """Параметры деградации сенсорного потока."""

    sensor_latency_ms: float = 0.0
    frame_loss_ratio: float = 0.0
    desync_ms: float = 0.0
    exposure_jitter_us: float = 0.0
    seed: int = DEFAULT_SEED

    def __post_init__(self) -> None:
        if self.sensor_latency_ms < 0.0:
            raise ValueError("задержка сенсора не может быть отрицательной")
        if not 0.0 <= self.frame_loss_ratio <= 1.0:
            raise ValueError("доля потерь должна быть в диапазоне 0…1")
        if abs(self.desync_ms) > MAX_DESYNC_MS + 1e-12:
            raise ValueError(
                f"рассинхронизация {self.desync_ms:g} мс вне диапазона ±{MAX_DESYNC_MS:g} мс"
            )
        if self.exposure_jitter_us < 0.0:
            raise ValueError("джиттер выдержки не может быть отрицательным")

    @property
    def active(self) -> bool:
        """Есть ли хоть один ненулевой параметр."""
        return bool(
            self.sensor_latency_ms
            or self.frame_loss_ratio
            or self.desync_ms
            or self.exposure_jitter_us
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_latency_ms": round(self.sensor_latency_ms, 3),
            "frame_loss_ratio": round(self.frame_loss_ratio, 4),
            "desync_ms": round(self.desync_ms, 4),
            "exposure_jitter_us": round(self.exposure_jitter_us, 3),
            "seed": self.seed,
            "active": self.active,
        }


@dataclass(slots=True)
class DegradationInjector:
    """Применяет деградацию и считает, что именно испорчено."""

    config: Degradation = field(default_factory=Degradation)
    _rng: random.Random = field(init=False, repr=False)
    _applied: int = 0
    _lost: int = 0
    _max_desync_ns: int = 0
    _max_jitter_us: float = 0.0

    def __post_init__(self) -> None:
        self._rng = random.Random(self.config.seed)

    # --- состояние ------------------------------------------------------

    @property
    def applied(self) -> int:
        return self._applied

    @property
    def lost(self) -> int:
        return self._lost

    @property
    def max_desync_ns(self) -> int:
        return self._max_desync_ns

    @property
    def max_jitter_us(self) -> float:
        return self._max_jitter_us

    def stats(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "applied": self._applied,
            "lost": self._lost,
            "max_desync_ns": self._max_desync_ns,
            "max_jitter_us": round(self._max_jitter_us, 3),
        }

    # --- применение -----------------------------------------------------

    def apply(self, packet: FramePacket) -> FramePacket | None:
        """Возвращает испорченный кадр или ``None``, если кадр потерян."""
        config = self.config
        if not config.active:
            return packet
        if config.frame_loss_ratio > 0.0 and self._rng.random() < config.frame_loss_ratio:
            self._lost += 1
            return None

        latency_ns = round(config.sensor_latency_ms * 1e6)
        desync_ns = round(config.desync_ms * 1e6)
        self._max_desync_ns = max(self._max_desync_ns, abs(desync_ns))
        jitter_us = 0.0
        if config.exposure_jitter_us > 0.0:
            jitter_us = self._rng.uniform(-config.exposure_jitter_us, config.exposure_jitter_us)
            self._max_jitter_us = max(self._max_jitter_us, abs(jitter_us))

        payload = packet.payload
        if jitter_us:
            # Джиттер выдержки меняет нагрузку кадра: хэш тоже меняется.
            stamp = round(jitter_us * 1000).to_bytes(4, "little", signed=True)
            payload = payload + stamp

        self._applied += 1
        return packet.with_timing(
            t_monotonic_ns=packet.t_monotonic_ns + latency_ns + desync_ns,
            payload=payload,
        )
