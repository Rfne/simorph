"""Библиотека примитивов сцены (T4-02, FR-WLD-10).

Сцена в MVP собирается из параметрических примитивов: боксов, цилиндров
и сфер. Ассет — это именованный примитив с размером и цветом; заказчик
может ссылаться на ассет по ``id`` и масштабу, а не задавать геометрию
числами.

Неизвестный ассет не ломает сборку: возвращается «общий» примитив и текст
замечания. Так ошибка в анкете видна пользователю, но сцена всё равно
строится — иначе один опечатанный класс оборудования останавливал бы
весь мир.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Literal

__all__ = [
    "ASSET_IDS",
    "BUILTIN_ASSETS",
    "AssetSpec",
    "PrimitiveShape",
    "resolve_asset",
]

#: Форма примитива.
PrimitiveShape = Literal["box", "cylinder", "sphere"]

#: Цвет в формате RGBA, компоненты 0…1.
Color = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class AssetSpec:
    """Параметрический примитив: форма, габариты и цвет."""

    id: str
    shape: PrimitiveShape
    size: tuple[float, float, float]
    color: Color
    title: str = ""

    def scaled(self, scale: float) -> AssetSpec:
        """Копия с умноженными габаритами; масштаб ограничен разумным диапазоном."""
        factor = min(max(float(scale), 0.05), 20.0)
        return replace(
            self,
            size=(self.size[0] * factor, self.size[1] * factor, self.size[2] * factor),
        )


#: Встроенные ассеты: идентификатор → примитив.
BUILTIN_ASSETS: Final[dict[str, AssetSpec]] = {
    "generic": AssetSpec(
        "generic", "box", (2.0, 2.0, 2.0), (0.62, 0.64, 0.68, 1.0), "Типовой объект"
    ),
    "pump": AssetSpec("pump", "box", (1.6, 1.2, 2.0), (0.55, 0.58, 0.62, 1.0), "Насос"),
    "tank": AssetSpec("tank", "cylinder", (6.0, 6.0, 8.0), (0.78, 0.78, 0.74, 1.0), "Резервуар"),
    "column": AssetSpec("column", "cylinder", (2.4, 2.4, 14.0), (0.70, 0.71, 0.74, 1.0), "Колонна"),
    "silo": AssetSpec("silo", "cylinder", (8.0, 8.0, 12.0), (0.80, 0.78, 0.70, 1.0), "Силос"),
    "crane": AssetSpec("crane", "box", (2.0, 2.0, 12.0), (0.86, 0.70, 0.22, 1.0), "Кран"),
    "conveyor": AssetSpec("conveyor", "box", (10.0, 1.4, 1.2), (0.50, 0.52, 0.55, 1.0), "Конвейер"),
    "pole": AssetSpec("pole", "cylinder", (0.4, 0.4, 10.0), (0.45, 0.47, 0.50, 1.0), "Опора"),
    "container": AssetSpec(
        "container", "box", (6.0, 2.4, 2.6), (0.35, 0.48, 0.62, 1.0), "Контейнер"
    ),
    "panel": AssetSpec("panel", "box", (1.0, 0.25, 1.6), (0.30, 0.42, 0.36, 1.0), "Шкаф"),
    "tower": AssetSpec("tower", "cylinder", (5.0, 5.0, 20.0), (0.72, 0.72, 0.70, 1.0), "Башня"),
    "cooling_tower": AssetSpec(
        "cooling_tower", "cylinder", (12.0, 12.0, 12.0), (0.75, 0.76, 0.78, 1.0), "Градирня"
    ),
    "building": AssetSpec("building", "box", (20.0, 12.0, 9.0), (0.58, 0.60, 0.64, 1.0), "Здание"),
}

#: Идентификаторы ассетов в порядке словаря.
ASSET_IDS: Final[tuple[str, ...]] = tuple(BUILTIN_ASSETS)

#: Подстроки названий оборудования → ассет.
_ASSET_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("силос", "silo"),
    ("элеватор", "silo"),
    ("резервуар", "tank"),
    ("ёмкост", "tank"),
    ("емкост", "tank"),
    ("факел", "column"),
    ("колонн", "column"),
    ("градирн", "cooling_tower"),
    ("башн", "tower"),
    ("кран", "crane"),
    ("конвейер", "conveyor"),
    ("опор", "pole"),
    ("лэп", "pole"),
    ("насос", "pump"),
    ("контейнер", "container"),
    ("щит", "panel"),
    ("шкаф", "panel"),
)


def resolve_asset(asset_id: str | None, scale: float = 1.0) -> tuple[AssetSpec, str | None]:
    """Подбирает ассет по идентификатору или названию класса.

    Args:
        asset_id: идентификатор ассета или свободное название класса
            оборудования (``"силос"``, ``"pump"``).
        scale: масштаб габаритов.

    Returns:
        Пару «ассет, замечание». Замечание заполнено, если ассет не найден:
        сцена получит типовой примитив, а пользователь — объяснение.
    """
    key = (asset_id or "").strip().lower()
    if not key:
        return BUILTIN_ASSETS["generic"].scaled(scale), None
    if key in BUILTIN_ASSETS:
        return BUILTIN_ASSETS[key].scaled(scale), None
    for hint, target in _ASSET_HINTS:
        if hint in key:
            return BUILTIN_ASSETS[target].scaled(scale), None
    message = f"ассет {asset_id!r} не найден: подставлен типовой объект"
    return BUILTIN_ASSETS["generic"].scaled(scale), message
