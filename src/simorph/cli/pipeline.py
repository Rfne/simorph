"""Команды ``simorph pipeline``: сборка, проверка, прогон и исходники (этап 8).

Те же расчёты, что в редакторе: граф по портам, бюджет критического пути,
валидатор совместимости и прогон по кадрам. ``sources`` показывает дерево
исходников, сохраняет снимок хэшей и проверяет устаревание варианта:
изменение файла после снимка даёт статус ``stale`` и код 1.

Код возврата: 0 — успех, 1 — ошибка данных, в том числе конвейер,
который не проходит валидацию, или устаревшие исходники.
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
from simorph.core.models.pipeline import PipelineSection
from simorph.core.models.project import ProjectDocument
from simorph.core.pipeline import (
    PipelineConfig,
    PipelineRunner,
    build_pipeline_graph,
    can_run,
    check_stale,
    external_open,
    load_snapshot,
    make_backend,
    pipeline_issues,
    save_snapshot,
    scan_sources,
    snapshot_path,
)
from simorph.core.repository import load_project

__all__ = ["build_pipeline_parser", "run_pipeline_command"]


def build_pipeline_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``pipeline``."""
    pipeline = subparsers.add_parser(
        "pipeline", help="конвейеры: граф, проверка, прогон и исходники"
    )
    add_common_options(pipeline)
    actions = pipeline.add_subparsers(dest="pipeline_command", metavar="ДЕЙСТВИЕ")

    build_parser = actions.add_parser("build", help="показать граф и бюджет конвейера")
    add_common_options(build_parser)
    build_parser.add_argument("path", type=Path, help="путь к project.json")
    _add_selection(build_parser)

    validate_parser = actions.add_parser("validate", help="проверить порты, граф и параметры")
    add_common_options(validate_parser)
    validate_parser.add_argument("path", type=Path, help="путь к project.json")
    _add_selection(validate_parser)

    run_parser = actions.add_parser("run", help="прогнать конвейер по кадрам")
    add_common_options(run_parser)
    run_parser.add_argument("path", type=Path, help="путь к project.json")
    _add_selection(run_parser)
    run_parser.add_argument("--frames", type=int, default=120, help="число кадров")
    run_parser.add_argument("--fps", type=float, default=120.0, help="частота источника, Гц")
    run_parser.add_argument("--seed", type=int, default=20261122, help="seed нагрузки кадра")
    run_parser.add_argument("--max-queue", type=int, default=4, help="предел кадров в работе")
    run_parser.add_argument(
        "--realtime-drop",
        action="store_true",
        help="явно разрешить отбрасывание кадров при переполнении",
    )
    run_parser.add_argument(
        "--work-scale",
        type=float,
        default=1.0,
        help="множитель плановой работы узлов",
    )
    run_parser.add_argument(
        "--backend",
        default="stub",
        help="бэкенд инференса: stub, onnx, openvino, rknn, nbg",
    )

    sources_parser = actions.add_parser("sources", help="дерево исходников и устаревание")
    add_common_options(sources_parser)
    sources_parser.add_argument("path", type=Path, help="путь к project.json")
    sources_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="каталог исходников (по умолчанию рядом с проектом)",
    )
    sources_parser.add_argument(
        "--snapshot",
        action="store_true",
        help="сохранить снимок хэшей рядом с проектом",
    )
    sources_parser.add_argument(
        "--check",
        action="store_true",
        help="сравнить дерево со снимком и вернуть код 1 при устаревании",
    )
    sources_parser.add_argument("--open", dest="open_path", default=None, help="открыть файл")


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index", type=int, default=0, help="номер конвейера в проекте")


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2))


def _load(path: Path) -> ProjectDocument:
    return load_project(path).document


def _pipeline(document: ProjectDocument, index: int) -> PipelineSection:
    if not document.pipelines:
        raise ProjectError("в проекте нет конвейеров")
    if not 0 <= index < len(document.pipelines):
        raise ProjectError(
            f"конвейер с номером {index} не найден: в проекте {len(document.pipelines)}"
        )
    return document.pipelines[index]


