"""Минимальное окно Qt для проверки платформенного плагина.

Проверка нужна, чтобы поймать поломку плагина платформы на целевой версии
Python и Qt до того, как появится настоящий интерфейс.
"""

from __future__ import annotations

import os
import sys

from simorph.cli.exit_codes import ExitCode


def run_ui_selftest(*, offscreen: bool = False, lifetime_ms: int = 0) -> int:
    """Открывает и закрывает окно, возвращая код возврата.

    Args:
        offscreen: переключить Qt на платформу ``offscreen`` до импорта виджетов.
        lifetime_ms: через сколько миллисекунд закрыть окно (0 — сразу).
    """
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    try:
        from PySide6.QtCore import QCoreApplication, QTimer, qVersion
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
    except Exception:
        return int(ExitCode.INTERNAL_ERROR)

    existing: QCoreApplication | None = QApplication.instance()
    application: QCoreApplication = QApplication(sys.argv[:1]) if existing is None else existing

    if not QGuiApplication.platformName():
        return int(ExitCode.INTERNAL_ERROR)

    window = QMainWindow()
    window.setWindowTitle("simorph selftest")
    window.resize(360, 180)
    window.setCentralWidget(QLabel(f"simorph selftest: Qt {qVersion()}"))
    window.show()

    QTimer.singleShot(max(lifetime_ms, 0), application.quit)
    code = application.exec()
    window.close()

    return int(ExitCode.OK) if code == 0 else int(ExitCode.INTERNAL_ERROR)
