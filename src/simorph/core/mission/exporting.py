"""Экспорт миссии и упражнения в JSON и YAML (T6-07, FR-MSN-12).

Формат — та же схема раздела ``exercise``, что лежит в проекте: миссия
экспортируется без обёртки, упражнение — с миссиями. JSON пишется
с отступами и переводами строк ``\\n``, YAML — через ``safe_dump``
с сохранением порядка полей, чтобы файл был читаемым и диффуемым.

Расширение файла выбирает формат: ``.yaml``/``.yml`` — YAML, иначе JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from simorph.core.models.exercise import ExerciseSection, Mission

__all__ = [
    "exercise_payload",
    "mission_payload",
    "to_json",
    "to_yaml",
    "write_exercise",
    "write_mission",
    "write_payload",
]

#: Расширения, которые трактуются как YAML.
YAML_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})


def mission_payload(mission: Mission) -> dict[str, Any]:
    """Миссия без обёртки: идентификатор, агент и элементы."""
    return mission.model_dump(mode="json", by_alias=True)


def exercise_payload(exercise: ExerciseSection) -> dict[str, Any]:
    """Упражнение целиком: мир, состав агентов и все миссии."""
    return exercise.model_dump(mode="json", by_alias=True)


def to_json(payload: dict[str, Any]) -> str:
    """JSON с отступами и завершающим переводом строки."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def to_yaml(payload: dict[str, Any]) -> str:
    """YAML с сохранением порядка полей и юникода."""
    return str(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)
    )


def write_payload(payload: dict[str, Any], path: Path) -> Path:
    """Пишет JSON или YAML по расширению, создавая каталоги."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = to_yaml(payload) if path.suffix.lower() in YAML_SUFFIXES else to_json(payload)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_mission(mission: Mission, path: Path) -> Path:
    """Экспорт одной миссии."""
    return write_payload(mission_payload(mission), path)


def write_exercise(exercise: ExerciseSection, path: Path) -> Path:
    """Экспорт упражнения с миссиями."""
    return write_payload(exercise_payload(exercise), path)
