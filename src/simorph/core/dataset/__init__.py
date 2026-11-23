"""Датасет для дообучения головы детекции (этап 10).

Пакет не зависит от Qt и от тренера: сборка датасета, сплиты и аугментация
используются и CLI ``simorph dataset``, и тестами, и самим обучением.

Источник — сцена PyBullet: кадр и его разметка снимаются одним рендером,
поэтому рамки, маска и ключевые точки согласованы по построению. Сплиты
строятся по сценам, так что кадры одной сцены не попадают одновременно
в train и val.
"""

from __future__ import annotations

from simorph.core.dataset.augment import AugmentConfig, AugmentParams, augment_frame
from simorph.core.dataset.builder import (
    DATASET_FORMAT,
    MANIFEST_NAME,
    build_dataset,
    load_manifest,
    manifest_hash,
    read_frames,
    report_from_manifest,
)
from simorph.core.dataset.schema import (
    CLASS_NAMES,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    Box,
    DatasetConfig,
    DatasetFrame,
    DatasetReport,
    box_iou,
)
from simorph.core.dataset.source import (
    CLASS_STYLES,
    FRAME_STEP_NS,
    SyntheticSceneSource,
    build_scene_layout,
    scene_seed,
)
from simorph.core.dataset.splits import SplitPlan, plan_splits

__all__ = [
    "CLASS_NAMES",
    "CLASS_STYLES",
    "DATASET_FORMAT",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "FRAME_STEP_NS",
    "MANIFEST_NAME",
    "AugmentConfig",
    "AugmentParams",
    "Box",
    "DatasetConfig",
    "DatasetFrame",
    "DatasetReport",
    "SplitPlan",
    "SyntheticSceneSource",
    "augment_frame",
    "box_iou",
    "build_dataset",
    "build_scene_layout",
    "load_manifest",
    "manifest_hash",
    "plan_splits",
    "read_frames",
    "report_from_manifest",
    "scene_seed",
]
