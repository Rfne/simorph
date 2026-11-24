"""Манифест пакета: схема, хэш содержимого и проверка файлов (T11-05, G11-04).

Манифест — единственный документ, по которому борт узнаёт, что за пакет
ему привезли: цель, паспорт SoC, конвейер, модели с хэшами, калибровка и
бюджеты. Поэтому схема строгая (лишние поля запрещены), а проверка умеет
не только «поля на месте», но и «файлы совпадают с хэшами».

Хэш манифеста считается по содержимому: ``created_at``, ``checksums`` и
сам ``manifest_hash`` из него исключены, иначе две сборки одного пакета
в разное время давали бы разные подписи.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from simorph.core.errors import ProjectError
from simorph.core.package.profiles import TargetProfile, profile_for

__all__ = [
    "CHECKSUMS_NAME",
    "HASH_EXCLUDED",
    "MANIFEST_NAME",
    "PACKAGE_FORMAT",
    "Budgets",
    "ManifestCheck",
    "ModelEntry",
    "PackageManifest",
    "SocSpec",
    "file_hash",
    "load_manifest",
    "manifest_hash",
    "verify_manifest",
]

#: Версия формата пакета: читатель проверяет её до разбора.
PACKAGE_FORMAT: Final = "simorph-package/1"

#: Имена файлов пакета.
MANIFEST_NAME: Final = "manifest.yaml"
CHECKSUMS_NAME: Final = "checksums.sha256"

#: Поля, которые не входят в хэш содержимого манифеста.
HASH_EXCLUDED: Final[frozenset[str]] = frozenset({"manifest_hash", "checksums", "created_at"})

#: Формат хэша файла: алгоритм и 64 шестнадцатеричных знака.
_HASH_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")


def file_hash(path: Path | str) -> str:
    """Хэш файла в том же формате, что и в манифесте."""
    return f"sha256:{hashlib.sha256(Path(path).read_bytes()).hexdigest()}"


def manifest_hash(payload: dict[str, Any]) -> str:
    """Хэш содержимого манифеста: стабильный на всех платформах."""
    content = {key: value for key, value in payload.items() if key not in HASH_EXCLUDED}
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


class SocSpec(BaseModel):
    """Паспорт SoC в манифесте: CPU, NPU и тулчейн."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu: str = Field(min_length=1)
    npu_tops: float = Field(gt=0.0)
    toolchain: str = Field(min_length=1)


class Budgets(BaseModel):
    """Бюджеты борта: задержка инференса и память."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    inference_p95_ms: float = Field(default=400.0, gt=0.0)
    memory_mb: float = Field(default=512.0, gt=0.0)


class ModelEntry(BaseModel):
    """Файл модели в пакете: имя, хэш, формат и роль."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    hash: str
    format: str | None = None
    role: str = "model"
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("hash")
    @classmethod
    def _check_hash(cls, value: str) -> str:
        if not _HASH_PATTERN.match(value):
            raise ValueError(f"хэш {value!r} не в формате sha256:<64 hex>")
        return value


