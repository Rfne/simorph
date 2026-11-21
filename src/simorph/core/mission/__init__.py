"""Миссии: элементы, граф, тайминги, валидация и обмен (этап 6).

Пакет не зависит от Qt: граф, бюджет, валидатор и импорт/экспорт работают
и в CLI, и в тестах без интерфейса. Трёхпанельный редактор живёт в
``simorph.ui.mission_view`` и использует те же функции.
"""

from __future__ import annotations

from simorph.core.mission.elements import (
    ELEMENT_CATEGORIES,
    ELEMENT_SPECS,
    ELEMENT_TYPES,
    ElementSpec,
    element_payload,
    element_spec,
    required_fields,
)
from simorph.core.mission.exporting import (
    exercise_payload,
    mission_payload,
    to_json,
    to_yaml,
    write_exercise,
    write_mission,
    write_payload,
)
from simorph.core.mission.graph import MissionGraph, build_graph
from simorph.core.mission.importing import (
    COMMAND_LOOP_FIELDS,
    ImportedMission,
    import_mission,
    load_payload,
    mission_from_payload,
)
from simorph.core.mission.timings import MissionBudget, element_duration, mission_budget
from simorph.core.mission.validation import (
    MAX_ALTITUDE_M,
    MIN_ALTITUDE_M,
    MISSION_BUDGET_LIMIT_S,
    can_run,
    exercise_issues,
    mission_issues,
)

__all__ = [
    "COMMAND_LOOP_FIELDS",
    "ELEMENT_CATEGORIES",
    "ELEMENT_SPECS",
    "ELEMENT_TYPES",
    "MAX_ALTITUDE_M",
    "MIN_ALTITUDE_M",
    "MISSION_BUDGET_LIMIT_S",
    "ElementSpec",
    "ImportedMission",
    "MissionBudget",
    "MissionGraph",
    "build_graph",
    "can_run",
    "element_duration",
    "element_payload",
    "element_spec",
    "exercise_issues",
    "exercise_payload",
    "import_mission",
    "load_payload",
    "mission_budget",
    "mission_from_payload",
    "mission_issues",
    "mission_payload",
    "required_fields",
    "to_json",
    "to_yaml",
    "write_exercise",
    "write_mission",
    "write_payload",
]
