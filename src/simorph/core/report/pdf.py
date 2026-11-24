"""PDF-выгрузка отчёта через Qt (T11-03, FR-REP-05).

PDF не пишется своим кодом: кириллица требует встроенного шрифта, а таблицы
и переносы — раскладки. Qt уже есть в зависимостях, поэтому документ
печатается ``QTextDocument`` в ``QPdfWriter``: тот же HTML, что и для
автономной выгрузки, только картинки берутся файлами, а не data-URI
(``QTextDocument`` не загружает base64-ресурсы сам).

Печать работает без окна: при отсутствии дисплея платформа Qt переводится
в ``offscreen``. Если Qt недоступен, это ошибка данных с понятным текстом,
а не падение.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from simorph.core.errors import ProjectError

__all__ = ["PdfUnavailableError", "render_pdf"]

#: Живое приложение Qt: без ссылки оно собирается сборщиком мусора.
_APPLICATION: object | None = None

#: Поля страницы A4, мм.
PAGE_MARGIN_MM: float = 12.0

#: Разрешение печати, DPI.
PAGE_DPI: int = 96


class PdfUnavailableError(ProjectError):
    """PDF не удалось собрать: нет Qt или платформы печати."""


def _ensure_application() -> None:
    """Поднимает QGuiApplication в offscreen-режиме, если его ещё нет."""
    global _APPLICATION
    try:
        from PySide6.QtGui import QGuiApplication
    except ImportError as exc:  # pragma: no cover - Qt входит в зависимости
        raise PdfUnavailableError("для PDF нужен PySide6 (Qt): модуль не установлен") from exc
    if QGuiApplication.instance() is not None:
        return
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        _APPLICATION = QGuiApplication([])
    except Exception as exc:  # pragma: no cover - зависит от платформы
        raise PdfUnavailableError(f"Qt не смог запуститься для PDF: {exc}") from exc


def render_pdf(html: str, path: Path | str) -> Path:
    """Печатает HTML в PDF формата A4."""
    _ensure_application()
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(target))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(
        QMarginsF(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM),
        QPageLayout.Unit.Millimeter,
    )
    writer.setResolution(PAGE_DPI)

    document = QTextDocument()
    document.setHtml(html)
    document.setPageSize(QSizeF(writer.width(), writer.height()))
    document.print_(writer)
    if not target.is_file() or target.stat().st_size == 0:
        raise PdfUnavailableError(f"PDF не создан: {target}")
    return target
