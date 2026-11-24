"""Рендер отчёта: графики, Markdown и автономный HTML (T11-01, T11-03).

Один и тот же набор данных рендерится в два документа. Markdown ссылается
на PNG-файлы рядом с собой, HTML встраивает те же PNG как data-URI — поэтому
HTML открывается без сети, без каталога картинок и без внешних CDN.

Шаблоны лежат в пакете (`templates/*.j2`) и перекрываются пользовательским
шаблоном из конфигурации отчёта: если файл существует, берётся он. Так
FR-REP-08 (пользовательские шаблоны) не требует правки кода.
"""

from __future__ import annotations

import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from simorph.core.report.collect import collect_report_data
from simorph.core.report.conclusions import build_conclusions
from simorph.core.report.plots import (
    Chart,
    bar_chart,
    histogram_chart,
    line_chart,
    stage_chart,
)
from simorph.core.report.schema import (
    Conclusion,
    MetricSeries,
    ReportConfig,
    ReportData,
    ReportResult,
)

__all__ = [
    "TEMPLATE_DIR",
    "build_plots",
    "render_report",
    "render_report_files",
]

#: Каталог встроенных шаблонов.
TEMPLATE_DIR: Final = Path(__file__).parent / "templates"

#: Имена встроенных шаблонов.
MARKDOWN_TEMPLATE: Final = "run_report.md.j2"
HTML_TEMPLATE: Final = "run_report.html.j2"

#: Предел числа рядов ступеней на одном графике.
MAX_STAGE_SERIES: Final = 4


