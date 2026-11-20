"""Базовые типы схемы проекта.

Все модели наследуются от :class:`StrictModel`: неизвестные поля запрещены,
поэтому опечатка в имени поля не проходит молча (T1-01).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from simorph.core.schema_version import SchemaVersion

#: Идентификатор объекта внутри раздела: латиница, цифры, ``_``, ``-``, ``.``.
EntityId = Annotated[
    str,
    StringConstraints(pattern=r"^[\w][\w.\-]*$", min_length=1, max_length=64),
]

#: Идентификатор параметра профиля (допускает кириллицу: ``П-06``).
ParameterId = Annotated[
    str,
    StringConstraints(pattern=r"^[\w][\w.\-]*$", min_length=1, max_length=64),
]

#: Отображаемое имя.
Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]

#: Относительный путь внутри проекта.
RelativePath = Annotated[str, StringConstraints(min_length=1, max_length=512)]

#: Доля от 0 до 1.
Unit = Annotated[float, Field(ge=0.0, le=1.0)]

#: Неотрицательное число.
NonNegative = Annotated[float, Field(ge=0.0)]

#: Положительное число.
Positive = Annotated[float, Field(gt=0.0)]

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


class StrictModel(BaseModel):
    """Модель с запретом лишних полей и поддержкой алиасов."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class SectionModel(StrictModel):
    """Раздел проекта: у каждого свой ``schema_version`` (TRD 5.11)."""

    schema_version: SchemaVersion


#: Значение параметра профиля: число, строка или флаг.
ParameterValue = float | int | str | bool

#: Произвольные параметры узла конвейера.
NodeParams = dict[str, Any]
