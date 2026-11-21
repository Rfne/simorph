"""Команды ``simorph world``: сборка сцены и снимок (T4-01, T4-08).

Команды работают headless: тот же ``Scene``, что и в интерфейсе, только без
окна. ``build`` печатает статистику объектов и замечания, ``render`` пишет
PNG и JSON с камерой и глубиной — по нему снимок можно воспроизвести.

Код возврата: 0 — сцена собрана, 1 — сцена прервана сбоем или проект
не читается.
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
from simorph.core.repository import load_project
from simorph.core.world import CameraSpec, OrbitCamera, Scene

__all__ = ["build_world_parser", "run_world_command"]


def build_world_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``world``."""
    world = subparsers.add_parser("world", help="сцена мира: сборка и снимок")
    add_common_options(world)
    actions = world.add_subparsers(dest="world_command", metavar="ДЕЙСТВИЕ")

    build_parser = actions.add_parser("build", help="собрать сцену и показать статистику")
    add_common_options(build_parser)
    build_parser.add_argument("path", type=Path, help="путь к project.json")
    build_parser.add_argument("--limit", type=int, default=None, help="лимит объектов сцены")

    render_parser = actions.add_parser("render", help="отрисовать кадр в PNG")
    add_common_options(render_parser)
    render_parser.add_argument("path", type=Path, help="путь к project.json")
    render_parser.add_argument("-o", "--output", type=Path, required=True, help="куда писать PNG")
    render_parser.add_argument("--width", type=int, default=640, help="ширина кадра")
    render_parser.add_argument("--height", type=int, default=480, help="высота кадра")
    render_parser.add_argument("--distance", type=float, default=400.0, help="дистанция камеры, м")
    render_parser.add_argument("--yaw", type=float, default=45.0, help="азимут камеры, °")
    render_parser.add_argument("--pitch", type=float, default=-40.0, help="наклон камеры, °")
    render_parser.add_argument("--hour", type=float, default=None, help="время суток, ч")
    render_parser.add_argument(
        "--precipitation",
        choices=("none", "rain", "snow"),
        default=None,
        help="осадки в кадре",
    )
    render_parser.add_argument(
        "--no-realism",
        action="store_true",
        help="плоские цвета без освещения",
    )


def _load(path: Path) -> Any:
    return load_project(path).document


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2))


def _camera(args: argparse.Namespace) -> OrbitCamera:
    return OrbitCamera(
        target=(0.0, 0.0, 0.0),
        distance=float(args.distance),
        yaw_deg=float(args.yaw),
        pitch_deg=float(args.pitch),
    )


def _cmd_build(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    limit = args.limit if args.limit is not None else 1000
    with Scene(document.world, document.profile, limit=limit) as scene:
        stats = scene.build()
        layout = scene.layout
        payload = {
            "status": stats.status,
            "world_id": document.world.world_id,
            "seed": document.world.seed,
            "scene": stats.to_dict(),
            "generation_keys": sorted(layout.generation),
            "warnings": [item.to_dict() for item in layout.warnings],
            "error": stats.error,
        }
        if as_json:
            _write_json(payload)
        else:
            _write(f"мир      {document.world.world_id}, seed {document.world.seed}")
            _write(f"статус   {stats.status}")
            _write(
                f"объектов {stats.objects}, тел {stats.bodies},"
                f" сборка {stats.build_seconds * 1000:.1f} мс"
            )
            for name, count in stats.by_category.items():
                _write(f"  {name:<10} {count}")
            if stats.over_limit:
                _write(f"лимит {stats.limit}: пропущено {stats.truncated}")
            for item in layout.warnings:
                _write(f"  {item.path}: {item.message}")
            if stats.error:
                _write(f"ошибка: {stats.error}")
        if stats.status != "ready":
            logger.error("сцена прервана: %s", stats.error)
            return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_render(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    camera = _camera(args)
    spec = CameraSpec(width=args.width, height=args.height)
    with Scene(document.world, document.profile) as scene:
        stats = scene.build()
        if stats.status != "ready":
            logger.error("сцена прервана: %s", stats.error)
            if as_json:
                _write_json({"status": "error", "error": stats.error})
            else:
                sys.stderr.write(f"simorph: сцена прервана: {stats.error}\n")
            return int(ExitCode.DATA_ERROR)
        scene.set_realism(not args.no_realism)
        if args.hour is not None:
            scene.set_hour(args.hour)
        if args.precipitation is not None:
            scene.set_precipitation(args.precipitation)
        image, meta_path = scene.snapshot(args.output, camera, spec)
        frame = scene.render(camera, spec)
        visible = len(set(frame.body_labels.values()))
        payload = {
            "status": "ok",
            "image": str(image),
            "meta": str(meta_path),
            "width": spec.width,
            "height": spec.height,
            "depth_min": round(frame.depth_range()[0], 3),
            "depth_max": round(frame.depth_range()[1], 3),
            "objects": stats.objects,
            "visible_objects": visible,
        }
        if as_json:
            _write_json(payload)
        else:
            _write(f"снимок {image}")
            _write(f"описание {meta_path}")
            _write(
                f"{spec.width}×{spec.height}, глубина"
                f" {payload['depth_min']}…{payload['depth_max']} м,"
                f" объектов {stats.objects}"
            )
    return int(ExitCode.OK)


def run_world_command(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Диспетчер подкоманд ``world``."""
    command = getattr(args, "world_command", None)
    try:
        if command == "build":
            return _cmd_build(args, as_json=as_json, logger=logger)
        if command == "render":
            return _cmd_render(args, as_json=as_json, logger=logger)
    except ProjectError as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("не указано действие для world: build или render")
    return int(ExitCode.DATA_ERROR)
