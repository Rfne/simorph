"""Импорт профиля заказчика (T3-01, FR-PRF-01).

Импорт не теряет данные: неизвестные поля раздела и параметра складываются
в ``extra`` и возвращаются на прежнее место при экспорте. Это важно для
анкет заказчика: файл часто содержит лишние колонки, и молча отбросить их
значит потерять сведения, которые ещё пригодятся.

Ошибка разбора JSON сообщает строку и колонку, а не только текст: редактор
по ней ставит курсор, CLI печатает позицию.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from simorph.core.errors import ProjectFormatError, ProjectValidationError
from simorph.core.issues import Issue, IssueCode, Severity, issue, issues_from_validation_error
from simorph.core.models.profile import ProfileSection
from simorph.core.profile.spec import SPEC_BY_ID
from simorph.core.schema_version import CURRENT_VERSION

__all__ = [
    "ImportedProfile",
    "JsonPositionError",
    "UnknownField",
    "import_profile_payload",
    "import_profile_text",
    "parse_profile_json",
]

#: Поля раздела, известные схеме.
_SECTION_FIELDS = frozenset({"schema_version", "customer", "nda", "parameters", "extra"})

#: Поля параметра, известные схеме (включая алиас ``class``).
_PARAMETER_FIELDS = frozenset(
    {
        "id",
        "block",
        "value",
        "range",
        "class",
        "klass",
        "target",
        "projection",
        "unit",
        "source",
        "comment",
        "extra",
    }
)


class JsonPositionError(ProjectFormatError):
    """JSON не разбирается; известны строка и колонка."""

    def __init__(self, message: str, *, line: int, column: int, position: int = 0) -> None:
        super().__init__(f"строка {line}, позиция {column}: {message}")
        self.message = message
        self.line = line
        self.column = column
        self.position = position


@dataclass(frozen=True, slots=True)
class UnknownField:
    """Неизвестное поле, сохранённое при импорте."""

    path: str
    name: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "value": self.value}


@dataclass(slots=True)
class ImportedProfile:
    """Результат импорта: профиль, неизвестные поля и замечания."""

    section: ProfileSection
    unknown: list[UnknownField] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def parameter_count(self) -> int:
        return len(self.section.parameters)

    def report(self) -> dict[str, Any]:
        """Отчёт для CLI и журнала."""
        return {
            "customer": self.section.customer,
            "nda": self.section.nda,
            "parameters": self.parameter_count,
            "unknown": [item.to_dict() for item in self.unknown],
            "issues": [item.to_dict() for item in self.issues],
        }


def parse_profile_json(text: str) -> dict[str, Any]:
    """Разбирает JSON профиля, сообщая строку и колонку ошибки.

    Raises:
        JsonPositionError: текст не является корректным JSON-объектом.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonPositionError(
            exc.msg,
            line=exc.lineno,
            column=exc.colno,
            position=exc.pos,
        ) from exc
    if not isinstance(payload, dict):
        raise JsonPositionError("ожидался JSON-объект", line=1, column=1, position=0)
    return payload


def _unwrap(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Снимает обёртку экспорта: ``{"schema_version", "hash", "profile"}``."""
    inner = payload.get("profile")
    if isinstance(inner, dict) and "parameters" in inner:
        envelope = {key: value for key, value in payload.items() if key != "profile"}
        return inner, envelope
    return payload, {}


def _split_unknown(
    payload: dict[str, Any],
    known: frozenset[str],
    *,
    path: str,
) -> tuple[dict[str, Any], dict[str, Any], list[UnknownField]]:
    """Делит объект на известные поля, ``extra`` и список неизвестных."""
    kept: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    unknown: list[UnknownField] = []
    for name, value in payload.items():
        if name in known:
            if name == "extra" and isinstance(value, dict):
                extra.update(value)
            else:
                kept[name] = value
            continue
        extra[name] = value
        unknown.append(UnknownField(path=path, name=name, value=value))
    return kept, extra, unknown


def _unit_issues(parameters: list[dict[str, Any]]) -> list[Issue]:
    """Единица измерения известного параметра должна совпадать со спекой."""
    issues: list[Issue] = []
    for index, parameter in enumerate(parameters):
        pid = parameter.get("id")
        spec = SPEC_BY_ID.get(pid) if isinstance(pid, str) else None
        if spec is None or spec.unit is None:
            continue
        unit = parameter.get("unit")
        if unit is not None and unit != spec.unit:
            issues.append(
                issue(
                    IssueCode.INVALID_VALUE,
                    f"parameters.{index}.unit",
                    f"единица {unit!r} не совпадает с профилем: ожидается {spec.unit!r}",
                    severity=Severity.WARNING,
                )
            )
    return issues


def import_profile_payload(payload: dict[str, Any]) -> ImportedProfile:
    """Импортирует профиль из разобранного JSON.

    Принимает как «голый» профиль, так и обёртку экспорта. Неизвестные поля
    сохраняются в ``extra`` раздела и параметров.

    Raises:
        ProjectValidationError: профиль не соответствует схеме; замечания
            содержат путь к полю.
    """
    inner, envelope = _unwrap(payload)
    kept, section_extra, unknown = _split_unknown(inner, _SECTION_FIELDS, path="profile")
    # Анкета заказчика приходит без версии схемы: подставляем текущую.
    kept.setdefault("schema_version", CURRENT_VERSION)

    raw_parameters = kept.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise ProjectValidationError(
            "поле 'parameters' должно быть списком",
            issues=[
                issue(IssueCode.INVALID_TYPE, "parameters", "ожидался список параметров"),
            ],
        )

    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(raw_parameters):
        if not isinstance(item, dict):
            raise ProjectValidationError(
                "параметр профиля должен быть объектом",
                issues=[
                    issue(
                        IssueCode.INVALID_TYPE,
                        f"parameters.{index}",
                        "ожидался объект параметра",
                    )
                ],
            )
        parameter, parameter_extra, parameter_unknown = _split_unknown(
            item,
            _PARAMETER_FIELDS,
            path=f"parameters.{index}",
        )
        if parameter_extra:
            parameter["extra"] = parameter_extra
        cleaned.append(parameter)
        unknown.extend(parameter_unknown)

    if section_extra:
        kept["extra"] = section_extra
    kept["parameters"] = cleaned

    try:
        section = ProfileSection.model_validate(kept)
    except ValidationError as exc:
        raise ProjectValidationError(
            "профиль не прошёл проверку схемы",
            issues=issues_from_validation_error(exc),
        ) from exc

    issues = _unit_issues(cleaned)
    _ = envelope  # обёртка проверена на структуру, метаданные экспорта не импортируются
    return ImportedProfile(section=section, unknown=unknown, issues=issues)


def import_profile_text(text: str) -> ImportedProfile:
    """Импортирует профиль из текста JSON.

    Raises:
        JsonPositionError: текст не разбирается как JSON-объект.
        ProjectValidationError: профиль не соответствует схеме.
    """
    return import_profile_payload(parse_profile_json(text))
