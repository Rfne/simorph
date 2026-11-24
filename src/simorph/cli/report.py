"""Команда ``simorph report``: отчёт по прогону без графической оболочки (T11-07).

Команда читает журнал, собирает графики и выводы и пишет отчёт в каталог.
Ни окна, ни сети: её можно ставить в CI и в ночные скрипты. Форматы
выбираются ``--format``; PDF печатается через Qt в offscreen-режиме.

Код возврата: 0 — отчёт собран, 1 — ошибка данных (нет журнала или прогона,
неверные секции/форматы), 2 — внутренняя ошибка.
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
from simorph.core.report import (
    DEFAULT_SECTIONS,
    ReportConfig,
    collect_report_data,
    render_report,
)
from simorph.core.repository import load_project

__all__ = ["build_report_parser", "run_report_command"]

#: Форматы по умолчанию: текст и автономный HTML.
DEFAULT_FORMATS: tuple[str, ...] = ("md", "html")


def build_report_parser(subparsers: Any) -> None:
    """Регистрирует ``simorph report``."""
    parser = subparsers.add_parser("report", help="отчёт по прогону: MD, HTML и PDF")
    add_common_options(parser)
    parser.add_argument("path", type=Path, help="путь к project.json или run.sqlite")
    parser.add_argument("run_id", nargs="?", default=None, help="идентификатор прогона")
    parser.add_argument("-o", "--output", type=Path, default=None, help="каталог отчёта")
    parser.add_argument("--title", default=None, help="заголовок отчёта")
    parser.add_argument(
        "--template", type=Path, default=None, help="пользовательский шаблон Jinja2"
    )
    parser.add_argument(
        "--sections",
        default=None,
        help=f"секции через запятую (по умолчанию {','.join(DEFAULT_SECTIONS)})",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        default=None,
        help=f"форматы через запятую (по умолчанию {','.join(DEFAULT_FORMATS)})",
    )
    parser.add_argument(
        "--threshold",
        action="append",
        default=[],
        metavar="ИМЯ=ЗНАЧЕНИЕ",
        help="порог вывода, можно повторять",
    )


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _split(value: str | None, default: tuple[str, ...]) -> list[str]:
    if value is None:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _thresholds(items: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in items:
        name, _, raw = item.partition("=")
        if not name.strip() or not raw.strip():
            raise ProjectError(f"порог {item!r} должен иметь вид ИМЯ=ЗНАЧЕНИЕ")
        try:
            result[name.strip()] = float(raw)
        except ValueError as exc:
            raise ProjectError(f"порог {item!r}: {raw!r} не число") from exc
    return result


def _default_output(path: Path, run_id: str) -> Path:
    """Каталог по умолчанию: ``reports/<run8>`` рядом с проектом или журналом."""
    base = path if path.is_dir() else path.parent
    return base / "reports" / run_id[:8]


def _is_database(path: Path) -> bool:
    return path.suffix.lower() in {".sqlite", ".sqlite3", ".db"} or path.name == "run.sqlite"


def _project_config(path: Path) -> ReportConfig | None:
    """Раздел ``report`` проекта как базовая конфигурация отчёта."""
    if _is_database(path) or not path.is_file():
        return None
    loaded = load_project(path)
    if not loaded.document.reports:
        return None
    section = loaded.document.reports[0]
    payload = section.model_dump(mode="json")
    template = Path(str(payload.get("template") or ""))
    if template and not template.is_absolute():
        payload["template"] = str(path.parent / template)
    return ReportConfig.from_dict(payload)


def _build_config(
    args: argparse.Namespace,
    run_id: str,
    base: Path,
    project_config: ReportConfig | None,
) -> ReportConfig:
    """Флаги командной строки перекрывают проект, проект — значения по умолчанию."""
    base_config = project_config or ReportConfig()
    template = base_config.template
    if args.template is not None:
        candidate = Path(args.template)
        template = str(candidate if candidate.is_absolute() else base / candidate)
    thresholds = dict(base_config.thresholds)
    thresholds.update(_thresholds(list(args.threshold)))
    return ReportConfig(
        report_id=base_config.report_id,
        title=args.title
        or (base_config.title if project_config else f"Отчёт по прогону {run_id[:8]}"),
        sections=_split(args.sections, tuple(base_config.sections)),
        metrics=list(base_config.metrics),
        thresholds=thresholds,
        export=_split(args.formats, tuple(base_config.export)),
        template=template,
        pipeline_ids=list(base_config.pipeline_ids),
    )


def _cmd_report(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    data = collect_report_data(args.path, args.run_id)
    run_id = data.run_id
    source = Path(args.path)
    base = source if source.is_dir() else source.parent
    config = _build_config(args, run_id, base, _project_config(source))
    output = args.output if args.output is not None else _default_output(Path(args.path), run_id)
    result = render_report(data, config, output_dir=output)

    payload = {"status": "ok" if result.ok else "warn", **result.to_dict()}
    if as_json:
        _write_json(payload)
    else:
        _write(result.summary())
        for level in ("fail", "warn", "ok", "info"):
            items = [item for item in result.conclusions if item.level == level]
            for item in items:
                _write(f"  {level.upper():<4} {item.text}")
    logger.info("отчёт собран: %s", output)
    return int(ExitCode.OK)


def run_report_command(
    args: argparse.Namespace,
    *,
    as_json: bool,
    logger: logging.Logger,
) -> int:
    """Диспетчер ``simorph report``."""
    try:
        return _cmd_report(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError, KeyError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
