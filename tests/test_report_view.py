"""Тесты вкладки «Отчёт»: две панели, предпросмотр и пороги (T11-04, G11-03)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QSplitter, QTextBrowser

from simorph.settings import ApplicationSettings
from simorph.ui.report_view import ReportView


@pytest.fixture
def view(
    qtbot: object,
    isolated_settings: ApplicationSettings,
    project_path: Path,
    report_database: Path,
) -> Iterator[ReportView]:
    created = ReportView(isolated_settings)
    qtbot.addWidget(created)  # type: ignore[attr-defined]
    created.set_project_path(project_path)
    yield created
    created.dispose()


@pytest.mark.ui
def test_report_view_has_two_panels(view: ReportView) -> None:
    splitter = view.findChild(QSplitter)
    preview = view.findChild(QTextBrowser, "report_preview")

    assert splitter is not None
    assert splitter.count() == 2
    assert preview is not None


@pytest.mark.ui
def test_report_view_lists_runs(view: ReportView) -> None:
    combo = view.findChild(QTextBrowser, "report_preview")

    assert combo is not None
    assert view.last_html is not None
    assert "1. Метаданные прогона" in view.last_html


@pytest.mark.ui
def test_report_view_preview_under_one_second(view: ReportView) -> None:
    view.refresh()

    assert view.last_seconds < 1.0
    assert view.last_seconds > 0.0


@pytest.mark.ui
def test_report_view_sections_toggle(view: ReportView) -> None:
    view._section_boxes["metrics"].setChecked(False)

    assert "metrics" not in view.sections()
    assert "3. Метрики" not in (view.last_html or "")


@pytest.mark.ui
def test_report_view_threshold_drives_conclusions(view: ReportView) -> None:
    view._threshold.setValue(1.0)

    html = view.last_html or ""
    assert "k.p95" in html
    assert "level-fail" in html


@pytest.mark.ui
def test_report_view_formats_and_dispose(view: ReportView, tmp_path: Path) -> None:
    view._format_boxes["pdf"].setChecked(True)

    assert view.formats() == ["md", "html", "pdf"]
    temp = view._temp
    assert temp is not None and temp.is_dir()

    view.dispose()

    assert view.last_html is None
    assert not temp.exists()
