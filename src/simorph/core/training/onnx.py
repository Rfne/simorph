"""Экспорт ONNX и сверка с PyTorch (T10-06, G10-05).

ONNX — единственный источник весов для борта, поэтому экспорт фиксирует
вход ``[1, 3, H, W]`` и три выхода (карта классов, размер, смещение).
Динамические оси не используются: бортовая конвертация в RKNN/NBG требует
статических форм.

Критерий гейта — эталонная реализация ONNX на NumPy: она считает ровно тот
граф, который записан в файл, и не зависит от ядер конкретного CPU.
Прогон через OpenVINO идёт следом и пишется в отчёт отдельным числом:
на разных процессорах OpenVINO выбирает разные свёрточные ядра, и его
расхождение с PyTorch на порядки больше эталонного (на AVX-512 раннере
1,2·10⁻² против 1·10⁻⁶), хотя граф тот же. Поэтому OpenVINO — проверка
рантайма, а не гейта.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import torch

from simorph.core.training.model import DetectionHead

__all__ = [
    "DEFAULT_OPSET",
    "ONNX_TOLERANCE",
    "OnnxReport",
    "export_and_verify",
    "export_onnx",
    "verify_onnx",
]

#: Версия opset: 17 поддерживается и RKNN, и OpenVINO.
DEFAULT_OPSET: Final = 17

#: Допуск совпадения ONNX с PyTorch (G10-05).
ONNX_TOLERANCE: Final = 1e-4

#: Имена входов и выходов в графе.
INPUT_NAME: Final = "image"
OUTPUT_NAMES: Final[tuple[str, ...]] = ("heatmap", "size", "offset")


@dataclass(slots=True)
class OnnxReport:
    """Итог экспорта: файл, формы, расхождение и проверка рантайма."""

    path: str
    opset: int
    input_shape: tuple[int, ...]
    output_shapes: tuple[tuple[int, ...], ...]
    max_abs_diff: float
    tolerance: float
    verified: bool
    size_bytes: int
    seconds: float = 0.0
    runtime: str | None = None
    runtime_diff: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "path": self.path,
            "opset": self.opset,
            "input_shape": list(self.input_shape),
            "output_shapes": [list(shape) for shape in self.output_shapes],
            "max_abs_diff": float(f"{self.max_abs_diff:.3e}"),
            "tolerance": self.tolerance,
            "verified": self.verified,
            "runtime": self.runtime,
            "runtime_diff": (
                None if self.runtime_diff is None else float(f"{self.runtime_diff:.3e}")
            ),
            "size_bytes": self.size_bytes,
            "seconds": round(self.seconds, 3),
        }

    def summary(self) -> str:
        state = "совпадает" if self.verified else "РАСХОДИТСЯ"
        runtime = ""
        if self.runtime is not None and self.runtime_diff is not None:
            runtime = f" · {self.runtime} {self.runtime_diff:.2e}"
        return (
            f"ONNX {self.path}: вход {list(self.input_shape)}"
            f" · расхождение {self.max_abs_diff:.2e} ({state}, допуск {self.tolerance:g})"
            f"{runtime} · {self.size_bytes / 1024:.1f} КБ"
        )


def export_onnx(
    model: DetectionHead,
    path: Path | str,
    *,
    opset: int = DEFAULT_OPSET,
) -> Path:
    """Пишет ONNX с фиксированными формами входа и выходов."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    spec = model.spec
    sample = torch.zeros((1, 3, spec.height, spec.width), dtype=torch.float32)
    model.eval()
    with warnings.catch_warnings():
        # Легаси-экспортёр помечает себя устаревшим, но именно он даёт
        # статический граф без onnxscript; новый экспортёр требует лишнюю
        # зависимость и динамические оси.
        warnings.simplefilter("ignore", DeprecationWarning)
        torch.onnx.export(
            model,
            (sample,),
            str(target),
            input_names=[INPUT_NAME],
            output_names=list(OUTPUT_NAMES),
            opset_version=opset,
            do_constant_folding=True,
            dynamo=False,
        )
    return target


