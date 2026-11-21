"""Тесты раскладки сцены: профиль, seed, лимит, ассеты (T4-01, T4-02, T4-09)."""

from __future__ import annotations

from collections import Counter

import pytest

from simorph.core.factory import new_project
from simorph.core.models.world import (
    Building,
    Equipment,
    Tree,
    WaterBody,
    WorldSection,
    Zone,
)
from simorph.core.profile import build_reference_profile
from simorph.core.world import MAX_OBJECTS, build_layout, resolve_asset


def _world(project_document) -> WorldSection:
    return project_document.world


def test_layout_is_deterministic(project_document) -> None:
    profile = build_reference_profile("agroforce")

    first = build_layout(_world(project_document), profile)
    second = build_layout(_world(project_document), profile)

    assert first.signature() == second.signature()
    assert first.stats() == second.stats()


def test_other_seed_gives_other_layout(project_document) -> None:
    profile = build_reference_profile("agroforce")
    world = _world(project_document)
    other = world.model_copy(update={"seed": world.seed + 1})

    first = build_layout(world, profile)
    second = build_layout(other, profile)

    assert first.signature() != second.signature()


def test_layout_starts_with_terrain_and_zone(project_document) -> None:
    layout = build_layout(_world(project_document))

    assert layout.objects[0].id == "terrain"
    assert layout.objects[0].category == "terrain"
    assert layout.stats()["zone"] == 1
    assert layout.objects[1].category in {"terrain", "zone"}


def test_explicit_world_objects_are_authoritative(project_document) -> None:
    world = _world(project_document).model_copy(
        update={
            "zones": [Zone(id="Z-09", polygon=[(0, 0), (100, 0), (100, 80), (0, 80)], kind="tech")],
            "buildings": [Building(id="B-09", zone="Z-09", footprint=(20, 10), floors=2)],
            "equipment": [
                Equipment(id="E-09", **{"class": "pump"}, pose=(10, 10, 0), height_m=2.0)
            ],
            "trees": [Tree(id="T-09", pose=(50, 50, 0), height_m=7.0)],
            "water": [WaterBody(id="W-09", polygon=[(0, 0), (20, 0), (20, 20), (0, 20)])],
        }
    )

    layout = build_layout(world, build_reference_profile("agroforce"))
    ids = {item.id for item in layout.objects}

    assert {"Z-09", "B-09", "E-09", "T-09", "W-09"} <= ids
    assert layout.stats()["zone"] == 1
    assert layout.stats()["building"] == 1
    assert layout.stats()["equipment"] == 1
    assert layout.stats()["tree"] == 1
    assert layout.stats()["water"] == 1


def test_profile_drives_generated_counts(project_document) -> None:
    world = _world(project_document).model_copy(
        update={"zones": [], "buildings": [], "equipment": [], "trees": [], "water": []}
    )
    profile = build_reference_profile("agroforce")

    layout = build_layout(world, profile)
    stats = layout.stats()

    assert "zone_partition_count" in layout.generation
    assert stats["zone"] >= 1
    assert stats["building"] >= 1
    assert stats["equipment"] >= 1
    assert stats["tree"] >= 1
    assert stats["water"] >= 1


def test_generated_scene_respects_caps(project_document) -> None:
    world = _world(project_document).model_copy(
        update={"zones": [], "buildings": [], "equipment": [], "trees": [], "water": []}
    )
    layout = build_layout(world, build_reference_profile("tatneft"))

    assert layout.stats()["equipment"] <= 40 * layout.stats()["zone"]
    assert layout.stats()["tree"] <= 150
    assert layout.stats()["zone"] <= 12


def test_limit_truncates_and_warns(project_document) -> None:
    layout = build_layout(_world(project_document), build_reference_profile("agroforce"), limit=10)

    assert len(layout.objects) == 10
    assert layout.truncated > 0
    assert layout.over_limit
    assert layout.warnings
    assert "лимит" in layout.warnings[0].message


def test_default_limit_is_thousand(project_document) -> None:
    layout = build_layout(_world(project_document))

    assert MAX_OBJECTS == 1000
    assert layout.limit == 1000
    assert not layout.over_limit


def test_missing_asset_falls_back_with_message(project_document) -> None:
    world = _world(project_document).model_copy(
        update={
            "equipment": [
                Equipment(
                    id="E-X",
                    **{"class": "неизвестный-агрегат"},
                    pose=(0, 0, 0),
                    height_m=3.0,
                )
            ]
        }
    )

    layout = build_layout(world)

    assert layout.stats()["equipment"] == 1
    assert layout.warnings
    assert "не найден" in layout.warnings[0].message
    assert layout.by_category("equipment")[0].asset == "generic"


def test_resolve_asset_by_hint() -> None:
    asset, warning = resolve_asset("силос элеватора")

    assert warning is None
    assert asset.id == "silo"


def test_resolve_asset_scales_size() -> None:
    asset, _ = resolve_asset("pump", scale=2.0)

    assert asset.size[2] == pytest.approx(4.0)


def test_layout_stats_are_complete(project_document) -> None:
    layout = build_layout(_world(project_document), build_reference_profile("volga-kama"))

    assert set(layout.stats()) == {"terrain", "zone", "building", "equipment", "tree", "water"}
    assert sum(layout.stats().values()) == len(layout.objects)
    assert Counter(item.category for item in layout.objects) == Counter(layout.stats())


def test_layout_is_built_without_profile(project_document) -> None:
    layout = build_layout(_world(project_document), None)

    assert layout.objects
    assert layout.objects[0].category == "terrain"
    assert layout.stats()["tree"] > 0
    assert "zone_partition_count" not in layout.generation


def test_new_project_world_builds(project_document) -> None:
    document = new_project("test", seed=7)

    layout = build_layout(document.world, document.profile)

    assert layout.objects
    assert layout.objects[0].id == "terrain"
