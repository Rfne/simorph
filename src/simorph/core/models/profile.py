"""Раздел ``profile``: параметры профиля заказчика (TRD 5.2, 6.1).

Профиль — плоский список параметров с блоками A–J («Проработка.md», 3.3.2).
Класс параметра обязателен по смыслу: без него неизвестно, участвует ли
параметр в генерации, поэтому умолчания нет — незаполненный класс виден
и блокирует проекцию (T3-03).

Параметр проецируется в один из трёх векторов (``P_gen``, ``P_sim``,
``P_hw``) и перечисляет ключи, под которыми его значение попадает в вектор.
Класс ``N`` не проецируется вовсе: такие параметры сохраняются, но
в генерации и обучении не участвуют (FR-PRF-09).

Неизвестные поля импортированного профиля не теряются: они лежат в ``extra``
у параметра и у раздела, а экспорт возвращает их на прежнее место.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from simorph.core.models.base import (
    NonNegative,
    ParameterId,
    ParameterValue,
    SectionModel,
    StrictModel,
)

#: Класс параметра: синтез геометрии, эмуляция, метаданные, не воспроизводится.
ParameterClass = Literal["C", "E", "M", "N"]

#: Блок профиля A–J («Проработка.md», 3.3.2).
ProfileBlock = Literal["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]

#: Целевой вектор, в который проецируется параметр.
ParameterTarget = Literal["P_gen", "P_sim", "P_hw"]


class ProfileParameter(StrictModel):
    """Один параметр профиля.

    Параметр задаётся либо точным значением ``value``, либо диапазоном
    ``range``; одновременное отсутствие обоих считается ошибкой.
    """

    id: ParameterId
    block: ProfileBlock | None = None
    value: ParameterValue | None = None
    range: tuple[NonNegative, NonNegative] | None = None
    klass: ParameterClass | None = Field(alias="class", default=None)
    target: ParameterTarget | None = None
    projection: list[str] = Field(default_factory=list)
    unit: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=2000)
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.value is None and self.range is None:
            raise ValueError("нужно значение 'value' или диапазон 'range'")
        if self.range is not None and self.range[0] > self.range[1]:
            raise ValueError("нижняя граница диапазона больше верхней")
        if self.klass == "N" and self.target is not None:
            raise ValueError("параметр класса 'N' не проецируется в целевой вектор")
        if self.klass == "N" and self.projection:
            raise ValueError("параметр класса 'N' не имеет проекции")
        return self


class ProfileSection(SectionModel):
    """Профиль заказчика: блоки параметров A–J (TRD 5.2)."""

    customer: str | None = Field(default=None, max_length=200)
    nda: bool = False
    parameters: list[ProfileParameter] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
