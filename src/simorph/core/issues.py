"""Структурированные замечания валидатора.

Одно замечание — это код, путь к полю и текст. Путь строится так же, как
в pydantic: ``agents.1.agent_id``, поэтому UI сможет перейти к объекту.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import ValidationError


class IssueCode(StrEnum):
    """Классы ошибок, которые обязан различать валидатор (G1-05)."""

    MISSING_FIELD = "missing_field"
    INVALID_TYPE = "invalid_type"
    UNKNOWN_FIELD = "unknown_field"
    INVALID_RANGE = "invalid_range"
    DANGLING_REFERENCE = "dangling_reference"
    DUPLICATE_ID = "duplicate_id"
    UNKNOWN_VERSION = "unknown_version"
    INVALID_GRAPH = "invalid_graph"
    INVALID_JSON = "invalid_json"
    INVALID_VALUE = "invalid_value"


class Severity(StrEnum):
    """Важность замечания."""

    ERROR = "error"
    WARNING = "warning"


#: Типы ошибок pydantic, означающие нарушение диапазона или длины.
_RANGE_ERROR_TYPES = frozenset(
    {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "too_long",
        "too_short",
    }
)

#: Типы ошибок pydantic, означающие неверное значение известного типа.
_VALUE_ERROR_TYPES = frozenset(
    {
        "literal_error",
        "string_pattern_mismatch",
        "string_type",
        "bool_type",
        "enum",
        "value_error",
        "tuple_type",
        "list_type",
        "dict_type",
        "datetime_type",
        "datetime_parsing",
        "uuid_type",
        "uuid_parsing",
    }
)


def classify_pydantic_error(error_type: str) -> IssueCode:
    """Переводит тип ошибки pydantic в класс замечания."""
    if error_type == "missing":
        return IssueCode.MISSING_FIELD
    if error_type == "extra_forbidden":
        return IssueCode.UNKNOWN_FIELD
    if error_type in _RANGE_ERROR_TYPES:
        return IssueCode.INVALID_RANGE
    if error_type in _VALUE_ERROR_TYPES:
        return IssueCode.INVALID_TYPE
    return IssueCode.INVALID_TYPE


def format_location(loc: tuple[int | str, ...]) -> str:
    """Собирает путь к полю из кортежа pydantic."""
    return ".".join(str(part) for part in loc)


@dataclass(frozen=True, slots=True)
class Issue:
    """Одно замечание валидатора."""

    code: IssueCode
    path: str
    message: str
    severity: Severity = Severity.ERROR

    def to_dict(self) -> dict[str, str]:
        """Представление для JSON-вывода CLI."""
        return {
            "code": str(self.code),
            "path": self.path,
            "message": self.message,
            "severity": str(self.severity),
        }


def issues_from_validation_error(error: ValidationError) -> list[Issue]:
    """Преобразует ошибки pydantic в список замечаний."""
    issues: list[Issue] = []
    for item in error.errors():
        loc = item.get("loc", ())
        path = format_location(loc) if isinstance(loc, tuple) else str(loc)
        issues.append(
            Issue(
                code=classify_pydantic_error(str(item.get("type", ""))),
                path=path,
                message=str(item.get("msg", "")),
            )
        )
    return issues


def issue(
    code: IssueCode,
    path: str,
    message: str,
    *,
    severity: Severity = Severity.ERROR,
) -> Issue:
    """Короткая форма создания замечания."""
    return Issue(code=code, path=path, message=message, severity=severity)


def has_errors(issues: list[Issue]) -> bool:
    """Есть ли среди замечаний хотя бы одна ошибка."""
    return any(item.severity is Severity.ERROR for item in issues)


def issues_to_payload(issues: list[Issue]) -> list[dict[str, Any]]:
    """Список замечаний для JSON-вывода."""
    return [item.to_dict() for item in issues]