def _shape_of(value_info: Any) -> tuple[int, ...]:
    """Статическая форма тензора из описания входа или выхода графа."""
    return tuple(int(dim.dim_value) for dim in value_info.type.tensor_type.shape.dim)


def _reference_outputs(
    path: Path,
    sample: np.ndarray,
) -> tuple[list[np.ndarray], tuple[int, ...], tuple[tuple[int, ...], ...]]:
    """Считает граф эталонной реализацией ONNX на NumPy."""
    import onnx
    from onnx.reference import ReferenceEvaluator

    graph = onnx.load(str(path))
    session = ReferenceEvaluator(graph)
    outputs = [np.asarray(item) for item in session.run(list(OUTPUT_NAMES), {INPUT_NAME: sample})]
    input_shape = _shape_of(graph.graph.input[0])
    output_shapes = tuple(_shape_of(item) for item in graph.graph.output)
    return outputs, input_shape, output_shapes


def _runtime_diff(path: Path, sample: np.ndarray, reference: list[np.ndarray]) -> float | None:
    """Расхождение при прогоне в OpenVINO: свойство ядер, а не графа.

    Точность задаётся явно (f32): без подсказки OpenVINO на процессорах
    с AVX-512 уходит в пониженную точность свёрток, и число получается
    в тысячи раз больше эталонного при том же графе.
    """
    try:
        import openvino as ov
    except ImportError:  # pragma: no cover - OpenVINO входит в зависимости
        return None
    core = ov.Core()
    compiled = core.compile_model(
        core.read_model(str(path)),
        "CPU",
        {"INFERENCE_PRECISION_HINT": "f32"},
    )
    results = compiled([sample])
    exported = [np.asarray(results[index]) for index in range(len(OUTPUT_NAMES))]
    return max(
        float(np.max(np.abs(actual - expected)))
        for actual, expected in zip(exported, reference, strict=True)
    )


def verify_onnx(
    model: DetectionHead,
    path: Path | str,
    *,
    sample: np.ndarray | None = None,
    tolerance: float = ONNX_TOLERANCE,
) -> OnnxReport:
    """Сверяет записанный граф с PyTorch на эталонном кадре.

    Критерий — эталонная реализация ONNX: она воспроизводит ровно тот граф,
    что лежит в файле, и одинакова на всех платформах. Прогон в OpenVINO
    пишется отдельным числом и на вердикт не влияет.
    """
    started = time.perf_counter()
    target = Path(path)
    spec = model.spec
    if sample is None:
        generator = np.random.default_rng(20261122)
        sample = generator.random((1, 3, spec.height, spec.width), dtype=np.float32)
    sample = np.ascontiguousarray(sample, dtype=np.float32)
    tensor = torch.from_numpy(sample)
    model.eval()
    with torch.no_grad():
        reference = [output.detach().numpy() for output in model(tensor)]

    exported, input_shape, output_shapes = _reference_outputs(target, sample)
    difference = max(
        float(np.max(np.abs(actual - expected)))
        for actual, expected in zip(exported, reference, strict=True)
    )
    runtime_diff = _runtime_diff(target, sample, reference)
    return OnnxReport(
        path=str(target),
        opset=DEFAULT_OPSET,
        input_shape=input_shape,
        output_shapes=output_shapes,
        max_abs_diff=difference,
        tolerance=tolerance,
        verified=difference <= tolerance,
        size_bytes=target.stat().st_size,
        seconds=time.perf_counter() - started,
        runtime=None if runtime_diff is None else "openvino",
        runtime_diff=runtime_diff,
    )


def export_and_verify(
    model: DetectionHead,
    path: Path | str,
    *,
    opset: int = DEFAULT_OPSET,
    tolerance: float = ONNX_TOLERANCE,
    sample: np.ndarray | None = None,
) -> OnnxReport:
    """Экспорт и сверка одной операцией: то, что делает CLI."""
    target = export_onnx(model, path, opset=opset)
    report = verify_onnx(model, target, sample=sample, tolerance=tolerance)
    report.opset = opset
    return report
