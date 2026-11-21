"""Ветер, осадки и колебания среды (T4-04, FR-WLD-04, FR-WLD-08).

Поле ветра детерминировано: порывы считаются суммой синусоид с частотами
Драйдена, поэтому при одном и том же времени суток и seed картинка
повторяется. Колебания камеры — функция того же времени, а не случайного
числа.

Осадки и вибрация — визуальный слой: они меняют кадр предпросмотра, но не
позу сенсорной камеры и не геометрию, поэтому ground truth остаётся
неизменным (G4-04).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

from simorph.core.models.world import WorldSection

__all__ = [
    "Precipitation",
    "WeatherState",
    "weather_state",
]

#: Вид осадков.
Precipitation = Literal["none", "rain", "snow"]

#: Частоты Драйдена, рад/с.
_GUST_FREQUENCIES: Final = (0.4, 0.9, 1.7, 3.1)


@dataclass(frozen=True, slots=True)
class WeatherState:
    """Погода: ветер, осадки и амплитуда колебаний."""

    speed_mps: float
    direction_deg: float
    gust_model: str
    precipitation: Precipitation
    cloudiness: float
    vibration_amplitude_m: float

    @property
    def wind_vector(self) -> tuple[float, float, float]:
        """Вектор ветра: куда дует, м/с."""
        azimuth = math.radians(self.direction_deg)
        return (self.speed_mps * math.cos(azimuth), self.speed_mps * math.sin(azimuth), 0.0)

    def gust(self, time_s: float) -> float:
        """Порыв как множитель скорости: 1.0 — ровный ветер."""
        if self.gust_model != "dryden":
            return 1.0
        value = 0.0
        for index, frequency in enumerate(_GUST_FREQUENCIES):
            value += math.sin(time_s * frequency + index * 1.7) / (index + 1)
        return 1.0 + 0.25 * value

    def vibration(self, time_s: float) -> tuple[float, float, float]:
        """Смещение камеры от ветра и вибрации, м."""
        amplitude = self.vibration_amplitude_m
        if amplitude <= 0.0:
            return (0.0, 0.0, 0.0)
        gust = self.gust(time_s)
        return (
            amplitude * gust * math.sin(time_s * 2.1),
            amplitude * gust * math.sin(time_s * 2.7 + 1.1),
            amplitude * 0.5 * math.sin(time_s * 3.3 + 2.2),
        )

    @property
    def particles_per_frame(self) -> int:
        """Сколько частиц осадков рисуется на кадр."""
        if self.precipitation == "none":
            return 0
        base = 60 if self.precipitation == "rain" else 40
        return int(base * (0.4 + 0.6 * self.cloudiness))

    def to_dict(self) -> dict[str, object]:
        return {
            "speed_mps": self.speed_mps,
            "direction_deg": self.direction_deg,
            "gust_model": self.gust_model,
            "precipitation": self.precipitation,
            "vibration_amplitude_m": self.vibration_amplitude_m,
        }


def weather_state(
    world: WorldSection,
    *,
    precipitation: Precipitation | None = None,
    vibrations: bool = True,
) -> WeatherState:
    """Погода из раздела ``world``.

    Осадки по умолчанию выводятся из облачности: ясно — нет, пасмурно —
    дождь. Амплитуда колебаний растёт со скоростью ветра и порывами.
    """
    wind = world.wind
    cloudiness = float(world.lighting.cloudiness)
    kind: Precipitation = precipitation or ("none" if cloudiness < 0.25 else "rain")
    amplitude = 0.0
    if vibrations and wind.speed_mps > 0.0:
        gust = 1.6 if wind.gust_model == "dryden" else 1.0
        amplitude = min(0.25, 0.004 * float(wind.speed_mps) * gust)
    return WeatherState(
        speed_mps=float(wind.speed_mps),
        direction_deg=float(wind.direction_deg),
        gust_model=str(wind.gust_model),
        precipitation=kind,
        cloudiness=cloudiness,
        vibration_amplitude_m=amplitude,
    )
