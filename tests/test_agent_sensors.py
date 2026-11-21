"""Тесты публикации данных сенсоров (T5-02, FR-AGT-03, FR-AGT-06)."""

from __future__ import annotations

import pytest

from simorph.core.agent import SensorSuite, build_drone, build_rover, sensor_types, supported_types
from simorph.core.models.agent import Sensor

ALL_TYPES = {
    "imu9",
    "gps",
    "stereo_camera",
    "camera",
    "distance",
    "angle",
    "angular_rate",
    "external_io",
    "error_signal",
}


@pytest.mark.unit
def test_all_nine_types_supported_and_present() -> None:
    assert set(supported_types()) == ALL_TYPES
    assert sensor_types(build_rover()) == ALL_TYPES
    assert sensor_types(build_drone()) == {
        "camera",
        "imu9",
        "gps",
        "angular_rate",
        "distance",
        "error_signal",
    }


@pytest.mark.unit
def test_sample_covers_every_sensor() -> None:
    rover = build_rover()
    suite = SensorSuite(rover, seed=7)

    samples = suite.sample(0.5)

    assert len(samples) == len(rover.sensors)
    assert {item.type for item in samples} == ALL_TYPES
    assert all(item.t_ns == 500_000_000 for item in samples)
    assert suite.published == len(samples)


@pytest.mark.unit
def test_samples_are_deterministic_for_seed() -> None:
    first = [item.to_dict() for item in SensorSuite(build_rover(), seed=3).sample(1.25)]
    second = [item.to_dict() for item in SensorSuite(build_rover(), seed=3).sample(1.25)]
    other = [item.to_dict() for item in SensorSuite(build_rover(), seed=4).sample(1.25)]

    assert first == second
    assert first != other


@pytest.mark.unit
def test_imu_and_gps_fields() -> None:
    suite = SensorSuite(build_rover(), seed=1)
    sensors = suite.sensors

    imu = suite.sample_one(sensors["imu"], 2.0).values
    assert {"roll_deg", "pitch_deg", "yaw_deg", "quaternion", "accel_mps2", "gyro_rad_s"} <= set(
        imu
    )
    assert len(imu["quaternion"]) == 4
    assert abs(imu["accel_mps2"][2] - 9.80665) < 0.5

    gps = suite.sample_one(sensors["gps"], 2.0).values
    assert gps["fix"] is True
    assert 59.0 < gps["latitude_deg"] < 60.5
    assert 29.5 < gps["longitude_deg"] < 31.0


@pytest.mark.unit
def test_camera_frames_keep_declared_size() -> None:
    suite = SensorSuite(build_rover(), seed=1)
    sensors = suite.sensors

    stereo = suite.sample_one(sensors["cam_stereo"], 0.4).values
    assert len(stereo["left"]) == 5
    assert len(stereo["left"][0]) == 8
    assert stereo["left"] != stereo["right"]
    assert (stereo["width"], stereo["height"]) == (1280, 800)
    assert stereo["frames"] == 2
    assert stereo["depth_gt_m"][1] > stereo["depth_gt_m"][0]

    rgb = suite.sample_one(sensors["cam_rgb"], 0.4).values
    assert len(rgb["frame"]) == 5
    assert (rgb["width"], rgb["height"]) == (2560, 1440)
    assert rgb["bit_depth"] == 8


@pytest.mark.unit
def test_range_sensors_stay_within_limits() -> None:
    suite = SensorSuite(build_rover(), seed=1)
    sensors = suite.sensors

    for time_s in (0.0, 0.5, 1.0, 2.5):
        distance = suite.sample_one(sensors["lidar"], time_s).values
        assert 0.0 <= distance["distance_m"] <= distance["range_m"] == 30.0

        angle = suite.sample_one(sensors["pan_angle"], time_s).values
        assert -90.0 <= angle["angle_deg"] <= 90.0

        rate = suite.sample_one(sensors["yaw_rate"], time_s).values
        assert -200.0 <= rate["rate_dps"] <= 200.0
        assert rate["rate_rad_s"] == pytest.approx(
            rate["rate_dps"] * 3.141592653589793 / 180.0, rel=1e-4
        )


@pytest.mark.unit
def test_external_io_and_error_signal() -> None:
    suite = SensorSuite(build_rover(), seed=1)
    sensors = suite.sensors

    io = suite.sample_one(sensors["payload_io"], 0.2).values
    assert io["channels"] == 4
    assert io["levels"] == "digital"
    assert len(io["values"]) == 4
    assert all(value in (0, 1) for value in io["values"])

    fault = suite.sample_one(sensors["fault"], 0.2).values
    assert isinstance(fault["active"], bool)
    assert fault["active_level"] == "high"


@pytest.mark.unit
def test_timestamps_include_latency() -> None:
    suite = SensorSuite(build_rover(), seed=1)

    sample = suite.sample_one(suite.sensors["gps"], 1.0).values

    assert sample["t_fsin_ns"] == 1_000_000_000
    assert sample["t_monotonic_ns"] >= sample["t_fsin_ns"]


@pytest.mark.unit
def test_analog_external_io_values() -> None:
    sensor = Sensor(id="io", type="external_io", rate_hz=10.0, channels=2, levels="analog")
    agent = build_drone().model_copy(update={"sensors": [sensor]})

    values = SensorSuite(agent, seed=2).sample_one(sensor, 0.3).values

    assert values["levels"] == "analog"
    assert all(isinstance(value, float) for value in values["values"])
