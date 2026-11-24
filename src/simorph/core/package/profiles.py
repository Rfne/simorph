"""Целевые профили борта: RK3588 и A733 (T11-05, FR-DEP-01, FR-DEP-02).

Профиль — это паспорт железа, под которое собирается пакет: состав CPU,
производительность NPU, тулчейн конвертации, формат модели и бюджеты
задержки и памяти. Он попадает в манифест пакета, поэтому проверка
«пакет собран под нужный борт» сводится к сравнению двух словарей.

Данные профилей — публичные характеристики SoC: RK3588 (4×A76 + 4×A55,
NPU 6 TOPS, RKNN-Toolkit2) и A733 (2×A76 + 6×A55, NPU до 3 TOPS,
ACUITY/NBG). Вендорские SDK в пакет не входят (NFR-LIC-03): профиль
описывает, чем конвертировать, но не содержит сам тулчейн.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from simorph.core.errors import ProjectError

__all__ = [
    "PROFILE_NAMES",
    "TARGET_PROFILES",
    "TargetProfile",
    "profile_for",
]

#: Бюджеты по умолчанию: те же числа, что в TRD 8.4.
DEFAULT_INFERENCE_P95_MS: Final = 400.0
DEFAULT_MEMORY_MB: Final = 512


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """Паспорт целевого борта: железо, тулчейн, формат и бюджеты."""

    target: str
    title: str
    cpu: str
    npu_tops: float
    toolchain: str
    toolchain_module: str
    precision: str
    model_format: str
    memory_mb: int = DEFAULT_MEMORY_MB
    inference_p95_ms: float = DEFAULT_INFERENCE_P95_MS
    notes: tuple[str, ...] = ()

    @property
    def budgets(self) -> dict[str, float]:
        return {
            "inference_p95_ms": self.inference_p95_ms,
            "memory_mb": float(self.memory_mb),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "title": self.title,
            "soc": {
                "cpu": self.cpu,
                "npu_tops": self.npu_tops,
                "toolchain": self.toolchain,
            },
            "precision": self.precision,
            "model_format": self.model_format,
            "budgets": self.budgets,
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        return (
            f"{self.target}: {self.cpu}, NPU {self.npu_tops:g} TOPS"
            f" · {self.toolchain} → {self.model_format}"
        )


#: Профили бортов, под которые собирается пакет.
TARGET_PROFILES: Final[dict[str, TargetProfile]] = {
    "rk3588": TargetProfile(
        target="rk3588",
        title="Rockchip RK3588",
        cpu="4x A76 + 4x A55",
        npu_tops=6.0,
        toolchain="RKNN-Toolkit2",
        toolchain_module="rknn",
        precision="int8",
        model_format="rknn",
        notes=(
            "конвертация: python -m rknn.api.rknn",
            "квантизация: калибровочный набор из пакета (calib/)",
        ),
    ),
    "a733": TargetProfile(
        target="a733",
        title="Allwinner A733",
        cpu="2x A76 + 6x A55",
        npu_tops=3.0,
        toolchain="ACUITY Toolkit",
        toolchain_module="acuitylib",
        precision="uint8",
        model_format="nbg",
        notes=(
            "конвертация: pegasus import onnx → pegasus quantize → pegasus export ovxlib",
            "NBG собирается тулчейном ACUITY на отдельной машине",
        ),
    ),
}

#: Имена поддерживаемых целей: порядок важен для справки CLI.
PROFILE_NAMES: Final[tuple[str, ...]] = tuple(TARGET_PROFILES)


def profile_for(target: str) -> TargetProfile:
    """Профиль по имени цели; неизвестная цель — ошибка данных, не сбой."""
    key = target.strip().lower()
    profile = TARGET_PROFILES.get(key)
    if profile is None:
        raise ProjectError(
            f"неизвестный целевой борт {target!r}: поддерживаются {', '.join(PROFILE_NAMES)}"
        )
    return profile
