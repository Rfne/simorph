"""Команды ``simorph profile``: project и export (T3-05, T3-07).

Команды работают headless: тот же расчёт κ/κ+ и ту же проекцию, что
показывает интерфейс. Экспорт пишет обёртку с версией и хэшем и уважает
NDA-каталог.
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
from simorph.core.errors import ProjectNotFoundError
from simorph.core.profile import (
    check_nda_export,
    completeness,
    export_profile_payload,
    import_profile_text,
    project_profile,
)
from simorph.core.profile.exporting import dump_profile_export

__all__ = ["build_profile_parser", "run_profile_command"]


def build_profile_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``profile``."""
    profile = subparsers.add_parser("profile", help="операции с профилем заказчика")
    add_common_options(profile)
    actions = profile.add_subparsers(dest="profile_command", metavar="ДЕЙСТВИЕ")

    project_parser = actions.add_parser("project", help="посчитать κ, κ+ и векторы")
    add_common_options(project_parser)
    project_parser.add_argument("path", type=Path, help="путь к JSON профиля")

    export_parser = actions.add_parser("export", help="экспортировать профиль с версией и хэшем")
    add_common_options(export_parser)
    export_parser.add_argument("path", type=Path, help="путь к JSON профиля")
    export_parser.add_argument("-o", "--output", type=Path, required=True, help="куда писать")
    export_parser.add_argument(
        "--nda-root",
        type=Path,
        action="append",
        default=None,
        help="защищённый каталог (можно несколько); по умолчанию SIMORPH_NDA_ROOTS",
    )

    check_parser = actions.add_parser("check", help="проверить импорт профиля")
    add_common_options(check_parser)
    check_parser.add_argument("path", type=Path, help="путь к JSON профиля")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProjectNotFoundError(f"файл профиля не найден: {path}") from exc


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_project(path: Path, *, as_json: bool, logger: logging.Logger) -> int:
    imported = import_profile_text(_read_text(path))
    metrics = completeness(imported.section)
    projection = project_profile(imported.section)

    payload = {
        "status": "ok" if projection.ready else "blocked",
        "path": str(path),
        "customer": imported.section.customer,
        "nda": imported.section.nda,
        "parameters": imported.parameter_count,
        "unknown_fields": [item.to_dict() for item in imported.unknown],
        "completeness": metrics.to_dict(),
        "projection": projection.to_dict(),
    }
    if as_json:
        _write_json(payload)
    else:
        _write(f"профиль  {path}")
        _write(f"заказчик {imported.section.customer or '—'}")
        _write(f"параметров {metrics.total}, неизвестных полей {len(imported.unknown)}")
        _write(f"κ  {metrics.kappa_text} ({metrics.kappa_percent:.1f} %)")
        _write(f"κ+ {metrics.kappa_plus_text} ({metrics.kappa_plus_percent:.1f} %)")
        counts = projection.counts()
        _write("векторы  " + ", ".join(f"{target}: {count}" for target, count in counts.items()))
        if projection.uncovered:
            _write(f"не покрыто {len(projection.uncovered)} параметров")
        for item in projection.issues:
            _write(f"  {item.path}: {item.message}")

    if not projection.ready:
        logger.error("проекция заблокирована: %s", path)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_check(path: Path, *, as_json: bool, logger: logging.Logger) -> int:
    imported = import_profile_text(_read_text(path))
    payload = imported.report()
    if as_json:
        _write_json({"status": "ok", **payload})
    else:
        _write(f"профиль  {path}")
        _write(f"параметров {imported.parameter_count}")
        _write(f"неизвестных полей {len(imported.unknown)}")
        for item in imported.unknown:
            _write(f"  сохранено: {item.path}.{item.name}")
        for issue_item in imported.issues:
            _write(f"  {issue_item.path}: {issue_item.message}")
    if any(item.severity == "error" for item in imported.issues):
        logger.error("профиль содержит ошибки: %s", path)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_export(
    path: Path,
    output: Path,
    *,
    as_json: bool,
    logger: logging.Logger,
    nda_root: list[Path] | None,
) -> int:
    imported = import_profile_text(_read_text(path))
    roots = nda_root
    violations = check_nda_export(imported.section, output, roots=roots)
    if violations:
        for item in violations:
            logger.error("%s", item.message)
        if as_json:
            _write_json(
                {
                    "status": "error",
                    "error": "экспорт запрещён",
                    "issues": [item.to_dict() for item in violations],
                }
            )
        else:
            for item in violations:
                sys.stderr.write(f"simorph: {item.message}\n")
        return int(ExitCode.DATA_ERROR)

    payload = export_profile_payload(imported.section, destination=output, roots=roots)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dump_profile_export(payload), encoding="utf-8", newline="\n")

    if as_json:
        _write_json({"status": "ok", "path": str(output), "hash": payload["hash"]})
    else:
        _write(f"экспортирован профиль: {output}")
        _write(f"hash {payload['hash']}")
    return int(ExitCode.OK)


def run_profile_command(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Диспетчер подкоманд ``profile``."""
    command = getattr(args, "profile_command", None)
    if command == "project":
        return _cmd_project(args.path, as_json=as_json, logger=logger)
    if command == "check":
        return _cmd_check(args.path, as_json=as_json, logger=logger)
    if command == "export":
        return _cmd_export(
            args.path,
            args.output,
            as_json=as_json,
            logger=logger,
            nda_root=args.nda_root,
        )
    logger.error("не указано действие для profile: project, check или export")
    return int(ExitCode.DATA_ERROR)
