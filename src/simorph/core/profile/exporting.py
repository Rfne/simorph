"""Экспорт профиля: версия, хэш и отсутствие абсолютных путей (T3-07).

Экспортированный профиль должен открываться на другой машине, поэтому:

- в строковых полях не остаётся абсолютных путей — от них берётся только
  имя файла;
- в обёртке указаны версия схемы и sha256 содержимого профиля, чтобы
  получатель мог убедиться, что файл не изменился;
- неизвестные поля, сохранённые при импорте, возвращаются на прежнее место,
  поэтому обмен с заказчиком не теряет его колонки.

Хэш считается по каноническому JSON профиля (сортировка ключей, без
пробелов) — так же, как хэш проекта, и не зависит от форматирования файла.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Final

from simorph.core.canonical import HASH_PREFIX
from simorph.core.models.profile import ProfileSection
from simorph.core.profile.nda import NdaViolation, check_nda_export

__all__ = [
    "PROFILE_EXPORT_VERSION",
    "dump_profile_export",
    "export_profile_payload",
    "profile_hash",
    "profile_payload",
    "sanitize_paths",
]

#: Версия формата обёртки экспорта.
PROFILE_EXPORT_VERSION: Final = "1.0"

#: Абсолютный путь Windows (``C:\``, ``D:/``) или POSIX (``/``).
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")


def _strip_path(text: str) -> str:
    """Оставляет от абсолютного пути только имя файла."""
    if not _ABSOLUTE_PATH.match(text):
        return text
    tail = re.split(r"[\\/]", text.rstrip("\\/"))[-1]
    return tail or "<path>"


def sanitize_paths(value: Any) -> Any:
    """Рекурсивно убирает абсолютные пути из строк."""
    if isinstance(value, str):
        return _strip_path(value)
    if isinstance(value, dict):
        return {str(key): sanitize_paths(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_paths(item) for item in value]
    return value


def _merge_extra(payload: dict[str, Any]) -> dict[str, Any]:
    """Возвращает неизвестные поля на прежнее место (рядом с известными)."""
    merged = {key: value for key, value in payload.items() if key != "extra"}
    extra = payload.get("extra")
    if isinstance(extra, dict):
        for key, value in extra.items():
            merged.setdefault(key, value)
    return merged


def profile_payload(section: ProfileSection, *, sanitize: bool = True) -> dict[str, Any]:
    """JSON-представление профиля без служебной обёртки."""
    raw = section.model_dump(mode="json", by_alias=True)
    parameters = [_merge_extra(item) for item in raw.get("parameters", [])]
    raw["parameters"] = parameters
    merged = _merge_extra(raw)
    return sanitize_paths(merged) if sanitize else merged


def _canonical_text(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def profile_hash(section: ProfileSection) -> str:
    """sha256 канонического JSON профиля."""
    digest = hashlib.sha256(_canonical_text(profile_payload(section)).encode("utf-8")).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def export_profile_payload(
    section: ProfileSection,
    *,
    destination: str | Path | None = None,
    roots: Any = None,
) -> dict[str, Any]:
    """Собирает обёртку экспорта с версией и хэшем.

    Raises:
        NdaViolation: профиль под NDA и место назначения вне защищённого
            каталога (или не задано).
    """
    violations = check_nda_export(section, destination, roots=roots)
    if violations:
        raise NdaViolation("; ".join(item.message for item in violations))
    return {
        "schema_version": section.schema_version,
        "export_version": PROFILE_EXPORT_VERSION,
        "hash": profile_hash(section),
        "profile": profile_payload(section),
    }


def dump_profile_export(payload: dict[str, Any]) -> str:
    """Читаемый вид обёртки: сортировка ключей, отступ 2, ``\\n`` в конце."""
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
