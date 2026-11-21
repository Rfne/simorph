"""Тесты реестра элементов миссии (T6-02, FR-MSN-05)."""

from __future__ import annotations

import pytest

from simorph.core.mission.elements import (
    ELEMENT_CATEGORIES,
    ELEMENT_SPECS,
    ELEMENT_TYPES,
    element_payload,
    element_spec,
    required_fields,
)
from simorph.core.models.exercise import ElementType, MissionElement

#: Типы, которые обязаны быть в реестре по TRD 5.7.
EXPECTED_TYPES = {
    "start",
    "waypoint",
    "transit",
    "observe",
    "dwell",
    "activate_sensor",
    "capture",
    "return_to_base",
    "abort",
    "loop",
    "parallel",
    "condition",
}


@pytest.mark.unit
def test_registry_covers_all_mvp_types() -> None:
    assert set(ELEMENT_TYPES) == EXPECTED_TYPES
    assert len(ELEMENT_TYPES) == 12
    assert set(ELEMENT_TYPES) == set(ElementType.__args__)


@pytest.mark.unit
def test_registry_order_is_stable() -> None:
    assert tuple(spec.type for spec in ELEMENT_SPECS) == ELEMENT_TYPES
    assert ELEMENT_TYPES[0] == "start"
    assert ELEMENT_TYPES[-1] == "condition"


@pytest.mark.unit
def test_every_spec_has_known_category_and_title() -> None:
    for spec in ELEMENT_SPECS:
        assert spec.category in ELEMENT_CATEGORIES, spec.type
        assert spec.title.strip(), spec.type
        assert spec.description.strip(), spec.type
    assert len({spec.title for spec in ELEMENT_SPECS}) == len(ELEMENT_SPECS)


@pytest.mark.unit
def test_categories_cover_registry() -> None:
    used = {spec.category for spec in ELEMENT_SPECS}
    assert used == set(ELEMENT_CATEGORIES)
    assert ELEMENT_CATEGORIES == ("control", "motion", "observation", "safety")


@pytest.mark.unit
def test_only_abort_is_terminal() -> None:
    terminal = [spec.type for spec in ELEMENT_SPECS if spec.terminal]
    assert terminal == ["abort"]


@pytest.mark.unit
def test_required_fields_match_contract() -> None:
    assert required_fields("observe") == ("zone", "sensor")
    assert required_fields("loop") == ("iterations",)
    assert required_fields("waypoint") == ("pose",)
    assert required_fields("abort") == ("condition",)
    assert required_fields("start") == ()


@pytest.mark.unit
def test_required_fields_of_unknown_type_is_empty() -> None:
    assert required_fields("teleport") == ()


@pytest.mark.unit
def test_element_spec_rejects_unknown_type() -> None:
    with pytest.raises(KeyError):
        element_spec("teleport")


@pytest.mark.unit
def test_element_payload_has_title_and_filled_fields() -> None:
    element = MissionElement(
        id="wp-1",
        type="waypoint",
        next=["wp-2"],
        pose=(10.0, 20.0, 5.0),
        speed_mps=4.0,
        ttl_s=30.0,
    )

    payload = element_payload(element)

    assert payload["id"] == "wp-1"
    assert payload["type"] == "waypoint"
    assert payload["title"] == "Точка маршрута"
    assert payload["category"] == "motion"
    assert payload["next"] == ["wp-2"]
    assert payload["pose"] == [10.0, 20.0, 5.0]
    assert payload["speed_mps"] == 4.0
    assert payload["ttl_s"] == 30.0
    assert "zone" not in payload
    assert "iterations" not in payload


@pytest.mark.unit
def test_element_payload_of_control_element() -> None:
    element = MissionElement(id="loop-1", type="loop", iterations=3, next=["wp-1"])

    payload = element_payload(element)

    assert payload["title"] == "Цикл"
    assert payload["category"] == "control"
    assert payload["iterations"] == 3
