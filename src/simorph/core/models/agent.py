"""Раздел ``agent``: звенья, суставы, сенсоры, актуаторы (TRD 5.4–5.6)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from simorph.core.models.base import (
    EntityId,
    NonNegative,
    Positive,
    SectionModel,
    StrictModel,
    Unit,
    Vec3,
)

#: Тип звена.
LinkType = Literal["box", "cylinder", "sphere", "mesh"]

#: Тип сустава.
JointType = Literal["fixed", "revolute", "continuous", "prismatic"]

#: Тип сенсора (TRD 5.5).
SensorType = Literal[
    "imu9",
    "gps",
    "stereo_camera",
    "camera",
    "distance",
    "angle",
    "angular_rate",
    "external_io",
    "error_signal",
]

#: Тип актуатора (TRD 5.6).
ActuatorType = Literal[
    "rotary_axis",
    "prismatic",
    "linkage",
    "thrust_vector",
    "drive_axis",
    "optical_focus",
    "external_io",
]

#: Режим стереокамеры.
CameraMode = Literal["mono", "color"]

#: Роль агента в сценарии.
AgentRole = Literal["rover", "drone"]

#: Уровень сигнала внешнего оборудования.
SignalLevel = Literal["digital", "analog"]

#: Активный уровень дискретного сигнала.
ActiveLevel = Literal["low", "high"]

#: Актуаторы, которые обязаны ссылаться на сустав.
_JOINT_ACTUATORS = frozenset({"rotary_axis", "prismatic", "drive_axis"})


class Link(StrictModel):
    """Звено кинематической модели."""

    id: EntityId
    type: LinkType
    size_m: Vec3
    mass_kg: Positive
    pose: Vec3 = (0.0, 0.0, 0.0)


class Joint(StrictModel):
    """Сустав между звеньями."""

    id: EntityId
    parent: EntityId
    child: EntityId
    type: JointType
    axis: Vec3 = (0.0, 1.0, 0.0)


class Sensor(StrictModel):
    """Сенсор, закреплённый на звене."""

    id: EntityId
    type: SensorType
    link: EntityId | None = None
    rate_hz: Positive
    resolution: tuple[int, int] | None = None
    bit_depth: int | None = Field(default=None, ge=1, le=32)
    mode: CameraMode | None = None
    baseline_m: Positive | None = None
    fov_deg: float | None = Field(default=None, gt=0.0, le=180.0)
    range_m: Positive | None = None
    noise: NonNegative | None = None
    latency_ms: NonNegative | None = None
    #: Диапазон измерения угла или угловой скорости, °.
    range_deg: tuple[float, float] | None = None
    #: Диапазон угловой скорости, °/с.
    range_dps: tuple[float, float] | None = None
    #: Точность GPS, м.
    accuracy_m: NonNegative | None = None
    #: Систематическая ошибка измерения.
    bias: float | None = None
    #: Число каналов внешнего оборудования.
    channels: int | None = Field(default=None, ge=1, le=256)
    #: Уровень сигнала внешнего оборудования.
    levels: SignalLevel | None = None
    #: Активный уровень дискретного сигнала ошибки.
    active_level: ActiveLevel | None = None
    #: Выдержка камеры, мкс.
    exposure_us: NonNegative | None = None
    #: Синхронизация камеры.
    sync: Literal["hardware", "software"] | None = None

    @model_validator(mode="after")
    def _check_type_requirements(self) -> Self:
        if self.type == "stereo_camera":
            if self.baseline_m is None:
                raise ValueError("стереокамера требует 'baseline_m'")
            if self.resolution is None:
                raise ValueError("стереокамера требует 'resolution'")
        if self.type in {"stereo_camera", "camera"} and self.resolution is None:
            raise ValueError("камера требует 'resolution'")
        if self.type == "gps" and self.range_m is not None:
            raise ValueError("GPS не использует 'range_m'")
        if self.type == "external_io" and self.channels is None:
            raise ValueError("внешнее оборудование требует 'channels'")
        return self


class Actuator(StrictModel):
    """Актуатор, приводимый командой конвейера."""

    id: EntityId
    type: ActuatorType
    joint: EntityId | None = None
    max_speed_rad_s: NonNegative | None = None
    max_speed_mps: NonNegative | None = None
    max_torque_nm: NonNegative | None = None
    max_force_n: NonNegative | None = None
    range_deg: tuple[float, float] | None = None
    range_m: tuple[NonNegative, NonNegative] | None = None
    response_ms: NonNegative | None = None
    channels: int | None = Field(default=None, ge=1, le=256)
    #: Максимальная скорость поворотного актуатора, °/с.
    max_speed_dps: NonNegative | None = None
    #: Максимальная тяга реактивного вектора, Н.
    thrust_max_n: NonNegative | None = None
    #: Максимальный наклон вектора тяги, °.
    gimbal_deg: NonNegative | None = None
    #: Суставы многозвенника.
    joints: list[EntityId] | None = None
    #: Длины звеньев многозвенника, м.
    lengths_m: list[Positive] | None = None
    #: Уровень сигнала внешнего оборудования.
    levels: SignalLevel | None = None

    @model_validator(mode="after")
    def _check_type_requirements(self) -> Self:
        if self.type in _JOINT_ACTUATORS and self.joint is None:
            raise ValueError(f"актуатор типа {self.type!r} требует 'joint'")
        if self.type == "linkage" and not self.joints:
            raise ValueError("многозвенник требует 'joints'")
        if self.type == "thrust_vector" and self.thrust_max_n is None:
            raise ValueError("реактивный вектор требует 'thrust_max_n'")
        if self.type == "optical_focus" and self.range_m is None:
            raise ValueError("оптический фокус требует 'range_m'")
        if self.type == "external_io" and self.channels is None:
            raise ValueError("внешнее оборудование требует 'channels'")
        if self.range_m is not None and self.range_m[0] > self.range_m[1]:
            raise ValueError("нижняя граница 'range_m' выше верхней")
        if self.range_deg is not None and self.range_deg[0] > self.range_deg[1]:
            raise ValueError("нижняя граница 'range_deg' выше верхней")
        return self


class CommLink(StrictModel):
    """Канал связи агента."""

    id: EntityId
    type: str = Field(min_length=1, max_length=64)
    bandwidth_mbps: Positive
    latency_ms: NonNegative = 0.0
    loss: Unit = 0.0


class DynamicsConstants(StrictModel):
    """Группа ``TC-DYN``: динамика, масса и тяга."""

    mass_kg: Positive = 12.0
    inertia_kg_m2: Vec3 = (0.20, 0.20, 0.35)
    max_thrust_n: Positive = 40.0
    motor_time_constant_s: Positive = 0.05
    max_linear_speed_mps: Positive = 15.0
    max_linear_accel_mps2: Positive = 5.0
    max_angular_rate_rad_s: Positive = 3.0
    drag_coefficient: NonNegative = 0.35
    motor_arm_m: Positive = 0.25


class SensorConstants(StrictModel):
    """Группа ``TC-SEN``: камера, шумы и задержки сенсоров."""

    camera_resolution: tuple[int, int] = (1280, 800)
    camera_fps: Positive = 120.0
    camera_fov_deg: float = Field(default=90.0, gt=0.0, le=180.0)
    gyro_noise_rad_s: NonNegative = 0.002
    accel_noise_mps2: NonNegative = 0.02
    sensor_latency_ms: NonNegative = 5.0
    detection_range_m: Positive = 60.0


class ComputeConstants(StrictModel):
    """Группа ``TC-CMP``: бюджеты и частоты контуров."""

    cpu_budget_ms: Positive = 12.0
    gpu_budget_ms: Positive = 20.0
    frame_buffer_frames: int = Field(default=4, ge=1, le=256)
    stabilization_rate_hz: Positive = 400.0
    planning_rate_hz: Positive = 20.0
    heavy_light_threshold_ms: Positive = 8.0
    pid_roll_pitch: Vec3 = (0.8, 0.05, 0.2)
    pid_yaw: Vec3 = (1.2, 0.02, 0.3)
    command_queue_limit: int = Field(default=64, ge=1, le=4096)


class EnergyConstants(StrictModel):
    """Группа ``TC-ENE``: батарея и потребление."""

    battery_capacity_wh: Positive = 99.0
    hover_power_w: Positive = 220.0
    max_thrust_power_w: Positive = 520.0
    onboard_computer_power_w: Positive = 25.0
    critical_charge_pct: float = Field(default=20.0, ge=0.0, le=100.0)


class CommConstants(StrictModel):
    """Группа ``TC-COM``: канал связи."""

    bandwidth_mbps: Positive = 50.0
    base_latency_ms: NonNegative = 5.0
    mtu_bytes: int = Field(default=1500, ge=64, le=65535)
    retransmit_buffer: int = Field(default=32, ge=1, le=1024)
    link_timeout_ms: Positive = 1000.0


class HardwareConstants(StrictModel):
    """Бортовые константы ``P_hw``: пять групп ``TC-*`` (TRD 5.4.1)."""

    tc_dyn: DynamicsConstants = Field(default_factory=DynamicsConstants)
    tc_sen: SensorConstants = Field(default_factory=SensorConstants)
    tc_cmp: ComputeConstants = Field(default_factory=ComputeConstants)
    tc_ene: EnergyConstants = Field(default_factory=EnergyConstants)
    tc_com: CommConstants = Field(default_factory=CommConstants)


class AgentSection(SectionModel):
    """Агент НРТК или БВС (TRD 5.4)."""

    agent_id: EntityId
    role: AgentRole = "rover"
    links: list[Link] = Field(default_factory=list)
    joints: list[Joint] = Field(default_factory=list)
    sensors: list[Sensor] = Field(default_factory=list)
    actuators: list[Actuator] = Field(default_factory=list)
    links_comm: list[CommLink] = Field(default_factory=list)
    hw: HardwareConstants = Field(default_factory=HardwareConstants)
