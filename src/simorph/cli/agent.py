"""Команды ``simorph agent``: сборка, проверка, экспорт, предпросмотр.

Те же расчёты, что в интерфейсе: кинематическое дерево, физическая
валидация, URDF-подобный экспорт и кадр предпросмотра. Команды работают
headless — PyBullet подключается только для ``preview``.

Код возврата: 0 — успех, 1 — ошибка данных, в том числе физически
несогласованный агент.
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
from simorph.core.agent import (
    AgentPreview,
    build_tree,
    check_agent,
    export_manifest,
    hw_groups,
    initial_camera,
    thrust_margin,
    write_urdf,
)
from simorph.core.errors import ProjectError
from simorph.core.issues import Severity, issues_to_payload
from simorph.core.models.agent import AgentSection
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import load_project
from simorph.core.world import CameraSpec, save_snapshot

__all__ = ["build_agent_parser", "run_agent_command"]


def build_agent_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``agent``."""
    agent = subparsers.add_parser("agent", help="агент: дерево, проверка, экспорт, предпросмотр")
    add_common_options(agent)
    actions = agent.add_subparsers(dest="agent_command", metavar="ДЕЙСТВИЕ")

    build_parser = actions.add_parser("build", help="показать дерево, сенсоры и P_hw")
    add_common_options(build_parser)
    build_parser.add_argument("path", type=Path, help="путь к project.json")
    build_parser.add_argument("--index", type=int, default=0, help="номер агента в проекте")

    validate_parser = actions.add_parser("validate", help="физическая валидация агента")
    add_common_options(validate_parser)
    validate_parser.add_argument("path", type=Path, help="путь к project.json")
    validate_parser.add_argument("--index", type=int, default=0, help="номер агента в проекте")

    export_parser = actions.add_parser("export", help="экспорт URDF-подобного файла")
    add_common_options(export_parser)
    export_parser.add_argument("path", type=Path, help="путь к project.json")
    export_parser.add_argument("-o", "--output", type=Path, required=True, help="куда писать XML")
    export_parser.add_argument("--index", type=int, default=0, help="номер агента в проекте")

    preview_parser = actions.add_parser("preview", help="отрисовать предпросмотр в PNG")
    add_common_options(preview_parser)
    preview_parser.add_argument("path", type=Path, help="путь к project.json")
    preview_parser.add_argument("-o", "--output", type=Path, required=True, help="куда писать PNG")
    preview_parser.add_argument("--index", type=int, default=0, help="номер агента в проекте")
    preview_parser.add_argument("--width", type=int, default=640, help="ширина кадра")
    preview_parser.add_argument("--height", type=int, default=480, help="высота кадра")
    preview_parser.add_argument("--distance", type=float, default=None, help="дистанция камеры, м")
    preview_parser.add_argument("--yaw", type=float, default=35.0, help="азимут камеры, °")
    preview_parser.add_argument("--pitch", type=float, default=-25.0, help="наклон камеры, °")
    preview_parser.add_argument("--no-zones", action="store_true", help="без зон обслуживания")


def _load(path: Path) -> ProjectDocument:
    return load_project(path).document


