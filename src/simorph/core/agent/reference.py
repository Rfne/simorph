"""Эталонные агенты НРТК и БВС (G5-01, G5-02).

Два агента покрывают обе роли и все типы сенсоров и актуаторов:

- НРТК — ровер: корпус, четыре колеса, поворотная камера и мачта;
  ведущие оси, поворотный и продольный актуаторы;
- БВС — квадрокоптер: корпус, четыре винта, подвес камеры;
  реактивные векторы, многозвенник, оптический фокус и внешнее
  оборудование.

Они же служат примерами для CLI и тестов: эталон проходит физическую
валидацию без ошибок, поэтому любая регрессия видна сразу.
"""

from __future__ import annotations

from simorph.core.models.agent import (
    Actuator,
    AgentSection,
    CommLink,
    HardwareConstants,
    Joint,
    Link,
    Sensor,
)
from simorph.core.schema_version import CURRENT_VERSION

__all__ = ["REFERENCE_AGENTS", "build_drone", "build_rover"]

#: Коды эталонных агентов.
REFERENCE_AGENTS: tuple[str, ...] = ("rover", "drone")


def build_rover(agent_id: str = "NRTC-01") -> AgentSection:
    """НРТК: ровер с четырьмя ведущими колёсами и поворотной камерой."""
    links = [
        Link(id="base", type="box", size_m=(1.2, 0.8, 0.4), mass_kg=45.0, pose=(0.0, 0.0, 0.25)),
        Link(
            id="wheel_fl",
            type="cylinder",
            size_m=(0.2, 0.2, 0.1),
            mass_kg=2.0,
            pose=(0.7, 0.45, -0.15),
        ),
        Link(
            id="wheel_fr",
            type="cylinder",
            size_m=(0.2, 0.2, 0.1),
            mass_kg=2.0,
            pose=(0.7, -0.45, -0.15),
        ),
        Link(
            id="wheel_rl",
            type="cylinder",
            size_m=(0.2, 0.2, 0.1),
            mass_kg=2.0,
            pose=(-0.7, 0.45, -0.15),
        ),
        Link(
            id="wheel_rr",
            type="cylinder",
            size_m=(0.2, 0.2, 0.1),
            mass_kg=2.0,
            pose=(-0.7, -0.45, -0.15),
        ),
        Link(
            id="cam_pan",
            type="cylinder",
            size_m=(0.12, 0.12, 0.1),
            mass_kg=0.6,
            pose=(0.0, 0.0, 0.3),
        ),
        Link(
            id="mast", type="cylinder", size_m=(0.06, 0.06, 0.8), mass_kg=0.8, pose=(0.0, 0.0, 0.9)
        ),
    ]
    joints = [
        Joint(
            id="wheel_fl_joint",
            parent="base",
            child="wheel_fl",
            type="continuous",
            axis=(0.0, 1.0, 0.0),
        ),
        Joint(
            id="wheel_fr_joint",
            parent="base",
            child="wheel_fr",
            type="continuous",
            axis=(0.0, 1.0, 0.0),
        ),
        Joint(
            id="wheel_rl_joint",
            parent="base",
            child="wheel_rl",
            type="continuous",
            axis=(0.0, 1.0, 0.0),
        ),
        Joint(
            id="wheel_rr_joint",
            parent="base",
            child="wheel_rr",
            type="continuous",
            axis=(0.0, 1.0, 0.0),
        ),
        Joint(
            id="cam_pan_joint",
            parent="base",
            child="cam_pan",
            type="revolute",
            axis=(0.0, 0.0, 1.0),
        ),
        Joint(id="mast_joint", parent="base", child="mast", type="prismatic", axis=(0.0, 0.0, 1.0)),
    ]
    sensors = [
        Sensor(
            id="cam_stereo",
            type="stereo_camera",
            link="cam_pan",
            rate_hz=120.0,
            resolution=(1280, 800),
            bit_depth=8,
            mode="mono",
            baseline_m=0.12,
            fov_deg=90.0,
            noise=0.01,
            latency_ms=4.0,
            sync="hardware",
        ),
        Sensor(
            id="cam_rgb",
            type="camera",
            link="cam_pan",
            rate_hz=30.0,
            resolution=(2560, 1440),
            bit_depth=8,
            fov_deg=70.0,
            exposure_us=8000.0,
            noise=0.01,
        ),
        Sensor(id="imu", type="imu9", link="base", rate_hz=200.0, noise=0.002, latency_ms=1.0),
        Sensor(id="gps", type="gps", link="base", rate_hz=10.0, accuracy_m=1.5, latency_ms=20.0),
        Sensor(id="lidar", type="distance", link="mast", rate_hz=20.0, range_m=30.0, noise=0.02),
        Sensor(id="pan_angle", type="angle", link="cam_pan", rate_hz=50.0, range_deg=(-90.0, 90.0)),
        Sensor(
            id="yaw_rate",
            type="angular_rate",
            link="base",
            rate_hz=100.0,
            range_dps=(-200.0, 200.0),
        ),
        Sensor(id="payload_io", type="external_io", rate_hz=10.0, channels=4, levels="digital"),
        Sensor(id="fault", type="error_signal", rate_hz=1.0, active_level="high"),
    ]
    actuators = [
        Actuator(
            id="drive_fl",
            type="drive_axis",
            joint="wheel_fl_joint",
            max_speed_rad_s=20.0,
            max_torque_nm=8.0,
            response_ms=20.0,
        ),
        Actuator(
            id="drive_fr",
            type="drive_axis",
            joint="wheel_fr_joint",
            max_speed_rad_s=20.0,
            max_torque_nm=8.0,
            response_ms=20.0,
        ),
        Actuator(
            id="drive_rl",
            type="drive_axis",
            joint="wheel_rl_joint",
            max_speed_rad_s=20.0,
            max_torque_nm=8.0,
            response_ms=20.0,
        ),
        Actuator(
            id="drive_rr",
            type="drive_axis",
            joint="wheel_rr_joint",
            max_speed_rad_s=20.0,
            max_torque_nm=8.0,
            response_ms=20.0,
        ),
        Actuator(
            id="cam_rotary",
            type="rotary_axis",
            joint="cam_pan_joint",
            range_deg=(-90.0, 90.0),
            max_speed_dps=60.0,
            max_torque_nm=2.0,
            response_ms=80.0,
        ),
        Actuator(
            id="mast_lift",
            type="prismatic",
            joint="mast_joint",
            range_m=(0.0, 0.8),
            max_speed_mps=0.1,
            max_force_n=120.0,
            response_ms=150.0,
        ),
    ]
    base = HardwareConstants()
    hw = base.model_copy(
        update={
            "tc_dyn": base.tc_dyn.model_copy(
                update={
                    "mass_kg": 53.0,
                    "inertia_kg_m2": (2.5, 3.5, 5.0),
                    "max_linear_speed_mps": 3.0,
                    "max_linear_accel_mps2": 1.5,
                    "max_angular_rate_rad_s": 1.2,
                    "motor_arm_m": 0.45,
                }
            ),
            "tc_cmp": base.tc_cmp.model_copy(
                update={
                    "cpu_budget_ms": 4.0,
                    "gpu_budget_ms": 6.0,
                    "stabilization_rate_hz": 200.0,
                    "planning_rate_hz": 20.0,
                    "heavy_light_threshold_ms": 3.0,
                }
            ),
            "tc_ene": base.tc_ene.model_copy(
                update={
                    "battery_capacity_wh": 480.0,
                    "hover_power_w": 120.0,
                    "max_thrust_power_w": 400.0,
                    "onboard_computer_power_w": 35.0,
                }
            ),
            "tc_com": base.tc_com.model_copy(update={"bandwidth_mbps": 500.0}),
        }
    )
    return AgentSection(
        schema_version=CURRENT_VERSION,
        agent_id=agent_id,
        role="rover",
        links=links,
        joints=joints,
        sensors=sensors,
        actuators=actuators,
        links_comm=[
            CommLink(id="wifi", type="wifi6", bandwidth_mbps=500.0, latency_ms=5.0, loss=0.01)
        ],
        hw=hw,
    )


