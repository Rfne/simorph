"""Раздел ``profile``: параметры профиля заказчика (TRD 5.2)."""

from __future__ import annotations

from typing import Literal, Self

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

#: Целевой вектор, в который проецируется параметр.
ParameterTarget = Literal["P_gen", "P_sim", "P_hw"]


class ProfileParameter(StrictModel):
    """Один параметр профиля.

    Параметр задаётся либо точным значением ``value``, либо диапазоном
    ``range``; одновременное отсутствие обоих считается ошибкой.
    """

    id: ParameterId
    value: ParameterValue | None = None
    range: tuple[NonNegative, NonNegative] | None = None
    klass: ParameterClass = Field(alias="class", default="M")
    target: ParameterTarget | None = None
    unit: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_value_or_range(self) -> Self:
        if self.value is None and self.range is None:
            raise ValueError("нужно значение 'value' или диапазон 'range'")
        if self.range is not None and self.range[0] > self.range[1]:
            raise ValueError("нижняя граница диапазона больше верхней")
        if self.klass == "N" and self.target is not None:
            raise ValueError("параметр класса 'N' не проецируется в целевой вектор")
        return self


class ProfileSection(SectionModel):
    """Профиль заказчика: блоки параметров A–J (TRD 5.2)."""

    customer: str | None = Field(default=None, max_length=200)
    parameters: list[ProfileParameter] = Field(default_factory=list)
