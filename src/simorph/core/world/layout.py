"""Детерминированная раскладка сцены (T4-01, T4-09, FR-WLD-01, FR-WLD-05).

Раскладка — это список примитивов с позами, построенный из трёх источников:

1. явные объекты раздела ``world`` (зоны, здания, оборудование, деревья,
   водоёмы) — они авторитетны и не перегенерируются;
2. проекция профиля ``P_gen`` — оттуда берутся площадь, рельеф, плотность
   растительности, число зданий и оборудования;
3. seed мира — им заполняются недостающие объекты.

Все три источника сведены в чистую функцию без побочных эффектов: тот же
профиль, мир и seed дают ту же раскладку, поэтому сцену можно сравнить
побайтово (G4-02, G4-06). Порядок объектов фиксирован: рельеф, зоны,
здания, оборудование, деревья, вода.

Если объектов больше лимита, лишние не строятся, а в замечания попадает
предупреждение: приложение не падает и не пытается отрисовать миллион
тел (FR-WLD-11, T4-09).
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from simorph.core.issues import Issue, IssueCode, Severity, issue
from simorph.core.models.profile import ProfileSection
from simorph.core.models.world import WorldSection
from simorph.core.world.primitives import Color, PrimitiveShape, resolve_asset

__all__ = [
    "MAX_OBJECTS",
    "SceneLayout",
    "SceneObject",
    "build_layout",
    "generation_values",
]

#: Лимит объектов сцены (FR-WLD-11).
MAX_OBJECTS: Final = 1000

#: Категория объекта: она же подпись в статистике.
SceneCategory = Literal["terrain", "zone", "building", "equipment", "tree", "water"]

#: Цвета категорий по умолчанию.
_ZONE_COLORS: Final[dict[str, Color]] = {
    "tech": (0.46, 0.47, 0.50, 1.0),
    "road": (0.30, 0.30, 0.32, 1.0),
    "water": (0.18, 0.40, 0.66, 1.0),
    "green": (0.28, 0.48, 0.26, 1.0),
    "building": (0.55, 0.52, 0.46, 1.0),
    "other": (0.42, 0.43, 0.45, 1.0),
}
_TERRAIN_COLOR: Final = (0.34, 0.42, 0.28, 1.0)
_BUILDING_COLOR: Final = (0.58, 0.60, 0.64, 1.0)
_TREE_COLOR: Final = (0.24, 0.44, 0.22, 1.0)
_WATER_COLOR: Final = (0.18, 0.40, 0.66, 1.0)

#: Сколько зданий, единиц оборудования и деревьев строить максимум.
_BUILDINGS_PER_ZONE_CAP: Final = 8
_EQUIPMENT_PER_ZONE_CAP: Final = 40
_TREE_CAP: Final = 150
_WATER_CAP: Final = 12
_ZONE_CAP: Final = 12
_HILL_CAP: Final = 8


@dataclass(frozen=True, slots=True)
class SceneObject:
    """Один примитив сцены: поза, габариты и цвет."""

    id: str
    category: SceneCategory
    shape: PrimitiveShape
    pose: tuple[float, float, float]
    size: tuple[float, float, float]
    color: Color
    zone: str | None = None
    asset: str | None = None

    def signature(self) -> tuple[Any, ...]:
        """Сравнимое представление для проверки детерминизма."""
        return (
            self.id,
            self.category,
            self.shape,
            tuple(round(value, 6) for value in self.pose),
            tuple(round(value, 6) for value in self.size),
        )


@dataclass(slots=True)
class SceneLayout:
    """Результат раскладки: объекты, замечания и использованные параметры."""

    objects: list[SceneObject] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    seed: int = 0
    limit: int = MAX_OBJECTS
    generation: dict[str, Any] = field(default_factory=dict)
    truncated: int = 0

    @property
    def over_limit(self) -> bool:
        """Была ли раскладка урезана лимитом."""
        return self.truncated > 0

    def stats(self) -> dict[str, int]:
        """Число объектов по категориям в фиксированном порядке."""
        counts = dict.fromkeys(("terrain", "zone", "building", "equipment", "tree", "water"), 0)
        for item in self.objects:
            counts[item.category] = counts.get(item.category, 0) + 1
        return counts

    def signature(self) -> tuple[Any, ...]:
        return tuple(item.signature() for item in self.objects)

    def by_category(self, category: str) -> list[SceneObject]:
        return [item for item in self.objects if item.category == category]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "limit": self.limit,
            "objects": len(self.objects),
            "truncated": self.truncated,
            "by_category": self.stats(),
            "warnings": [item.to_dict() for item in self.warnings],
        }


def generation_values(profile: ProfileSection | None) -> dict[str, Any]:
    """Собирает значения параметров профиля по ключам проекции.

    Значения диапазонов попадают в словарь парой ``[низ, верх]``. Если один
    ключ задан несколькими параметрами, побеждает последний: спецификация
    упорядочена так, что уточняющий параметр идёт ниже обобщающего.
    """
    values: dict[str, Any] = {}
    if profile is None:
        return values
    for parameter in profile.parameters:
        if parameter.klass in {None, "N"}:
            continue
        if parameter.value is not None:
            value: Any = parameter.value
        elif parameter.range is not None:
            value = list(parameter.range)
        else:
            continue
        for key in parameter.projection:
            values[key] = value
    return values


def _as_int(value: Any, default: int) -> int:
    """Целое из числа, строки-числа или списка через запятую."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        parts = [part for part in value.replace(";", ",").split(",") if part.strip()]
        if len(parts) > 1:
            return len(parts)
        try:
            return int(float(parts[0].strip()))
        except (ValueError, IndexError):
            return default
    if isinstance(value, (list, tuple)) and value:
        try:
            return int(sum(float(item) for item in value) / len(value))
        except (TypeError, ValueError):
            return default
    return default


