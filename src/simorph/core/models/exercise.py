"""Раздел ``exercise``: упражнение, миссии, элементы графа (TRD 5.7)."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from simorph.core.models.base import EntityId, NonNegative, SectionModel, StrictModel, Vec3

#: Типы элементов миссии MVP (TRD 5.7).
ElementType = Literal[
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
]

#: Типы элементов, которые обязаны задавать позу.
_POSED_ELEMENTS = frozenset({"waypoint", "transit"})


class MissionElement(StrictModel):
    """Узел графа миссии."""

    id: EntityId
    type: ElementType
    next: list[EntityId] = Field(default_factory=list)
    pose: Vec3 | None = None
    speed_mps: NonNegative | None = None
    zone: EntityId | None = None
    sensor: EntityId | None = None
    duration_s: NonNegative | None = None
    ttl_s: NonNegative | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    iterations: int | None = Field(default=None, ge=1, le=10_000)
    condition: Annotated[str, StringConstraints(max_length=500)] | None = None

    @model_validator(mode="after")
    def _check_type_requirements(self) -> Self:
        if self.type in _POSED_ELEMENTS and self.pose is None:
            raise ValueError(f"элемент типа {self.type!r} требует 'pose'")
        if self.type == "observe":
            if self.zone is None:
                raise ValueError("элемент 'observe' требует 'zone'")
            if self.sensor is None:
                raise ValueError("элемент 'observe' требует 'sensor'")
        if self.type == "loop" and self.iterations is None:
            raise ValueError("элемент 'loop' требует 'iterations'")
        return self


class Mission(StrictModel):
    """Миссия одного агента внутри упражнения."""

    mission_id: EntityId
    agent_id: EntityId
    elements: Annotated[list[MissionElement], Field(min_length=1)]


class ExerciseSection(SectionModel):
    """Упражнение: мир, участники и миссии (TRD 5.7)."""

    exercise_id: EntityId
    seed: int = Field(ge=0, le=2**32 - 1)
    world_id: EntityId
    agents: list[EntityId] = Field(default_factory=list)
    missions: list[Mission] = Field(default_factory=list)
