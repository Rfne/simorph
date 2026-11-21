"""Поиск по проекту с историей запросов (T2-08).

Поиск идёт по документу, а не по виджетам: имя, идентификатор, заголовок
и теги проверяются на каждой сущности, поэтому вложенные элементы находятся
независимо от того, раскрыта ветка дерева или нет. История запросов живёт
в ``QSettings``: она принадлежит машине пользователя, а не проекту.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from simorph.core.models.project import ProjectDocument
from simorph.settings import ApplicationSettings

__all__ = ["SearchHit", "SearchPanel", "search_document"]

#: Поля, по которым сущность находится.
_SEARCH_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "agent_id",
    "exercise_id",
    "pipeline_id",
    "report_id",
)


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Найденный объект."""

    path: str
    title: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "title": self.title, "detail": self.detail}


def _walk(value: Any, path: str, hits: list[SearchHit], needle: str) -> None:
    """Обходит документ, собирая сущности с совпадением."""
    if isinstance(value, BaseModel):
        labels = []
        for field in _SEARCH_FIELDS:
            candidate = getattr(value, field, None)
            if candidate:
                labels.append(str(candidate))
        title = labels[0] if labels else value.__class__.__name__
        haystack = " ".join(labels).casefold()
        if labels and needle in haystack:
            hits.append(SearchHit(path=path, title=title, detail=value.__class__.__name__))
        for name in type(value).model_fields:
            alias = type(value).model_fields[name].alias or name
            child_path = f"{path}.{alias}" if path else alias
            _walk(getattr(value, name), child_path, hits, needle)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk(item, f"{path}.{index}", hits, needle)


def search_document(document: ProjectDocument, query: str) -> list[SearchHit]:
    """Ищет сущности по имени, идентификатору и заголовку."""
    needle = query.strip().casefold()
    if not needle:
        return []
    hits: list[SearchHit] = []
    _walk(document, "", hits, needle)
    return hits


class SearchPanel(QWidget):
    """Панель поиска с историей."""

    #: Запрос перехода к найденному объекту.
    navigate_requested = Signal(str)

    def __init__(
        self,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._document: ProjectDocument | None = None

        self._query = QLineEdit()
        self._query.setPlaceholderText("имя, идентификатор или заголовок")
        self._query.setClearButtonEnabled(True)
        self._query.returnPressed.connect(self._submit)
        self._query.textChanged.connect(self._on_text_changed)

        self._history = QComboBox()
        self._history.setEditable(False)
        self._history.addItem("история запросов")
        self._history.currentTextChanged.connect(self._on_history_selected)
        self._reload_history()

        self._results = QListWidget()
        self._results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._results.itemActivated.connect(self._on_activated)
        self._results.itemDoubleClicked.connect(self._on_activated)

        self._summary = QLabel("Введите запрос")
        self._hits: list[SearchHit] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._query)
        layout.addWidget(self._history)
        layout.addWidget(self._results, 1)
        layout.addWidget(self._summary)

    # --- данные ---------------------------------------------------------

    def set_document(self, document: ProjectDocument) -> None:
        self._document = document
        self.run_search()

    def hits(self) -> list[SearchHit]:
        """Текущие результаты поиска."""
        return list(self._hits)

    # --- поиск ----------------------------------------------------------

    def run_search(self) -> list[SearchHit]:
        """Выполняет поиск по текущему запросу."""
        text = self._query.text()
        if self._document is None:
            self._hits = []
            self._results.clear()
            self._summary.setText("Проект не открыт")
            return []
        self._hits = search_document(self._document, text)
        self._results.clear()
        for hit in self._hits:
            item = QListWidgetItem(f"{hit.title} — {hit.detail}")
            item.setData(int(Qt.ItemDataRole.UserRole), hit.path)
            item.setToolTip(hit.path)
            self._results.addItem(item)
        self._summary.setText(
            "Введите запрос" if not text.strip() else f"Найдено: {len(self._hits)}"
        )
        return self._hits

    def _submit(self) -> None:
        """Выполняет поиск и запоминает запрос в истории."""
        self.run_search()
        text = self._query.text().strip()
        if text:
            self._remember(text)

    def _on_text_changed(self, _text: str) -> None:
        self.run_search()

    def _on_activated(self, item: QListWidgetItem) -> None:
        path = item.data(int(Qt.ItemDataRole.UserRole))
        if isinstance(path, str) and path:
            self.navigate_requested.emit(path)

    # --- история --------------------------------------------------------

    def _reload_history(self) -> None:
        items = self._settings.search_history
        self._history.blockSignals(True)
        self._history.clear()
        self._history.addItem("история запросов")
        self._history.addItems(items)
        self._history.setCurrentIndex(0)
        self._history.blockSignals(False)

    def _remember(self, text: str) -> None:
        self._settings.add_search_query(text)
        self._reload_history()

    def _on_history_selected(self, text: str) -> None:
        if self._history.currentIndex() <= 0:
            return
        self._query.setText(text)
        self.run_search()
