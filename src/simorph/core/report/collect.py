"""Сбор данных отчёта из журнала прогонов (T11-01, T11-02).

Отчёт читает только ``run.sqlite``: прогон, его метрики, события и артефакты.
Никаких обращений к симуляции нет — отчёт по прогону можно собрать на другой
машине, если перенести журнал. Поэтому цифры в отчёте и в журнале совпадают
по построению.

Ряды группируются по имени метрики. Сводки (``k.p50``, ``frames.dropped``)
имеют по одному значению и попадают в те же ряды: отчёт не различает
«ряд» и «итог» по имени, а показывает то, что есть.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from simorph.core.logs import RunStore, default_log_path, open_store
from simorph.core.report.schema import (
    MetricSeries,
    ReportData,
    ScaleRow,
    StageFact,
)
from simorph.version import __version__

__all__ = [
    "collect_report_data",
    "open_report_store",
    "report_data_from_run",
]

#: Предел строк метрик, читаемых в отчёт: с запасом на длинный прогон.
METRIC_LIMIT: Final = 1_000_000

#: Метрики секции масштаба: имя в журнале и человеческая подпись.
SCALE_METRICS: Final[tuple[tuple[str, str, str], ...]] = (
    ("k.p50", "Масштаб k, медиана", "доля"),
    ("k.p95", "Масштаб k, p95", "доля"),
    ("k.max", "Масштаб k, максимум", "доля"),
    ("k.share_over_plan", "Доля интервалов с перерасходом", "доля"),
    ("frames.dropped", "Отброшено кадров", "кадров"),
    ("frames.pushed", "Отправлено кадров", "кадров"),
    ("frames.consumed", "Обработано кадров", "кадров"),
    ("queue.max_depth", "Максимум кадров в очереди", "кадров"),
    ("queue.wait_ms", "Ожидание в очереди", "мс"),
    ("pipeline.critical_path_ms", "Критический путь", "мс"),
    ("pipeline.total_work_ms", "Суммарная работа", "мс"),
)

#: Предел числа ступеней в секции план/факт.
MAX_STAGES: Final = 32


def open_report_store(path: Path | str) -> RunStore:
    """Открывает журнал: явный путь к ``.sqlite`` или проект рядом с ним."""
    target = Path(path)
    if target.suffix.lower() in {".sqlite", ".sqlite3", ".db"} or target.name == "run.sqlite":
        database = target
    else:
        database = default_log_path(target)
    if not database.exists():
        raise FileNotFoundError(f"журнал не найден: {database}")
    return open_store(database)


def _resolve_run(store: RunStore, run_id: str | None) -> dict[str, Any]:
    if run_id is not None:
        found = store.get_run(run_id)
        if found is None:
            raise KeyError(f"прогон {run_id!r} не найден")
        return found
    runs = store.list_runs(limit=1)
    if not runs:
        raise KeyError("журнал пуст: прогонов нет")
    return runs[0]


def _series_from_rows(rows: list[dict[str, Any]]) -> dict[str, MetricSeries]:
    """Группирует строки метрик в ряды по имени."""
    grouped: dict[str, MetricSeries] = {}
    for row in rows:
        name = str(row["name"])
        value = row.get("value")
        if value is None:
            continue
        series = grouped.get(name)
        if series is None:
            series = MetricSeries(name=name, unit=row.get("unit"), tags=dict(row.get("tags") or {}))
            grouped[name] = series
        series.t_ns.append(int(row.get("t_ns") or 0))
        series.values.append(float(value))
    return grouped


def _stage_facts(series: dict[str, MetricSeries]) -> list[StageFact]:
    """План и факт по ступеням: план — последнее значение, факт — ряд."""
    facts: list[StageFact] = []
    for name, item in series.items():
        if not name.startswith("stage.") or not name.endswith(".actual_ms"):
            continue
        stage = name[len("stage.") : -len(".actual_ms")]
        plan_series = series.get(f"stage.{stage}.plan_ms")
        if plan_series is None or not plan_series.values:
            continue
        stats = item.statistics()
        facts.append(
            StageFact(
                stage=stage,
                plan_ms=plan_series.values[-1],
                actual_p50_ms=stats.get("p50", 0.0),
                actual_p95_ms=stats.get("p95", 0.0),
                actual_max_ms=stats.get("max", 0.0),
                samples=int(stats.get("count", 0.0)),
            )
        )
    facts.sort(key=lambda item: item.plan_ms, reverse=True)
    return facts[:MAX_STAGES]


def _scale_rows(series: dict[str, MetricSeries]) -> list[ScaleRow]:
    """Секция масштаба: итоговые метрики в человеческом порядке."""
    rows: list[ScaleRow] = []
    for name, title, unit in SCALE_METRICS:
        item = series.get(name)
        if item is None or not item.values:
            continue
        rows.append(ScaleRow(name=title, value=item.values[-1], unit=unit, note=name))
    return rows


def report_data_from_run(
    store: RunStore,
    run_id: str | None = None,
    *,
    generated_at: str | None = None,
) -> ReportData:
    """Собирает данные отчёта по прогону журнала."""
    run = _resolve_run(store, run_id)
    identifier = str(run["run_id"])
    rows = store.metrics(identifier, limit=METRIC_LIMIT)
    series = _series_from_rows(rows)
    counts = store.counts(identifier)
    return ReportData(
        run=run,
        series=series,
        artifacts=store.artifacts(identifier),
        events=int(counts.get("run_events", 0)),
        stages=_stage_facts(series),
        scale=_scale_rows(series),
        database=str(store.path),
        app_version=str(run.get("app_version") or __version__),
        project_hash=run.get("project_hash"),
        generated_at=generated_at,
    )


def collect_report_data(
    path: Path | str,
    run_id: str | None = None,
    *,
    generated_at: str | None = None,
) -> ReportData:
    """Открывает журнал, собирает данные и закрывает его."""
    with open_report_store(path) as store:
        return report_data_from_run(store, run_id, generated_at=generated_at)
