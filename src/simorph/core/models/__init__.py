"""Схемы семи разделов проекта."""

from simorph.core.models.agent import Actuator, AgentSection, CommLink, Joint, Link, Sensor
from simorph.core.models.exercise import ExerciseSection, Mission, MissionElement
from simorph.core.models.pipeline import PipelineEdge, PipelineNode, PipelineSection
from simorph.core.models.profile import ProfileParameter, ProfileSection
from simorph.core.models.project import (
    SECTION_NAMES,
    ProjectDocument,
    ProjectSettings,
    Transport,
    touch,
)
from simorph.core.models.report import ReportSection
from simorph.core.models.world import (
    Building,
    Equipment,
    Lighting,
    Terrain,
    Tree,
    WaterBody,
    Wind,
    WorldSection,
    Zone,
)

__all__ = [
    "SECTION_NAMES",
    "Actuator",
    "AgentSection",
    "Building",
    "CommLink",
    "Equipment",
    "ExerciseSection",
    "Joint",
    "Lighting",
    "Link",
    "Mission",
    "MissionElement",
    "PipelineEdge",
    "PipelineNode",
    "PipelineSection",
    "ProfileParameter",
    "ProfileSection",
    "ProjectDocument",
    "ProjectSettings",
    "ReportSection",
    "Sensor",
    "Terrain",
    "Transport",
    "Tree",
    "WaterBody",
    "Wind",
    "WorldSection",
    "Zone",
    "touch",
]
