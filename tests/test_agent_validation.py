"""Тесты физической валидации агента (T5-05, FR-AGT-08, FR-AGT-14)."""

from __future__ import annotations

import pytest

from simorph.core.agent import (
    agent_issues,
    build_drone,
    build_rover,
    can_launch,
    check_agent,
    sensor_data_rate_mbps,
    thrust_margin,
)
from simorph.core.issues import IssueCode, Severity
from simorph.core.models.agent import Actuator, CommLink, Sensor


def _errors(issues: list[object]) -> list[object]:
    return [item for item in issues if item.severity is Severity.ERROR]


def _warnings(issues: list[object]) -> list[object]:
    return [item for item in issues if item.severity is Severity.WARNING]


@pytest.mark.unit
def test_reference_agents_are_launchable() -> None:
    for agent in (build_rover(), build_drone()):
        issues = check_agent(agent)
        assert issues == [], agent.agent_id
        assert can_launch(agent) is True


@pytest.mark.unit
def test_agent_issues_is_synonym() -> None:
    agent = build_rover()

    assert agent_issues(agent) == check_agent(agent)


@pytest.mark.unit
def test_thrust_margin_matches_reference() -> None:
    assert thrust_margin(build_rover()) == pytest.approx(0.0)
    assert thrust_margin(build_drone()) == pytest.approx(1.255, abs=0.005)


@pytest.mark.unit
def test_weak_thrust_is_error() -> None:
    drone = build_drone()
    motors = [
        item.model_copy(update={"thrust_max_n": 1.0}) if item.type == "thrust_vector" else item
        for item in drone.actuators
    ]
    weak = drone.model_copy(update={"actuators": motors})

    issues = check_agent(weak)

    assert any("не превышает вес" in item.message for item in _errors(issues))
    assert can_launch(weak) is False


@pytest.mark.unit
def test_drone_without_motors_is_error() -> None:
    drone = build_drone()
    stripped = drone.model_copy(
        update={"actuators": [item for item in drone.actuators if item.type != "thrust_vector"]}
    )

    issues = check_agent(stripped)

    assert any("тяга отсутствует" in item.message for item in _errors(issues))


@pytest.mark.unit
def test_empty_agent_is_error() -> None:
    empty = build_rover().model_copy(
        update={"links": [], "joints": [], "sensors": [], "actuators": []}
    )

    issues = check_agent(empty)

    assert any("нет звеньев" in item.message for item in _errors(issues))
    assert can_launch(empty) is False


@pytest.mark.unit
def test_mass_mismatch_is_warning() -> None:
    rover = build_rover()
    hw = rover.hw.model_copy(
        update={"tc_dyn": rover.hw.tc_dyn.model_copy(update={"mass_kg": 200.0})}
    )
    heavy = rover.model_copy(update={"hw": hw})

    issues = check_agent(heavy)

    assert any("TC-DYN-01" in item.message for item in _warnings(issues))
    assert can_launch(heavy) is True


@pytest.mark.unit
def test_cpu_budget_over_cycle_is_error() -> None:
    rover = build_rover()
    hw = rover.hw.model_copy(
        update={"tc_cmp": rover.hw.tc_cmp.model_copy(update={"cpu_budget_ms": 100.0})}
    )

    issues = check_agent(rover.model_copy(update={"hw": hw}))

    assert any(
        item.code is IssueCode.INVALID_VALUE and "TC-CMP-01" in item.message for item in issues
    )
    assert any(item.path.endswith("cpu_budget_ms") for item in _errors(issues))


@pytest.mark.unit
def test_gpu_budget_and_threshold_are_warnings() -> None:
    rover = build_rover()
    hw = rover.hw.model_copy(
        update={
            "tc_cmp": rover.hw.tc_cmp.model_copy(
                update={"gpu_budget_ms": 100.0, "heavy_light_threshold_ms": 200.0}
            )
        }
    )

    issues = check_agent(rover.model_copy(update={"hw": hw}))

    assert any("TC-CMP-02" in item.message for item in _warnings(issues))
    assert any("TC-CMP-06" in item.message for item in _warnings(issues))


