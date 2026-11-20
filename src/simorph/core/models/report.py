"""Раздел ``report``: конфигурация отчёта (TRD 5.10)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from simorph.core.models.base import EntityId, RelativePath, SectionModel

#: Секция отчёта.
ReportSectionName = Literal["metadata", "config", "metrics", "plots", "conclusions"]

#: Формат экспорта.
ExportFormat = Literal["md", "html", "pdf"]


def _default_sections() -> list[ReportSectionName]:
    return ["metadata", "config", "metrics", "plots", "conclusions"]


def _default_export() -> list[ExportFormat]:
    return ["md"]


class ReportSection(SectionModel):
    """Отчёт: шаблон, метрики, пороги и форматы экспорта."""

    report_id: EntityId
    template: RelativePath
    pipeline_ids: list[EntityId] = Field(default_factory=list)
    sections: Annotated[list[ReportSectionName], Field(min_length=1)] = Field(
        default_factory=_default_sections
    )
    metrics: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(
        default_factory=list
    )
    thresholds: dict[str, float] = Field(default_factory=dict)
    export: Annotated[list[ExportFormat], Field(min_length=1)] = Field(
        default_factory=_default_export
    )
