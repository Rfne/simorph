"""Доступ к значениям документа по пути pydantic (T2-03, T2-05).

Путь — та же строка, которой pydantic адресует ошибку: ``exercises.0.world_id``.
Поэтому инспектор, дерево и панель валидации говорят на одном языке, и переход
по ошибке — это просто поиск узла с таким путём.

Запись идёт через ``model_dump`` → правка → ``model_validate``: значение
проверяется всей схемой целиком, включая межполевые валидаторы. Модели
считаются неизменяемыми, поэтому возвращается новый документ.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from simorph.core.errors import ProjectError, ProjectValidationError
from simorph.core.issues import issues_from_validation_error
from simorph.core.models.project import ProjectDocument

__all__ = ["read_value", "split_path", "write_value"]


def split_path(path: str) -> list[str]:
    """Делит путь на части, отбрасывая пустые."""
    return [part for part in path.split(".") if part]


def _navigate(payload: Any, parts: list[str], *, path: str) -> Any:
    """Спускается по частям пути, сообщая, где именно он сломался."""
    current = payload
    for part in parts:
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise ProjectError(f"{path}: ожидался индекс списка, получено {part!r}") from exc
            if not 0 <= index < len(current):
                raise ProjectError(f"{path}: индекс {index} вне диапазона 0…{len(current) - 1}")
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise ProjectError(f"{path}: поле {part!r} отсутствует")
            current = current[part]
        else:
            raise ProjectError(f"{path}: {part!r} — не контейнер")
    return current


def _navigate_live(current: Any, part: str, *, path: str) -> Any:
    """Спускается по живому объекту pydantic, а не по словарю.

    Инспектору нужны модели и списки моделей: из словаря он не может
    построить форму по ``model_fields``. Алиасы (``class`` вместо ``klass``)
    учитываются так же, как в JSON-представлении.
    """
    if isinstance(current, BaseModel):
        for name, info in type(current).model_fields.items():
            if (info.alias or name) == part:
                return getattr(current, name)
        raise ProjectError(f"{path}: поле {part!r} отсутствует")
    if isinstance(current, (list, tuple)):
        try:
            index = int(part)
        except ValueError as exc:
            raise ProjectError(f"{path}: ожидался индекс списка, получено {part!r}") from exc
        if not 0 <= index < len(current):
            raise ProjectError(f"{path}: индекс {index} вне диапазона 0…{len(current) - 1}")
        return current[index]
    raise ProjectError(f"{path}: {part!r} — не контейнер")


def read_value(document: ProjectDocument, path: str) -> Any:
    """Возвращает значение по пути.

    Для моделей и списков возвращается сам объект pydantic, а не его
    JSON-дамп: инспектор строит форму по ``model_fields``.

    Raises:
        ProjectError: путь не существует в документе.
    """
    current: Any = document
    for part in split_path(path):
        current = _navigate_live(current, part, path=path)
    return current


def write_value(document: ProjectDocument, path: str, value: Any) -> ProjectDocument:
    """Возвращает копию документа с новым значением по пути.

    Raises:
        ProjectError: путь не существует или не является полем.
        ProjectValidationError: новое значение не проходит схему.
    """
    parts = split_path(path)
    if not parts:
        raise ProjectError("пустой путь: нужен путь к полю")

    payload = document.model_dump(mode="python", by_alias=True)
    container = _navigate(payload, parts[:-1], path=path)
    key = parts[-1]

    if isinstance(container, list):
        try:
            index = int(key)
        except ValueError as exc:
            raise ProjectError(f"{path}: ожидался индекс списка, получено {key!r}") from exc
        if not 0 <= index < len(container):
            raise ProjectError(f"{path}: индекс {index} вне диапазона 0…{len(container) - 1}")
        container[index] = value
    elif isinstance(container, dict):
        if key not in container:
            raise ProjectError(f"{path}: поле {key!r} отсутствует")
        container[key] = value
    else:
        raise ProjectError(f"{path}: {key!r} — не поле объекта")

    try:
        return ProjectDocument.model_validate(payload)
    except ValidationError as exc:
        raise ProjectValidationError(
            f"значение не прошло проверку схемы: {path}",
            issues=issues_from_validation_error(exc),
        ) from exc