@pytest.mark.unit
def test_short_battery_is_warning() -> None:
    rover = build_rover()
    hw = rover.hw.model_copy(
        update={"tc_ene": rover.hw.tc_ene.model_copy(update={"battery_capacity_wh": 1.0})}
    )

    issues = check_agent(rover.model_copy(update={"hw": hw}))

    assert any("меньше 5 мин" in item.message for item in _warnings(issues))


@pytest.mark.unit
def test_comm_budget_is_warning() -> None:
    rover = build_rover()
    hw = rover.hw.model_copy(
        update={"tc_com": rover.hw.tc_com.model_copy(update={"bandwidth_mbps": 10.0})}
    )

    issues = check_agent(rover.model_copy(update={"hw": hw}))

    messages = [item.message for item in _warnings(issues)]
    assert any("шире TC-COM-01" in message for message in messages)
    assert any("сенсоры требуют" in message for message in messages)


@pytest.mark.unit
def test_unlinked_sensor_is_warning_and_dangling_is_error() -> None:
    rover = build_rover()
    free = Sensor(id="free_imu", type="imu9", rate_hz=50.0)
    issues = check_agent(rover.model_copy(update={"sensors": [*rover.sensors, free]}))
    assert any("не закреплён" in item.message for item in _warnings(issues))

    dangling = Sensor(id="ghost", type="imu9", link="ghost", rate_hz=50.0)
    issues = check_agent(rover.model_copy(update={"sensors": [*rover.sensors, dangling]}))
    assert any(item.code is IssueCode.DANGLING_REFERENCE for item in _errors(issues))


@pytest.mark.unit
def test_dangling_actuator_joint_is_error() -> None:
    rover = build_rover()
    ghost = Actuator(id="ghost_axis", type="rotary_axis", joint="ghost_joint", range_deg=(0.0, 1.0))

    issues = check_agent(rover.model_copy(update={"actuators": [*rover.actuators, ghost]}))

    assert any(item.code is IssueCode.DANGLING_REFERENCE for item in _errors(issues))


@pytest.mark.unit
def test_rover_without_drive_is_warning() -> None:
    rover = build_rover()
    stripped = rover.model_copy(
        update={"actuators": [item for item in rover.actuators if item.type != "drive_axis"]}
    )

    issues = check_agent(stripped)

    assert any("нет ведущей оси" in item.message for item in _warnings(issues))


@pytest.mark.unit
def test_sensor_data_rate_is_compressed() -> None:
    camera = Sensor(id="cam", type="camera", rate_hz=30.0, resolution=(2560, 1440), bit_depth=8)
    stereo = Sensor(
        id="stereo",
        type="stereo_camera",
        rate_hz=30.0,
        resolution=(2560, 1440),
        bit_depth=8,
        baseline_m=0.1,
    )
    imu = Sensor(id="imu", type="imu9", rate_hz=200.0)

    assert sensor_data_rate_mbps(camera) == pytest.approx(44.2368)
    assert sensor_data_rate_mbps(stereo) == pytest.approx(88.4736)
    assert sensor_data_rate_mbps(imu) == 0.0


@pytest.mark.unit
def test_comm_link_wider_than_budget_is_warning() -> None:
    rover = build_rover()
    hw = rover.hw.model_copy(
        update={"tc_com": rover.hw.tc_com.model_copy(update={"bandwidth_mbps": 50.0})}
    )
    wide = CommLink(id="fast", type="wifi6", bandwidth_mbps=900.0)
    issues = check_agent(rover.model_copy(update={"hw": hw, "links_comm": [wide]}))

    assert any("шире TC-COM-01" in item.message for item in _warnings(issues))
