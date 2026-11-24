"""Структуры отчёта: конфигурация, собранные данные, пороги и результат (T11-01).

Отчёт отделён от источника данных: ``ReportConfig`` описывает, что показать,
``ReportData`` — то, что удалось собрать из журнала, а ``ReportResult`` —
готовые тексты и файлы. Поэтому один и тот же рендер работает и по прогону
из ``run.sqlite``, и по данным, собранным тестом или вкладкой.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Final, Literal

__all__ = [
    "DEFAULT_SECTIONS",
    "REPORT_FORMAT",
    "Conclusion",
    "ConclusionLevel",
    "MetricSeries",
    "ReportConfig",
    "ReportData",
    "ReportResult",
    "ScaleRow",
    "StageFact",
]

#: Версия формата отчёта: читатель проверяет её до разбора.
REPORT_FORMAT: Final = "simorph-report/1"

#: Имя секции отчёта.
SectionName = Literal["metadata", "config", "metrics", "plots", "conclusions", "scale"]

#: Формат выгрузки отчёта.
OutputFormat = Literal["md", "html", "pdf"]

#: Секции по умолчанию: те же, что в TRD 5.10, плюс секция масштаба.
DEFAULT_SECTIONS: Final[tuple[str, ...]] = (
    "metadata",
    "config",
    "metrics",
    "plots",
    "conclusions",
    "scale",
)

#: Уровень вывода: порог пройден, на границе, нарушен.
ConclusionLevel = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True, slots=True)
class MetricSeries:
    """Ряд метрики одного прогона: значения по времени сцены."""

    name: str
    unit: str | None
    t_ns: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    tags: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.t_ns) != len(self.values):
            raise ValueError(f"ряд {self.name!r}: метки времени и значения разной длины")

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def total(self) -> float:
        return sum(self.values)

    def statistics(self) -> dict[str, float]:
        """Сводка ряда: среднее, минимум, максимум, p50 и p95."""
        if not self.values:
            return {"count": 0.0}
        ordered = sorted(self.values)
        return {
            "count": float(len(ordered)),
            "mean": sum(ordered) / len(ordered),
            "min": ordered[0],
            "max": ordered[-1],
            "p50": _percentile(ordered, 50.0),
            "p95": _percentile(ordered, 95.0),
            "sum": sum(ordered),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unit": self.unit,
            "count": self.count,
            "t_ns": list(self.t_ns),
            "values": [round(value, 6) for value in self.values],
            "tags": dict(self.tags),
        }


def _percentile(ordered: list[float], percent: float) -> float:
    """Перцентиль по ближайшему рангу: без интерполяции и зависимостей.

    Ранг — ``ceil(p·n)``: для четырёх значений медиана попадает во второй
    элемент, а p95 — в последний. Так сводка совпадает с ручным пересчётом
    и не зависит от округления половины.
    """
    if not ordered:
        return 0.0
    rank = math.ceil(percent / 100.0 * len(ordered))
    index = max(0, min(len(ordered) - 1, rank - 1))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class Conclusion:
    """Вывод по порогу: что проверяли, что получили и чем это кончилось."""

    metric: str
    level: ConclusionLevel
    text: str
    value: float | None = None
    threshold: float | None = None
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "level": self.level,
            "text": self.text,
            "value": None if self.value is None else round(self.value, 6),
            "threshold": self.threshold,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class StageFact:
    """План и факт одной ступени: сколько было заложено и сколько вышло."""

    stage: str
    plan_ms: float
    actual_p50_ms: float
    actual_p95_ms: float
    actual_max_ms: float
    samples: int

    @property
    def overrun_ms(self) -> float:
        return max(0.0, self.actual_p95_ms - self.plan_ms)

    @property
    def ratio(self) -> float:
        return self.actual_p95_ms / self.plan_ms if self.plan_ms > 0.0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "plan_ms": round(self.plan_ms, 3),
            "actual_p50_ms": round(self.actual_p50_ms, 3),
            "actual_p95_ms": round(self.actual_p95_ms, 3),
            "actual_max_ms": round(self.actual_max_ms, 3),
            "overrun_ms": round(self.overrun_ms, 3),
            "ratio": round(self.ratio, 3),
            "samples": self.samples,
        }


@dataclass(frozen=True, slots=True)
class ScaleRow:
    """Строка секции масштаба: параметр мягкого масштаба времени."""

    name: str
    value: float
    unit: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 6),
            "unit": self.unit,
            "note": self.note,
        }


@dataclass(slots=True)
class ReportConfig:
    """Что показать в отчёте: секции, метрики, пороги и форматы."""

    report_id: str = "REP-01"
    title: str = "Отчёт по прогону"
    sections: list[str] = field(default_factory=lambda: list(DEFAULT_SECTIONS))
    metrics: list[str] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    export: list[str] = field(default_factory=lambda: ["md", "html"])
    template: str | None = None
    pipeline_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.sections:
            raise ValueError("нужна хотя бы одна секция отчёта")
        unknown = [item for item in self.sections if item not in DEFAULT_SECTIONS]
        if unknown:
            raise ValueError(f"неизвестные секции отчёта: {', '.join(unknown)}")
        if not self.export:
            raise ValueError("нужен хотя бы один формат экспорта")
        unknown_formats = [item for item in self.export if item not in ("md", "html", "pdf")]
        if unknown_formats:
            raise ValueError(f"неизвестные форматы отчёта: {', '.join(unknown_formats)}")
        if not self.title.strip():
            raise ValueError("заголовок отчёта не может быть пустым")

    @property
    def has(self) -> set[str]:
        return set(self.sections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "title": self.title,
            "sections": list(self.sections),
            "metrics": list(self.metrics),
            "thresholds": dict(self.thresholds),
            "export": list(self.export),
            "template": self.template,
            "pipeline_ids": list(self.pipeline_ids),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReportConfig:
        return cls(
            report_id=str(payload.get("report_id", "REP-01")),
            title=str(payload.get("title", "Отчёт по прогону")),
            sections=[str(item) for item in payload.get("sections", DEFAULT_SECTIONS)],
            metrics=[str(item) for item in payload.get("metrics", [])],
            thresholds={
                str(key): float(value) for key, value in payload.get("thresholds", {}).items()
            },
            export=[str(item) for item in payload.get("export", ["md", "html"])],
            template=payload.get("template"),
            pipeline_ids=[str(item) for item in payload.get("pipeline_ids", [])],
        )


@dataclass(slots=True)
class ReportData:
    """Данные отчёта: прогон, его метрики, артефакты и производные срезы."""

    run: dict[str, Any]
    series: dict[str, MetricSeries] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    events: int = 0
    stages: list[StageFact] = field(default_factory=list)
    scale: list[ScaleRow] = field(default_factory=list)
    database: str | None = None
    app_version: str | None = None
    project_hash: str | None = None
    generated_at: str | None = None

    @property
    def run_id(self) -> str:
        return str(self.run.get("run_id", "—"))

    @property
    def kind(self) -> str:
        return str(self.run.get("kind", "—"))

    def values(self, prefix: str) -> dict[str, float]:
        """Последние значения метрик по префиксу: удобно для сводок."""
        result: dict[str, float] = {}
        for name, series in self.series.items():
            if name.startswith(prefix) and series.values:
                result[name] = series.values[-1]
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": REPORT_FORMAT,
            "run": dict(self.run),
            "database": self.database,
            "app_version": self.app_version,
            "project_hash": self.project_hash,
            "generated_at": self.generated_at,
            "events": self.events,
            "series": {name: item.to_dict() for name, item in sorted(self.series.items())},
            "artifacts": list(self.artifacts),
            "stages": [item.to_dict() for item in self.stages],
            "scale": [item.to_dict() for item in self.scale],
        }


@dataclass(slots=True)
class ReportResult:
    """Готовый отчёт: тексты по форматам, графики и выводы."""

    config: ReportConfig
    data: ReportData
    markdown: str = ""
    html: str = ""
    conclusions: list[Conclusion] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    plots: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Нет ли выводов уровня ``fail``: отчёт собран, пороги не нарушены."""
        return not any(item.level == "fail" for item in self.conclusions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.ok else "warn",
            "report_id": self.config.report_id,
            "run_id": self.data.run_id,
            "kind": self.data.kind,
            "sections": list(self.config.sections),
            "conclusions": [item.to_dict() for item in self.conclusions],
            "files": dict(self.files),
            "plots": list(self.plots),
            "seconds": round(self.seconds, 3),
        }

    def summary(self) -> str:
        failed = sum(1 for item in self.conclusions if item.level == "fail")
        warned = sum(1 for item in self.conclusions if item.level == "warn")
        files = ", ".join(f"{key}: {value}" for key, value in self.files.items()) or "—"
        return (
            f"отчёт {self.config.report_id} по прогону {self.data.run_id}"
            f" ({self.data.kind}): выводов {len(self.conclusions)}"
            f" (нарушено {failed}, на границе {warned}) · файлы {files}"
        )