class PackageManifest(BaseModel):
    """Манифест пакета: цель, конвейер, модели, калибровка и бюджеты."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: str = PACKAGE_FORMAT
    package_id: str = Field(min_length=1)
    run_id: str | None = None
    target: str = Field(min_length=1)
    soc: SocSpec
    pipeline: str = Field(min_length=1)
    models: list[ModelEntry] = Field(min_length=1)
    calibration: str | None = None
    budgets: Budgets = Field(default_factory=Budgets)
    checksums: str = CHECKSUMS_NAME
    project_hash: str | None = None
    app_version: str | None = None
    created_at: str | None = None
    manifest_hash: str | None = None

    @field_validator("target")
    @classmethod
    def _check_target(cls, value: str) -> str:
        profile_for(value)
        return value.strip().lower()

    @model_validator(mode="after")
    def _check_unique_models(self) -> PackageManifest:
        names = [item.name for item in self.models]
        if len(names) != len(set(names)):
            raise ValueError("имена моделей в манифесте повторяются")
        return self

    @property
    def profile(self) -> TargetProfile:
        return profile_for(self.target)

    def payload(self) -> dict[str, Any]:
        """Манифест как словарь в порядке полей TRD 8.4."""
        result: dict[str, Any] = {
            "format": self.format,
            "package_id": self.package_id,
            "run_id": self.run_id,
            "target": self.target,
            "soc": self.soc.model_dump(mode="json"),
            "pipeline": self.pipeline,
            "models": [item.model_dump(mode="json") for item in self.models],
            "calibration": self.calibration,
            "budgets": self.budgets.model_dump(mode="json"),
            "checksums": self.checksums,
            "project_hash": self.project_hash,
            "app_version": self.app_version,
            "created_at": self.created_at,
        }
        if self.manifest_hash is not None:
            result["manifest_hash"] = self.manifest_hash
        return result

    def computed_hash(self) -> str:
        """Хэш содержимого без служебных полей."""
        return manifest_hash(self.model_dump(mode="json"))

    def seal(self) -> PackageManifest:
        """Копия манифеста с заполненным ``manifest_hash``."""
        return self.model_copy(update={"manifest_hash": self.computed_hash()})

    def to_yaml(self) -> str:
        return str(
            yaml.safe_dump(
                self.payload(),
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        )

    def check_profile(self) -> list[str]:
        """Сверяет паспорт SoC с известным профилем цели."""
        profile = profile_for(self.target)
        issues: list[str] = []
        if self.soc.cpu != profile.cpu:
            issues.append(f"cpu {self.soc.cpu!r} не совпадает с профилем {profile.cpu!r}")
        if abs(self.soc.npu_tops - profile.npu_tops) > 1e-9:
            issues.append(
                f"npu_tops {self.soc.npu_tops:g} не совпадает с профилем {profile.npu_tops:g}"
            )
        if self.soc.toolchain != profile.toolchain:
            issues.append(
                f"toolchain {self.soc.toolchain!r} не совпадает с профилем {profile.toolchain!r}"
            )
        if self.format != PACKAGE_FORMAT:
            issues.append(f"формат пакета {self.format!r} неизвестен")
        return issues


@dataclass(slots=True)
class ManifestCheck:
    """Результат проверки манифеста и файлов пакета."""

    manifest: PackageManifest | None = None
    issues: list[str] = field(default_factory=list)
    checked_files: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues and self.manifest is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "error",
            "target": None if self.manifest is None else self.manifest.target,
            "package_id": None if self.manifest is None else self.manifest.package_id,
            "manifest_hash": None if self.manifest is None else self.manifest.manifest_hash,
            "checked_files": self.checked_files,
            "issues": list(self.issues),
        }

    def summary(self) -> str:
        if self.manifest is None:
            return "манифест не прочитан: " + "; ".join(self.issues or ["файл отсутствует"])
        state = "ok" if self.ok else "ошибки"
        return (
            f"манифест {self.manifest.package_id} ({self.manifest.target}): {state}"
            f" · файлов проверено {self.checked_files}"
            + ("" if self.ok else " · " + "; ".join(self.issues))
        )


def verify_manifest(path: Path | str, *, root: Path | str | None = None) -> ManifestCheck:
    """Читает манифест и сверяет его с файлами пакета.

    ``root`` — корень пакета: если он задан, каждый файл из манифеста должен
    существовать и совпасть по хэшу. Ошибки собираются списком, а не
    исключением: пакет с расхождением нужно уметь показать пользователю.
    """
    target = Path(path)
    check = ManifestCheck()
    if not target.is_file():
        check.issues.append(f"манифест не найден: {target}")
        return check
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        check.issues.append(f"манифест не является YAML: {exc}")
        return check
    if not isinstance(payload, dict):
        check.issues.append("манифест должен быть словарём")
        return check
    try:
        manifest = PackageManifest.model_validate(payload)
    except ValueError as exc:
        check.issues.append(f"манифест не соответствует схеме: {exc}")
        return check

    check.manifest = manifest
    check.issues.extend(manifest.check_profile())
    if manifest.manifest_hash is not None and manifest.manifest_hash != manifest.computed_hash():
        check.issues.append("manifest_hash не совпадает с содержимым манифеста")

    base = Path(root) if root is not None else target.parent
    files: list[tuple[str, str]] = [(item.name, item.hash) for item in manifest.models]
    files.append((manifest.pipeline, _hash_of(manifest.pipeline, base, manifest)))
    if manifest.calibration is not None:
        files.append((manifest.calibration, _hash_of(manifest.calibration, base, manifest)))
    for name, expected in files:
        candidate = base / name
        if not candidate.is_file():
            check.issues.append(f"файл пакета отсутствует: {name}")
            continue
        check.checked_files += 1
        actual = file_hash(candidate)
        if expected and actual != expected:
            check.issues.append(f"хэш файла {name} не совпадает: {actual} != {expected}")
    return check


def _hash_of(name: str, base: Path, manifest: PackageManifest) -> str:
    """Ожидаемый хэш вспомогательного файла: из checksums.sha256, если он есть."""
    checksums = base / manifest.checksums
    if not checksums.is_file():
        return ""
    for line in checksums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return f"sha256:{parts[0]}"
    return ""


def load_manifest(path: Path | str) -> PackageManifest:
    """Читает манифест или сообщает ошибку данных."""
    target = Path(path)
    if not target.is_file():
        raise ProjectError(f"манифест не найден: {target}")
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    try:
        return PackageManifest.model_validate(payload)
    except ValueError as exc:
        raise ProjectError(f"манифест {target} не соответствует схеме: {exc}") from exc
