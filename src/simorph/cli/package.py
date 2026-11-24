"""Команды ``simorph export-package`` и ``simorph verify-package`` (T11-07, G11-05).

``export-package`` собирает каталог для борта: манифест, модели, конвейер,
калибровку, скрипт конвертации и контрольные суммы. Один вызов может собрать
пакеты для нескольких целей: ``--target rk3588 --target a733``.

``verify-package`` — обратная операция: читает манифест, сверяет схему и
хэши файлов. Обе команды работают без GUI и без сети.

Код возврата: 0 — успех, 1 — ошибка данных или расхождение, 2 — внутренняя.
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
from simorph.core.canonical import compute_hash
from simorph.core.errors import ProjectError
from simorph.core.package import (
    MANIFEST_NAME,
    PROFILE_NAMES,
    PackageConfig,
    build_package,
    verify_checksums,
    verify_manifest,
)
from simorph.core.repository import load_project

__all__ = ["build_package_parser", "run_package_command"]


def build_package_parser(subparsers: Any) -> None:
    """Регистрирует ``export-package`` и ``verify-package``."""
    export_parser = subparsers.add_parser(
        "export-package",
        help="собрать пакет для борта: манифест, модели, скрипты и суммы",
    )
    add_common_options(export_parser)
    export_parser.add_argument(
        "--model",
        action="append",
        default=[],
        type=Path,
        metavar="ФАЙЛ",
        help="модель пакета: ONNX-источник или готовый RKNN/NBG (можно повторять)",
    )
    export_parser.add_argument("--pipeline", type=Path, default=None, help="конфигурация конвейера")
    export_parser.add_argument("--calibration", type=Path, default=None, help="калибровка")
    export_parser.add_argument(
        "--target",
        action="append",
        choices=PROFILE_NAMES,
        default=None,
        help=f"целевой борт (можно повторять); по умолчанию {PROFILE_NAMES[0]}",
    )
    export_parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="project.json: оттуда берутся хэш проекта и каталог вывода",
    )
    export_parser.add_argument("-o", "--output", type=Path, default=None, help="каталог пакетов")
    export_parser.add_argument("--run", dest="run_id", default=None, help="идентификатор прогона")
    export_parser.add_argument(
        "--convert",
        action="store_true",
        help="сконвертировать ONNX в формат цели, если тулчейн установлен",
    )

    verify_parser = subparsers.add_parser(
        "verify-package",
        help="проверить манифест и контрольные суммы пакета",
    )
    add_common_options(verify_parser)
    verify_parser.add_argument("path", type=Path, help="каталог пакета")


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _project_context(path: Path | None) -> tuple[str | None, Path | None]:
    """Хэш проекта и каталог рядом с ним."""
    if path is None:
        return None, None
    if not path.is_file():
        raise ProjectError(f"проект не найден: {path}")
    loaded = load_project(path)
    return compute_hash(loaded.document), path.parent


def _cmd_export(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    if not args.model:
        raise ProjectError("нужна хотя бы одна модель: --model")
    project_hash, project_dir = _project_context(args.project)
    targets = args.target or [PROFILE_NAMES[0]]
    output = args.output if args.output is not None else (project_dir or Path.cwd()) / "packages"

    reports = []
    for target in targets:
        report = build_package(
            output / target,
            PackageConfig(
                target=target,
                models=list(args.model),
                pipeline=args.pipeline,
                calibration=args.calibration,
                run_id=args.run_id,
                project_hash=project_hash,
                convert=bool(args.convert),
            ),
        )
        reports.append(report)
        if as_json:
            continue
        _write(report.summary())
        for conversion in report.conversions:
            _write(f"  {conversion.summary()}")

    if as_json:
        _write_json(
            {
                "status": "ok" if all(item.ok for item in reports) else "error",
                "packages": [item.to_dict() for item in reports],
            }
        )
    logger.info("пакеты собраны: %d", len(reports))
    return int(ExitCode.OK if all(item.ok for item in reports) else ExitCode.DATA_ERROR)


def _cmd_verify(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    base = args.path
    if not base.is_dir():
        raise ProjectError(f"каталог пакета не найден: {base}")
    manifest = verify_manifest(base / MANIFEST_NAME, root=base)
    checksums = verify_checksums(base)
    ok = manifest.ok and checksums.ok
    if as_json:
        _write_json(
            {
                "status": "ok" if ok else "error",
                "manifest": manifest.to_dict(),
                "checksums": checksums.to_dict(),
            }
        )
    else:
        _write(manifest.summary())
        _write(checksums.summary())
    logger.info("проверка пакета: %s", "ok" if ok else "ошибки")
    return int(ExitCode.OK if ok else ExitCode.DATA_ERROR)


def run_package_command(
    args: argparse.Namespace,
    *,
    as_json: bool,
    logger: logging.Logger,
) -> int:
    """Диспетчер команд пакета."""
    try:
        if args.command == "export-package":
            return _cmd_export(args, as_json=as_json, logger=logger)
        return _cmd_verify(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