def _agent(document: ProjectDocument, index: int) -> AgentSection:
    if not document.agents:
        raise ProjectError("в проекте нет агентов")
    if not 0 <= index < len(document.agents):
        raise ProjectError(f"агент с номером {index} не найден: в проекте {len(document.agents)}")
    return document.agents[index]


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_build(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    agent = _agent(document, args.index)
    tree = build_tree(agent)
    groups = hw_groups(agent)
    payload: dict[str, Any] = {
        "status": "ok",
        "agent_id": agent.agent_id,
        "role": agent.role,
        "tree": tree.to_dict(),
        "sensors": [
            {"id": item.id, "type": item.type, "link": item.link} for item in agent.sensors
        ],
        "actuators": [{"id": item.id, "type": item.type} for item in agent.actuators],
        "sensor_types": sorted({item.type for item in agent.sensors}),
        "actuator_types": sorted({item.type for item in agent.actuators}),
        "hw": groups,
        "hw_constants": sum(len(group) for group in groups.values()),
    }
    if as_json:
        _write_json(payload)
        return int(ExitCode.OK)
    _write(f"агент    {agent.agent_id} ({agent.role})")
    _write(
        f"звеньев {len(agent.links)}, суставов {len(agent.joints)},"
        f" корень {tree.root or '—'}, масса {tree.total_mass_kg:.2f} кг"
    )
    _write(f"сенсоры  {len(agent.sensors)}: {', '.join(payload['sensor_types'])}")
    _write(f"актуаторы {len(agent.actuators)}: {', '.join(payload['actuator_types'])}")
    for group, values in groups.items():
        _write(f"  {group:<7} {len(values)} констант")
    for message in tree.errors:
        _write(f"  ошибка дерева: {message}")
    return int(ExitCode.OK)


def _cmd_validate(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    agent = _agent(document, args.index)
    issues = check_agent(agent, index=args.index)
    errors = sum(1 for item in issues if item.severity is Severity.ERROR)
    payload = {
        "status": "ok" if errors == 0 else "error",
        "agent_id": agent.agent_id,
        "errors": errors,
        "warnings": len(issues) - errors,
        "issues": issues_to_payload(issues),
        "thrust_margin": round(thrust_margin(agent), 3),
        "launch_allowed": errors == 0,
    }
    if as_json:
        _write_json(payload)
    else:
        _write(f"агент {agent.agent_id}: ошибок {errors}, предупреждений {len(issues) - errors}")
        _write(f"тяга/вес {payload['thrust_margin']}")
        for item in issues:
            _write(f"  {item.severity}: {item.path}: {item.message}")
    if errors:
        logger.error("агент не проходит физическую валидацию: ошибок %d", errors)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_export(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    agent = _agent(document, args.index)
    path = write_urdf(agent, args.output)
    manifest = export_manifest(agent)
    payload = {"status": "ok", "file": str(path), "manifest": manifest}
    if as_json:
        _write_json(payload)
    else:
        _write(f"экспорт {path}")
        _write(
            f"звеньев {manifest['links']}, суставов {manifest['joints']},"
            f" сенсоров {manifest['sensors']}, актуаторов {manifest['actuators']},"
            f" P_hw {manifest['hw_constants']}"
        )
    return int(ExitCode.OK)


def _cmd_preview(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    agent = _agent(document, args.index)
    camera = initial_camera(agent)
    if args.distance is not None:
        camera.distance = float(args.distance)
    camera.yaw_deg = float(args.yaw)
    camera.pitch_deg = float(args.pitch)
    spec = CameraSpec(width=args.width, height=args.height)
    preview = AgentPreview(agent)
    try:
        stats = preview.rebuild(zones=not args.no_zones)
        if stats.status != "ready":
            logger.error("предпросмотр прерван: %s", stats.error)
            if as_json:
                _write_json({"status": "error", "error": stats.error})
            else:
                sys.stderr.write(f"simorph: предпросмотр прерван: {stats.error}\n")
            return int(ExitCode.DATA_ERROR)
        frame = preview.render(camera, spec)
        meta = {
            "agent_id": agent.agent_id,
            "role": agent.role,
            "camera": camera.to_dict(),
            "sensor": spec.to_dict(),
            "preview": stats.to_dict(),
            "depth_min": round(frame.depth_range()[0], 3),
            "depth_max": round(frame.depth_range()[1], 3),
        }
        image, meta_path = save_snapshot(args.output, frame.rgb, meta)
        payload = {
            "status": "ok",
            "image": str(image),
            "meta": str(meta_path),
            "links": stats.links,
            "zones": stats.zones,
            "collisions": [list(pair) for pair in stats.collisions],
            "build_ms": round(stats.build_ms, 3),
        }
        if as_json:
            _write_json(payload)
        else:
            _write(f"предпросмотр {image}")
            _write(
                f"звеньев {stats.links}, зон {stats.zones},"
                f" коллизий {len(stats.collisions)}, сборка {stats.build_ms:.1f} мс"
            )
    finally:
        preview.close()
    return int(ExitCode.OK)


def run_agent_command(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Диспетчер подкоманд ``agent``."""
    command = getattr(args, "agent_command", None)
    try:
        if command == "build":
            return _cmd_build(args, as_json=as_json, logger=logger)
        if command == "validate":
            return _cmd_validate(args, as_json=as_json, logger=logger)
        if command == "export":
            return _cmd_export(args, as_json=as_json, logger=logger)
        if command == "preview":
            return _cmd_preview(args, as_json=as_json, logger=logger)
    except ProjectError as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("не указано действие для agent: build, validate, export или preview")
    return int(ExitCode.DATA_ERROR)
