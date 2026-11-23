"""Команды ``simorph runs``: журнал прогонов без графической оболочки (T9-06).

Команды работают с тем же ``run.sqlite``, что и вкладка «Журналы»:
``list`` показывает прогоны, ``show`` — сводку и ряды, ``export`` выгружает
метрики или события в CSV/Parquet, ``annotate`` добавляет разметку кадра,
``prune`` удаляет старые прогоны. Ни одна команда не требует окна, поэтому
выгрузку можно ставить в CI и в ночные скрипты.

Код возврата: 0 — успех, 1 — ошибка данных (нет журнала, нет прогона,
неизвестная таблица), 2 — внутренняя ошибка.
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
from simorph.core.logs import (
    RUN_KINDS,
    AnnotationRow,
    ExportResult,
    RunStore,
    default_log_path,
    export_table,
    open_store,
    parse_moment,
)

__all__ = ["build_runs_parser", "run_runs_command"]

#: Предел строк выборки по умолчанию для текстового вывода.
TEXT_LIMIT = 40


def build_runs_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``runs``."""
    runs = subparsers.add_parser("runs", help="журнал прогонов: список, просмотр и экспорт")
    add_common_options(runs)
    actions = runs.add_subparsers(dest="runs_command", metavar="ДЕЙСТВИЕ")

    list_parser = actions.add_parser("list", help="список прогонов")
    add_common_options(list_parser)
    list_parser.add_argument("path", type=Path, help="путь к project.json или run.sqlite")
    list_parser.add_argument("--kind", choices=RUN_KINDS, default=None)
    list_parser.add_argument("--status", default=None, help="running, finished, interrupted")
    list_parser.add_argument("--pipeline", dest="pipeline_id", default=None)
    list_parser.add_argument("--mission", dest="mission_id", default=None)
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--all", action="store_true", help="включая все статусы")

    show_parser = actions.add_parser("show", help="сводка прогона и ряды")
    add_common_options(show_parser)
    show_parser.add_argument("path", type=Path, help="путь к project.json или run.sqlite")
    show_parser.add_argument("run_id", nargs="?", default=None, help="идентификатор прогона")
    show_parser.add_argument("--last", action="store_true", help="последний прогон")
    show_parser.add_argument("--metric", default=None, help="фильтр по имени метрики")
    show_parser.add_argument("--prefix", default=None, help="фильтр по префиксу метрики")
    show_parser.add_argument("--event", default=None, help="фильтр по типу события")
    show_parser.add_argument("--limit", type=int, default=TEXT_LIMIT)

    export_parser = actions.add_parser("export", help="выгрузить ряды в CSV или Parquet")
    add_common_options(export_parser)
    export_parser.add_argument("path", type=Path, help="путь к project.json или run.sqlite")
    export_parser.add_argument("-o", "--output", type=Path, required=True, help="файл выгрузки")
    export_parser.add_argument("--table", choices=("metrics", "events", "runs"), default="metrics")
    export_parser.add_argument("--run", dest="run_id", default=None, help="идентификатор прогона")
    export_parser.add_argument("--metric", default=None, help="фильтр по имени метрики")
    export_parser.add_argument("--prefix", default=None, help="фильтр по префиксу метрики")
    export_parser.add_argument("--since-ns", type=int, default=None, help="начало интервала")
    export_parser.add_argument("--until-ns", type=int, default=None, help="конец интервала")
    export_parser.add_argument("--format", choices=("csv", "parquet"), default=None)

    annotate_parser = actions.add_parser("annotate", help="добавить разметку кадра")
    add_common_options(annotate_parser)
    annotate_parser.add_argument("path", type=Path, help="путь к project.json или run.sqlite")
    annotate_parser.add_argument("run_id", help="идентификатор прогона")
    annotate_parser.add_argument("--frame", type=int, default=None, help="номер кадра")
    annotate_parser.add_argument("--class-id", dest="class_id", default=None)
    annotate_parser.add_argument(
        "--bbox",
        default=None,
        help="рамка: x1,y1,x2,y2",
    )
    annotate_parser.add_argument("--source", default="user", help="источник разметки")

    prune_parser = actions.add_parser("prune", help="удалить старые прогоны")
    add_common_options(prune_parser)
    prune_parser.add_argument("path", type=Path, help="путь к project.json или run.sqlite")
    prune_parser.add_argument("--before", default=None, help="удалить прогоны старше даты")
    prune_parser.add_argument("--keep-last", type=int, default=None, help="оставить N свежих")
    prune_parser.add_argument("--dry-run", action="store_true", help="только показать кандидатов")


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _db_path(path: Path) -> Path:
    """Журнал рядом с проектом или явный путь к ``.sqlite``."""
    target = Path(path)
    if target.suffix.lower() in {".sqlite", ".sqlite3", ".db"} or target.name == "run.sqlite":
        return target
    return default_log_path(target)


def _open(path: Path) -> RunStore:
    database = _db_path(path)
    if not database.exists():
        raise ProjectError(f"журнал не найден: {database}")
    return open_store(database)


def _resolve_run(store: RunStore, run_id: str | None, *, last: bool) -> dict[str, Any]:
    if run_id is not None:
        found = store.get_run(run_id)
        if found is None:
            raise ProjectError(f"прогон {run_id!r} не найден")
        return found
    if last:
        runs = store.list_runs(limit=1)
        if not runs:
            raise ProjectError("журнал пуст: прогонов нет")
        return runs[0]
    raise ProjectError("нужен run_id или флаг --last")


