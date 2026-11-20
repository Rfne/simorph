"""Документ проекта: семь разделов в одном переносимом JSON (TRD 5.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from simorph.core.models.agent import AgentSection
from simorph.core.models.base import Label, SectionModel, StrictModel
from simorph.core.models.exercise import ExerciseSection
from simorph.core.models.pipeline import PipelineSection
from simorph.core.models.profile import ProfileSection
from simorph.core.models.report import ReportSection
from simorph.core.models.world import WorldSection

#: Режим транспорта между процессами (TRD 8.2).
TransportMode = Literal["inproc", "ros2_emulated", "ros2_native"]

#: Тема оформления.
Theme = Literal["dark", "light", "system"]

#: Язык интерфейса.
Language = Literal["ru", "en"]

#: Имена семи разделов проекта в порядке TRD.
SECTION_NAMES: tuple[str, ...] = (
    "project",
    "profile",
    "world",
    "agent",
    "exercise",
    "pipeline",
    "report",
)


class Transport(StrictModel):
    """Транспорт между процессами; в MVP — ``inproc``."""

    mode: TransportMode = "inproc"
    domain_id: int = Field(default=0, ge=0, le=232)


class ProjectSettings(StrictModel):
    """Настройки, которые принадлежат проекту, а не пользователю.

    Настройки приложения (недавние файлы, путь к редактору, геометрия окон)
    хранятся в ``QSettings`` и сюда не попадают (T1-06).
    """

    theme: Theme = "dark"
    language: Language = "ru"
    offline: bool = True


class ProjectDocument(SectionModel):
    """Проект целиком: ``project.json`` из семи разделов."""

    project_id: UUID
    name: Label
    created_at: AwareDatetime
    updated_at: AwareDatetime
    profile: ProfileSection
    world: WorldSection
    agents: list[AgentSection] = Field(default_factory=list)
    exercises: list[ExerciseSection] = Field(default_factory=list)
    pipelines: list[PipelineSection] = Field(default_factory=list)
    reports: list[ReportSection] = Field(default_factory=list)
    transport: Transport = Field(default_factory=Transport)
    settings: ProjectSettings = Field(default_factory=ProjectSettings)

    @model_validator(mode="after")
    def _check_timestamps(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("'updated_at' раньше 'created_at'")
        return self

    def sections(self) -> dict[str, object]:
        """Возвращает разделы по именам, включая сам проект."""
        return {
            "project": self,
            "profile": self.profile,
            "world": self.world,
            "agent": self.agents,
            "exercise": self.exercises,
            "pipeline": self.pipelines,
            "report": self.reports,
        }


def touch(document: ProjectDocument, moment: datetime) -> ProjectDocument:
    """Возвращает копию документа с обновлённым ``updated_at``."""
    return document.model_copy(update={"updated_at": moment})
