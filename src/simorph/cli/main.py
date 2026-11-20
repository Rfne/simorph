"""Командная строка simorph."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from simorph.cli.exit_codes import ExitCode
from simorph.logging_setup import configure_logging
from simorph.version import collect_versions

#: Зависимости, без которых калибратор не запускается.
REQUIRED_MODULES: tuple[str, ...] = ("python", "qt", "opencv", "numpy", "torch", "sqlite")


class UsageError(Exception):
    """Неверные аргументы командной строки: это ошибка данных, а не сбой."""


class _ArgumentParser(argparse.ArgumentParser):
    """Парсер, который не завершает процесс сам, а сообщает об ошибке ввода."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """Добавляет общие флаги.

    ``default=argparse.SUPPRESS`` нужен, чтобы подкоманда не затирала значения,
    переданные до её имени (``simorph --json version``).
    """
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="машиночитаемый вывод в stdout",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
        help="подробность лога: -v — info, -vv — debug",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=argparse.SUPPRESS,
        help="путь к JSON-lines файлу лога",
    )


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер командной строки."""
    parser = _ArgumentParser(
        prog="simorph",
        description="Настольная среда калибровки бортовых конвейеров патрульного робота",
    )
    _add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="КОМАНДА")

    version_command = subparsers.add_parser("version", help="версии приложения и окружения")
    _add_common_options(version_command)

    selftest_command = subparsers.add_parser("selftest", help="самопроверка окружения")
    _add_common_options(selftest_command)
    selftest_command.add_argument(
        "--ui",
        action="store_true",
        help="открыть и закрыть минимальное окно Qt",
    )
    selftest_command.add_argument(
        "--offscreen",
        action="store_true",
        help="платформа Qt offscreen (CI без дисплея)",
    )

    return parser


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def _cmd_version(*, as_json: bool) -> int:
    versions = collect_versions()
    if as_json:
        _write_json(versions)
        return int(ExitCode.OK)

    width = max(len(name) for name in versions)
    for name, value in versions.items():
        _write(f"{name:<{width}}  {value if value is not None else '—'}")
    return int(ExitCode.OK)


def _cmd_selftest(
    *,
    as_json: bool,
    ui: bool,
    offscreen: bool,
    logger: logging.Logger,
) -> int:
    versions = collect_versions()
    missing = [name for name in REQUIRED_MODULES if not versions.get(name)]

    ui_status = "not-requested"
    ui_code = int(ExitCode.OK)
    if ui:
        from simorph.selftest.ui import run_ui_selftest

        ui_code = run_ui_selftest(offscreen=offscreen)
        ui_status = "ok" if ui_code == int(ExitCode.OK) else "failed"
        if ui_code != int(ExitCode.OK):
            logger.error("Qt selftest завершился с кодом %s", ui_code)

    if missing:
        logger.error("не найдены обязательные зависимости: %s", ", ".join(missing))

    status = "ok" if not missing and ui_code == int(ExitCode.OK) else "error"
    if as_json:
        _write_json({"status": status, "missing": missing, "ui": ui_status, "versions": versions})
    else:
        _write(f"status  {status}")
        _write(f"ui      {ui_status}")
        _write(f"missing {', '.join(missing) if missing else '—'}")

    if ui_code != int(ExitCode.OK):
        return int(ExitCode.INTERNAL_ERROR)
    if missing:
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI. Возвращает код возврата."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as exc:
        sys.stderr.write(f"simorph: {exc}\n")
        sys.stderr.write(parser.format_usage())
        return int(ExitCode.DATA_ERROR)

    if args.command is None:
        parser.print_help(sys.stderr)
        return int(ExitCode.DATA_ERROR)

    as_json = bool(getattr(args, "json", False))
    logger = configure_logging(
        log_file=getattr(args, "log_file", None),
        verbose=int(getattr(args, "verbose", 0) or 0),
    )

    try:
        if args.command == "version":
            return _cmd_version(as_json=as_json)
        return _cmd_selftest(
            as_json=as_json,
            ui=bool(args.ui),
            offscreen=bool(args.offscreen),
            logger=logger,
        )
    except Exception:
        logger.exception("необработанная ошибка команды %s", args.command)
        if as_json:
            _write_json({"status": "error", "command": args.command})
        else:
            sys.stderr.write("simorph: внутренняя ошибка, подробности в логе\n")
        return int(ExitCode.INTERNAL_ERROR)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
