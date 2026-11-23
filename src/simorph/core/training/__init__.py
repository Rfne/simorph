"""Дообучение головы детекции и экспорт ONNX (этап 10).

Пакет не зависит от Qt: CLI ``simorph train|eval|export-onnx``, тесты и
будущая вкладка используют одни и те же классы. Голова — маленькая
свёрточная сеть с выходом в сетке: тепловая карта классов, логарифмы
размеров и смещения центра.

Чекпойнт — единственный носитель состояния обучения: он хранит веса, форму
входа, конфигурацию и историю, поэтому продолжение после отмены не требует
ничего, кроме самого файла.
"""

from __future__ import annotations

from simorph.core.training.evaluate import (
    FPPI_TARGETS,
    EvalReport,
    average_precision,
    evaluate,
    recall_at_fppi,
)
from simorph.core.training.journal import record_evaluation, record_training
from simorph.core.training.model import DEFAULT_STRIDE, DetectionHead, ModelSpec
from simorph.core.training.onnx import (
    DEFAULT_OPSET,
    ONNX_TOLERANCE,
    OnnxReport,
    export_and_verify,
    export_onnx,
    verify_onnx,
)
from simorph.core.training.targets import (
    Targets,
    decode_predictions,
    encode_targets,
    focal_loss,
    nms,
)
from simorph.core.training.trainer import (
    CHECKPOINT_NAME,
    EpochRecord,
    TrainConfig,
    Trainer,
    TrainReport,
)

__all__ = [
    "CHECKPOINT_NAME",
    "DEFAULT_OPSET",
    "DEFAULT_STRIDE",
    "FPPI_TARGETS",
    "ONNX_TOLERANCE",
    "DetectionHead",
    "EpochRecord",
    "EvalReport",
    "ModelSpec",
    "OnnxReport",
    "Targets",
    "TrainConfig",
    "TrainReport",
    "Trainer",
    "average_precision",
    "decode_predictions",
    "encode_targets",
    "evaluate",
    "export_and_verify",
    "export_onnx",
    "focal_loss",
    "nms",
    "recall_at_fppi",
    "record_evaluation",
    "record_training",
    "verify_onnx",
]
