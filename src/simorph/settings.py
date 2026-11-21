"""Настройки приложения вне проекта (T1-06, G1-08).

Тема, язык, недавние файлы и путь к внешнему редактору принадлежат машине
пользователя, а не проекту: они живут в ``QSettings`` и не попадают
в ``project.json``. Qt импортируется лениво, чтобы core и CLI работали
без графической библиотеки.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - только для типизации
    from PySide6.QtCore import QSettings

#: Организация и приложение для реестра/файла настроек.
ORGANIZATION = "simorph"
APPLICATION = "simorph"

#: Ключи настроек.
KEY_THEME = "ui/theme"
KEY_LANGUAGE = "ui/language"
KEY_RECENT = "files/recent"
KEY_EDITOR = "tools/editor"
KEY_LAST_DIR = "files/last_dir"
KEY_SEARCH_HISTORY = "search/history"
KEY_LAYOUT = "ui/layout"

#: Сколько недавних проектов помнить.
RECENT_LIMIT = 10

#: Сколько поисковых запросов помнить.
SEARCH_HISTORY_LIMIT = 10

#: Допустимые темы и языки.
THEMES = ("dark", "light", "system")
LANGUAGES = ("ru", "en")


def default_settings() -> QSettings:
    """Создаёт ``QSettings`` для приложения.

    Организация и приложение задаются явно: без них PySide6 положил бы файл
    настроек в путь по умолчанию, зависящий от окружения.
    """
    from PySide6.QtCore import QSettings

    return QSettings(ORGANIZATION, APPLICATION)


class ApplicationSettings:
    """Обёртка над ``QSettings`` с проверкой значений."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings if settings is not None else default_settings()

    @property
    def theme(self) -> str:
        value = str(self._settings.value(KEY_THEME, "dark"))
        return value if value in THEMES else "dark"

    @theme.setter
    def theme(self, value: str) -> None:
        if value not in THEMES:
            raise ValueError(f"неизвестная тема: {value!r}")
        self._settings.setValue(KEY_THEME, value)

    @property
    def language(self) -> str:
        value = str(self._settings.value(KEY_LANGUAGE, "ru"))
        return value if value in LANGUAGES else "ru"

    @language.setter
    def language(self, value: str) -> None:
        if value not in LANGUAGES:
            raise ValueError(f"неизвестный язык: {value!r}")
        self._settings.setValue(KEY_LANGUAGE, value)

    @property
    def editor_path(self) -> Path | None:
        value = self._settings.value(KEY_EDITOR)
        if not value:
            return None
        return Path(str(value))

    @editor_path.setter
    def editor_path(self, value: Path | str | None) -> None:
        if value is None:
            self._settings.remove(KEY_EDITOR)
            return
        self._settings.setValue(KEY_EDITOR, str(value))

    @property
    def last_dir(self) -> Path | None:
        value = self._settings.value(KEY_LAST_DIR)
        if not value:
            return None
        return Path(str(value))

    @last_dir.setter
    def last_dir(self, value: Path | str | None) -> None:
        if value is None:
            self._settings.remove(KEY_LAST_DIR)
            return
        self._settings.setValue(KEY_LAST_DIR, str(value))

    @property
    def recent_projects(self) -> list[Path]:
        raw = self._settings.value(KEY_RECENT, [])
        if isinstance(raw, str):
            items: Iterable[Any] = [raw] if raw else []
        elif isinstance(raw, (list, tuple)):
            items = raw
        else:
            items = []
        return [Path(str(item)) for item in items if str(item).strip()]

    def add_recent(self, path: Path | str) -> None:
        """Добавляет проект в начало списка недавних без дублей."""
        candidate = Path(path)
        others = [item for item in self.recent_projects if item != candidate]
        ordered = [candidate, *others][:RECENT_LIMIT]
        self._settings.setValue(KEY_RECENT, [str(item) for item in ordered])

    def clear_recent(self) -> None:
        self._settings.remove(KEY_RECENT)

    @property
    def search_history(self) -> list[str]:
        """История поисковых запросов, новые первыми."""
        raw = self._settings.value(KEY_SEARCH_HISTORY, [])
        if isinstance(raw, str):
            items: Iterable[Any] = [raw] if raw else []
        elif isinstance(raw, (list, tuple)):
            items = raw
        else:
            items = []
        return [str(item) for item in items if str(item).strip()]

    def add_search_query(self, query: str) -> None:
        """Добавляет запрос в историю без дублей."""
        text = query.strip()
        if not text:
            return
        others = [item for item in self.search_history if item != text]
        self._settings.setValue(KEY_SEARCH_HISTORY, [text, *others][:SEARCH_HISTORY_LIMIT])

    def clear_search_history(self) -> None:
        self._settings.remove(KEY_SEARCH_HISTORY)

    @property
    def layout(self) -> list[str]:
        """Порядок вкладок редакторов, сохранённый в прошлом сеансе."""
        raw = self._settings.value(KEY_LAYOUT, [])
        if isinstance(raw, str):
            items: Iterable[Any] = [raw] if raw else []
        elif isinstance(raw, (list, tuple)):
            items = raw
        else:
            items = []
        return [str(item) for item in items if str(item).strip()]

    @layout.setter
    def layout(self, value: list[str]) -> None:
        self._settings.setValue(KEY_LAYOUT, list(value))

    def to_dict(self) -> dict[str, Any]:
        """Снимок настроек для диагностики."""
        editor = self.editor_path
        last_dir = self.last_dir
        return {
            "theme": self.theme,
            "language": self.language,
            "editor_path": str(editor) if editor else None,
            "last_dir": str(last_dir) if last_dir else None,
            "recent": [str(item) for item in self.recent_projects],
            "search_history": self.search_history,
            "layout": self.layout,
        }

    def sync(self) -> None:
        """Сбрасывает настройки на диск."""
        self._settings.sync()
