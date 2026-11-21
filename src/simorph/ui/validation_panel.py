"""Панель валидации с переходом к объекту (T2-07).

Замечания группируются по важности, путь каждого — путь pydantic, поэтому
двойной клик открывает ровно то поле, о котором говорит валидатор.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simorph.core.issues import Issue, Severity

__all__ = ["ValidationPanel"]

#: Роль пути к объекту в элементе дерева.
PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1

#: Подписи групп по важности.
_GROUP_TITLES: dict[Severity, str] = {
    Severity.ERROR: "Ошибки",
    Severity.WARNING: "Предупреждения",
}


class ValidationPanel(QWidget):
    """Список замечаний валидатора."""

    #: Запрос перехода к объекту по пути.
    navigate_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._issues: list[Issue] = []
        self._summary = QLabel("Замечаний нет")
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Замечание", "Путь", "Код"])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.itemActivated.connect(self._on_activated)
        self._tree.itemDoubleClicked.connect(self._on_activated)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._summary)
        layout.addWidget(self._tree, 1)

    def issues(self) -> list[Issue]:
        """Замечания, отображаемые сейчас."""
        return list(self._issues)

    def set_issues(self, issues: list[Issue]) -> None:
        """Перестраивает список замечаний."""
        self._issues = list(issues)
        self._tree.clear()

        errors = [item for item in self._issues if item.severity is Severity.ERROR]
        warnings = [item for item in self._issues if item.severity is not Severity.ERROR]
        self._summary.setText(
            "Замечаний нет"
            if not self._issues
            else f"Ошибок: {len(errors)}, предупреждений: {len(warnings)}"
        )

        for severity, group in ((Severity.ERROR, errors), (Severity.WARNING, warnings)):
            if not group:
                continue
            parent = QTreeWidgetItem([f"{_GROUP_TITLES[severity]} ({len(group)})", "", ""])
            parent.setFirstColumnSpanned(True)
            self._tree.addTopLevelItem(parent)
            for item in group:
                child = QTreeWidgetItem([item.message, item.path, str(item.code)])
                child.setData(0, PATH_ROLE, item.path)
                child.setToolTip(0, item.message)
                parent.addChild(child)
            parent.setExpanded(True)

    def _on_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path = item.data(0, PATH_ROLE)
        if isinstance(path, str) and path:
            self.navigate_requested.emit(path)
