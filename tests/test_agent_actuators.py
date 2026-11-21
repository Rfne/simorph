"""Тесты приёма команд актуаторами (T5-03, FR-AGT-04, FR-AGT-07)."""

from __future__ import annotations

import pytest

from simorph.core.agent import ActuatorCommand, ActuatorController, build_drone, build_rover
from simorph.core.models.agent import Actuator

ALL_TYPES = {
    "rotary_axis",
    "prismatic",
    "linkage",
    "thrust_vector",
    "drive_axis",
    "optical_focus",
    "external_io",
}


@pytest.mark.unit
def test_reference_agents_cover_all_types() -> None:
    rover = {item.type for item in build_rover().actuators}
    drone = {item.type for item in build_drone().actuators}

    assert rover | drone == ALL_TYPES
    assert rover == {"drive_axis", "rotary_axis", "prismatic"}


@pytest.mark.unit
def test_initial_state_matches_actuator_set() -> None:
    rover = build_rover()
    controller = ActuatorController(rover)

    assert set(controller.states) == {item.id for item in rover.actuators}
    assert controller.state("drive_fl").position == pytest.approx(0.0)
    assert controller.state("cam_rotary").accepted is True


@pytest.mark.unit
def test_rotary_axis_clamps_and_limits_speed() -> None:
    controller = ActuatorController(build_rover())

    state = controller.command("cam_rotary", 200.0, dt_s=0.1)

    assert state.target == pytest.approx(90.0)
    assert state.saturated is True
    assert state.message
    assert 0.0 < state.position <= 60.0 * 0.1 + 1e-9
    assert state.velocity == pytest.approx(state.position / 0.1, rel=1e-6)


@pytest.mark.unit
def test_prismatic_and_drive_limits() -> None:
    controller = ActuatorController(build_rover())

    lift = controller.command("mast_lift", 5.0, dt_s=1.0)
    assert lift.target == pytest.approx(0.8)
    assert lift.saturated is True
    assert 0.0 < lift.position <= 0.1

    drive = controller.command("drive_fl", 50.0, dt_s=0.5)
    assert drive.target == pytest.approx(20.0)
    assert drive.saturated is True
    assert -20.0 <= drive.position <= 20.0


@pytest.mark.unit
def test_drone_actuators_accept_commands() -> None:
    controller = ActuatorController(build_drone())

    thrust = controller.command("motor_fl", 100.0, dt_s=0.05)
    assert thrust.target == pytest.approx(8.0)
    assert thrust.saturated is True
    assert thrust.accepted is True

    linkage = controller.command("gimbal", [30.0, -15.0], dt_s=0.05)
    assert isinstance(linkage.target, list)
    assert len(linkage.target) == 2
    assert isinstance(linkage.position, list)

    focus = controller.command("focus", 100.0, dt_s=0.2)
    assert focus.target == pytest.approx(30.0)
    assert focus.saturated is True

    payload = controller.command("payload", 0.7, dt_s=0.01)
    assert payload.target == pytest.approx(1.0)
    assert payload.saturated is True


@pytest.mark.unit
def test_analog_external_io_keeps_value() -> None:
    actuator = Actuator(id="dac", type="external_io", channels=2, levels="analog", response_ms=0.0)
    agent = build_rover().model_copy(update={"actuators": [actuator]})
    controller = ActuatorController(agent)

    state = controller.command("dac", 0.4, dt_s=0.01)

    assert state.target == pytest.approx(0.4)
    assert state.position == pytest.approx(0.4)
    assert state.saturated is False


@pytest.mark.unit
def test_commands_converge_to_target() -> None:
    controller = ActuatorController(build_rover())

    for _ in range(400):
        controller.command("cam_rotary", 45.0, dt_s=0.01)

    state = controller.state("cam_rotary")
    assert state.position == pytest.approx(45.0, abs=1e-6)
    assert state.saturated is False


@pytest.mark.unit
def test_command_many_keeps_order_and_reports() -> None:
    controller = ActuatorController(build_rover())

    states = controller.command_many(
        [
            ActuatorCommand("drive_fl", 1.0),
            ActuatorCommand("drive_fr", 2.0),
            ActuatorCommand("drive_rl", 3.0),
        ],
        dt_s=0.01,
    )

    assert [item.actuator_id for item in states] == ["drive_fl", "drive_fr", "drive_rl"]
    assert all(item.accepted for item in states)
    assert all(isinstance(item.to_dict()["velocity"], float) for item in states)


@pytest.mark.unit
def test_unknown_actuator_raises() -> None:
    controller = ActuatorController(build_rover())

    with pytest.raises(KeyError):
        controller.command("ghost", 1.0)


@pytest.mark.unit
def test_state_to_dict_is_plain_data() -> None:
    controller = ActuatorController(build_drone())

    payload = controller.command("gimbal", [10.0, 5.0]).to_dict()

    assert payload["actuator_id"] == "gimbal"
    assert payload["type"] == "linkage"
    assert isinstance(payload["target"], list)
    assert payload["accepted"] is True
