"""Реестр типов элементов миссии (T6-02, FR-MSN-05).

Двенадцать типов MVP различаются не только названием, но и контрактом:
какие поля обязательны, можно ли из элемента идти дальше и является ли он
терминальным. Реестр нужен, чтобы редактор, валидатор и отчёт говорили
об одном и том же наборе, а не дублировали условия по коду.

Категории группируют элементы в панели графа: управление, движение,
наблюдение и безопасность.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from simorph.core.models.exercise import ElementType, MissionElement

__all__ = [
    "ELEMENT_CATEGORIES",
    "ELEMENT_SPECS",
    "ELEMENT_TYPES",
    "ElementSpec",
    "element_payload",
    "element_spec",
    "required_fields",
]

#: Категории элементов в порядке панели графа.
ELEMENT_CATEGORIES: Final[tuple[str, ...]] = (
    "control",
    "motion",
    "observation",
    "safety",
)


@dataclass(frozen=True, slots=True)
class ElementSpec:
    """Контракт одного типа элемента."""

    type: ElementType
    title: str
    category: str
    description: str
    #: Поля, без которых элемент не имеет смысла.
    required: tuple[str, ...] = ()
    #: Терминальный элемент не может иметь исходящих переходов.
    terminal: bool = False


#: Полный перечень типов элементов MVP (TRD 5.7).
ELEMENT_SPECS: Final[tuple[ElementSpec, ...]] = (
    ElementSpec("start", "Старт", "control", "Точка входа миссии; ровно одна на миссию"),
    ElementSpec("waypoint", "Точка маршрута", "motion", "Прибытие в позу", ("pose",)),
    ElementSpec("transit", "Переход", "motion", "Движение в позу с заданной скоростью", ("pose",)),
    ElementSpec(
        "observe",
        "Наблюдение",
        "observation",
        "Работа сенсором в зоне заданное время",
        ("zone", "sensor"),
    ),
    ElementSpec("dwell", "Зависание", "motion", "Ожидание на месте", ("duration_s",)),
    ElementSpec(
        "activate_sensor",
        "Включить сенсор",
        "observation",
        "Включение сенсора перед наблюдением",
        ("sensor",),
    ),
    ElementSpec("capture", "Съёмка", "observation", "Съёмка кадров камерой", ("sensor",)),
    ElementSpec("return_to_base", "Возврат на базу", "motion", "Возврат в точку старта"),
    ElementSpec(
        "abort",
        "Аварийное завершение",
        "safety",
        "Прерывание миссии с указанием причины",
        ("condition",),
        terminal=True,
    ),
    ElementSpec(
        "loop",
        "Цикл",
        "control",
        "Повтор участка миссии с явным лимитом итераций",
        ("iterations",),
    ),
    ElementSpec(
        "parallel",
        "Параллельно",
        "control",
        "Ветвление на несколько одновременных ветвей",
    ),
    ElementSpec(
        "condition",
        "Условие",
        "control",
        "Выбор ветви по условию перехода",
        ("condition",),
    ),
)

#: Типы элементов в порядке реестра.
ELEMENT_TYPES: Final[tuple[str, ...]] = tuple(spec.type for spec in ELEMENT_SPECS)

_SPECS_BY_TYPE: Final[dict[str, ElementSpec]] = {spec.type: spec for spec in ELEMENT_SPECS}


def element_spec(kind: str) -> ElementSpec:
    """Контракт типа; неизвестный тип не должен сюда попадать.

    Raises:
        KeyError: типа нет в реестре.
    """
    return _SPECS_BY_TYPE[kind]


def required_fields(kind: str) -> tuple[str, ...]:
    """Обязательные поля типа элемента."""
    spec = _SPECS_BY_TYPE.get(kind)
    return spec.required if spec is not None else ()


def element_payload(element: MissionElement) -> dict[str, object]:
    """Элемент для отчёта и JSON: тип, подпись и заполненные поля."""
    spec = _SPECS_BY_TYPE.get(element.type)
    payload: dict[str, object] = {
        "id": element.id,
        "type": element.type,
        "title": spec.title if spec is not None else element.type,
        "category": spec.category if spec is not None else "other",
        "next": list(element.next),
    }
    for name in (
        "pose",
        "speed_mps",
        "zone",
        "sensor",
        "duration_s",
        "ttl_s",
        "priority",
        "iterations",
        "condition",
    ):
        value = getattr(element, name)
        if value is not None:
            payload[name] = list(value) if isinstance(value, tuple) else value
    return payload
