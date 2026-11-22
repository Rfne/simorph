"""Импорт отчётов прогонов в журнал (T9-02, G9-01).

Единственное место, где отчёт превращается в строки журнала: CLI и
вкладки вызывают ``record_simulation``/``record_pipeline`` и не знают,
как устроены таблицы. Поэтому запись симуляции и запись конвейера
одинаково видны в списке прогонов, фильтрах и экспорте.

Путь журнала по умолчанию — ``run.sqlite`` рядом с проектом: журнал
принадлежит рабочему месту, а не документу, и не попадает в ``project.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from simorph.core.logs.records import RunBundle, pipeline_bundle, simulation_bundle
from simorph.core.logs.store import RunStore, open_store

__all__ = [
    "DEFAULT_LOG_NAME",
    "default_log_path",
    "record_pipeline",
    "record_simulation",
    "record_to_file",
]

#: Имя журнала рядом с проектом.
DEFAULT_LOG_NAME = "run.sqlite"


def default_log_path(project_path: Path | str) -> Path:
    """Журнал рядом с проектом: ``<каталог проекта>/run.sqlite``."""
    target = Path(project_path)
    return target.parent / DEFAULT_LOG_NAME


def record_simulation(
    store: RunStore,
    report: Any,
    *,
    run_id: str | None = None,
    project_hash: str | None = None,
    exercise_id: str | None = None,
    app_version: str | None = None,
) -> dict[str, Any]:
    """Пишет отчёт симуляции и возвращает сводку записи."""
    bundle = simulation_bundle(
        report,
        run_id=run_id,
        project_hash=project_hash,
        exercise_id=exercise_id,
        app_version=app_version,
    )
    return _record(store, bundle)


def record_pipeline(
    store: RunStore,
    report: Any,
    *,
    run_id: str | None = None,
    project_hash: str | None = None,
    app_version: str | None = None,
) -> dict[str, Any]:
    """Пишет отчёт конвейера и возвращает сводку записи."""
    bundle = pipeline_bundle(
        report,
        run_id=run_id,
        project_hash=project_hash,
        app_version=app_version,
    )
    return _record(store, bundle)


def record_to_file(
    path: Path | str,
    report: Any,
    *,
    kind: str = "simulation",
    project_hash: str | None = None,
    exercise_id: str | None = None,
    app_version: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Открывает журнал, пишет прогон и закрывает его."""
    target = Path(path)
    with open_store(target) as store:
        if kind == "pipeline":
            summary = record_pipeline(
                store,
                report,
                run_id=run_id,
                project_hash=project_hash,
                app_version=app_version,
            )
        else:
            summary = record_simulation(
                store,
                report,
                run_id=run_id,
                project_hash=project_hash,
                exercise_id=exercise_id,
                app_version=app_version,
            )
    summary["path"] = str(target)
    return summary


def _record(store: RunStore, bundle: RunBundle) -> dict[str, Any]:
    written = store.record(bundle)
    return {
        "run_id": bundle.run.run_id,
        "kind": bundle.run.kind,
        "status": bundle.run.status,
        **written,
        "counts": store.counts(bundle.run.run_id),
    }
