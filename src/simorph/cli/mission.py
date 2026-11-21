"""Команды ``simorph mission``: сборка, проверка, экспорт, импорт (этап 6).

Те же расчёты, что в редакторе: граф миссии, бюджет, пять классов замечаний
и обмен JSON/YAML. Импорт всегда read-only: файл читается, но не меняется,
поэтому команду можно запускать на пакете командного контура без риска.

Код возврата: 0 — успех, 1 — ошибка данных, в том числе миссия, которая
не проходит валидацию.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from simorph.cli.exit_codes import ExitCode
from simorph.cli.project import add_common_options
from simorph.core.errors import ProjectError
from simorph.core.issues import Severity, issues_to_payload
from simorph.core.mission import (
    build_graph,
    exercise_payload,
    import_mission,
    mission_budget,
    mission_issues,
    mission_payload,
    write_payload,
)
from simorph.core.models.exercise import ExerciseSection, Mission
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import load_project

__all__ = ["build_mission_parser", "run_mission_command"]


def build_mission_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``mission``."""
    mission = subparsers.add_parser(
        "mission", help="упражнения и миссии: граф, бюджет, проверка, экспорт, импорт"
    )
    add_common_options(mission)
    actions = mission.add_subparsers(dest="mission_command", metavar="ДЕЙСТВИЕ")

    build_parser = actions.add_parser("build", help="показать граф и бюджет миссии")
    add_common_options(build_parser)
    build_parser.add_argument("path", type=Path, help="путь к project.json")
    _add_selection(build_parser)

    validate_parser = actions.add_parser("validate", help="проверить граф, тайминги и geofence")
    add_common_options(validate_parser)
    validate_parser.add_argument("path", type=Path, help="путь к project.json")
    _add_selection(validate_parser)

    export_parser = actions.add_parser("export", help="экспорт миссии или упражнения")
    add_common_options(export_parser)
    export_parser.add_argument("path", type=Path, help="путь к project.json")
    export_parser.add_argument("-o", "--output", type=Path, required=True, help="куда писать файл")
    _add_selection(export_parser)
    export_parser.add_argument(
        "--exercise-only",
        action="store_true",
        help="экспортировать упражнение целиком, а не одну миссию",
    )

    import_parser = actions.add_parser("import", help="read-only импорт миссии или пакета")
    add_common_options(import_parser)
    import_parser.add_argument("path", type=Path, help="файл миссии JSON/YAML")


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--exercise", type=int, default=0, help="номер упражнения в проекте")
    parser.add_argument("--mission", type=int, default=0, help="номер миссии в упражнении")


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2))


def _load(path: Path) -> ProjectDocument:
    return load_project(path).document


def _exercise(document: ProjectDocument, index: int) -> ExerciseSection:
    if not document.exercises:
        raise ProjectError("в проекте нет упражнений")
    if not 0 <= index < len(document.exercises):
        raise ProjectError(
            f"упражнение с номером {index} не найдено: в проекте {len(document.exercises)}"
        )
    return document.exercises[index]


def _mission(exercise: ExerciseSection, index: int) -> Mission:
    if not exercise.missions:
        raise ProjectError(f"в упражнении {exercise.exercise_id!r} нет миссий")
    if not 0 <= index < len(exercise.missions):
        raise ProjectError(
            f"миссия с номером {index} не найдена: в упражнении {len(exercise.missions)}"
        )
    return exercise.missions[index]


