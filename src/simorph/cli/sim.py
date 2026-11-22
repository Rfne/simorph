"""Команды ``simorph sim``: headless-прогон и replay (этап 7).

``sim run`` исполняет миссию без 3D-окна и печатает метрики мягкого
масштаба. ``sim replay`` читает записанный поток и сверяет хэши кадров,
не запуская физику. Обе команды работают с тем же раннером, что и вкладка
«Симуляция», поэтому оконный и headless-режимы дают одинаковые числа.

Код возврата: 0 — успех, 1 — ошибка данных или незавершённый прогон.
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
from simorph.core.models.agent import AgentSection
from simorph.core.models.exercise import ExerciseSection, Mission
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import load_project
from simorph.core.simulation import (
    Degradation,
    SimulationConfig,
    SimulationRunner,
    replay_stream,
)

__all__ = ["build_sim_parser", "run_sim_command"]


def build_sim_parser(subparsers: Any) -> None:
    """Регистрирует подкоманды ``sim``."""
    sim = subparsers.add_parser("sim", help="симуляция: прогон и replay потока")
    add_common_options(sim)
    actions = sim.add_subparsers(dest="sim_command", metavar="ДЕЙСТВИЕ")

    run_parser = actions.add_parser("run", help="headless-прогон миссии")
    add_common_options(run_parser)
    run_parser.add_argument("path", type=Path, help="путь к project.json")
    run_parser.add_argument("--exercise", type=int, default=0, help="номер упражнения")
    run_parser.add_argument("--mission", type=int, default=0, help="номер миссии")
    run_parser.add_argument("--scale", type=float, default=1.0, help="целевой масштаб времени")
    run_parser.add_argument("--step", type=float, default=None, help="шаг физики, с")
    run_parser.add_argument("--duration", type=float, default=None, help="длительность, с")
    run_parser.add_argument("--slots", type=int, default=3, help="слотов в кольце (2–4)")
    run_parser.add_argument(
        "--realtime-drop",
        action="store_true",
        help="явно разрешить отбрасывание кадров при переполнении",
    )
    run_parser.add_argument("--latency-ms", type=float, default=0.0, help="задержка сенсора, мс")
    run_parser.add_argument("--loss", type=float, default=0.0, help="доля потерь кадров 0…1")
    run_parser.add_argument("--desync-ms", type=float, default=0.0, help="рассинхронизация ±0,5 мс")
    run_parser.add_argument("--jitter-us", type=float, default=0.0, help="джиттер выдержки, мкс")
    run_parser.add_argument("--work-ms", type=float, default=0.0, help="искусственная работа такта")
    run_parser.add_argument("--slowdown", type=float, default=1.0, help="замедление ступени, раз")
    run_parser.add_argument("--record", type=Path, default=None, help="записать поток кадров")
    run_parser.add_argument("--report", type=Path, default=None, help="записать отчёт JSON")
    run_parser.add_argument(
        "--no-pace",
        action="store_true",
        help="не выдерживать плановый интервал (максимальная скорость)",
    )

    replay_parser = actions.add_parser("replay", help="воспроизвести поток без физики")
    add_common_options(replay_parser)
    replay_parser.add_argument("path", type=Path, help="файл потока")
    replay_parser.add_argument(
        "--no-compare",
        action="store_true",
        help="только прочитать поток, не сверяя хэши",
    )


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


def _agent(document: ProjectDocument, mission: Mission) -> AgentSection | None:
    for agent in document.agents:
        if agent.agent_id == mission.agent_id:
            return agent
    return None


def _cmd_run(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    document = _load(args.path)
    exercise = _exercise(document, args.exercise)
    mission = _mission(exercise, args.mission)
    config = SimulationConfig(
        scale=args.scale,
        step_s=args.step,
        duration_s=args.duration,
        slots=args.slots,
        realtime_drop=bool(args.realtime_drop),
        degradation=Degradation(
            sensor_latency_ms=args.latency_ms,
            frame_loss_ratio=args.loss,
            desync_ms=args.desync_ms,
            exposure_jitter_us=args.jitter_us,
        ),
        work_ms=args.work_ms,
        slowdown=args.slowdown,
        record_path=args.record,
        report_path=args.report,
        pace=not args.no_pace,
    )
    runner = SimulationRunner(mission, agent=_agent(document, mission), config=config)
    try:
        report = runner.run()
    finally:
        runner.close()
    if as_json:
        _write_json(report.to_dict())
    else:
        _write(report.summary())
        _write(
            f"масштаб {report.clock.get('scale', 1.0):g}"
            f" · шаг {report.clock.get('step_s', 0.0) * 1000:.2f} мс"
            f" · интервалов {report.metrics.get('intervals', 0)}"
            f" · потеря {report.loss_ratio * 100:.2f}%"
        )
        if report.record:
            _write(f"поток: {report.record}")
        if report.status:
            _write(f"состояние: {report.status}")
        if report.reason:
            _write(f"причина: {report.reason}")
    if not report.ok:
        logger.error("прогон не завершён: %s (%s)", report.state, report.reason)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_replay(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    report = replay_stream(args.path, compare=not args.no_compare)
    if as_json:
        _write_json(report.to_dict())
    else:
        _write(
            f"поток {report.source}: кадров {report.frames}"
            f" · совпало {report.matched}"
            f" · расхождений {len(report.mismatched)}"
            f" · нарушений меток {len(report.bad_timing)}"
        )
        if report.mismatched:
            _write(f"  расхождения: {', '.join(str(i) for i in report.mismatched[:10])}")
        if report.bad_timing:
            _write(f"  метки: {', '.join(str(i) for i in report.bad_timing[:10])}")
    if not report.ok:
        logger.error(
            "replay не совпал: расхождений %d, нарушений меток %d",
            len(report.mismatched),
            len(report.bad_timing),
        )
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def run_sim_command(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    """Диспетчер подкоманд ``sim``."""
    command = getattr(args, "sim_command", None)
    try:
        if command == "run":
            return _cmd_run(args, as_json=as_json, logger=logger)
        if command == "replay":
            return _cmd_replay(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("не указано действие для sim: run или replay")
    return int(ExitCode.DATA_ERROR)
