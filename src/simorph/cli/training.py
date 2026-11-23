"""Команды этапа 10: датасет, обучение, оценка и экспорт ONNX (T10-07).

Цикл дообучения без графической оболочки — это четыре шага: собрать
датасет из сцен, обучить голову с чекпойнтами, посчитать метрики и
выгрузить веса в ONNX. Каждый шаг пишется в тот же ``run.sqlite``, что и
симуляция с конвейером, поэтому цифра в отчёте и цифра в журнале — одна
и та же.

Код возврата: 0 — успех, 1 — ошибка данных (нет датасета, нет чекпойнта,
ONNX разошёлся с PyTorch), 2 — внутренняя ошибка.
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
from simorph.core.dataset import (
    CLASS_NAMES,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    AugmentConfig,
    DatasetConfig,
    build_dataset,
    read_frames,
    report_from_manifest,
)
from simorph.core.errors import ProjectError
from simorph.core.logs import open_store
from simorph.core.training import (
    DEFAULT_OPSET,
    ONNX_TOLERANCE,
    DetectionHead,
    ModelSpec,
    TrainConfig,
    Trainer,
    evaluate,
    export_and_verify,
    record_evaluation,
    record_training,
)

__all__ = ["build_training_parser", "run_dataset_command", "run_train_command"]

#: Каталог обучения по умолчанию внутри датасета.
TRAINING_DIR = "training"

#: Сколько последних эпох показывать в текстовом выводе.
TEXT_EPOCHS = 5


def build_training_parser(subparsers: Any) -> None:
    """Регистрирует ``dataset``, ``train``, ``eval`` и ``export-onnx``."""
    dataset = subparsers.add_parser("dataset", help="датасет: сборка из сцен и сводка")
    add_common_options(dataset)
    actions = dataset.add_subparsers(dest="dataset_command", metavar="ДЕЙСТВИЕ")

    build_parser = actions.add_parser("build", help="собрать датасет из сцен PyBullet")
    add_common_options(build_parser)
    build_parser.add_argument("path", type=Path, help="каталог датасета")
    build_parser.add_argument("--scenes", type=int, default=4, help="сцен обучения")
    build_parser.add_argument("--frames", type=int, default=80, help="кадров на сцену")
    build_parser.add_argument("--val-scenes", type=int, default=1, help="валидационных сцен")
    build_parser.add_argument("--seed", type=int, default=20261122, help="seed раскладки сцен")
    build_parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="ширина кадра")
    build_parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="высота кадра")
    build_parser.add_argument(
        "--min-visibility",
        type=float,
        default=0.2,
        help="доля видимой площади рамки, ниже которой объект не попадает в разметку",
    )
    build_parser.add_argument("--run-id", default=None, help="идентификатор прогона")
    build_parser.add_argument("--log", type=Path, default=None, help="журнал SQLite")

    info_parser = actions.add_parser("info", help="сводка по собранному датасету")
    add_common_options(info_parser)
    info_parser.add_argument("path", type=Path, help="каталог датасета")

    train_parser = subparsers.add_parser("train", help="дообучить голову детекции")
    add_common_options(train_parser)
    train_parser.add_argument("path", type=Path, help="каталог датасета")
    train_parser.add_argument("--epochs", type=int, default=120, help="число эпох")
    train_parser.add_argument("--batch", type=int, default=16, help="размер батча")
    train_parser.add_argument("--lr", type=float, default=2e-3, help="скорость обучения")
    train_parser.add_argument("--seed", type=int, default=20261122, help="seed обучения")
    train_parser.add_argument("--threads", type=int, default=2, help="потоков CPU")
    train_parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="каталог чекпойнтов (по умолчанию <датасет>/training)",
    )
    train_parser.add_argument(
        "--resume",
        action="store_true",
        help="продолжить с чекпойнта в каталоге обучения",
    )
    train_parser.add_argument(
        "--augment",
        action="store_true",
        help="включить аугментацию кадров (по умолчанию выключена)",
    )
    train_parser.add_argument("--log", type=Path, default=None, help="журнал SQLite")

    eval_parser = subparsers.add_parser("eval", help="оценить чекпойнт на сплите")
    add_common_options(eval_parser)
    eval_parser.add_argument("path", type=Path, help="каталог датасета")
    eval_parser.add_argument("--checkpoint", type=Path, required=True, help="файл чекпойнта")
    eval_parser.add_argument("--split", default="val", help="сплит оценки")
    eval_parser.add_argument("--iou", type=float, default=0.5, help="порог IoU для AP")
    eval_parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.05,
        help="порог уверенности при декодировании",
    )
    eval_parser.add_argument("--limit", type=int, default=None, help="предел кадров")
    eval_parser.add_argument("--log", type=Path, default=None, help="журнал SQLite")

    export_parser = subparsers.add_parser("export-onnx", help="экспорт чекпойнта в ONNX")
    add_common_options(export_parser)
    export_parser.add_argument("checkpoint", type=Path, help="файл чекпойнта")
    export_parser.add_argument("-o", "--output", type=Path, default=None, help="файл ONNX")
    export_parser.add_argument("--opset", type=int, default=DEFAULT_OPSET, help="версия opset")
    export_parser.add_argument(
        "--tolerance",
        type=float,
        default=ONNX_TOLERANCE,
        help="допуск расхождения с PyTorch",
    )


def _write(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _write_json(payload: object) -> None:
    _write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _config_from_args(args: argparse.Namespace) -> DatasetConfig:
    return DatasetConfig(
        scenes=args.scenes,
        frames_per_scene=args.frames,
        val_scenes=args.val_scenes,
        width=args.width,
        height=args.height,
        seed=args.seed,
        min_visibility=args.min_visibility,
    )


def _cmd_dataset_build(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    config = _config_from_args(args)
    store = open_store(args.log) if args.log is not None else None
    try:
        report = build_dataset(args.path, config, run_id=args.run_id, store=store)
    finally:
        if store is not None:
            store.close()
    logger.info("датасет собран: %s", report.path)
    if as_json:
        _write_json(report.to_dict())
        return int(ExitCode.OK)
    _write(report.summary())
    for name, scenes in report.scenes.items():
        _write(f"  {name}: сцены {', '.join(str(item) for item in scenes)}")
    _write(f"  время сборки {report.seconds:.1f} с")
    if report.run_id:
        _write(f"  прогон {report.run_id}")
    return int(ExitCode.OK)


def _cmd_dataset_info(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    try:
        report = report_from_manifest(args.path)
    except (FileNotFoundError, ValueError) as exc:
        raise ProjectError(str(exc)) from exc
    logger.debug("сводка датасета %s", report.path)
    if as_json:
        _write_json(report.to_dict())
        return int(ExitCode.OK)
    _write(report.summary())
    for name, scenes in report.scenes.items():
        _write(f"  {name}: сцены {', '.join(str(item) for item in scenes)}")
    _write(f"  классы: {', '.join(CLASS_NAMES)}")
    _write(f"  манифест {report.manifest_hash}")
    return int(ExitCode.OK)


def _cmd_train(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    dataset = Path(args.path)
    workdir = Path(args.workdir) if args.workdir is not None else dataset / TRAINING_DIR
    frames = read_frames(dataset, split="train")
    if not frames:
        raise ProjectError(f"в датасете нет кадров обучения: {dataset}")

    checkpoint_path = workdir / "checkpoint.pt"
    resuming = bool(args.resume) and checkpoint_path.is_file()
    if resuming:
        model, _ = DetectionHead.load(checkpoint_path)
    else:
        spec = ModelSpec(classes=CLASS_NAMES, width=frames[0].width, height=frames[0].height)
        model = DetectionHead(spec, seed=args.seed)

    config = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch,
        lr=args.lr,
        seed=args.seed,
        threads=args.threads,
        augment=AugmentConfig(seed=args.seed) if args.augment else None,
    )
    trainer = Trainer(model, config, workdir=workdir)
    if resuming:
        trainer.resume(checkpoint_path)
        logger.info("обучение продолжено с эпохи %d", trainer.start_epoch)
    report = trainer.fit(frames)

    logged: str | None = None
    if args.log is not None:
        with open_store(args.log) as store:
            logged = record_training(
                store,
                report,
                dataset_path=dataset,
                seed=args.seed,
                config={"train": config.to_dict(), "dataset": str(dataset)},
            )
    logger.info("обучение завершено: потеря %.4f", report.loss)
    if as_json:
        payload = report.to_dict()
        payload["log"] = logged
        _write_json(payload)
    else:
        _write(report.summary())
        for item in report.history[-TEXT_EPOCHS:]:
            _write(f"  эпоха {item.epoch:>3}: потеря {item.loss:.4f}")
        if logged is not None:
            _write(f"журнал: {logged}")
    if report.cancelled:
        logger.error("обучение прервано: %s", report.checkpoint)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def _cmd_eval(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    dataset = Path(args.path)
    if not Path(args.checkpoint).is_file():
        raise ProjectError(f"чекпойнт не найден: {args.checkpoint}")
    frames = read_frames(dataset, split=args.split, limit=args.limit)
    if not frames:
        raise ProjectError(f"в сплите {args.split!r} нет кадров: {dataset}")
    model, _ = DetectionHead.load(args.checkpoint)
    report = evaluate(
        model,
        frames,
        iou_threshold=args.iou,
        score_threshold=args.score_threshold,
    )
    logged: str | None = None
    if args.log is not None:
        with open_store(args.log) as store:
            logged = record_evaluation(
                store,
                report,
                dataset_path=dataset,
                model_path=args.checkpoint,
            )
    logger.info("оценка: mAP@0.5 %.3f", report.map50)
    if as_json:
        payload = report.to_dict()
        payload["log"] = logged
        _write_json(payload)
    else:
        _write(report.summary())
        if logged is not None:
            _write(f"журнал: {logged}")
    return int(ExitCode.OK)


def _cmd_export(args: argparse.Namespace, *, as_json: bool, logger: logging.Logger) -> int:
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_file():
        raise ProjectError(f"чекпойнт не найден: {checkpoint}")
    target = Path(args.output) if args.output is not None else checkpoint.with_suffix(".onnx")
    model, _ = DetectionHead.load(checkpoint)
    report = export_and_verify(model, target, opset=args.opset, tolerance=args.tolerance)
    logger.info("ONNX: расхождение %.2e", report.max_abs_diff)
    if as_json:
        _write_json(report.to_dict())
    else:
        _write(report.summary())
    if not report.verified:
        logger.error("ONNX разошёлся с PyTorch: %.2e > %.2e", report.max_abs_diff, report.tolerance)
        return int(ExitCode.DATA_ERROR)
    return int(ExitCode.OK)


def run_dataset_command(
    args: argparse.Namespace,
    *,
    as_json: bool,
    logger: logging.Logger,
) -> int:
    """Диспетчер подкоманд ``dataset``."""
    command = getattr(args, "dataset_command", None)
    try:
        if command == "build":
            return _cmd_dataset_build(args, as_json=as_json, logger=logger)
        if command == "info":
            return _cmd_dataset_info(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("не указано действие для dataset: build или info")
    return int(ExitCode.DATA_ERROR)


def run_train_command(
    args: argparse.Namespace,
    *,
    as_json: bool,
    logger: logging.Logger,
) -> int:
    """Диспетчер ``train``, ``eval`` и ``export-onnx``."""
    try:
        if args.command == "train":
            return _cmd_train(args, as_json=as_json, logger=logger)
        if args.command == "eval":
            return _cmd_eval(args, as_json=as_json, logger=logger)
        if args.command == "export-onnx":
            return _cmd_export(args, as_json=as_json, logger=logger)
    except (ProjectError, ValueError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        if as_json:
            _write_json({"status": "error", "error": str(exc)})
        else:
            sys.stderr.write(f"simorph: {exc}\n")
        return int(ExitCode.DATA_ERROR)
    logger.error("неизвестная команда обучения: %s", args.command)
    return int(ExitCode.DATA_ERROR)