def _cmd_build(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    pipeline = _pipeline(document, args.index)
    graph = build_pipeline_graph(pipeline)
    logger.debug(
        "сборка конвейера %s: узлов %d, рёбер %d, слоёв %d",
        pipeline.pipeline_id,
        len(pipeline.nodes),
        len(pipeline.edges),
        graph.layer_count,
    )
    issues = pipeline_issues(pipeline, prefix=f"pipelines.{args.index}")
    errors = sum(1 for item in issues if item.severity is Severity.ERROR)
    payload = {
        "status": "ok" if errors == 0 else "error",
        "pipeline_id": pipeline.pipeline_id,
        "kind": pipeline.kind,
        "variant": pipeline.variant,
        "nodes": len(pipeline.nodes),
        "edges": len(pipeline.edges),
        "types": sorted({node.type for node in pipeline.nodes}),
        "graph": graph.to_dict(),
        "errors": errors,
        "warnings": len(issues) - errors,
    }
    if as_json:
        _write_json(payload)
        return int(ExitCode.OK if errors == 0 else ExitCode.DATA_ERROR)
    _write(f"конвейер {pipeline.pipeline_id} ({pipeline.kind}, вариант {pipeline.variant or '—'})")
    _write(
        f"узлов {len(pipeline.nodes)}, рёбер {len(pipeline.edges)},"
        f" слоёв {graph.layer_count}, план {graph.critical_path_ms:.1f} мс"
    )
    _write(f"критический путь: {' → '.join(graph.critical_path) or '—'}")
    _write(f"типы: {', '.join(sorted({node.type for node in pipeline.nodes}))}")
    _write(f"замечания: ошибок {errors}, предупреждений {len(issues) - errors}")
    return int(ExitCode.OK if errors == 0 else ExitCode.DATA_ERROR)


def _cmd_validate(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    pipeline = _pipeline(document, args.index)
    issues = pipeline_issues(pipeline, prefix=f"pipelines.{args.index}")
    errors = sum(1 for item in issues if item.severity is Severity.ERROR)
    payload = {
        "status": "ok" if errors == 0 else "error",
        "pipeline_id": pipeline.pipeline_id,
        "errors": errors,
        "warnings": len(issues) - errors,
        "issues": issues_to_payload(issues),
        "can_run": can_run(pipeline),
    }
    if as_json:
        _write_json(payload)
    else:
        _write(
            f"конвейер {pipeline.pipeline_id}: ошибок {errors},"
            f" предупреждений {len(issues) - errors}"
        )
        for item in issues:
            _write(f"  {item.severity}: {item.path}: {item.message}")
    if errors:
        logger.error("конвейер не проходит валидацию: ошибок %d", errors)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_run(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    pipeline = _pipeline(document, args.index)
    config = PipelineConfig(
        frames=args.frames,
        fps=args.fps,
        seed=args.seed,
        max_queue=args.max_queue,
        realtime_drop=bool(args.realtime_drop),
        work_scale=args.work_scale,
        backend=make_backend(args.backend),
    )
    runner = PipelineRunner(pipeline, config=config)
    try:
        report = runner.run()
    finally:
        runner.close()
    if as_json:
        _write_json(report.to_dict())
    else:
        _write(report.summary())
        for name, item in report.metrics.nodes.items():
            _write(
                f"  {name} ({item.type}): план {item.plan_ms:.2f} мс,"
                f" факт p50 {item.p50_ms:.2f}, p95 {item.p95_ms:.2f}, max {item.max_ms:.2f}"
            )
        if report.reason:
            _write(f"причина: {report.reason}")
    if not report.ok:
        logger.error("прогон конвейера не завершён: %s", report.reason)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_sources(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    project_path = Path(args.path)
    root = Path(args.root) if args.root is not None else _default_root(project_path)
    tree = scan_sources(root)
    snapshot_file = snapshot_path(project_path)
    status = None
    if args.snapshot:
        save_snapshot(tree, snapshot_file)
    if args.check:
        status = check_stale(tree, load_snapshot(snapshot_file))
    payload: dict[str, Any] = {
        "status": "ok",
        "root": str(tree.root),
        "digest": tree.digest,
        "files": [item.to_dict() for item in tree.files],
        "truncated": tree.truncated,
        "snapshot": str(snapshot_file) if args.snapshot else None,
        "check": None if status is None else status.to_dict(),
    }
    if status is not None and status.stale:
        payload["status"] = "stale"
    if as_json:
        _write_json(payload)
    else:
        _write(f"исходники: {tree.root}")
        _write(f"файлов {len(tree.files)}, хэш {tree.digest[:16]}…")
        for item in tree.files[:40]:
            _write(f"  {item.path}  {item.language}  {item.size} Б")
        if len(tree.files) > 40:
            _write(f"  … ещё {len(tree.files) - 40}")
        if args.snapshot:
            _write(f"снимок: {snapshot_file}")
        if status is not None:
            _write(f"состояние: {status.state}")
            if status.changed:
                _write(f"  изменены: {', '.join(status.changed)}")
            if status.added:
                _write(f"  добавлены: {', '.join(status.added)}")
            if status.removed:
                _write(f"  удалены: {', '.join(status.removed)}")
    if args.open_path:
        target = tree.root / str(args.open_path)
        external_open(target)
    if status is not None and status.stale:
        logger.error("исходники изменились после снимка: %s", ", ".join(status.changed))
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _default_root(project_path: Path) -> Path:
    candidate = project_path.parent / "src"
    return candidate if candidate.is_dir() else project_path.parent


def run_pipeline_command(
    args: argparse.Namespace,
    *,
    as_json: bool,
    logger: logging.Logger,
) -> int:
    """Диспетчер подкоманд ``pipeline``."""
    command = getattr(args, "pipeline_command", None)
    try:
        if command == "build":
            return _cmd_build(args, as_json=as_json, logger=logger)
        if command == "validate":
            return _cmd_validate(args, as_json=as_json, logger=logger)
        if command == "run":
            return _cmd_run(args, as_json=as_json, logger=logger)
        if command == "sources":
            return _cmd_sources(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("не указано действие для pipeline: build, validate, run или sources")
    return int(ExitCode.DATA_ERROR)
