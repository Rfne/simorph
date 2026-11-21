"""Освещение и время суток (T4-03, FR-WLD-07).

Солнце задано азимутом и высотой; из них считается направление света,
яркость и цвет. Время суток — это удобная обёртка над теми же двумя углами:
полдень даёт высокое солнце, полночь — отрицательную высоту и синий цвет.

Освещение влияет только на цвет материалов, поэтому включение и выключение
реализма не меняет геометрию и ground truth (G4-04).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

from simorph.core.models.world import Lighting
from simorph.core.world.primitives import Color

__all__ = [
    "DEFAULT_HOUR",
    "SunState",
    "lighting_for_hour",
    "phase_title",
    "sun_state",
]

#: Час, соответствующий освещению по умолчанию (азимут 135°, высота 40°).
DEFAULT_HOUR: Final = 12.0

#: Фаза суток.
Phase = Literal["night", "twilight", "day"]

_PHASE_TITLES: Final[dict[str, str]] = {
    "night": "ночь",
    "twilight": "сумерки",
    "day": "день",
}


@dataclass(frozen=True, slots=True)
class SunState:
    """Состояние солнца: углы, яркость и цвет."""

    azimuth_deg: float
    elevation_deg: float
    cloudiness: float

    @property
    def direction(self) -> tuple[float, float, float]:
        """Единичный вектор от земли к солнцу."""
        azimuth = math.radians(self.azimuth_deg)
        elevation = math.radians(self.elevation_deg)
        horizontal = math.cos(elevation)
        return (
            horizontal * math.cos(azimuth),
            horizontal * math.sin(azimuth),
            math.sin(elevation),
        )

    @property
    def intensity(self) -> float:
        """Яркость 0…1: высота солнца минус облачность."""
        if self.elevation_deg <= 0.0:
            return 0.0
        clear = min(1.0, self.elevation_deg / 60.0)
        return max(0.0, clear * (1.0 - 0.65 * self.cloudiness))

    @property
    def phase(self) -> Phase:
        if self.elevation_deg <= -6.0:
            return "night"
        if self.elevation_deg <= 8.0:
            return "twilight"
        return "day"

    @property
    def phase_title(self) -> str:
        return _PHASE_TITLES[self.phase]

    @property
    def color(self) -> tuple[float, float, float]:
        """Цвет света: тёплый на рассвете, белый днём, синий ночью."""
        if self.phase == "night":
            return (0.35, 0.42, 0.65)
        if self.phase == "twilight":
            return (0.95, 0.62, 0.38)
        return (1.0, 0.97, 0.92)

    def shade(self, base: Color, *, realism: bool = True) -> Color:
        """Применяет свет к цвету материала.

        Без реализма возвращается исходный цвет: плоская заливка нужна для
        быстрого предпросмотра и для сравнения ground truth.
        """
        if not realism:
            return base
        level = 0.25 + 0.75 * self.intensity
        tint = self.color
        return (
            min(1.0, base[0] * level * tint[0] + 0.05),
            min(1.0, base[1] * level * tint[1] + 0.05),
            min(1.0, base[2] * level * tint[2] + 0.05),
            base[3],
        )


def sun_state(lighting: Lighting) -> SunState:
    """Состояние солнца из раздела ``world.lighting``."""
    return SunState(
        azimuth_deg=float(lighting.sun_azimuth_deg),
        elevation_deg=float(lighting.sun_elevation_deg),
        cloudiness=float(lighting.cloudiness),
    )


def lighting_for_hour(hour: float, *, cloudiness: float = 0.0) -> Lighting:
    """Освещение для часа суток: 6:00 — восход, 12:00 — полдень, 0:00 — ночь.

    Азимут растёт от востока к западу, высота описывается синусоидой с
    максимумом в полдень. Формула детерминирована и не зависит от даты:
    в MVP время суток — это угол солнца, а не астрономический расчёт.
    """
    normalized = float(hour) % 24.0
    azimuth = (normalized / 24.0) * 360.0
    elevation = 60.0 * math.sin(math.pi * (normalized - 6.0) / 12.0)
    return Lighting(
        sun_azimuth_deg=azimuth % 360.0,
        sun_elevation_deg=max(-30.0, min(90.0, elevation)),
        cloudiness=min(1.0, max(0.0, float(cloudiness))),
    )


def phase_title(state: SunState) -> str:
    """Подпись фазы суток для интерфейса."""
    return state.phase_title