def _as_pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    """Пара чисел из диапазона, числа или строки."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), float(value)
    return default


def _stable_seed(seed: int, world_id: str) -> int:
    """Seed, не зависящий от хеш-рандомизации Python между запусками."""
    return (int(seed) & 0xFFFFFFFF) ^ zlib.crc32(world_id.encode("utf-8"))


def _polygon_bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Габариты и центр полигона: (cx, cy, ширина, длина)."""
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (
        (min(xs) + max(xs)) / 2.0,
        (min(ys) + max(ys)) / 2.0,
        max(xs) - min(xs),
        max(ys) - min(ys),
    )


def build_layout(
    world: WorldSection,
    profile: ProfileSection | None = None,
    *,
    limit: int = MAX_OBJECTS,
) -> SceneLayout:
    """Строит раскладку сцены из мира, профиля и seed.

    Args:
        world: раздел ``world`` — явные объекты и seed.
        profile: профиль заказчика; из него берутся параметры ``P_gen``.
        limit: предел числа объектов.

    Returns:
        Раскладка с объектами, замечаниями и статистикой. Функция не бросает
        исключений: всё, что мешает построить объект, становится замечанием.
    """
    values = generation_values(profile)
    rng = random.Random(_stable_seed(world.seed, world.world_id))
    layout = SceneLayout(seed=world.seed, limit=limit, generation=values)

    size_x, size_y = world.terrain.size_m
    elevation_low, elevation_high = world.terrain.elevation_m
    height_range = max(0.0, float(elevation_high) - float(elevation_low))

    layout.objects.append(
        SceneObject(
            id="terrain",
            category="terrain",
            shape="box",
            pose=(0.0, 0.0, float(elevation_low) - 0.5),
            size=(float(size_x), float(size_y), 1.0),
            color=_TERRAIN_COLOR,
        )
    )
    layout.objects.extend(_build_hills(rng, size_x, size_y, elevation_low, height_range))
    zones = _build_zones(world, values, rng, elevation_low, layout)
    layout.objects.extend(zones)
    layout.objects.extend(_build_buildings(world, values, rng, zones, elevation_low, layout))
    layout.objects.extend(_build_equipment(world, values, rng, zones, elevation_low, layout))
    layout.objects.extend(_build_trees(world, values, rng, size_x, size_y, elevation_low))
    layout.objects.extend(_build_water(world, values, rng, size_x, size_y, elevation_low))

    if len(layout.objects) > limit:
        layout.truncated = len(layout.objects) - limit
        layout.objects = layout.objects[:limit]
        layout.warnings.append(
            issue(
                IssueCode.INVALID_VALUE,
                "world",
                f"объектов {len(layout.objects) + layout.truncated} больше лимита"
                f" {limit}: построено {limit}, пропущено {layout.truncated}",
                severity=Severity.WARNING,
            )
        )
    return layout


