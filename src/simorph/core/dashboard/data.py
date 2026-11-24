"""Данные дашборда: прогоны и их метрики из журнала (T11-08, TRD 5.12).

Дашборд читает только ``run.sqlite`` — те же ряды, что и статический отчёт.
Симуляция не запускается, прогон не меняется: панель — форма отчёта, а не
пульт управления. Поэтому цифры на дашборде и в Markdown совпадают.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simorph.core.errors import ProjectError
from simorph.core.report import ReportData, report_data_from_run
from simorph.core.report.collect import open_report_store

__all__ = [
    "DashboardData",
    "DashboardRun",
    "load_dashboard_data",
]

#: Предел прогонов по умолчанию.
DEFAULT_LIMIT: int = 20


@dataclass(slots=True)
class DashboardRun:
    """Один прогон на дашборде: подпись, сводка и данные отчёта."""

    run_id: str
    label: str
    data: ReportData

    @property
    def kind(self) -> str:
        return self.data.kind

    @property
    def started_at(self) -> str:
        return str(self.data.run.get("started_at", "—"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "kind": self.kind,
            "started_at": self.started_at,
            "metrics": len(self.data.series),
            "stages": len(self.data.stages),
        }


@dataclass(slots=True)
class DashboardData:
    """Набор прогонов для дашборда и путь к журналу."""

    database: str
    runs: list[DashboardRun]

    @property
    def run_ids(self) -> list[str]:
        return [item.run_id for item in self.runs]

    def by_id(self, run_id: str) -> DashboardRun | None:
        for item in self.runs:
            if item.run_id == run_id:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "database": self.database,
            "runs": [item.to_dict() for item in self.runs],
        }


def _label(run: dict[str, Any]) -> str:
    object_id = run.get("pipeline_id") or run.get("mission_id") or run.get("exercise_id")
    parts = [str(run["run_id"])[:8], str(run.get("kind", "—")), str(run.get("started_at", "—"))]
    if object_id:
        parts.append(str(object_id))
    return " · ".join(parts)


def load_dashboard_data(
    path: Path | str,
    *,
    run_ids: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> DashboardData:
    """Читает выбранные прогоны (или последние) из журнала."""
    with open_report_store(path) as store:
        if run_ids:
            selected: list[dict[str, Any]] = []
            for run_id in run_ids:
                found = store.get_run(run_id)
                if found is None:
                    raise ProjectError(f"прогон {run_id!r} не найден")
                selected.append(found)
        else:
            selected = store.list_runs(limit=max(1, int(limit)))
        if not selected:
            raise ProjectError("журнал пуст: прогонов нет")
        runs = [
            DashboardRun(
                run_id=str(run["run_id"]),
                label=_label(run),
                data=report_data_from_run(store, str(run["run_id"])),
            )
            for run in selected
        ]
        return DashboardData(database=str(store.path), runs=runs)
