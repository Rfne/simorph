"""Тесты реестра бортовых констант ``P_hw`` (T5-04, FR-AGT-12, G5-04)."""

from __future__ import annotations

import json

import pytest

from simorph.core.agent import (
    HW_GROUPS,
    HW_SPECS,
    build_drone,
    build_rover,
    hw_groups,
    hw_payload,
    hw_value,
)
from simorph.core.models.agent import AgentSection, HardwareConstants

#: Ожидаемое число констант по группам TRD 5.4.1.
GROUP_SIZES = {
    "TC-DYN": 9,
    "TC-SEN": 7,
    "TC-CMP": 9,
    "TC-ENE": 5,
    "TC-COM": 5,
}


@pytest.mark.unit
def test_registry_covers_trd_table() -> None:
    assert len(HW_SPECS) == 35
    assert tuple(dict.fromkeys(spec.group for spec in HW_SPECS)) == HW_GROUPS
    assert len({spec.id for spec in HW_SPECS}) == 35
    for group, size in GROUP_SIZES.items():
        assert sum(1 for spec in HW_SPECS if spec.group == group) == size


@pytest.mark.unit
def test_every_path_resolves_in_schema() -> None:
    defaults = HardwareConstants()
    for spec in HW_SPECS:
        group, field = spec.path.split(".", 1)
        assert hasattr(getattr(defaults, group), field), spec.id
        assert spec.unit, spec.id
        assert spec.title, spec.id


@pytest.mark.unit
def test_value_and_payload_follow_agent() -> None:
    agent = build_rover()
    for spec in HW_SPECS:
        assert hw_value(agent.hw, spec) is not None

    payload = hw_payload(agent)
    assert len(payload) == 35
    mass = next(item for item in payload if item["id"] == "TC-DYN-01")
    assert mass["value"] == pytest.approx(53.0)
    assert mass["unit"] == "кг"
    assert isinstance(mass["adjustable"], bool)


@pytest.mark.unit
def test_groups_hold_all_constants() -> None:
    groups = hw_groups(build_drone())

    assert list(groups) == list(HW_GROUPS)
    assert {name: len(values) for name, values in groups.items()} == GROUP_SIZES
    assert groups["TC-DYN"]["TC-DYN-03"] == pytest.approx(8.0)
    assert groups["TC-COM"]["TC-COM-01"] == pytest.approx(100.0)


@pytest.mark.unit
def test_hw_round_trip_through_json() -> None:
    agent = build_rover()
    restored = AgentSection.model_validate_json(agent.model_dump_json())

    assert restored.hw == agent.hw
    assert hw_groups(restored) == hw_groups(agent)
    raw = json.loads(agent.model_dump_json())
    assert raw["hw"]["tc_ene"]["battery_capacity_wh"] == pytest.approx(480.0)


@pytest.mark.unit
def test_defaults_match_trd_units() -> None:
    defaults = HardwareConstants()

    assert defaults.tc_sen.camera_resolution == (1280, 800)
    assert defaults.tc_cmp.frame_buffer_frames == 4
    assert defaults.tc_com.mtu_bytes == 1500
    assert 0.0 <= defaults.tc_ene.critical_charge_pct <= 100.0