def build_drone(agent_id: str = "BVS-01") -> AgentSection:
    """БВС: квадрокоптер с подвесом камеры и внешним оборудованием."""
    links = [
        Link(id="body", type="box", size_m=(0.4, 0.4, 0.12), mass_kg=2.2, pose=(0.0, 0.0, 0.06)),
        Link(
            id="rotor_fl",
            type="cylinder",
            size_m=(0.25, 0.25, 0.02),
            mass_kg=0.05,
            pose=(0.33, 0.33, 0.13),
        ),
        Link(
            id="rotor_fr",
            type="cylinder",
            size_m=(0.25, 0.25, 0.02),
            mass_kg=0.05,
            pose=(0.33, -0.33, 0.13),
        ),
        Link(
            id="rotor_rl",
            type="cylinder",
            size_m=(0.25, 0.25, 0.02),
            mass_kg=0.05,
            pose=(-0.33, 0.33, 0.13),
        ),
        Link(
            id="rotor_rr",
            type="cylinder",
            size_m=(0.25, 0.25, 0.02),
            mass_kg=0.05,
            pose=(-0.33, -0.33, 0.13),
        ),
        Link(
            id="cam_pan",
            type="cylinder",
            size_m=(0.06, 0.06, 0.05),
            mass_kg=0.08,
            pose=(0.0, 0.0, -0.1),
        ),
        Link(
            id="cam_tilt",
            type="box",
            size_m=(0.09, 0.05, 0.05),
            mass_kg=0.12,
            pose=(0.0, 0.0, -0.05),
        ),
    ]
    joints = [
        Joint(
            id="rotor_fl_joint", parent="body", child="rotor_fl", type="fixed", axis=(0.0, 0.0, 1.0)
        ),
        Joint(
            id="rotor_fr_joint", parent="body", child="rotor_fr", type="fixed", axis=(0.0, 0.0, 1.0)
        ),
        Joint(
            id="rotor_rl_joint", parent="body", child="rotor_rl", type="fixed", axis=(0.0, 0.0, 1.0)
        ),
        Joint(
            id="rotor_rr_joint", parent="body", child="rotor_rr", type="fixed", axis=(0.0, 0.0, 1.0)
        ),
        Joint(
            id="gimbal_pan", parent="body", child="cam_pan", type="revolute", axis=(0.0, 0.0, 1.0)
        ),
        Joint(
            id="gimbal_tilt",
            parent="cam_pan",
            child="cam_tilt",
            type="revolute",
            axis=(0.0, 1.0, 0.0),
        ),
    ]
    sensors = [
        Sensor(
            id="cam_main",
            type="camera",
            link="cam_tilt",
            rate_hz=30.0,
            resolution=(2560, 1440),
            bit_depth=8,
            fov_deg=70.0,
            exposure_us=6000.0,
            noise=0.01,
            latency_ms=6.0,
        ),
        Sensor(id="imu", type="imu9", link="body", rate_hz=400.0, noise=0.001, latency_ms=1.0),
        Sensor(id="gps", type="gps", link="body", rate_hz=10.0, accuracy_m=1.0, latency_ms=15.0),
        Sensor(
            id="yaw_rate",
            type="angular_rate",
            link="body",
            rate_hz=200.0,
            range_dps=(-500.0, 500.0),
        ),
        Sensor(id="altitude", type="distance", link="body", rate_hz=25.0, range_m=40.0, noise=0.03),
        Sensor(id="fault", type="error_signal", rate_hz=1.0, active_level="low"),
    ]
    actuators = [
        Actuator(
            id="motor_fl",
            type="thrust_vector",
            joint="rotor_fl_joint",
            thrust_max_n=8.0,
            gimbal_deg=0.0,
            response_ms=30.0,
            max_force_n=8.0,
        ),
        Actuator(
            id="motor_fr",
            type="thrust_vector",
            joint="rotor_fr_joint",
            thrust_max_n=8.0,
            gimbal_deg=0.0,
            response_ms=30.0,
            max_force_n=8.0,
        ),
        Actuator(
            id="motor_rl",
            type="thrust_vector",
            joint="rotor_rl_joint",
            thrust_max_n=8.0,
            gimbal_deg=0.0,
            response_ms=30.0,
            max_force_n=8.0,
        ),
        Actuator(
            id="motor_rr",
            type="thrust_vector",
            joint="rotor_rr_joint",
            thrust_max_n=8.0,
            gimbal_deg=0.0,
            response_ms=30.0,
            max_force_n=8.0,
        ),
        Actuator(
            id="gimbal",
            type="linkage",
            joints=["gimbal_pan", "gimbal_tilt"],
            lengths_m=[0.06, 0.05],
            max_torque_nm=1.5,
            response_ms=60.0,
        ),
        Actuator(
            id="focus",
            type="optical_focus",
            joint="gimbal_tilt",
            range_m=(0.5, 30.0),
            max_speed_mps=2.0,
            response_ms=100.0,
        ),
        Actuator(id="payload", type="external_io", channels=4, levels="digital", response_ms=5.0),
    ]
    base = HardwareConstants()
    hw = base.model_copy(
        update={
            "tc_dyn": base.tc_dyn.model_copy(
                update={
                    "mass_kg": 2.4,
                    "inertia_kg_m2": (0.02, 0.02, 0.035),
                    "max_thrust_n": 8.0,
                    "max_linear_speed_mps": 15.0,
                    "max_linear_accel_mps2": 5.0,
                    "max_angular_rate_rad_s": 3.0,
                    "motor_arm_m": 0.25,
                }
            ),
            "tc_sen": base.tc_sen.model_copy(
                update={
                    "camera_resolution": (2560, 1440),
                    "camera_fps": 30.0,
                    "camera_fov_deg": 70.0,
                    "detection_range_m": 40.0,
                }
            ),
            "tc_cmp": base.tc_cmp.model_copy(
                update={
                    "cpu_budget_ms": 1.5,
                    "gpu_budget_ms": 8.0,
                    "stabilization_rate_hz": 400.0,
                    "planning_rate_hz": 20.0,
                    "heavy_light_threshold_ms": 5.0,
                }
            ),
            "tc_ene": base.tc_ene.model_copy(
                update={
                    "battery_capacity_wh": 99.0,
                    "hover_power_w": 220.0,
                    "max_thrust_power_w": 520.0,
                }
            ),
            "tc_com": base.tc_com.model_copy(update={"bandwidth_mbps": 100.0}),
        }
    )
    return AgentSection(
        schema_version=CURRENT_VERSION,
        agent_id=agent_id,
        role="drone",
        links=links,
        joints=joints,
        sensors=sensors,
        actuators=actuators,
        links_comm=[
            CommLink(id="radio", type="lte", bandwidth_mbps=100.0, latency_ms=20.0, loss=0.02)
        ],
        hw=hw,
    )
