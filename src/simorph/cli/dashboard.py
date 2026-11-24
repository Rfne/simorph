"""Команда ``simorph dashboard``: локальный дашборд прогона (T11-09, G11-08).

Дашборд читает журнал, собирает панель Panel и поднимает сервер на
``127.0.0.1``. Сеть не нужна: ресурсы встраиваются, внешние сервисы не
вызываются. Флаг ``--check`` собирает панель и выходит — так проверяются
время сборки, виджеты и LaTeX в CI без браузера.

Код возврата: 0 — успех, 1 — ошибка данных (нет журнала, прогона или
конфигурации), 2 — внутренняя ошибка.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from simorph.cli.exit_codes import ExitCode
from simorph.cli.project import add_common_options
from simorph.core.dashboard import (
    DashboardConfig,
    dashboard_summary,
    dashboard_url,
    load_dashboard_config,
    load_dashboard_data,
    serve_dashboard,
)
from simorph.core.errors import ProjectError

__all__ = ["build_dashboard_parser", "run_dashboard_command"]


def build_dashboard_parser(subparsers: Any) -> None:
    """Регистрирует ``simorph dashboard``."""
    parser = subparsers.add_parser("dashboard", help="дашборд прогона на Panel")
    add_common_options(parser)
    parser.add_argument("path", type=Path, help="путь к project.json или run.sqlite")
    parser.add_argument(
        "--run",
        dest="run_ids",
        action="append",
        default=[],
        help="идентификатор прогона (можно повторять)",
    )
    parser.add_argument("--config", type=Path, default=None, help="файл dash_*.yaml")
    parser.add_argument("--port", type=int, default=None, help="порт локального сервера")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    parser.add_argument(
        "--check",
        action="store_true",
        help="собрать панель, проверить виджеты и выйти без сервера",
    )


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _count_widgets(app: Any) -> int:
    """Считает виджеты в дереве панели: Row/Column раскрываются."""
    import panel as pn

    total = 0
    stack: list[Any] = [app]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, pn.widgets.Widget):
            total += 1
        children = getattr(item, "objects", None)
        if isinstance(children, list):
            stack.extend(children)
    return total


def _check(app: Any, data: Any, config: DashboardConfig, seconds: float) -> dict[str, Any]:
    """Сводка проверки: виджеты, LaTeX и время сборки."""
    import panel as pn

    blocks = dashboard_summary(
        data.runs[0],
        config,
        threshold_ms=float(config.thresholds.get("inference_p95_ms", 400.0)),
        degraded_only=False,
    )
    return {
        "status": "ok",
        "database": data.database,
        "runs": len(data.runs),
        "widgets": _count_widgets(app),
        "latex": sum(1 for item in blocks if isinstance(item, pn.pane.LaTeX)),
        "sections": list(config.sections),
        "seconds": round(seconds, 3),
    }


def _cmd_dashboard(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    config = load_dashboard_config(args.config)
    if args.port is not None:
        config.port = int(args.port)
    data = load_dashboard_data(
        args.path,
        run_ids=list(args.run_ids) or list(config.runs),
        limit=config.limit,
    )
    started = time.perf_counter()
    from simorph.core.dashboard import build_dashboard

    app = build_dashboard(data, config)
    seconds = time.perf_counter() - started

    if args.check:
        payload = _check(app, data, config, seconds)
        if as_json:
            _write_json(payload)
        else:
            _write(
                f"дашборд собран за {payload['seconds']:.2f} с:"
                f" прогонов {payload['runs']}, виджетов {payload['widgets']},"
                f" LaTeX {payload['latex']}"
            )
        return int(ExitCode.OK)

    url = dashboard_url(config)
    if as_json:
        _write_json({"status": "serving", "url": url, "runs": len(data.runs)})
    else:
        _write(f"дашборд: {url} · прогонов {len(data.runs)} · Ctrl+C для остановки")
    serve_dashboard(app, config, show=not args.no_browser)
    logger.info("дашборд остановлен: %s", url)
    return int(ExitCode.OK)


def run_dashboard_command(
    args: argparse.Namespace,
    *,
    as_json: bool,
    logger: logging.Logger,
) -> int:
    """Диспетчер ``simorph dashboard``."""
    try:
        return _cmd_dashboard(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
