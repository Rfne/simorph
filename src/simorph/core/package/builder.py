"""Сборка пакета для борта: каталоги, манифест, скрипты и суммы (T11-06, G11-05).

Пакет — это каталог, который можно унести на борт целиком:

    manifest.yaml        паспорт пакета и прогона
    checksums.sha256     контрольные суммы всех файлов
    INSTALL.md           шаги установки и конвертации
    pipelines/           конфигурация конвейера
    models/              веса: ONNX-источник и/или целевой RKNN/NBG
    calib/               калибровка квантизации
    scripts/             convert_rknn.py или convert_nbg.py

Сборка детерминирована: имена файлов сохраняются, порядок строк сумм —
сортировка по пути, переводы строк — ``\\n``. Один и тот же набор входов
даёт один и тот же ``manifest_hash`` на Windows и Linux.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simorph.core.errors import ProjectError
from simorph.core.package.checksums import (
    ChecksumCheck,
    ChecksumEntry,
    verify_checksums,
    write_checksums,
)
from simorph.core.package.convert import ConversionReport, convert_model
from simorph.core.package.manifest import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    Budgets,
    ManifestCheck,
    ModelEntry,
    PackageManifest,
    SocSpec,
    file_hash,
    verify_manifest,
)
from simorph.core.package.profiles import TargetProfile, profile_for
from simorph.core.package.scripts import INSTALL_DOC, SCRIPT_NAMES, script_for
from simorph.version import __version__

__all__ = [
    "PackageConfig",
    "PackageReport",
    "build_package",
    "default_pipeline",
    "package_id_for",
    "verify_package",
]

#: Расширение → формат и роль модели в пакете.
_MODEL_FORMATS: dict[str, tuple[str, str]] = {
    ".onnx": ("onnx", "source"),
    ".rknn": ("rknn", "target"),
    ".nbg": ("nbg", "target"),
}

#: Каталоги пакета.
PIPELINES_DIR: str = "pipelines"
MODELS_DIR: str = "models"
CALIB_DIR: str = "calib"
SCRIPTS_DIR: str = "scripts"


@dataclass(slots=True)
class PackageConfig:
    """Что положить в пакет: цель, модели, конвейер, калибровка и бюджеты."""

    target: str
    models: list[Path] = field(default_factory=list)
    pipeline: Path | None = None
    calibration: Path | None = None
    run_id: str | None = None
    project_hash: str | None = None
    package_id: str | None = None
    app_version: str | None = None
    budgets: dict[str, float] = field(default_factory=dict)
    created_at: str | None = None
    convert: bool = False


@dataclass(slots=True)
class PackageReport:
    """Итог сборки: манифест, файлы, суммы и предупреждения."""

    destination: str
    target: str
    package_id: str
    manifest_hash: str
    files: list[str] = field(default_factory=list)
    entries: list[ChecksumEntry] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conversions: list[ConversionReport] = field(default_factory=list)
    manifest: PackageManifest | None = None
    check: ManifestCheck | None = None
    checksums: ChecksumCheck | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "destination": self.destination,
            "target": self.target,
            "package_id": self.package_id,
            "manifest_hash": self.manifest_hash,
            "files": list(self.files),
            "checksums": None if self.checksums is None else self.checksums.to_dict(),
            "conversions": [item.to_dict() for item in self.conversions],
            "warnings": list(self.warnings),
            "issues": list(self.issues),
            "seconds": round(self.seconds, 3),
        }

    def summary(self) -> str:
        state = "ok" if self.ok else "ошибки"
        tail = ""
        if self.warnings:
            tail = " · предупреждения: " + "; ".join(self.warnings)
        return (
            f"пакет {self.package_id} ({self.target}) → {self.destination}: {state}"
            f" · файлов {len(self.entries)} · {self.manifest_hash[:19]}…"
            + ("" if self.ok else " · " + "; ".join(self.issues))
            + tail
        )


def package_id_for(target: str, run_id: str | None) -> str:
    """Идентификатор пакета: цель и прогон, без времени сборки."""
    stamp = (run_id or "local000")[:8]
    return f"PKG-{stamp}-{target}"


def default_pipeline(target: str) -> dict[str, Any]:
    """Заготовка конвейера, если пользователь не дал свой.

    Это честная заглушка: она помечена ``generated`` и не выдаёт себя
    за настроенный граф. Пакет без конфигурации неполон, поэтому лучше
    положить явную заготовку, чем молча пропустить файл.
    """
    return {
        "format": "simorph-pipeline/1",
        "pipeline_id": f"PIPE-DEP-{target.upper()}",
        "target": target,
        "generated": True,
        "note": "заготовка: замените графом из проекта перед установкой на борт",
        "nodes": [],
        "edges": [],
    }


def _model_entry(path: Path, relative: str) -> ModelEntry:
    fmt, role = _MODEL_FORMATS.get(path.suffix.lower(), (path.suffix.lstrip(".").lower(), "model"))
    return ModelEntry(
        name=relative,
        hash=file_hash(path),
        format=fmt or None,
        role=role,
        size_bytes=path.stat().st_size,
    )


def _copy(source: Path, destination: Path) -> Path:
    if not source.is_file():
        raise ProjectError(f"файл пакета не найден: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_package(destination: Path | str, config: PackageConfig) -> PackageReport:
    """Собирает пакет в каталог и проверяет его контрольные суммы."""
    started = time.perf_counter()
    profile: TargetProfile = profile_for(config.target)
    if not config.models:
        raise ProjectError("для пакета нужна хотя бы одна модель")

    base = Path(destination)
    base.mkdir(parents=True, exist_ok=True)
    report = PackageReport(
        destination=str(base),
        target=profile.target,
        package_id=config.package_id or package_id_for(profile.target, config.run_id),
        manifest_hash="",
    )

    model_entries: list[ModelEntry] = []
    for source in config.models:
        source_path = Path(source)
        target_path = _copy(source_path, base / MODELS_DIR / source_path.name)
        relative = target_path.relative_to(base).as_posix()
        model_entries.append(_model_entry(target_path, relative))
        report.files.append(relative)

    if config.pipeline is not None:
        pipeline_path = _copy(
            Path(config.pipeline), base / PIPELINES_DIR / Path(config.pipeline).name
        )
        pipeline_relative = pipeline_path.relative_to(base).as_posix()
    else:
        pipeline_relative = f"{PIPELINES_DIR}/pipe_nn_{profile.target}.json"
        pipeline_path = base / pipeline_relative
        pipeline_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(default_pipeline(profile.target), ensure_ascii=False, indent=2)
        pipeline_path.write_text(f"{text}\n", encoding="utf-8", newline="\n")
    report.files.append(pipeline_relative)

    calibration_relative: str | None = None
    if config.calibration is not None:
        calibration_path = _copy(
            Path(config.calibration), base / CALIB_DIR / Path(config.calibration).name
        )
        calibration_relative = calibration_path.relative_to(base).as_posix()
        report.files.append(calibration_relative)

    script_name = SCRIPT_NAMES[profile.target]
    script_path = base / SCRIPTS_DIR / script_name
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_for(profile.target), encoding="utf-8", newline="\n")
    report.files.append(script_path.relative_to(base).as_posix())

    install_path = base / "INSTALL.md"
    install_path.write_text(INSTALL_DOC, encoding="utf-8", newline="\n")
    report.files.append("INSTALL.md")

    if config.convert:
        conversions, converted = _convert_models(base, profile, model_entries, calibration_relative)
        report.conversions.extend(conversions)
        for entry in converted:
            if entry.name not in {item.name for item in model_entries}:
                model_entries.append(entry)
                report.files.append(entry.name)

    budgets = Budgets.model_validate(config.budgets or profile.budgets)
    manifest = PackageManifest(
        package_id=report.package_id,
        run_id=config.run_id,
        target=profile.target,
        soc=SocSpec(
            cpu=profile.cpu,
            npu_tops=profile.npu_tops,
            toolchain=profile.toolchain,
        ),
        pipeline=pipeline_relative,
        models=model_entries,
        calibration=calibration_relative,
        budgets=budgets,
        project_hash=config.project_hash,
        app_version=config.app_version or __version__,
        created_at=config.created_at or _now(),
    ).seal()
    (base / MANIFEST_NAME).write_text(manifest.to_yaml(), encoding="utf-8", newline="\n")
    report.files.append(MANIFEST_NAME)

    report.manifest = manifest
    report.manifest_hash = manifest.manifest_hash or ""

    entries = write_checksums(base)
    report.entries = entries
    report.files = list(dict.fromkeys([*report.files, *(entry.path for entry in entries)]))
    report.checksums = verify_checksums(base)
    report.check = verify_manifest(base / MANIFEST_NAME, root=base)

    if report.checksums is not None and not report.checksums.ok:
        report.issues.extend(report.checksums.issues)
    if report.check is not None and not report.check.ok:
        report.issues.extend(report.check.issues)
    report.warnings.extend(_model_warnings(manifest))
    report.seconds = time.perf_counter() - started
    return report


def _convert_models(
    base: Path,
    profile: TargetProfile,
    entries: list[ModelEntry],
    calibration: str | None,
) -> tuple[list[ConversionReport], list[ModelEntry]]:
    """Конвертирует ONNX-источники в формат цели, если тулчейн доступен."""
    reports: list[ConversionReport] = []
    converted: list[ModelEntry] = []
    for item in entries:
        if item.format != "onnx":
            continue
        source = base / item.name
        output = source.with_suffix(f".{profile.model_format}")
        report = convert_model(
            source,
            output,
            profile.target,
            calibration=None if calibration is None else base / calibration,
        )
        reports.append(report)
        if report.ok and output.is_file():
            converted.append(_model_entry(output, output.relative_to(base).as_posix()))
    return reports, converted


def _model_warnings(manifest: PackageManifest) -> list[str]:
    """Предупреждает, если целевой модели ещё нет: конвертация — отдельный шаг."""
    warnings: list[str] = []
    formats = {item.format for item in manifest.models}
    if "onnx" not in formats:
        warnings.append("в пакете нет ONNX-источника весов")
    if manifest.profile.model_format not in formats:
        warnings.append(
            f"нет модели формата {manifest.profile.model_format}:"
            " нужна конвертация (scripts/ или --convert)"
        )
    return warnings


def verify_package(destination: Path | str) -> tuple[ManifestCheck, ChecksumCheck]:
    """Проверяет манифест и суммы готового пакета."""
    base = Path(destination)
    return (
        verify_manifest(base / MANIFEST_NAME, root=base),
        verify_checksums(base, name=CHECKSUMS_NAME),
    )
