"""Канонизация JSON и хэш конфигурации (T1-02, TRD 5.11).

Каноническая форма не зависит от порядка ключей и от форматирования файла:
ключи сортируются, разделители фиксированы, переводы строк и отступы
отсутствуют. Время приводится к UTC с точностью до секунды, поэтому один и
тот же проект на двух машинах даёт одинаковый sha256.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from simorph.core.models.project import ProjectDocument

#: Префикс хэша в манифестах и ссылках.
HASH_PREFIX = "sha256:"


def _timestamp(moment: datetime) -> str:
    """Единый вид времени: UTC, секунды, суффикс ``Z``."""
    if moment.tzinfo is None:
        raise ValueError("время без часового пояса нельзя канонизировать")
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize(value: Any) -> Any:
    """Приводит значение к виду, который однозначно сериализуется."""
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _normalize(value.value)
    return value


def canonical_payload(document: ProjectDocument) -> dict[str, Any]:
    """Каноническое представление документа в виде словаря."""
    payload = _normalize(document)
    if not isinstance(payload, dict):  # pragma: no cover - защита от регрессии
        raise TypeError("документ должен сериализоваться в объект")
    return payload


def canonical_text(document: ProjectDocument) -> str:
    """Канонический JSON одной строкой, без пробелов вокруг разделителей."""
    return json.dumps(
        canonical_payload(document),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_bytes(document: ProjectDocument) -> bytes:
    """Канонический JSON в UTF-8."""
    return canonical_text(document).encode("utf-8")


def compute_hash(document: ProjectDocument) -> str:
    """sha256 по каноническому JSON с префиксом ``sha256:``."""
    digest = hashlib.sha256(canonical_bytes(document)).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def dump_document(document: ProjectDocument) -> str:
    """Читаемый вид для файла: сортировка ключей, отступ 2, ``\\n`` в конце."""
    return (
        json.dumps(
            canonical_payload(document),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