class ReportTemplateError(ValueError):
    """Шаблон отчёта не отрендерился: это ошибка данных, а не сбой."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _metric_rows(series: dict[str, MetricSeries]) -> list[dict[str, Any]]:
    """Строки таблицы метрик: сначала масштаб, затем по алфавиту."""

    def order(name: str) -> tuple[int, str]:
        if name == "k" or name.startswith("k."):
            return (0, name)
        if name.startswith("stage."):
            return (1, name)
        return (2, name)

    rows: list[dict[str, Any]] = []
    for name in sorted(series, key=order):
        item = series[name]
        stats = item.statistics()
        rows.append(
            {
                "name": name,
                "unit": item.unit,
                "count": int(stats.get("count", 0.0)),
                "mean": _round(stats.get("mean", 0.0)),
                "p50": _round(stats.get("p50", 0.0)),
                "p95": _round(stats.get("p95", 0.0)),
                "max": _round(stats.get("max", 0.0)),
            }
        )
    return rows


def _round(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000.0:
        return f"{value:.1f}"
    if magnitude >= 1.0:
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return f"{value:.4g}"


def _pick_series(data: ReportData, prefix: str, limit: int) -> list[MetricSeries]:
    items = [
        item
        for name, item in sorted(data.series.items())
        if name.startswith(prefix) and item.count > 0
    ]
    return items[:limit]


def build_plots(data: ReportData) -> list[Chart]:
    """Подбирает графики по данным прогона: только то, что есть в журнале."""
    charts: list[Chart] = []

    scale_series = [item for name, item in data.series.items() if name == "k" and item.count]
    if scale_series:
        charts.append(line_chart(scale_series, title="Коэффициент масштаба k", name="k"))
        if scale_series[0].count >= 3:
            charts.append(
                histogram_chart(
                    scale_series[0].values,
                    title="Распределение коэффициента k",
                    name="k_histogram",
                )
            )

    wall = data.series.get("interval.wall_time_ms")
    if wall is not None and wall.count:
        charts.append(line_chart([wall], title="Длительность интервала", name="interval"))

    stage_series = _pick_series(data, "stage.", MAX_STAGE_SERIES * 2)
    actual = [item for item in stage_series if item.name.endswith(".actual_ms")]
    if actual:
        charts.append(line_chart(actual, title="Факт по ступеням", name="stages_actual"))

    if data.stages:
        charts.append(stage_chart(data.stages, name="stages_plan_fact"))
        overruns = [
            (item.stage, round(item.overrun_ms, 3)) for item in data.stages if item.overrun_ms > 0.0
        ]
        if overruns:
            charts.append(bar_chart(overruns, title="Перерасход по ступеням", name="stage_overrun"))

    queue = data.series.get("queue_depth")
    if queue is not None and queue.count:
        charts.append(line_chart([queue], title="Глубина очереди", name="queue"))

    return charts


def _save_plots(charts: list[Chart], directory: Path) -> None:
    """Пишет PNG в каталог отчёта: Markdown ссылается на них по имени."""
    directory.mkdir(parents=True, exist_ok=True)
    for chart in charts:
        (directory / f"{chart.name}.png").write_bytes(chart.png)


def _plot_context(charts: list[Chart], *, directory: Path | None = None) -> list[dict[str, Any]]:
    """Графики для разметки: относительный путь в Markdown и data-URI в HTML.

    ``directory`` переключает картинки на файловые ссылки: так их читает
    ``QTextDocument`` при печати PDF, потому что base64-ресурсы он не грузит.
    """
    items: list[dict[str, Any]] = []
    for chart in charts:
        if directory is None:
            source = chart.data_uri()
        else:
            source = (directory / f"{chart.name}.png").resolve().as_uri()
        items.append(
            {
                "name": chart.name,
                "title": chart.title,
                "path": f"plots/{chart.name}.png",
                "data_uri": source,
                "width": chart.width,
                "height": chart.height,
            }
        )
    return items


def _render(
    template_name: str,
    context: dict[str, Any],
    *,
    override: str | None = None,
) -> str:
    """Рендерит шаблон: пользовательский, если файл есть, иначе встроенный.

    Отсутствие файла — не сбой: проект по умолчанию ссылается на шаблон,
    которого может не быть на диске. Подмена видна в выводах отчёта,
    а битый шаблон остаётся ошибкой данных.
    """
    environment = _environment()
    try:
        if override is not None and Path(override).is_file():
            template = environment.from_string(Path(override).read_text(encoding="utf-8"))
        else:
            template = environment.get_template(template_name)
        return template.render(**context)
    except TemplateError as exc:
        raise ReportTemplateError(f"шаблон отчёта не отрендерился: {exc}") from exc


def render_report(
    data: ReportData,
    config: ReportConfig,
    *,
    output_dir: Path | str | None = None,
) -> ReportResult:
    """Собирает отчёт: графики, выводы, Markdown и HTML.

    ``output_dir`` задаёт, куда писать PNG для Markdown. Если он не задан,
    графики живут во временном каталоге: так предпросмотр не мусорит
    в рабочем каталоге.
    """
    started = time.perf_counter()
    if data.generated_at is None:
        data.generated_at = _now()
    target = (
        Path(output_dir)
        if output_dir is not None
        else Path(tempfile.mkdtemp(prefix="simorph-report-"))
    )
    target.mkdir(parents=True, exist_ok=True)
    charts = build_plots(data) if "plots" in config.sections else []
    if charts:
        _save_plots(charts, target / "plots")
    conclusions = build_conclusions(data, config)
    if config.template is not None and not Path(config.template).is_file():
        conclusions.insert(
            0,
            Conclusion(
                metric="template",
                level="info",
                text=f"шаблон {config.template} не найден: использован встроенный",
            ),
        )
    context: dict[str, Any] = {
        "config": config,
        "data": data,
        "metrics": _metric_rows(data.series) if "metrics" in config.sections else [],
        "plots": _plot_context(charts),
        "stages": [item.to_dict() for item in data.stages],
        "scale": [item.to_dict() for item in data.scale],
        "conclusions": [item.to_dict() for item in conclusions],
        "artifacts": list(data.artifacts),
    }

    markdown = _render(MARKDOWN_TEMPLATE, context, override=config.template)
    html = _render(HTML_TEMPLATE, context)

    files: dict[str, str] = {}
    if "md" in config.export:
        path = target / "report.md"
        path.write_text(markdown, encoding="utf-8", newline="\n")
        files["md"] = str(path)
    if "html" in config.export:
        path = target / "report.html"
        path.write_text(html, encoding="utf-8", newline="\n")
        files["html"] = str(path)
    if "pdf" in config.export:
        from simorph.core.report.pdf import render_pdf

        pdf_context = dict(context)
        pdf_context["plots"] = _plot_context(charts, directory=target / "plots")
        pdf_html = _render(HTML_TEMPLATE, pdf_context)
        files["pdf"] = str(render_pdf(pdf_html, target / "report.pdf"))

    return ReportResult(
        config=config,
        data=data,
        markdown=markdown,
        html=html,
        conclusions=conclusions,
        files=files,
        plots=[chart.name for chart in charts],
        seconds=time.perf_counter() - started,
    )


def render_report_files(
    path: Path | str,
    run_id: str | None,
    config: ReportConfig,
    *,
    output_dir: Path | str,
    generated_at: str | None = None,
) -> ReportResult:
    """Читает журнал и рендерит отчёт в указанный каталог."""
    data = collect_report_data(path, run_id, generated_at=generated_at)
    return render_report(data, config, output_dir=output_dir)
