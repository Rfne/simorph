"""Вкладки редакторов с сохранением раскладки (T2-04).

Вкладка — это именованная страница (``key``). Порядок открытых вкладок
и активная вкладка сохраняются в ``QSettings`` при закрытии окна и
восстанавливаются при следующем запуске: пользователь возвращается
к тому же рабочему месту.

Закрытие вкладки с несохранёнными правками требует подтверждения: без него
правки могли бы исчезнуть молча.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMessageBox, QTabWidget, QWidget

from simorph.settings import ApplicationSettings

__all__ = ["EDITOR_KEY_PROPERTY", "EditorTabs"]

#: Свойство виджета, в котором хранится ключ вкладки.
EDITOR_KEY_PROPERTY = "editor_key"


class EditorTabs(QTabWidget):
    """Набор вкладок редакторов."""

    #: Вкладка закрыта пользователем.
    editor_closed = Signal(str)

    def __init__(
        self,
        settings: ApplicationSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._keys: dict[int, str] = {}
        self._dirty: Callable[[str], bool] = lambda _key: False
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self._on_close_requested)

    # --- состояние ------------------------------------------------------

    def set_dirty_checker(self, checker: Callable[[str], bool]) -> None:
        """Задаёт проверку «есть ли несохранённые правки во вкладке»."""
        self._dirty = checker

    def keys(self) -> list[str]:
        """Ключи вкладок в текущем порядке."""
        return [self._keys[index] for index in range(self.count())]

    def index_of(self, key: str) -> int:
        """Индекс вкладки по ключу или -1."""
        for index, value in self._keys.items():
            if value == key:
                return index
        return -1

    def current_key(self) -> str:
        """Ключ активной вкладки или пустая строка."""
        return self._keys.get(self.currentIndex(), "")

    # --- управление вкладками -------------------------------------------

    def open_editor(self, key: str, title: str, widget: QWidget) -> int:
        """Открывает вкладку или активирует уже открытую."""
        existing = self.index_of(key)
        if existing >= 0:
            self.setCurrentIndex(existing)
            return existing
        # Ключ живёт на виджете: при закрытии вкладки индексы съезжают,
        # и карта «индекс → ключ» уже не может служить источником истины.
        widget.setProperty(EDITOR_KEY_PROPERTY, key)
        index = self.addTab(widget, title)
        self._keys[index] = key
        self.setCurrentIndex(index)
        self._reindex()
        return index

    def close_editor(self, key: str, *, confirm: bool = True) -> bool:
        """Закрывает вкладку; ``False``, если пользователь отказался."""
        index = self.index_of(key)
        if index < 0:
            return True
        if confirm and self._dirty(key):
            answer = QMessageBox.question(
                self,
                "Несохранённые изменения",
                f"Вкладка «{self.tabText(index)}» содержит несохранённые изменения. Закрыть?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.removeTab(index)
        self._reindex()
        self.editor_closed.emit(key)
        return True

    def _on_close_requested(self, index: int) -> None:
        key = self._keys.get(index, "")
        if key:
            self.close_editor(key)

    def _reindex(self) -> None:
        """Пересобирает карту «индекс → ключ» после вставки или удаления."""
        order: list[str] = []
        for index in range(self.count()):
            widget = self.widget(index)
            key = ""
            if widget is not None:
                stored = widget.property(EDITOR_KEY_PROPERTY)
                key = stored if isinstance(stored, str) else ""
                if not key:
                    key = widget.objectName() or f"tab-{index}"
            order.append(key or f"tab-{index}")
        self._keys = dict(enumerate(order))

    # --- сохранение раскладки -------------------------------------------

    def save_layout(self) -> None:
        """Пишет порядок вкладок и активную вкладку в настройки."""
        self._settings.layout = self.keys()
        self._settings.sync()

    def restore_layout(self, factories: dict[str, Callable[[], tuple[str, QWidget]]]) -> int:
        """Открывает вкладки в сохранённом порядке.

        Args:
            factories: ключ → функция, возвращающая заголовок и виджет.

        Returns:
            Сколько вкладок удалось открыть.
        """
        opened = 0
        for key in self._settings.layout:
            factory = factories.get(key)
            if factory is None:
                continue
            title, widget = factory()
            self.open_editor(key, title, widget)
            opened += 1
        if self.count() and self._settings.layout:
            self.setCurrentIndex(0)
        return opened
