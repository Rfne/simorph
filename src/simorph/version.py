"""Версия приложения и сбор версий окружения.

Версии берутся из метаданных установленных дистрибутивов, а не через импорт:

* импорт PyBullet печатает баннер сборки в stdout и ломает JSON-вывод
  (баннер идёт мимо файлового дескриптора, поэтому перехват не помогает);
* импорт torch и openvino занимает секунды, а ``simorph version`` должна
  отвечать мгновенно.

Реальная загрузка тяжёлых модулей проверяется отдельно — командой ``selftest``.
"""

from __future__ import annotations

import platform
import sqlite3
from importlib import metadata


def _own_version() -> str:
    """Версия пакета из метаданных установки, чтобы не расходиться с pyproject."""
    try:
        return metadata.version("simorph")
    except metadata.PackageNotFoundError:  # pragma: no cover - запуск без установки
        return "0.0.0+unknown"


__version__ = _own_version()

#: Соответствие «ключ вывода → имя дистрибутива».
DISTRIBUTIONS: dict[str, str] = {
    "pybullet": "pybullet",
    "opencv": "opencv-contrib-python",
    "numpy": "numpy",
    "torch": "torch",
    "openvino": "openvino",
    "pydantic": "pydantic",
    "jinja2": "Jinja2",
    "panel": "panel",
    "bokeh": "bokeh",
}


def _distribution_version(distribution: str) -> str | None:
    """Версия установленного дистрибутива или ``None``, если он не установлен."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _qt_version() -> str | None:
    """Версия Qt, с которой собран PySide6.

    В PySide6 нет константы ``QT_VERSION_STR``, версия берётся из
    ``QtCore.qVersion()``. Импорт ``QtCore`` дешёвый и не пишет в stdout.
    """
    try:
        from PySide6.QtCore import qVersion
    except Exception:
        return None

    return str(qVersion())


def collect_versions() -> dict[str, str | None]:
    """Собирает версии приложения и окружения в порядке отображения."""
    versions: dict[str, str | None] = {
        "simorph": __version__,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "qt": _qt_version(),
    }
    for key, distribution in DISTRIBUTIONS.items():
        versions[key] = _distribution_version(distribution)
    versions["sqlite"] = sqlite3.sqlite_version
    return versions
