"""Публикация данных сенсоров девяти типов (T5-02, FR-AGT-03, FR-AGT-06).

Этап 5 не запускает физику: сенсоры должны доказать, что каждый тип
создаётся и даёт данные, а не подменять собой этап 7. Поэтому здесь
детерминированный источник показаний: значения зависят от seed агента,
идентификатора сенсора и времени, но не от случайного состояния процесса.

Камеры отдают маленькие кадры-отпечатки (8×5): полноразмерный кадр
появляется на этапе 7, когда сцена и физика уже есть. Форма ответа при
этом не меняется — кадр, метки времени и GT-глубина на месте.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from typing import Any

from simorph.core.models.agent import AgentSection, Sensor

__all__ = ["SensorSample", "SensorSuite", "sensor_types", "supported_types"]

#: Размер кадра-отпечатка камеры, px.
THUMBNAIL_WIDTH: int = 8
THUMBNAIL_HEIGHT: int = 5

#: Широта и долгота полигона по умолчанию (Санкт-Петербург).
BASE_LATITUDE: float = 59.93
BASE_LONGITUDE: float = 30.31

#: Все девять типов сенсоров TRD 5.5.
_ALL_TYPES: tuple[str, ...] = (
    "imu9",
    "gps",
    "stereo_camera",
    "camera",
    "distance",
    "angle",
    "angular_rate",
    "external_io",
    "error_signal",
)


def supported_types() -> tuple[str, ...]:
    """Типы, которые умеет публиковать набор."""
    return _ALL_TYPES


def sensor_types(agent: AgentSection) -> set[str]:
    """Типы сенсоров, реально присутствующие у агента."""
    return {sensor.type for sensor in agent.sensors}


@dataclass(frozen=True, slots=True)
class SensorSample:
    """Одно показание сенсора."""

    sensor_id: str
    type: str
    t_ns: int
    values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "type": self.type,
            "t_ns": self.t_ns,
            "values": dict(self.values),
        }


class SensorSuite:
    """Детерминированная публикация показаний всех сенсоров агента."""

    def __init__(self, agent: AgentSection, *, seed: int = 0) -> None:
        self._agent = agent
        self._seed = int(seed)
        self._sensors = {sensor.id: sensor for sensor in agent.sensors}
        self._published = 0

    @property
    def sensors(self) -> dict[str, Sensor]:
        return dict(self._sensors)

    @property
    def published(self) -> int:
        """Сколько показаний выдано с момента создания набора."""
        return self._published

    def _rng(self, sensor_id: str, time_s: float) -> random.Random:
        """Поток случайных чисел, привязанный к сенсору и моменту времени."""
        stamp = round(max(0.0, float(time_s)) * 1000.0)
        return random.Random((self._seed * 1_000_003) ^ zlib.crc32(sensor_id.encode()) ^ stamp)

    def sample(self, time_s: float) -> list[SensorSample]:
        """Показания всех сенсоров на момент времени, с."""
        samples = [self.sample_one(sensor, time_s) for sensor in self._agent.sensors]
        self._published += len(samples)
        return samples

    def sample_one(self, sensor: Sensor, time_s: float) -> SensorSample:
        """Показание одного сенсора."""
        t_ns = round(max(0.0, float(time_s)) * 1e9)
        rng = self._rng(sensor.id, time_s)
        values = self._values(sensor, rng, time_s, t_ns)
        values.setdefault("t_fsin_ns", t_ns)
        values.setdefault("t_monotonic_ns", t_ns + int(float(sensor.latency_ms or 0.0) * 1e6))
        return SensorSample(sensor_id=sensor.id, type=sensor.type, t_ns=t_ns, values=values)

    def _values(
        self,
        sensor: Sensor,
        rng: random.Random,
        time_s: float,
        t_ns: int,
    ) -> dict[str, Any]:
        noise = float(sensor.noise or 0.0)
        bias = float(sensor.bias or 0.0)
        phase = math.sin(2.0 * math.pi * min(1.0, float(sensor.rate_hz) / 1000.0) * time_s)

        if sensor.type == "imu9":
            roll = 4.0 * phase + rng.gauss(0.0, noise or 0.05) + bias
            pitch = 3.0 * math.cos(time_s * 0.7) + rng.gauss(0.0, noise or 0.05)
            yaw = (time_s * 12.0) % 360.0
            half = math.radians(roll) / 2.0
            return {
                "roll_deg": round(roll, 4),
                "pitch_deg": round(pitch, 4),
                "yaw_deg": round(yaw, 4),
                "quaternion": [
                    round(math.cos(half), 6),
                    round(math.sin(half), 6),
                    0.0,
                    0.0,
                ],
                "accel_mps2": [
                    round(rng.gauss(0.0, 0.05), 4),
                    round(rng.gauss(0.0, 0.05), 4),
                    round(9.80665 + rng.gauss(0.0, 0.05), 4),
                ],
                "gyro_rad_s": [
                    round(0.01 * phase, 5),
                    round(0.01 * math.cos(time_s), 5),
                    round(0.02 * phase, 5),
                ],
                "polar_center_m": round(1.2 + 0.3 * phase, 4),
            }

        if sensor.type == "gps":
            accuracy = float(sensor.accuracy_m or 1.5)
            return {
                "latitude_deg": round(BASE_LATITUDE + rng.gauss(0.0, accuracy) / 111_320.0, 7),
                "longitude_deg": round(BASE_LONGITUDE + rng.gauss(0.0, accuracy) / 64_000.0, 7),
                "altitude_m": round(12.0 + 0.5 * phase, 3),
                "speed_mps": round(abs(1.2 * phase), 3),
                "fix": True,
            }

        if sensor.type == "stereo_camera":
            width, height = sensor.resolution or (1280, 800)
            return {
                "left": _thumbnail(rng, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT),
                "right": _thumbnail(rng, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT),
                "frames": 2,
                "width": width,
                "height": height,
                "bit_depth": sensor.bit_depth or 8,
                "mode": sensor.mode or "mono",
                "baseline_m": float(sensor.baseline_m or 0.12),
                "fov_deg": float(sensor.fov_deg or 90.0),
                "depth_gt_m": [round(3.0 + 0.4 * phase, 3), round(40.0 + phase, 3)],
                "t_fsin_ns": t_ns,
                "t_monotonic_ns": t_ns,
            }

        if sensor.type == "camera":
            width, height = sensor.resolution or (2560, 1440)
            return {
                "frame": _thumbnail(rng, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT),
                "width": width,
                "height": height,
                "bit_depth": sensor.bit_depth or 8,
                "fov_deg": float(sensor.fov_deg or 90.0),
                "t_fsin_ns": t_ns,
                "t_monotonic_ns": t_ns,
            }

        if sensor.type == "distance":
            limit = float(sensor.range_m or 30.0)
            return {
                "distance_m": round(
                    min(limit, max(0.0, 8.0 + 2.0 * phase + rng.gauss(0.0, noise))), 4
                ),
                "range_m": limit,
            }

        if sensor.type == "angle":
            low, high = sensor.range_deg or (-180.0, 180.0)
            value = min(high, max(low, 15.0 * phase + bias + rng.gauss(0.0, noise)))
            return {"angle_deg": round(value, 4), "range_deg": [low, high]}

        if sensor.type == "angular_rate":
            low, high = sensor.range_dps or (-500.0, 500.0)
            value = min(high, max(low, 30.0 * phase + bias + rng.gauss(0.0, noise)))
            return {"rate_dps": round(value, 4), "rate_rad_s": round(math.radians(value), 6)}

        if sensor.type == "external_io":
            channels = int(sensor.channels or 1)
            analog = sensor.levels == "analog"
            values = [
                round(0.5 + 0.5 * phase + rng.gauss(0.0, noise), 4) if analog else rng.randint(0, 1)
                for _ in range(channels)
            ]
            return {"channels": channels, "levels": sensor.levels or "digital", "values": values}

        # error_signal
        active_high = sensor.active_level != "low"
        active = (math.sin(time_s) > 0.98) == active_high
        return {"active": bool(active), "active_level": sensor.active_level or "high"}


def _thumbnail(rng: random.Random, width: int, height: int) -> list[list[int]]:
    """Кадр-отпечаток: форма настоящего кадра, размер — для тестов и логов."""
    return [[rng.randint(0, 255) for _ in range(width)] for _ in range(height)]
