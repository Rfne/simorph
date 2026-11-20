"""Версии схемы JSON-документов проекта.

Версия — строка вида ``"1.0"``. Сравнение идёт по паре чисел, поэтому
``"1.10"`` больше ``"1.9"``; строковое сравнение для этого не годится.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import StringConstraints

#: Версия, которую пишет приложение.
CURRENT_VERSION = "1.0"

#: Версии, которые читаются без миграции.
SUPPORTED_VERSIONS: tuple[str, ...] = (CURRENT_VERSION,)

#: Версии, для которых есть цепочка миграций до :data:`CURRENT_VERSION`.
LEGACY_VERSIONS: tuple[str, ...] = ("0.9",)

#: Тип поля ``schema_version`` во всех разделах проекта.
SchemaVersion = Annotated[
    str,
    StringConstraints(pattern=r"^\d+\.\d+$", strip_whitespace=True),
]

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int]:
    """Разбирает версию на пару чисел.

    Raises:
        ValueError: строка не является версией вида ``N.M``.
    """
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"неверный формат версии: {text!r}")
    return int(match.group(1)), int(match.group(2))


def compare_versions(left: str, right: str) -> int:
    """Сравнивает две версии: -1, 0 или 1."""
    left_pair = parse_version(left)
    right_pair = parse_version(right)
    return (left_pair > right_pair) - (left_pair < right_pair)


def is_current(text: str) -> bool:
    """Версия совпадает с текущей."""
    return text == CURRENT_VERSION


def is_from_future(text: str) -> bool:
    """Версия новее текущей: такой файл мог быть создан другой сборкой."""
    return compare_versions(text, CURRENT_VERSION) > 0


def is_known(text: str) -> bool:
    """Версия известна: текущая или мигрируемая."""
    return text in SUPPORTED_VERSIONS or text in LEGACY_VERSIONS
