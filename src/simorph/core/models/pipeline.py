"""Раздел ``pipeline``: граф конвейера (TRD 5.8)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from simorph.core.models.base import EntityId, NodeParams, NonNegative, SectionModel, StrictModel

#: Вид конвейера.
PipelineKind = Literal["computational", "neural", "ros2"]

#: Тип узла: ``<группа>.<операция>``.
NodeType = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$", min_length=1, max_length=64
    ),
]

#: Ссылка на порт: ``<узел>.<порт>``.
PortRef = Annotated[
    str,
    StringConstraints(pattern=r"^[\w][\w.\-]*$", min_length=3, max_length=128),
]


class PipelineNode(StrictModel):
    """Узел конвейера."""

    id: EntityId
    type: NodeType
    params: NodeParams = Field(default_factory=dict)
    budget_ms: NonNegative | None = None


class PipelineEdge(StrictModel):
    """Ребро графа: выход узла-источника → вход узла-приёмника."""

    src: PortRef = Field(alias="from")
    dst: PortRef = Field(alias="to")


class PipelineSection(SectionModel):
    """Конвейер: узлы, рёбра, вариант сборки (TRD 5.8)."""

    pipeline_id: EntityId
    kind: PipelineKind = "computational"
    variant: str | None = Field(default=None, max_length=64)
    nodes: Annotated[list[PipelineNode], Field(min_length=1)]
    edges: list[PipelineEdge] = Field(default_factory=list)