def _cmd_build(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    exercise = _exercise(document, args.exercise)
    mission = _mission(exercise, args.mission)
    graph = build_graph(mission.elements)
    budget = mission_budget(mission.elements)
    issues = mission_issues(mission, world=document.world, agents=document.agents, prefix="mission")
    errors = sum(1 for item in issues if item.severity is Severity.ERROR)
    types = sorted({item.type for item in mission.elements})
    payload = {
        "status": "ok" if errors == 0 else "error",
        "exercise_id": exercise.exercise_id,
        "mission_id": mission.mission_id,
        "agent_id": mission.agent_id,
        "elements": len(mission.elements),
        "types": types,
        "graph": graph.to_dict(),
        "budget": budget.to_dict(),
        "errors": errors,
        "warnings": len(issues) - errors,
    }
    if as_json:
        _write_json(payload)
        return int(ExitCode.OK if errors == 0 else ExitCode.DATA_ERROR)
    _write(f"упражнение {exercise.exercise_id}, миссия {mission.mission_id} ({mission.agent_id})")
    _write(
        f"элементов {len(mission.elements)}, слоёв {graph.layer_count}, старт {graph.start or '—'}"
    )
    _write(f"типы: {', '.join(types)}")
    _write(
        f"бюджет {budget.total_s:.1f} с, критический путь"
        f" {' → '.join(budget.critical_path) or '—'}, параллелизм {budget.parallel_width}"
    )
    _write(f"замечания: ошибок {errors}, предупреждений {len(issues) - errors}")
    return int(ExitCode.OK if errors == 0 else ExitCode.DATA_ERROR)


def _cmd_validate(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    exercise = _exercise(document, args.exercise)
    mission = _mission(exercise, args.mission)
    issues = mission_issues(mission, world=document.world, agents=document.agents, prefix="mission")
    budget = mission_budget(mission.elements)
    errors = sum(1 for item in issues if item.severity is Severity.ERROR)
    payload = {
        "status": "ok" if errors == 0 else "error",
        "mission_id": mission.mission_id,
        "errors": errors,
        "warnings": len(issues) - errors,
        "issues": issues_to_payload(issues),
        "budget": budget.to_dict(),
        "can_run": errors == 0,
    }
    if as_json:
        _write_json(payload)
    else:
        _write(
            f"миссия {mission.mission_id}: ошибок {errors}, предупреждений {len(issues) - errors}"
        )
        _write(f"бюджет {budget.total_s:.1f} с при TTL {budget.ttl_s}")
        for item in issues:
            _write(f"  {item.severity}: {item.path}: {item.message}")
    if errors:
        logger.error("миссия не проходит валидацию: ошибок %d", errors)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_export(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    exercise = _exercise(document, args.exercise)
    if args.exercise_only:
        payload = exercise_payload(exercise)
        written = write_payload(payload, args.output)
        kind = "exercise"
        identifier = exercise.exercise_id
    else:
        mission = _mission(exercise, args.mission)
        payload = mission_payload(mission)
        written = write_payload(payload, args.output)
        kind = "mission"
        identifier = mission.mission_id
    result = {
        "status": "ok",
        "file": str(written),
        "kind": kind,
        "id": identifier,
        "bytes": written.stat().st_size,
    }
    if as_json:
        _write_json(result)
    else:
        _write(f"экспорт {kind} {identifier}: {written}")
    return int(ExitCode.OK)


def _cmd_import(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    imported = import_mission(args.path)
    payload = {"status": "ok", **imported.to_dict()}
    if as_json:
        _write_json(payload)
    else:
        _write(f"импорт (только чтение): {imported.source}")
        if imported.mission is not None:
            _write(
                f"миссия {imported.mission.mission_id} ({imported.mission.agent_id}):"
                f" элементов {len(imported.elements)}"
            )
        else:
            _write("миссия не распознана: только контекст пакета командного контура")
        if imported.fields:
            _write(f"поля пакета: {', '.join(sorted(imported.fields))}")
        for warning in imported.warnings:
            _write(f"  предупреждение: {warning}")
    return int(ExitCode.OK)


def run_mission_command(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Диспетчер подкоманд ``mission``."""
    command = getattr(args, "mission_command", None)
    try:
        if command == "build":
            return _cmd_build(args, as_json=as_json, logger=logger)
        if command == "validate":
            return _cmd_validate(args, as_json=as_json, logger=logger)
        if command == "export":
            return _cmd_export(args, as_json=as_json, logger=logger)
        if command == "import":
            return _cmd_import(args, as_json=as_json, logger=logger)
    except ProjectError as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("не указано действие для mission: build, validate, export или import")
    return int(ExitCode.DATA_ERROR)
