"""Экспорт пакета на целевой борт: профили, манифест, сборка и конвертация (этап 11).

Пакет не зависит от Qt и от вендорских SDK: сборка и проверка работают на
любой машине, а конвертация ONNX → RKNN/NBG выполняется отдельным шагом там,
где установлен тулчейн. Поэтому «экспорт на борт» — это воспроизводимый
каталог с манифестом и контрольными суммами, а не прошивка железа.
"""

from __future__ import annotations

from simorph.core.package.builder import (
    PackageConfig,
    PackageReport,
    build_package,
    default_pipeline,
    package_id_for,
    verify_package,
)
from simorph.core.package.checksums import (
    ChecksumCheck,
    ChecksumEntry,
    read_checksums,
    verify_checksums,
    write_checksums,
)
from simorph.core.package.convert import (
    ConversionReport,
    ToolchainStatus,
    check_toolchain,
    convert_model,
)
from simorph.core.package.manifest import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    PACKAGE_FORMAT,
    Budgets,
    ManifestCheck,
    ModelEntry,
    PackageManifest,
    SocSpec,
    file_hash,
    load_manifest,
    manifest_hash,
    verify_manifest,
)
from simorph.core.package.profiles import (
    PROFILE_NAMES,
    TARGET_PROFILES,
    TargetProfile,
    profile_for,
)

__all__ = [
    "CHECKSUMS_NAME",
    "MANIFEST_NAME",
    "PACKAGE_FORMAT",
    "PROFILE_NAMES",
    "TARGET_PROFILES",
    "Budgets",
    "ChecksumCheck",
    "ChecksumEntry",
    "ConversionReport",
    "ManifestCheck",
    "ModelEntry",
    "PackageConfig",
    "PackageManifest",
    "PackageReport",
    "SocSpec",
    "TargetProfile",
    "ToolchainStatus",
    "build_package",
    "check_toolchain",
    "convert_model",
    "default_pipeline",
    "file_hash",
    "load_manifest",
    "manifest_hash",
    "package_id_for",
    "profile_for",
    "read_checksums",
    "verify_checksums",
    "verify_manifest",
    "verify_package",
    "write_checksums",
]
