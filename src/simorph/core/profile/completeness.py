"""Показатели полноты профиля κ и κ+ (T3-05, FR-PRF-04).

κ — доля параметров, которые обязаны попасть в прогон: классы С (синтез
геометрии) и Э (эмуляция). κ+ — то же с добавлением класса М (метаданные
прогона). Класс Н осознанно не воспроизводится и в числитель не входит.

Для эталонного профиля из 75 параметров это 42/75 и 57/75 (G3-05).
Доли считаются ``Fraction``, поэтому отчёт печатает точную дробь, а не
округлённый процент.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Final

from simorph.core.models.profile import ProfileSection

__all__ = ["CLASS_TITLES", "Completeness", "completeness"]

#: Подписи классов в отчётах.
CLASS_TITLES: Final[dict[str, str]] = {
    "C": "С — синтез геометрии",
    "E": "Э — эмуляция",
    "M": "М — метаданные прогона",
    "N": "Н — не воспроизводится",
    "?": "класс не указан",
}

#: Классы, входящие в числитель κ и κ+.
_KAPPA_CLASSES: Final = frozenset({"C", "E"})
_KAPPA_PLUS_CLASSES: Final = frozenset({"C", "E", "M"})


@dataclass(frozen=True, slots=True)
class Completeness:
    """Полнота профиля: κ и κ+ как точные дроби."""

    total: int
    by_class: dict[str, int]
    unmarked: int
    kappa: Fraction
    kappa_plus: Fraction
    kappa_count: int = 0
    kappa_plus_count: int = 0

    @property
    def kappa_text(self) -> str:
        """Дробь в виде «42/75»: числитель и общее число параметров."""
        return f"{self.kappa_count}/{self.total}"

    @property
    def kappa_plus_text(self) -> str:
        return f"{self.kappa_plus_count}/{self.total}"

    @property
    def kappa_percent(self) -> float:
        return float(self.kappa) * 100.0

    @property
    def kappa_plus_percent(self) -> float:
        return float(self.kappa_plus) * 100.0

    @property
    def ready(self) -> bool:
        """Все параметры размечены: класс указан у каждого."""
        return self.unmarked == 0 and self.total > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_class": dict(self.by_class),
            "unmarked": self.unmarked,
            "kappa": {"fraction": self.kappa_text, "percent": round(self.kappa_percent, 2)},
            "kappa_plus": {
                "fraction": self.kappa_plus_text,
                "percent": round(self.kappa_plus_percent, 2),
            },
            "ready": self.ready,
        }


def completeness(section: ProfileSection) -> Completeness:
    """Считает κ и κ+ по разметке классов."""
    by_class: dict[str, int] = {"C": 0, "E": 0, "M": 0, "N": 0}
    unmarked = 0
    for parameter in section.parameters:
        if parameter.klass is None:
            unmarked += 1
            continue
        by_class[parameter.klass] = by_class.get(parameter.klass, 0) + 1

    total = len(section.parameters)
    if total == 0:
        return Completeness(
            total=0,
            by_class=by_class,
            unmarked=0,
            kappa=Fraction(0, 1),
            kappa_plus=Fraction(0, 1),
        )

    kappa_count = sum(by_class[key] for key in _KAPPA_CLASSES)
    kappa_plus_count = sum(by_class[key] for key in _KAPPA_PLUS_CLASSES)
    return Completeness(
        total=total,
        by_class=by_class,
        unmarked=unmarked,
        kappa=Fraction(kappa_count, total),
        kappa_plus=Fraction(kappa_plus_count, total),
        kappa_count=kappa_count,
        kappa_plus_count=kappa_plus_count,
    )
