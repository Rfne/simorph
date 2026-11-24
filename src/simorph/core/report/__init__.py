"""Отчёт по прогону: сбор данных, графики, выводы и рендер (этап 11).

Пакет не зависит от UI и сети: его можно вызвать из CLI, из вкладки и из
тестов. Точка входа для «собери отчёт по журналу» — :func:`render_report_files`.
"""

from __future__ import annotations

from simorph.core.report.collect import (
    collect_report_data,
    open_report_store,
    report_data_from_run,
)
from simorph.core.report.conclusions import (
    DEFAULT_THRESHOLDS,
    build_conclusions,
    derive_conclusions,
    evaluate_thresholds,
)
from simorph.core.report.pdf import PdfUnavailableError, render_pdf
from simorph.core.report.plots import Chart, bar_chart, histogram_chart, line_chart, stage_chart
from simorph.core.report.render import (
    TEMPLATE_DIR,
    ReportTemplateError,
    build_plots,
    render_report,
    render_report_files,
)
from simorph.core.report.schema import (
    DEFAULT_SECTIONS,
    REPORT_FORMAT,
    Conclusion,
    MetricSeries,
    ReportConfig,
    ReportData,
    ReportResult,
    ScaleRow,
    StageFact,
)

__all__ = [
    "DEFAULT_SECTIONS",
    "DEFAULT_THRESHOLDS",
    "REPORT_FORMAT",
    "TEMPLATE_DIR",
    "Chart",
    "Conclusion",
    "MetricSeries",
    "PdfUnavailableError",
    "ReportConfig",
    "ReportData",
    "ReportResult",
    "ReportTemplateError",
    "ScaleRow",
    "StageFact",
    "bar_chart",
    "build_conclusions",
    "build_plots",
    "collect_report_data",
    "derive_conclusions",
    "evaluate_thresholds",
    "histogram_chart",
    "line_chart",
    "open_report_store",
    "render_pdf",
    "render_report",
    "render_report_files",
    "report_data_from_run",
    "stage_chart",
]
