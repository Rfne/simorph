"""Раздел ``world``: описание сцены (TRD 5.3)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from simorph.core.models.base import (
    EntityId,
    NonNegative,
    Positive,
    SectionModel,
    StrictModel,
    Unit,
    Vec2,
    Vec3,
)

#: Тип зоны сцены.
ZoneKind = Literal["tech", "road", "water", "green", "building", "other"]

#: Модель порывов ветра.
GustModel = Literal["none", "dryden"]

#: Полигон: не меньше трёх вершин.
Polygon = Annotated[list[Vec2], Field(min_length=3)]


class Terrain(StrictModel):
    """Рельеф участка."""

    size_m: Vec2
    elevation_m: Vec2 = (0.0, 0.0)
    mesh: str | None = Field(default=None, max_length=512)


class Zone(StrictModel):
    """Технологическая зона сцены."""

    id: EntityId
    polygon: Polygon
    kind: ZoneKind = "other"


class Building(StrictModel):
    """Здание внутри зоны."""

    id: EntityId
    zone: EntityId
    footprint: Vec2
    floors: int = Field(ge=1, le=200)
    height_m: Positive | None = None


class Equipment(StrictModel):
    """Единица оборудования."""

    id: EntityId
    klass: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        alias="class", default="generic"
    )
    pose: Vec3
    height_m: Positive


class Tree(StrictModel):
    """Дерево."""

    id: EntityId
    pose: Vec3
    height_m: Positive


class WaterBody(StrictModel):
    """Водоём."""

    id: EntityId
    polygon: Polygon


class Lighting(StrictModel):
    """Освещение сцены."""

    sun_azimuth_deg: float = Field(default=135.0, ge=0.0, lt=360.0)
    sun_elevation_deg: float = Field(default=40.0, ge=-90.0, le=90.0)
    cloudiness: Unit = 0.0


class Wind(StrictModel):
    """Ветер."""

    speed_mps: NonNegative = 0.0
    direction_deg: float = Field(default=0.0, ge=0.0, lt=360.0)
    gust_model: GustModel = "none"


class WorldSection(SectionModel):
    """Мир: геометрия, зоны, оборудование, освещение (TRD 5.3)."""

    world_id: EntityId
    seed: int = Field(ge=0, le=2**32 - 1)
    terrain: Terrain
    zones: list[Zone] = Field(default_factory=list)
    buildings: list[Building] = Field(default_factory=list)
    equipment: list[Equipment] = Field(default_factory=list)
    trees: list[Tree] = Field(default_factory=list)
    water: list[WaterBody] = Field(default_factory=list)
    lighting: Lighting = Field(default_factory=Lighting)
    wind: Wind = Field(default_factory=Wind)
