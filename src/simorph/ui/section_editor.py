"""Редактор раздела проекта (T2-04).

Вкладка показывает поддерево одного раздела: те же узлы и те же пути, что
в общем дереве, поэтому выбор объекта и переход по ошибке работают одинаково
в обоих местах. Модель общая — вкладка лишь ограничивает её корень, так что
правка видна сразу во всех представлениях.

Полноценные редакторы разделов (профиль, мир, агент, миссии) появятся
на этапах 3–6; оболочке нужен контейнер, который умеет открываться,
закрываться и восстанавливать раскладку.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Signal
from PySide6.QtWidgets import QTreeView, QVBoxLayout, QWidget

from simorph.ui.tree_model import PATH_ROLE, ProjectTreeModel

__all__ = ["SectionEditor"]


class SectionEditor(QWidget):
    """Представление одного раздела проекта."""

    #: Запрос перехода к объекту по пути.
    navigate_requested = Signal(str)

    def __init__(
        self,
        model: ProjectTreeModel,
        section: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = model
        self._section = section
        self.setObjectName(f"editor_{section}")

        self._view = QTreeView()
        self._view.setModel(model)
        self._view.setUniformRowHeights(True)
        self._view.setAlternatingRowColors(True)
        self._view.setRootIsDecorated(True)
        self._view.header().setStretchLastSection(True)
        self._view.activated.connect(self._on_activated)
        self._view.doubleClicked.connect(self._on_activated)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self.refresh()

    @property
    def section(self) -> str:
        return self._section

    @property
    def view(self) -> QTreeView:
        return self._view

    def refresh(self) -> None:
        """Показывает поддерево раздела и раскрывает первый уровень."""
        index = self._model.index_for_path(self._section)
        if index.isValid():
            self._view.setRootIndex(index)
            self._view.expandToDepth(0)

    def _on_activated(self, index: QModelIndex) -> None:
        path = index.data(PATH_ROLE)
        if isinstance(path, str) and path:
            self.navigate_requested.emit(path)