def _build_hills(
    rng: random.Random,
    size_x: float,
    size_y: float,
    elevation_low: float,
    height_range: float,
) -> list[SceneObject]:
    """Пологая неровность рельефа: сферы, вдавленные в землю."""
    if height_range <= 0.05:
        return []
    count = min(_HILL_CAP, max(1, int(height_range / 2.0)))
    hills: list[SceneObject] = []
    for index in range(count):
        height = rng.uniform(0.4, 1.0) * height_range
        radius = max(4.0, height * 2.5)
        pose_x = rng.uniform(-0.4, 0.4) * size_x
        pose_y = rng.uniform(-0.4, 0.4) * size_y
        hills.append(
            SceneObject(
                id=f"H-{index + 1:02d}",
                category="terrain",
                shape="sphere",
                pose=(pose_x, pose_y, elevation_low - radius * 0.75),
                size=(radius * 2.0, radius * 2.0, height),
                color=_TERRAIN_COLOR,
            )
        )
    return hills


def _build_zones(
    world: WorldSection,
    values: dict[str, Any],
    rng: random.Random,
    elevation_low: float,
    layout: SceneLayout,
) -> list[SceneObject]:
    """Зоны: явные из мира, иначе — из числа сегментов профиля."""
    zones: list[SceneObject] = []
    if world.zones:
        for zone in world.zones:
            center_x, center_y, width, length = _polygon_bounds(zone.polygon)
            zones.append(
                SceneObject(
                    id=zone.id,
                    category="zone",
                    shape="box",
                    pose=(center_x, center_y, elevation_low + 0.05),
                    size=(max(width, 1.0), max(length, 1.0), 0.1),
                    color=_ZONE_COLORS.get(zone.kind, _ZONE_COLORS["other"]),
                    zone=zone.id,
                )
            )
        return zones

    count = min(_ZONE_CAP, max(1, _as_int(values.get("zone_partition_count"), 1)))
    size_x, size_y = world.terrain.size_m
    columns = max(1, int(count**0.5 + 0.5))
    rows = max(1, (count + columns - 1) // columns)
    cell_x = size_x / columns
    cell_y = size_y / rows
    for index in range(count):
        column = index % columns
        row = index // columns
        width = cell_x * rng.uniform(0.55, 0.85)
        length = cell_y * rng.uniform(0.55, 0.85)
        center_x = -size_x / 2 + cell_x * (column + 0.5)
        center_y = -size_y / 2 + cell_y * (row + 0.5)
        zones.append(
            SceneObject(
                id=f"Z-{index + 1:02d}",
                category="zone",
                shape="box",
                pose=(center_x, center_y, elevation_low + 0.05),
                size=(width, length, 0.1),
                color=_ZONE_COLORS["tech"],
                zone=f"Z-{index + 1:02d}",
            )
        )
    layout.generation["zones_generated"] = count
    return zones


def _build_buildings(
    world: WorldSection,
    values: dict[str, Any],
    rng: random.Random,
    zones: list[SceneObject],
    elevation_low: float,
    layout: SceneLayout,
) -> list[SceneObject]:
    """Здания: явные из мира, иначе — по числу на зону из профиля."""
    if world.buildings:
        by_id = {zone.id: zone for zone in zones}
        buildings: list[SceneObject] = []
        for building in world.buildings:
            zone = by_id.get(building.zone)
            center_x = zone.pose[0] if zone is not None else 0.0
            center_y = zone.pose[1] if zone is not None else 0.0
            width, length = building.footprint
            floors = building.floors
            height = building.height_m if building.height_m is not None else floors * 3.0
            offset_x = rng.uniform(-0.25, 0.25) * (zone.size[0] if zone else 10.0)
            offset_y = rng.uniform(-0.25, 0.25) * (zone.size[1] if zone else 10.0)
            buildings.append(
                SceneObject(
                    id=building.id,
                    category="building",
                    shape="box",
                    pose=(center_x + offset_x, center_y + offset_y, elevation_low + height / 2.0),
                    size=(float(width), float(length), float(height)),
                    color=_BUILDING_COLOR,
                    zone=building.zone,
                    asset="building",
                )
            )
        return buildings

    low, high = _as_pair(values.get("buildings_per_zone_range"), (1.0, 3.0))
    floors_low, floors_high = _as_pair(values.get("building_floors_range"), (1.0, 3.0))
    per_zone = min(_BUILDINGS_PER_ZONE_CAP, max(0, round((low + high) / 2.0)))
    buildings = []
    for zone in zones:
        for index in range(per_zone):
            width = rng.uniform(8.0, 24.0)
            length = rng.uniform(6.0, 18.0)
            floors = rng.randint(max(1, int(floors_low)), max(1, int(floors_high)))
            height = floors * 3.0
            pose_x = zone.pose[0] + rng.uniform(-0.35, 0.35) * zone.size[0]
            pose_y = zone.pose[1] + rng.uniform(-0.35, 0.35) * zone.size[1]
            buildings.append(
                SceneObject(
                    id=f"B-{zone.id}-{index + 1:02d}",
                    category="building",
                    shape="box",
                    pose=(pose_x, pose_y, elevation_low + height / 2.0),
                    size=(width, length, height),
                    color=_BUILDING_COLOR,
                    zone=zone.id,
                    asset="building",
                )
            )
    layout.generation["buildings_per_zone"] = per_zone
    return buildings


def _build_equipment(
    world: WorldSection,
    values: dict[str, Any],
    rng: random.Random,
    zones: list[SceneObject],
    elevation_low: float,
    layout: SceneLayout,
) -> list[SceneObject]:
    """Оборудование: явное из мира, иначе — из реестра объектов профиля."""
    if world.equipment:
        equipment: list[SceneObject] = []
        for item in world.equipment:
            asset, warning = resolve_asset(item.klass, scale=1.0)
            if warning:
                layout.warnings.append(
                    issue(
                        IssueCode.INVALID_VALUE,
                        f"world.equipment.{item.id}.class",
                        warning,
                        severity=Severity.WARNING,
                    )
                )
            height = float(item.height_m)
            scale = height / max(asset.size[2], 0.1)
            asset = asset.scaled(scale)
            equipment.append(
                SceneObject(
                    id=item.id,
                    category="equipment",
                    shape=asset.shape,
                    pose=(item.pose[0], item.pose[1], elevation_low + height / 2.0),
                    size=asset.size,
                    color=asset.color,
                    asset=asset.id,
                )
            )
        return equipment

    low, high = _as_pair(values.get("outdoor_equipment_count_range"), (1.0, 5.0))
    per_zone = min(_EQUIPMENT_PER_ZONE_CAP, max(0, round((low + high) / 2.0)))
    height_low, height_high = _as_pair(values.get("equipment_height_range"), (1.0, 4.0))
    classes = _equipment_classes(values)
    equipment = []
    for zone in zones:
        for index in range(per_zone):
            klass = classes[(index + rng.randint(0, len(classes) - 1)) % len(classes)]
            asset, _warning = resolve_asset(klass)
            height = rng.uniform(max(0.5, height_low), max(1.0, height_high))
            scale = height / max(asset.size[2], 0.1)
            asset = asset.scaled(scale)
            pose_x = zone.pose[0] + rng.uniform(-0.42, 0.42) * zone.size[0]
            pose_y = zone.pose[1] + rng.uniform(-0.42, 0.42) * zone.size[1]
            equipment.append(
                SceneObject(
                    id=f"E-{zone.id}-{index + 1:02d}",
                    category="equipment",
                    shape=asset.shape,
                    pose=(pose_x, pose_y, elevation_low + height / 2.0),
                    size=asset.size,
                    color=asset.color,
                    zone=zone.id,
                    asset=asset.id,
                )
            )
    layout.generation["equipment_per_zone"] = per_zone
    return equipment


def _equipment_classes(values: dict[str, Any]) -> list[str]:
    """Классы оборудования из отраслевых образов профиля."""
    raw = values.get("equipment_class_weights")
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
        if parts:
            return parts
    return ["generic"]


def _build_trees(
    world: WorldSection,
    values: dict[str, Any],
    rng: random.Random,
    size_x: float,
    size_y: float,
    elevation_low: float,
) -> list[SceneObject]:
    """Деревья: явные из мира, иначе — по плотности на гектар из профиля."""
    if world.trees:
        return [
            SceneObject(
                id=tree.id,
                category="tree",
                shape="cylinder",
                pose=(tree.pose[0], tree.pose[1], elevation_low + tree.height_m / 2.0),
                size=(
                    max(0.3, tree.height_m * 0.12),
                    max(0.3, tree.height_m * 0.12),
                    tree.height_m,
                ),
                color=_TREE_COLOR,
            )
            for tree in world.trees
        ]

    density_low, density_high = _as_pair(values.get("tree_density_range"), (1.0, 5.0))
    height_low, height_high = _as_pair(values.get("tree_height_range"), (3.0, 10.0))
    area_ha = max(0.0, float(size_x) * float(size_y)) / 10_000.0
    density = max(0.0, (density_low + density_high) / 2.0)
    count = min(_TREE_CAP, round(density * area_ha))
    trees: list[SceneObject] = []
    for index in range(count):
        height = rng.uniform(max(1.0, height_low), max(2.0, height_high))
        radius = max(0.4, height * 0.18)
        trees.append(
            SceneObject(
                id=f"T-{index + 1:03d}",
                category="tree",
                shape="cylinder",
                pose=(
                    rng.uniform(-size_x / 2, size_x / 2),
                    rng.uniform(-size_y / 2, size_y / 2),
                    elevation_low + height / 2.0,
                ),
                size=(radius * 2.0, radius * 2.0, height),
                color=_TREE_COLOR,
            )
        )
    return trees


def _build_water(
    world: WorldSection,
    values: dict[str, Any],
    rng: random.Random,
    size_x: float,
    size_y: float,
    elevation_low: float,
) -> list[SceneObject]:
    """Водоёмы: явные из мира, иначе — по числу из профиля."""
    if world.water:
        water: list[SceneObject] = []
        for body in world.water:
            center_x, center_y, width, length = _polygon_bounds(body.polygon)
            water.append(
                SceneObject(
                    id=body.id,
                    category="water",
                    shape="box",
                    pose=(center_x, center_y, elevation_low + 0.03),
                    size=(max(width, 1.0), max(length, 1.0), 0.06),
                    color=_WATER_COLOR,
                )
            )
        return water

    count = min(_WATER_CAP, max(0, _as_int(values.get("water_bodies_count"), 0)))
    water = []
    for index in range(count):
        width = rng.uniform(20.0, 60.0)
        length = rng.uniform(20.0, 60.0)
        water.append(
            SceneObject(
                id=f"W-{index + 1:02d}",
                category="water",
                shape="box",
                pose=(
                    rng.uniform(-size_x / 2 + width / 2, size_x / 2 - width / 2),
                    rng.uniform(-size_y / 2 + length / 2, size_y / 2 - length / 2),
                    elevation_low + 0.03,
                ),
                size=(width, length, 0.06),
                color=_WATER_COLOR,
            )
        )
    return water
