"""Сборка датасета на диск и манифест (T10-01, G10-01).

Датасет — это каталог с кадрами, разметкой, масками и манифестом.
Манифест перечисляет каждый файл с его sha256, поэтому «датасет
воспроизводим по ``run_id`` и seed» проверяется сравнением хэша манифеста:
две сборки с одним seed дают один и тот же хэш, с разным — разные.

Каждый кадр записывается тремя файлами: ``images/*.png`` (цвет),
``labels/*.json`` (рамки, ключевые точки, метка времени, поза камеры) и
``masks/*.png`` (маска в индексах классов). Разметка и маска — это
производные одного рендера, поэтому разойтись с кадром не могут.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import numpy as np

from simorph.core.dataset.png import read_png, write_gray_png, write_rgb_png
from simorph.core.dataset.schema import (
    CLASS_NAMES,
    DatasetConfig,
    DatasetFrame,
    DatasetReport,
)
from simorph.core.dataset.source import SyntheticSceneSource, scene_seed
from simorph.core.dataset.splits import plan_splits, split_of
from simorph.core.logs.records import ArtifactRow, MetricRow, RunRecord, utc_now
from simorph.core.logs.store import RunStore

__all__ = [
    "DATASET_FORMAT",
    "MANIFEST_NAME",
    "build_dataset",
    "load_manifest",
    "manifest_hash",
    "read_frames",
    "report_from_manifest",
]

#: Версия формата датасета: читатель проверяет её до разбора файлов.
DATASET_FORMAT: Final = "simorph-dataset/1"

#: Имя файла манифеста.
MANIFEST_NAME: Final = "manifest.json"

#: Поля манифеста, не входящие в хэш содержимого: время и номер прогона.
HASH_EXCLUDED: Final[frozenset[str]] = frozenset({"manifest_hash", "created_at", "run_id"})


def _write_png(path: Path, array: np.ndarray) -> None:
    if array.ndim == 2:
        write_gray_png(path, array)
    else:
        write_rgb_png(path, array)


def _read_png(path: Path) -> np.ndarray:
    return read_png(path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    # newline="\n" обязателен: иначе на Windows хэш манифеста зависит от ОС.
    path.write_text(f"{text}\n", encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_hash(manifest: dict[str, Any]) -> str:
    """Хэш содержимого манифеста: подпись датасета, а не момента его сборки.

    ``created_at`` и ``run_id`` — происхождение, а не содержимое: две сборки
    с одним seed обязаны совпасть по хэшу, даже если различаются временем
    и номером прогона.
    """
    payload = {key: value for key, value in manifest.items() if key not in HASH_EXCLUDED}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def build_dataset(
    destination: Path | str,
    config: DatasetConfig | None = None,
    *,
    run_id: str | None = None,
    store: RunStore | None = None,
    project_hash: str | None = None,
    app_version: str | None = None,
) -> DatasetReport:
    """Собирает датасет в каталог и, если дан журнал, пишет прогон."""
    started = time.perf_counter()
    target = Path(destination)
    settings = config if config is not None else DatasetConfig()
    target.mkdir(parents=True, exist_ok=True)

    source = SyntheticSceneSource(settings)
    scene_ids = [scene_seed(settings.seed, index) for index in range(settings.scene_count)]
    plan = plan_splits(scene_ids, seed=settings.seed, val_scenes=settings.val_scenes)

    records: list[dict[str, Any]] = []
    class_counts = dict.fromkeys(CLASS_NAMES, 0)
    split_counts = {"train": 0, "val": 0}
    frames_total = 0
    boxes_total = 0
    next_id = 0

    for scene_index in range(settings.scene_count):
        frames = source.render_scene(scene_index)
        if not frames:
            continue
        scene_id = frames[0].scene_id
        split = split_of(plan, scene_id)
        for frame in frames:
            frame.frame_id = next_id
            next_id += 1
            name = f"frame_{frame.frame_id:06d}"
            image_path = target / "images" / f"{name}.png"
            label_path = target / "labels" / f"{name}.json"
            mask_path = target / "masks" / f"{name}.png"
            _write_png(image_path, frame.image)
            _write_json(label_path, frame.to_meta())
            if frame.mask is not None:
                _write_png(mask_path, frame.mask)
            records.append(
                {
                    "frame_id": frame.frame_id,
                    "scene_id": scene_id,
                    "split": split,
                    "t_ns": frame.t_ns,
                    "image": _relative(image_path, target),
                    "label": _relative(label_path, target),
                    "mask": _relative(mask_path, target) if mask_path.exists() else None,
                    "boxes": len(frame.boxes),
                    "image_sha256": _sha256(image_path),
                    "label_sha256": _sha256(label_path),
                }
            )
            frames_total += 1
            boxes_total += len(frame.boxes)
            split_counts[split] += 1
            for box in frame.boxes:
                class_counts[box.label] += 1

    manifest: dict[str, Any] = {
        "format": DATASET_FORMAT,
        "run_id": run_id,
        "config": settings.to_dict(),
        "classes": list(CLASS_NAMES),
        "splits": plan.to_dict(),
        "counts": {
            "frames": frames_total,
            "boxes": boxes_total,
            "train": split_counts["train"],
            "val": split_counts["val"],
            "classes": class_counts,
        },
        "frames": records,
        "created_at": utc_now(),
    }
    digest = manifest_hash(manifest)
    manifest["manifest_hash"] = digest
    manifest_path = target / MANIFEST_NAME
    _write_json(manifest_path, manifest)

    seconds = time.perf_counter() - started
    report = DatasetReport(
        path=str(target),
        run_id=run_id,
        manifest_hash=digest,
        config=settings.to_dict(),
        frames=frames_total,
        boxes=boxes_total,
        classes=class_counts,
        splits=split_counts,
        scenes={"train": list(plan.train), "val": list(plan.val)},
        seconds=seconds,
    )

    if store is not None:
        report.run_id = _record_dataset_run(
            store,
            report,
            settings,
            manifest_path,
            project_hash=project_hash,
            app_version=app_version,
        )
    return report


def _record_dataset_run(
    store: RunStore,
    report: DatasetReport,
    config: DatasetConfig,
    manifest_path: Path,
    *,
    project_hash: str | None,
    app_version: str | None,
) -> str:
    """Пишет прогон сборки в журнал: прогон, метрики и манифест."""
    run_id = report.run_id or uuid4().hex
    record = RunRecord(
        run_id=run_id,
        kind="dataset",
        status="running",
        project_hash=project_hash,
        seed=config.seed,
        config={"dataset": config.to_dict(), "manifest_hash": report.manifest_hash},
        artifact_dir=report.path,
        app_version=app_version,
    )
    store.start_run(record)
    t_ns = int(time.time_ns())
    metrics = [
        MetricRow(name="dataset.frames", value=float(report.frames), t_ns=t_ns, unit="frames"),
        MetricRow(name="dataset.boxes", value=float(report.boxes), t_ns=t_ns, unit="boxes"),
        MetricRow(name="dataset.train", value=float(report.splits["train"]), t_ns=t_ns),
        MetricRow(name="dataset.val", value=float(report.splits["val"]), t_ns=t_ns),
    ]
    metrics.extend(
        MetricRow(name=f"dataset.class.{name}", value=float(count), t_ns=t_ns)
        for name, count in report.classes.items()
    )
    store.write_metrics(run_id, metrics)
    store.add_artifacts(run_id, [ArtifactRow.from_path(manifest_path, kind="manifest")])
    store.finish_run(run_id, status="finished")
    return run_id


def load_manifest(directory: Path | str) -> dict[str, Any]:
    """Читает и проверяет манифест датасета."""
    path = Path(directory) / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"манифест датасета не найден: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != DATASET_FORMAT:
        raise ValueError(f"неизвестный формат датасета: {payload.get('format')!r}")
    return dict(payload)


def report_from_manifest(directory: Path | str) -> DatasetReport:
    """Отчёт по существующему датасету: то же, что вернула сборка."""
    manifest = load_manifest(directory)
    counts = manifest["counts"]
    splits = manifest["splits"]
    return DatasetReport(
        path=str(Path(directory)),
        run_id=manifest.get("run_id"),
        manifest_hash=str(manifest.get("manifest_hash", manifest_hash(manifest))),
        config=dict(manifest["config"]),
        frames=int(counts["frames"]),
        boxes=int(counts["boxes"]),
        classes={name: int(counts["classes"].get(name, 0)) for name in CLASS_NAMES},
        splits={"train": int(counts["train"]), "val": int(counts["val"])},
        scenes={"train": list(splits["train"]), "val": list(splits["val"])},
        seconds=0.0,
    )


def read_frames(
    directory: Path | str,
    *,
    split: str | None = None,
    limit: int | None = None,
) -> list[DatasetFrame]:
    """Читает кадры с диска: изображение, рамки и маску."""
    from simorph.core.dataset.schema import Box

    root = Path(directory)
    manifest = load_manifest(root)
    frames: list[DatasetFrame] = []
    for record in manifest["frames"]:
        if split is not None and record["split"] != split:
            continue
        label = json.loads((root / record["label"]).read_text(encoding="utf-8"))
        image = _read_png(root / record["image"])
        mask = _read_png(root / record["mask"]) if record.get("mask") else None
        frames.append(
            DatasetFrame(
                frame_id=int(label["frame_id"]),
                scene_id=int(label["scene_id"]),
                t_ns=int(label["t_ns"]),
                image=image,
                boxes=[Box.from_dict(item) for item in label["boxes"]],
                mask=mask,
                keypoints={
                    name: [(float(u), float(v), float(d)) for u, v, d in points]
                    for name, points in label.get("keypoints", {}).items()
                },
                pose=dict(label.get("pose", {})),
                spec=dict(label.get("spec", {})),
            )
        )
        if limit is not None and len(frames) >= limit:
            break
    return frames