def _cmd_list(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    with _open(args.path) as store:
        runs = store.list_runs(
            kind=args.kind,
            status=None if args.all else args.status,
            pipeline_id=args.pipeline_id,
            mission_id=args.mission_id,
            limit=max(1, int(args.limit)),
        )
        payload = {
            "status": "ok",
            "database": str(store.path),
            "count": len(runs),
            "runs": runs,
        }
        if as_json:
            _write_json(payload)
            return int(ExitCode.OK)
        _write(f"журнал: {store.path} · прогонов {len(runs)}")
        for run in runs:
            _write(
                f"  {run['run_id']}  {run['kind']:<10} {run['status']:<11}"
                f" {run['started_at']}  {run.get('pipeline_id') or run.get('mission_id') or '—'}"
            )
    logger.debug("список прогонов: %d", len(runs))
    return int(ExitCode.OK)


def _cmd_show(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    with _open(args.path) as store:
        run = _resolve_run(store, args.run_id, last=bool(args.last))
        run_id = str(run["run_id"])
        counts = store.counts(run_id)
        metrics = store.metrics(
            run_id,
            name=args.metric,
            prefix=args.prefix,
            limit=max(1, int(args.limit)),
        )
        events = store.events(
            run_id,
            event_type=args.event,
            limit=max(1, int(args.limit)),
        )
        artifacts = store.artifacts(run_id)
        names = store.metric_names(run_id)
        payload = {
            "status": "ok",
            "run": run,
            "counts": counts,
            "metric_names": names,
            "metrics": metrics,
            "events": events,
            "artifacts": artifacts,
        }
        if as_json:
            _write_json(payload)
            return int(ExitCode.OK)
        _write(
            f"прогон {run_id} ({run['kind']}): {run['status']}"
            f" · начат {run['started_at']}"
            f" · завершён {run.get('finished_at') or '—'}"
        )
        target = run.get("pipeline_id") or run.get("mission_id") or "—"
        _write(
            f"объект: {target} · seed {run.get('seed')} · версия {run.get('app_version') or '—'}"
        )
        if run.get("reason"):
            _write(f"причина: {run['reason']}")
        _write(
            f"строк: событий {counts['run_events']}, метрик {counts['metrics']},"
            f" артефактов {counts['artifacts']}, разметки {counts['annotations']}"
        )
        _write(f"метрики ({len(names)}): {', '.join(names[:20]) or '—'}")
        for item in metrics:
            _write(f"  {item['t_ns']:>14}  {item['name']:<32} {item['value']} {item['unit'] or ''}")
        for item in events:
            _write(f"  событие {item['t_ns']:>14}  {item['type']}  {item['source']}")
        for item in artifacts:
            _write(f"  артефакт {item['kind']}: {item['path']}")
    logger.debug("просмотр прогона %s", run_id)
    return int(ExitCode.OK)


def _cmd_export(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    with _open(args.path) as store:
        result: ExportResult = export_table(
            store,
            args.table,
            args.output,
            fmt=args.format,
            run_id=args.run_id,
            name=args.metric,
            prefix=args.prefix,
            since_ns=args.since_ns,
            until_ns=args.until_ns,
        )
        payload = {"status": "ok", **result.to_dict()}
    if as_json:
        _write_json(payload)
    else:
        _write(
            f"выгружено {result.rows} строк ({result.table}) в {result.path}"
            f" · {result.format}, {result.bytes} Б"
        )
    logger.info("экспорт %s: %d строк", result.table, result.rows)
    return int(ExitCode.OK)


def _cmd_annotate(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    bbox: tuple[float, float, float, float] | tuple[()] = ()
    if args.bbox:
        parts = [item.strip() for item in str(args.bbox).split(",")]
        if len(parts) != 4:
            raise ProjectError("рамка должна иметь вид x1,y1,x2,y2")
        bbox = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    with _open(args.path) as store:
        if store.get_run(args.run_id) is None:
            raise ProjectError(f"прогон {args.run_id!r} не найден")
        annotation = AnnotationRow(
            frame_id=args.frame,
            class_id=args.class_id,
            bbox=bbox,
            source=args.source,
        )
        added = store.annotate(args.run_id, annotation)
        payload = {
            "status": "ok",
            "run_id": args.run_id,
            "annotations": added,
            "counts": store.counts(args.run_id),
        }
    if as_json:
        _write_json(payload)
    else:
        _write(f"разметка добавлена: прогон {args.run_id}, кадр {args.frame or '—'}")
    logger.debug("разметка: %s", args.run_id)
    return int(ExitCode.OK)


def _cmd_prune(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    if args.before is None and args.keep_last is None:
        raise ProjectError("укажите --before и/или --keep-last")
    with _open(args.path) as store:
        result = store.prune(
            before=parse_moment(args.before),
            keep_last=args.keep_last,
            dry_run=bool(args.dry_run),
        )
        payload = {"status": "ok", "database": str(store.path), **result}
    if as_json:
        _write_json(payload)
    else:
        action = "будет удалено" if result["dry_run"] else "удалено"
        _write(f"{action} прогонов: {result['removed']}")
        for run_id in result["candidates"][:TEXT_LIMIT]:
            _write(f"  {run_id}")
    logger.info("очистка журнала: %d", result["removed"])
    return int(ExitCode.OK)


def run_runs_command(
    args: argparse.Namespace,
    *,
    as_json: bool,
    logger: logging.Logger,
) -> int:
    """Диспетчер подкоманд ``runs``."""
    command = getattr(args, "runs_command", None)
    try:
        if command == "list":
            return _cmd_list(args, as_json=as_json, logger=logger)
        if command == "show":
            return _cmd_show(args, as_json=as_json, logger=logger)
        if command == "export":
            return _cmd_export(args, as_json=as_json, logger=logger)
        if command == "annotate":
            return _cmd_annotate(args, as_json=as_json, logger=logger)
        if command == "prune":
            return _cmd_prune(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("не указано действие для runs: list, show, export, annotate или prune")
    return int(ExitCode.DATA_ERROR)
