"""Выгрузка отчёта: Markdown, автономный HTML и PDF (T11-03, G11-01)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from simorph.core.report import (
    PdfUnavailableError,
    ReportConfig,
    ReportTemplateError,
    collect_report_data,
    render_report,
    render_report_files,
)

#: Идентификатор прогона в журнале фикстуры.
REPORT_RUN_ID = "sim-report"


@pytest.fixture
def data(report_database: Path):
    return collect_report_data(report_database, REPORT_RUN_ID)


@pytest.mark.integration
def test_render_markdown_and_html(data, tmp_path: Path) -> None:
    result = render_report(data, ReportConfig(), output_dir=tmp_path)

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    html = (tmp_path / "report.html").read_text(encoding="utf-8")

    assert result.files["md"] == str(tmp_path / "report.md")
    assert result.files["html"] == str(tmp_path / "report.html")
    assert markdown.startswith("# Отчёт по прогону")
    assert "## 1. Метаданные прогона" in markdown
    assert "## 6. Выводы" in markdown
    assert "| `k.p95` |" in markdown
    assert "![Коэффициент масштаба k](plots/k.png)" in markdown
    assert (tmp_path / "plots" / "k.png").read_bytes().startswith(b"\x89PNG")

    assert html.startswith("<!DOCTYPE html>")
    assert '<html lang="ru">' in html
    assert "data:image/png;base64," in html
    assert result.plots


@pytest.mark.integration
def test_html_is_offline(data, tmp_path: Path) -> None:
    render_report(data, ReportConfig(), output_dir=tmp_path)

    html = (tmp_path / "report.html").read_text(encoding="utf-8")

    assert not re.search(r"https?://", html)
    assert "<script" not in html
    assert "<link" not in html


@pytest.mark.integration
def test_sections_filter_output(data, tmp_path: Path) -> None:
    config = ReportConfig(sections=["metadata", "conclusions"], export=["md"])
    render_report(data, config, output_dir=tmp_path)

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert "## 1. Метаданные прогона" in markdown
    assert "## 3. Метрики" not in markdown
    assert "## 4. Графики" not in markdown
    assert "## 6. Выводы" in markdown
    assert not (tmp_path / "plots").exists()
    assert not (tmp_path / "report.html").exists()


@pytest.mark.integration
def test_custom_template_is_used(data, tmp_path: Path) -> None:
    template = tmp_path / "custom.j2"
    template.write_text(
        "# {{ config.title }}\n\nСвой шаблон: {{ data.run_id }}, метрик {{ metrics | length }}\n",
        encoding="utf-8",
    )
    config = ReportConfig(title="Свой отчёт", template=str(template), export=["md"])

    render_report(data, config, output_dir=tmp_path)

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Свой отчёт")
    assert "Свой шаблон: sim-report" in markdown
    assert "## 1. Метаданные" not in markdown


@pytest.mark.integration
def test_missing_template_falls_back_with_note(data, tmp_path: Path) -> None:
    config = ReportConfig(template=str(tmp_path / "missing.j2"), export=["md"])

    result = render_report(data, config, output_dir=tmp_path)

    assert result.files["md"] == str(tmp_path / "report.md")
    assert any("использован встроенный" in item.text for item in result.conclusions)
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Отчёт по прогону")


@pytest.mark.integration
def test_broken_template_is_data_error(data, tmp_path: Path) -> None:
    template = tmp_path / "broken.j2"
    template.write_text("{% for item in %}", encoding="utf-8")

    with pytest.raises(ReportTemplateError, match="не отрендерился"):
        render_report(data, ReportConfig(template=str(template)), output_dir=tmp_path)


@pytest.mark.integration
def test_render_report_files_from_database(report_database: Path, tmp_path: Path) -> None:
    result = render_report_files(
        report_database,
        REPORT_RUN_ID,
        ReportConfig(export=["md"]),
        output_dir=tmp_path,
    )

    assert result.data.run_id == REPORT_RUN_ID
    assert result.data.generated_at is not None
    assert "md" in result.files
    assert Path(result.files["md"]).is_file()
    assert "выводов" in result.summary()


@pytest.mark.integration
def test_pdf_export(data, tmp_path: Path) -> None:
    try:
        result = render_report(
            data,
            ReportConfig(export=["pdf"]),
            output_dir=tmp_path,
        )
    except PdfUnavailableError as exc:  # pragma: no cover - зависит от Qt
        pytest.skip(f"Qt недоступен для PDF: {exc}")

    pdf = Path(result.files["pdf"])
    assert pdf.is_file()
    assert pdf.read_bytes().startswith(b"%PDF")
    assert pdf.stat().st_size > 1000
