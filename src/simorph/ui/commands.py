"""Команды undo/redo над документом проекта (T2-05).

Одна правка свойства — одна команда. ``QUndoStack`` держит лимит шагов,
группирует их через ``beginMacro`` и умеет отключать запись, пока идёт
сохранение: сохранение не должно добавлять шаг.

Документ неизменяем, поэтому команда хранит прежний и новый снимок целиком.
Это дороже точечной правки, но снимает вопрос согласованности ссылок:
после undo документ побайтово тот же, что был до правки.
"""

from __future__ import annotations

from typing import Any, Final

from PySide6.QtGui import QUndoCommand, QUndoStack

from simorph.core.access import write_value
from simorph.core.models.project import ProjectDocument

__all__ = ["UNDO_LIMIT", "DocumentStore", "SetValueCommand"]

#: Лимит шагов истории (T2-05).
UNDO_LIMIT: Final = 100


class DocumentStore:
    """Текущий документ проекта и стек правок над ним."""

    def __init__(self, document: ProjectDocument, *, limit: int = UNDO_LIMIT) -> None:
        self._document = document
        self._stack = QUndoStack()
        self._stack.setUndoLimit(limit)
        self._listeners: list[Any] = []

    @property
    def document(self) -> ProjectDocument:
        return self._document

    @property
    def stack(self) -> QUndoStack:
        return self._stack

    def add_listener(self, callback: Any) -> None:
        """Подписывает на смену документа."""
        self._listeners.append(callback)

    def _emit(self) -> None:
        for callback in self._listeners:
            callback(self._document)

    def _replace(self, document: ProjectDocument) -> None:
        self._document = document
        self._emit()

    def reset(self, document: ProjectDocument) -> None:
        """Заменяет документ и очищает историю (открытие, новый проект)."""
        self._stack.clear()
        self._replace(document)

    def set_value(self, path: str, value: Any) -> None:
        """Правка свойства как одна команда undo.

        Новое состояние считается до ``push``: исключение из ``redo()``
        внутри стека PySide6 не переживает, поэтому невалидное значение
        отклоняется здесь же, а стек остаётся нетронутым.
        """
        before = self._document
        after = write_value(before, path, value)
        self._stack.push(SetValueCommand(self, before, after, text=f"изменение {path}"))

    def apply(self, document: ProjectDocument) -> None:
        """Применяет документ без записи в историю (загрузка, откат)."""
        self._replace(document)

    def undo_text(self) -> str:
        return self._stack.undoText()

    def redo_text(self) -> str:
        return self._stack.redoText()

    def can_undo(self) -> bool:
        return self._stack.canUndo()

    def can_redo(self) -> bool:
        return self._stack.canRedo()

    def undo(self) -> None:
        self._stack.undo()

    def redo(self) -> None:
        self._stack.redo()

    @property
    def is_modified(self) -> bool:
        """Есть ли неприменённые к файлу правки."""
        return not self._stack.isClean()

    def mark_saved(self) -> None:
        """Фиксирует точку сохранения: дальнейшие правки снова делают документ грязным."""
        self._stack.setClean()


class SetValueCommand(QUndoCommand):
    """Правка одного значения по пути.

    Оба состояния известны заранее, поэтому ``redo`` и ``undo`` только
    переставляют ссылки и не могут выбросить исключение.
    """

    def __init__(
        self,
        store: DocumentStore,
        before: ProjectDocument,
        after: ProjectDocument,
        *,
        text: str = "",
    ) -> None:
        super().__init__(text)
        self._store = store
        self._before = before
        self._after = after

    def redo(self) -> None:
        self._store._replace(self._after)

    def undo(self) -> None:
        self._store._replace(self._before)
