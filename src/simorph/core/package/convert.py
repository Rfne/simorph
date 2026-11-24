"""Конвертация ONNX в RKNN/NBG с проверкой тулчейна (T11-06, FR-DEP-05, FR-DEP-08).

Конвертация — отдельный шаг, а не часть сборки пакета: вендорские SDK
не входят в зависимости simorph (NFR-LIC-03). Поэтому функция сначала
смотрит, доступен ли тулчейн, и только потом что-то делает. Недоступный
тулчейн, неподдерживаемый оператор или отсутствующий калибровочный набор
возвращают отчёт с ``ok=False`` — экспорт от этого не падает (FR-DEP-09).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simorph.core.package.profiles import TargetProfile, profile_for

__all__ = [
    "ConversionReport",
    "ToolchainStatus",
    "check_toolchain",
    "convert_model",
]

#: Предел времени конвертации: ACUITY на большой модели считает минуты.
CONVERSION_TIMEOUT_S: float = 3600.0


@dataclass(frozen=True, slots=True)
class ToolchainStatus:
    """Наличие вендорского тулчейна для цели."""

    target: str
    toolchain: str
    module: str
    available: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "toolchain": self.toolchain,
            "module": self.module,
            "available": self.available,
            "detail": self.detail,
        }

    def summary(self) -> str:
        state = "доступен" if self.available else "не найден"
        return f"{self.toolchain} ({self.target}): {state} · {self.detail}"


@dataclass(slots=True)
class ConversionReport:
    """Итог конвертации: файл, тулчейн и причина отказа без исключения."""

    target: str
    source: str
    output: str
    toolchain: str
    available: bool
    ok: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "unavailable",
            "target": self.target,
            "source": self.source,
            "output": self.output,
            "toolchain": self.toolchain,
            "available": self.available,
            "reason": self.reason,
            "warnings": list(self.warnings),
            "seconds": round(self.seconds, 3),
        }

    def summary(self) -> str:
        if self.ok:
            return f"{self.target}: {self.output} собран за {self.seconds:.1f} с"
        return f"{self.target}: конвертация не выполнена · {self.reason}"


def check_toolchain(target: str) -> ToolchainStatus:
    """Ищет модуль тулчейна в текущем окружении, ничего не запуская."""
    profile: TargetProfile = profile_for(target)
    if profile.target == "a733":
        found = shutil.which("pegasus")
        return ToolchainStatus(
            target=profile.target,
            toolchain=profile.toolchain,
            module="pegasus",
            available=found is not None,
            detail=found or "pegasus не найден в PATH",
        )
    spec = importlib.util.find_spec(profile.toolchain_module)
    return ToolchainStatus(
        target=profile.target,
        toolchain=profile.toolchain,
        module=profile.toolchain_module,
        available=spec is not None,
        detail="модуль найден" if spec is not None else "модуль не установлен",
    )


def _convert_rknn(
    source: Path,
    output: Path,
    profile: TargetProfile,
    calibration: Path | None,
) -> tuple[bool, str, list[str]]:
    """RKNN через Python API тулчейна."""
    warnings: list[str] = []
    from rknn.api import RKNN

    session = RKNN(verbose=False)
    try:
        session.config(
            target_platform=profile.target,
            mean_values=[[0, 0, 0]],
            std_values=[[255, 255, 255]],
        )
        if session.load_onnx(model=str(source), inputs=["image"]) != 0:
            return False, "ONNX не загрузился: проверьте вход image", warnings
        quantize = calibration is not None and calibration.is_file()
        if not quantize:
            warnings.append("калибровочный набор не найден: сборка без квантизации")
        dataset = str(calibration) if quantize else None
        if session.build(do_quantization=quantize, dataset=dataset) != 0:
            return False, "сборка RKNN не удалась: проверьте поддержку операторов", warnings
        output.parent.mkdir(parents=True, exist_ok=True)
        if session.export_rknn(str(output)) != 0:
            return False, "экспорт RKNN не удался", warnings
    finally:
        session.release()
    return output.is_file(), "готово" if output.is_file() else "файл RKNN не создан", warnings


def _convert_nbg(
    source: Path,
    output: Path,
    calibration: Path | None,
) -> tuple[bool, str, list[str]]:
    """NBG через консольный тулчейн ACUITY (``pegasus``)."""
    warnings: list[str] = []
    work = output.parent / "acuity"
    work.mkdir(parents=True, exist_ok=True)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["pegasus", *args],
            capture_output=True,
            text=True,
            timeout=CONVERSION_TIMEOUT_S,
            check=False,
        )

    imported = run("import", "onnx", "--model", str(source), "--output-dir", str(work))
    if imported.returncode != 0:
        return False, f"импорт ONNX не удался: {imported.stderr.strip()}", warnings
    if calibration is not None and calibration.is_file():
        quantized = run("quantize", "--model", str(work), "--output-dir", str(work))
        if quantized.returncode != 0:
            return False, f"квантизация не удалась: {quantized.stderr.strip()}", warnings
    else:
        warnings.append("калибровочный набор не найден: квантизация пропущена")
    exported = run(
        "export",
        "ovxlib",
        "--model",
        str(work),
        "--output-dir",
        str(output.parent),
    )
    if exported.returncode != 0:
        return False, f"экспорт NBG не удался: {exported.stderr.strip()}", warnings
    if not output.is_file():
        candidates = sorted(output.parent.glob("*.nbg"))
        if candidates:
            candidates[0].replace(output)
    return output.is_file(), "готово" if output.is_file() else "файл NBG не создан", warnings


def convert_model(
    source: Path | str,
    output: Path | str,
    target: str,
    *,
    calibration: Path | str | None = None,
) -> ConversionReport:
    """Конвертирует ONNX в формат цели, если тулчейн доступен."""
    started = time.perf_counter()
    profile = profile_for(target)
    source_path = Path(source)
    output_path = Path(output)
    calibration_path = None if calibration is None else Path(calibration)
    status = check_toolchain(profile.target)
    report = ConversionReport(
        target=profile.target,
        source=str(source_path),
        output=str(output_path),
        toolchain=profile.toolchain,
        available=status.available,
        ok=False,
    )
    if not status.available:
        report.reason = f"{profile.toolchain} недоступен: {status.detail}"
        report.seconds = time.perf_counter() - started
        return report
    if not source_path.is_file():
        report.reason = f"нет исходной модели: {source_path}"
        report.seconds = time.perf_counter() - started
        return report

    try:
        if profile.target == "rk3588":
            ok, reason, warnings = _convert_rknn(
                source_path, output_path, profile, calibration_path
            )
        else:
            ok, reason, warnings = _convert_nbg(source_path, output_path, calibration_path)
    except Exception as exc:  # тулчейн чужой: падать нельзя, нужен отчёт
        ok, reason, warnings = False, f"{type(exc).__name__}: {exc}", []
    report.ok = ok
    report.reason = reason
    report.warnings = warnings
    report.seconds = time.perf_counter() - started
    return report
