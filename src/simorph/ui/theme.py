"""Темы оформления интерфейса (T2-01).

Тема — это палитра плюс небольшой набор правил QSS. Переключение идёт
без перезапуска: ``QApplication.setPalette`` перекрашивает уже созданные
виджеты, поэтому окно остаётся тем же объектом.

Режим ``system`` разрешается в конкретную тему по текущей светлоте палитры:
так поведение одинаково на Windows, Linux и в offscreen-режиме CI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - только для типизации
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

#: Имена тем.
DARK: Final = "dark"
LIGHT: Final = "light"
SYSTEM: Final = "system"

#: Все допустимые имена.
THEME_NAMES: Final = (DARK, LIGHT, SYSTEM)

_WINDOW: Final = {DARK: "#1e1f22", LIGHT: "#f4f5f7"}
_BASE: Final = {DARK: "#2b2d30", LIGHT: "#ffffff"}
_ALTERNATE: Final = {DARK: "#303236", LIGHT: "#eef0f3"}
_TEXT: Final = {DARK: "#dfe1e5", LIGHT: "#1f2328"}
_MUTED: Final = {DARK: "#8f949c", LIGHT: "#5c6370"}
_ACCENT: Final = {DARK: "#4c9aff", LIGHT: "#0b66c3"}
_BUTTON: Final = {DARK: "#33363b", LIGHT: "#e7e9ee"}


def resolve_theme(theme: str, *, system_dark: bool = True) -> str:
    """Приводит имя темы к конкретной: ``dark`` или ``light``."""
    if theme == SYSTEM:
        return DARK if system_dark else LIGHT
    return theme if theme in (DARK, LIGHT) else DARK


def system_is_dark(app: QApplication) -> bool:
    """Считает системную тему тёмной по светлоте фона окна."""
    from PySide6.QtGui import QPalette

    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def build_palette(theme: str) -> QPalette:
    """Собирает палитру для темы ``dark`` или ``light``."""
    from PySide6.QtGui import QColor, QPalette

    name = resolve_theme(theme)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(_WINDOW[name]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(_TEXT[name]))
    palette.setColor(QPalette.ColorRole.Base, QColor(_BASE[name]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(_ALTERNATE[name]))
    palette.setColor(QPalette.ColorRole.Text, QColor(_TEXT[name]))
    palette.setColor(QPalette.ColorRole.Button, QColor(_BUTTON[name]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(_TEXT[name]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(_BASE[name]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(_TEXT[name]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(_MUTED[name]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(_ACCENT[name]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, QPalette.ColorRole.Text, QColor(_MUTED[name]))
    palette.setColor(disabled, QPalette.ColorRole.WindowText, QColor(_MUTED[name]))
    return palette


def stylesheet(theme: str) -> str:
    """Небольшой QSS: рамки панелей и заголовки дерева."""
    name = resolve_theme(theme)
    border = _ALTERNATE[name]
    muted = _MUTED[name]
    return (
        f"QDockWidget::title {{ padding: 4px 8px; color: {muted}; }}"
        f"QTreeView, QTreeWidget {{ border: 1px solid {border}; }}"
        f"QHeaderView::section {{ padding: 4px 6px; border: none;"
        f" border-bottom: 1px solid {border}; }}"
        f"QTabWidget::pane {{ border: 1px solid {border}; }}"
    )


def apply_theme(app: QApplication, theme: str) -> str:
    """Применяет тему к приложению и возвращает её разрешённое имя."""
    resolved = resolve_theme(theme, system_dark=system_is_dark(app))
    app.setPalette(build_palette(resolved))
    app.setStyleSheet(stylesheet(resolved))
    return resolved
