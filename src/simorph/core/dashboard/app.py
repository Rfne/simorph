"""Панель дашборда на Panel: LaTeX, ползунки и галочки (T11-08, T11-09).

Дашборд — форма отчёта, а не отдельный анализатор: он берёт те же ряды и
те же выводы, что и статический Markdown, и пересобирает блоки при движении
виджетов. Поэтому графики — те же PNG из ``core.report.plots``, а формулы
LaTeX считаются из ``k.p50`` и ``k.p95``.

Ресурсы встраиваются (``pn.config.inline``): панель открывается на
``127.0.0.1`` без обращений в сеть. Если браузер не смог загрузить MathJax
из локального бандла, формулы остаются текстом, но данные и виджеты
работают.
"""

from __future__ import annotations

import time
from typing import Any

from simorph.core.dashboard.config import DashboardConfig
from simorph.core.dashboard.data import DashboardData, DashboardRun
from simorph.core.report import ReportConfig, build_conclusions, build_plots

__all__ = [
    "build_dashboard",
    "dashboard_summary",
    "latex_kappa",
]

#: Предел строк таблицы метрик на дашборде.
METRIC_ROWS: int = 12

#: Предел графиков на дашборде.
PLOT_LIMIT: int = 6


def latex_kappa(run: DashboardRun) -> str:
    """Формула масштаба: k как отношение факта к плану."""
    p50 = _last(run, "k.p50")
    p95 = _last(run, "k.p95")
    parts = [r"\kappa = \frac{t_{\mathrm{actual}}}{t_{\mathrm{plan}}}"]
    if p50 is not None:
        parts.append(rf"\kappa_{{p50}} = {p50:.2f}")
    if p95 is not None:
        parts.append(rf"\kappa_{{p95}} = {p95:.2f}")
    return "$" + r"\quad ".join(parts) + "$"


def _last(run: DashboardRun, name: str) -> float | None:
    series = run.data.series.get(name)
    if series is None or not series.values:
        return None
    return series.values[-1]


def _worst_latency(run: DashboardRun) -> float | None:
    """Худшая p95 задержки: инференс или самая медленная ступень."""
    values: list[float] = []
    latency = _last(run, "eval.latency_p95_ms")
    if latency is not None:
        values.append(latency)
    values.extend(item.actual_p95_ms for item in run.data.stages)
    return max(values) if values else None


def _metrics_table(run: DashboardRun) -> str:
    rows = ["| Метрика | Значений | p50 | p95 | Максимум |", "|---|---:|---:|---:|---:|"]
    names = sorted(
        run.data.series,
        key=lambda name: (0 if name.startswith("k") else 1, name),
    )
    for name in names[:METRIC_ROWS]:
        stats = run.data.series[name].statistics()
        if not stats.get("count"):
            continue
        rows.append(
            f"| `{name}` | {int(stats['count'])} | {stats['p50']:.3g}"
            f" | {stats['p95']:.3g} | {stats['max']:.3g} |"
        )
    if len(rows) == 2:
        rows.append("| — | — | — | — | — |")
    return "\n".join(rows)


def _stage_table(run: DashboardRun, *, degraded_only: bool) -> str:
    stages = run.data.stages
    if degraded_only:
        stages = [item for item in stages if item.overrun_ms > 0.0]
    if not stages:
        return "Ступеней с перерасходом нет." if degraded_only else "Ступени не записаны."
    rows = ["| Ступень | План, мс | p95, мс | Перерасход, мс |", "|---|---:|---:|---:|"]
    for item in stages:
        rows.append(
            f"| {item.stage} | {item.plan_ms:.2f} | {item.actual_p95_ms:.2f}"
            f" | {item.overrun_ms:.2f} |"
        )
    return "\n".join(rows)


def dashboard_summary(
    run: DashboardRun,
    config: DashboardConfig,
    *,
    threshold_ms: float,
    degraded_only: bool,
) -> list[Any]:
    """Блоки сводки для одного прогона: их пересобирает Panel при движении виджетов."""
    import panel as pn

    blocks: list[Any] = []
    latency = _worst_latency(run)
    state = "нет данных"
    if latency is not None:
        state = "в бюджете" if latency <= threshold_ms else "вне бюджета"
    latency_text = "—" if latency is None else f"{latency:.1f} мс"
    blocks.append(
        pn.pane.Markdown(
            f"### Прогон `{run.run_id}`\n\n"
            f"{run.kind} · начат {run.started_at} · метрик {len(run.data.series)}"
            f" · худшая p95 задержки {latency_text}"
            f" — **{state}** при пороге {threshold_ms:.0f} мс"
        )
    )
    blocks.append(pn.pane.LaTeX(latex_kappa(run)))

    report_config = ReportConfig(
        thresholds={
            "k.p95": config.thresholds.get("k_p95", 1.5),
            "eval.latency_p95_ms": threshold_ms,
        }
    )
    conclusions = build_conclusions(run.data, report_config)
    if degraded_only:
        conclusions = [item for item in conclusions if item.level in ("warn", "fail")]
    if "conclusions" in config.has:
        if conclusions:
            text = "\n".join(f"- **{item.level.upper()}** — {item.text}" for item in conclusions)
        else:
            text = "Нарушений нет: при текущем пороге выводы пусты."
        blocks.append(pn.pane.Markdown(f"**Выводы**\n\n{text}"))

    if "metrics" in config.has:
        blocks.append(pn.pane.Markdown(f"**Метрики**\n\n{_metrics_table(run)}"))
    if "scale" in config.has:
        blocks.append(
            pn.pane.Markdown(
                f"**План и факт по ступеням**\n\n{_stage_table(run, degraded_only=degraded_only)}"
            )
        )
    if "plots" in config.has:
        charts = build_plots(run.data)[:PLOT_LIMIT]
        for chart in charts:
            blocks.append(
                pn.pane.PNG(
                    chart.png,
                    alt_text=chart.title,
                    width=chart.width,
                    height=chart.height,
                    sizing_mode="fixed",
                )
            )
    return blocks


def build_dashboard(data: DashboardData, config: DashboardConfig) -> Any:
    """Собирает панель: выбор прогона, порог, галочка и живая сводка."""
    import panel as pn

    started = time.perf_counter()
    pn.extension("mathjax")
    pn.config.inline = True

    options = {item.label: item.run_id for item in data.runs}
    first = data.runs[0].run_id
    run_select = pn.widgets.Select(label="Прогон", options=options, value=first, width=420)
    threshold = pn.widgets.FloatSlider(
        label="Порог задержки p95, мс",
        start=50.0,
        end=1000.0,
        step=10.0,
        value=float(config.thresholds.get("inference_p95_ms", 400.0)),
        width=320,
    )
    degraded = pn.widgets.Checkbox(label="Только деградация", value=False)

    @pn.depends(run_select, threshold, degraded)
    def summary(run_id: str, threshold_ms: float, degraded_only: bool) -> Any:
        run = data.by_id(run_id) or data.runs[0]
        blocks = dashboard_summary(
            run,
            config,
            threshold_ms=float(threshold_ms),
            degraded_only=bool(degraded_only),
        )
        return pn.Column(*blocks, sizing_mode="stretch_width")

    header = pn.pane.Markdown(
        f"# {config.title}\n\n"
        f"Источник: `{data.database}` · прогонов {len(data.runs)}"
        f" · построено за {time.perf_counter() - started:.2f} с"
    )
    return pn.Column(
        header,
        pn.Row(run_select, threshold, degraded, sizing_mode="stretch_width"),
        summary,
        sizing_mode="stretch_width",
    )
