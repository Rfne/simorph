"""Профиль заказчика: спецификация, импорт, проекция, полнота, NDA, экспорт.

Пакет не зависит от Qt: те же функции использует CLI и интерфейс, поэтому
проверка профиля в редакторе и в headless-прогоне даёт одинаковый результат.
"""

from __future__ import annotations

from simorph.core.profile.completeness import Completeness, completeness
from simorph.core.profile.exporting import (
    PROFILE_EXPORT_VERSION,
    export_profile_payload,
    profile_hash,
    profile_payload,
    sanitize_paths,
)
from simorph.core.profile.importing import (
    ImportedProfile,
    JsonPositionError,
    UnknownField,
    import_profile_payload,
    import_profile_text,
    parse_profile_json,
)
from simorph.core.profile.nda import (
    NdaViolation,
    check_nda_export,
    is_nda_path,
    nda_roots,
)
from simorph.core.profile.projection import (
    ProjectionEntry,
    ProjectionResult,
    UncoveredParameter,
    project_profile,
)
from simorph.core.profile.reference import build_reference_profile
from simorph.core.profile.spec import (
    BLOCK_TITLES,
    CUSTOMER_CODES,
    PROFILE_SPEC,
    SPEC_BY_ID,
    ParameterSpec,
    spec_by_block,
)

__all__ = [
    "BLOCK_TITLES",
    "CUSTOMER_CODES",
    "PROFILE_EXPORT_VERSION",
    "PROFILE_SPEC",
    "SPEC_BY_ID",
    "Completeness",
    "ImportedProfile",
    "JsonPositionError",
    "NdaViolation",
    "ParameterSpec",
    "ProjectionEntry",
    "ProjectionResult",
    "UncoveredParameter",
    "UnknownField",
    "build_reference_profile",
    "check_nda_export",
    "completeness",
    "export_profile_payload",
    "import_profile_payload",
    "import_profile_text",
    "is_nda_path",
    "nda_roots",
    "parse_profile_json",
    "profile_hash",
    "profile_payload",
    "project_profile",
    "sanitize_paths",
    "spec_by_block",
]
