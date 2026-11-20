"""Проверки выбора графической библиотеки (ADR-0003).

Тесты фиксируют решение: проект использует PySide6 под LGPLv3, а PyQt6
(GPLv3) в окружении отсутствует. Без этого миграция не защищена
от случайного возврата.
"""

from __future__ import annotations

import pytest

from simorph import version


@pytest.mark.unit
def test_pyside6_is_importable() -> None:
    pytest.importorskip("PySide6.QtCore")


@pytest.mark.unit
def test_pyqt6_is_not_installed() -> None:
    """PyQt6 не должен вернуться: GPLv3 ограничивает поставку (NFR-LIC-02)."""
    with pytest.raises(ImportError):
        __import__("PyQt6.QtCore")


@pytest.mark.unit
def test_qt_version_is_reported() -> None:
    assert version.collect_versions()["qt"] == "6.12.0"


@pytest.mark.unit
def test_qt_api_matches_pyside6() -> None:
    """pytest-qt и код должны использовать один и тот же биндинг."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert config["tool"]["pytest"]["ini_options"]["qt_api"] == "pyside6"


@pytest.mark.unit
def test_pyside6_declared_as_dependency() -> None:
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = config["project"]["dependencies"]
    assert any(item.startswith("PySide6") for item in dependencies)
    assert not any(item.startswith("PyQt6") for item in dependencies)


@pytest.mark.unit
def test_own_version_matches_metadata() -> None:
    """Версия в коде не должна расходиться с pyproject."""
    from importlib import metadata

    assert version.__version__ == metadata.version("simorph")
