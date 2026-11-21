"""Импорт миссии: JSON, YAML и read-only пакет командного контура (T6-07).

Импорт ничего не пишет: он читает файл и возвращает разобранную миссию
или пакет командного контура. Это принципиально для FR-MSN-13: пакет
командного контура — внешний артефакт, и импорт не должен его перезаписать
или создать командный контур внутри проекта.

Пакет командного контура (``mission.yaml`` из «Проработки» 4.8.1) содержит
``tasks``, ``route``, ``constraints``, ``coordination`` и ``degradation``.
Задачи, чьи типы совпадают с MVP, отображаются в элементы миссии; остальные
попадают в предупреждения. ``coordination`` сохраняется как контекст
и не превращается в исполняемый алгоритм.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from simorph.core.errors import ProjectError
from simorph.core.mission.elements import ELEMENT_TYPES
from simorph.core.models.exercise import Mission, MissionElement

__all__ = [
    "COMMAND_LOOP_FIELDS",
    "ImportedMission",
    "import_mission",
    "load_payload",
    "mission_from_payload",
]

#: Поля пакета командного контура, которые показываются в инспекторе.
COMMAND_LOOP_FIELDS: tuple[str, ...] = (
    "tasks",
    "route",
    "constraints",
    "coordination",
    "degradation",
)

#: Расширения, которые трактуются как YAML.
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


@dataclass(slots=True)
class ImportedMission:
    """Результат импорта: миссия или пакет контура, всегда только для чтения."""

    source: Path
    read_only: bool = True
    mission: Mission | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def elements(self) -> list[MissionElement]:
        return list(self.mission.elements) if self.mission is not None else []

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "read_only": self.read_only,
            "mission_id": self.mission.mission_id if self.mission is not None else None,
            "agent_id": self.mission.agent_id if self.mission is not None else None,
            "elements": len(self.elements),
            "element_types": sorted({item.type for item in self.elements}),
            "fields": sorted(self.fields),
            "warnings": list(self.warnings),
        }


def load_payload(path: Path) -> dict[str, Any]:
    """Читает JSON или YAML в словарь.

    Raises:
        ProjectError: файл не найден, не читается или не является объектом.
    """
    if not path.exists():
        raise ProjectError(f"файл миссии не найден: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectError(f"файл миссии не читается: {path}: {exc}") from exc
    try:
        payload = (
            yaml.safe_load(text) if path.suffix.lower() in _YAML_SUFFIXES else json.loads(text)
        )
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ProjectError(f"файл миссии повреждён: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectError(f"корень файла миссии должен быть объектом: {path}")
    return payload


def mission_from_payload(payload: dict[str, Any]) -> Mission:
    """Собирает миссию из словаря.

    Raises:
        ProjectError: словарь не соответствует схеме миссии.
    """
    try:
        return Mission.model_validate(payload)
    except ValueError as exc:
        raise ProjectError(f"миссия не соответствует схеме: {exc}") from exc


def import_mission(path: Path) -> ImportedMission:
    """Импортирует миссию или пакет командного контура без записи на диск."""
    payload = load_payload(path)
    if "elements" in payload:
        return ImportedMission(source=path, mission=mission_from_payload(payload))
    missions = payload.get("missions")
    if isinstance(missions, list) and missions:
        first = missions[0]
        if isinstance(first, dict):
            return ImportedMission(source=path, mission=mission_from_payload(first))
    if "tasks" in payload or any(name in payload for name in COMMAND_LOOP_FIELDS):
        return _command_loop_package(path, payload)
    raise ProjectError(f"в файле нет ни миссии, ни пакета командного контура: {path}")


def _command_loop_package(path: Path, payload: dict[str, Any]) -> ImportedMission:
    """Пакет командного контура: задачи → элементы, остальное — контекст."""
    imported = ImportedMission(
        source=path,
        fields={name: payload[name] for name in COMMAND_LOOP_FIELDS if name in payload},
    )
    tasks = payload.get("tasks")
    elements: list[MissionElement] = []
    if isinstance(tasks, list):
        for position, task in enumerate(tasks):
            if not isinstance(task, dict):
                imported.warnings.append(f"задача #{position} не объект и пропущена")
                continue
            kind = task.get("type")
            if kind not in ELEMENT_TYPES:
                imported.warnings.append(
                    f"задача {task.get('id', f'#{position}')!r}: тип {kind!r} не из MVP"
                )
                continue
            try:
                elements.append(MissionElement.model_validate(task))
            except ValueError as exc:
                imported.warnings.append(
                    f"задача {task.get('id', f'#{position}')!r} не разобрана: {exc}"
                )
    if elements:
        imported.mission = Mission(
            mission_id=str(payload.get("mission_id", "imported")),
            agent_id=str(payload.get("agent_id", "imported")),
            elements=elements,
        )
    return imported
