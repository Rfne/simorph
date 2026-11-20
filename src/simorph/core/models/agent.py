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

    @model_validator(mode="after")
    def _check_type_requirements(self) -> Self:
        if self.type in _JOINT_ACTUATORS and self.joint is None:
            raise ValueError(f"актуатор типа {self.type!r} требует 'joint'")
        return self


class CommLink(StrictModel):
    """Канал связи агента."""

    id: EntityId
    type: str = Field(min_length=1, max_length=64)
    bandwidth_mbps: Positive
    latency_ms: NonNegative = 0.0
    loss: Unit = 0.0


class AgentSection(SectionModel):
    """Агент НРТК или БВС (TRD 5.4)."""

    agent_id: EntityId
    role: AgentRole = "rover"
    links: list[Link] = Field(default_factory=list)
    joints: list[Joint] = Field(default_factory=list)
    sensors: list[Sensor] = Field(default_factory=list)
    actuators: list[Actuator] = Field(default_factory=list)
    links_comm: list[CommLink] = Field(default_factory=list)
