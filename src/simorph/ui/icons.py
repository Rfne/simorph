"""Иконки дерева проекта без внешних файлов (T2-02).

Иконки рисуются в памяти: репозиторий не тащит бинарные ассеты, а тесты
не зависят от темы оформления. Если приложение ещё не создано, возвращается
пустая иконка — ``QPixmap`` без ``QGuiApplication`` не работает.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap

#: Цвета групп дерева.
GROUP_COLORS: dict[str, str] = {
    "profile": "#6f4fd8",
    "world": "#2f9e6b",
    "agents": "#c0782a",
    "exercises": "#2f7ec0",
    "pipelines": "#b04070",
    "reports": "#5c6b7a",
    "project": "#3d4b5c",
    "entity": "#4a5560",
}


@lru_cache(maxsize=64)
def letter_icon(letter: str, color: str, size: int = 16) -> QIcon:
    """Рисует квадрат с буквой — иконка группы или сущности."""
    if QGuiApplication.instance() is None:  # pragma: no cover - без приложения
        return QIcon()

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1.0, size - 1.0), 3.0, 3.0)
        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setPixelSize(max(8, int(size * 0.68)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0.0, 0.0, float(size), float(size)), Qt.AlignmentFlag.AlignCenter, letter
        )
    finally:
        painter.end()
    return QIcon(pixmap)


def group_icon(group: str, letter: str) -> QIcon:
    """Иконка группы по её имени."""
    return letter_icon(letter, GROUP_COLORS.get(group, GROUP_COLORS["entity"]))
