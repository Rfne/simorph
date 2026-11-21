"""Запуск графической оболочки (T2-01).

Точка входа отделена от окна, чтобы CLI мог выбрать платформу Qt до первого
импорта виджетов: в CI и в тестах нужен ``offscreen``, иначе приложение
попытается открыть дисплей.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from simorph.cli.exit_codes import ExitCode

__all__ = ["run_app"]


def run_app(
    path: Path | None = None,
    *,
    offscreen: bool = False,
    settings: object | None = None,
) -> int:
    """Открывает главное окно и запускает цикл событий.

    Args:
        path: проект, который нужно открыть сразу.
        offscreen: платформа Qt ``offscreen`` для запуска без дисплея.
        settings: подмена ``ApplicationSettings`` (тесты).
    """
    if offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from simorph.settings import ApplicationSettings
    from simorph.ui.main_window import MainWindow

    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = QApplication(sys.argv[:1])
    application.setApplicationName("simorph")
    application.setOrganizationName("simorph")

    resolved = settings if isinstance(settings, ApplicationSettings) else None
    window = MainWindow(resolved)
    window.show()

    if path is not None and not window.open_project(path, confirm=False):
        return int(ExitCode.DATA_ERROR)

    code = application.exec()
    return int(ExitCode.OK) if code == 0 else int(ExitCode.INTERNAL_ERROR)
