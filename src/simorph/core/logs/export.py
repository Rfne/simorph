"""Экспорт рядов журнала в CSV и Parquet (T9-04, G9-04).

Экспорт не пересчитывает метрики: он берёт те же ряды, что показывает
таблица, и пишет их в файл. Колонки фиксированы и типизированы:
``t_ns`` — целое (наносекунды), ``value`` — число с плавающей точкой,
``run_id``/``name``/``unit`` — строки. Поэтому pandas читает Parquet без
приведения типов и без потери меток времени.

CSV пишется с заголовком и теми же колонками: его достаточно для
табличного анализа, а JSON-поля (``tags_json``, ``payload_json``) остаются
строками, чтобы не терять вложенность.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from simorph.core.logs.store import RunStore

__all__ = [
    "EXPORT_COLUMNS",
    "ExportFormat",
    "ExportResult",
    "export_rows",
    "export_table",
    "rows_for_table",
]

#: Колонки экспорта по таблицам.
EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "metrics": ("id", "run_id", "name", "value", "unit", "t_ns", "tags_json"),
    "events": ("id", "run_id", "t_ns", "source", "type", "payload_json"),
    "runs": (
        "run_id",
        "kind",
        "status",
        "started_at",
        "finished_at",
        "project_hash",
        "exercise_id",
        "mission_id",
        "pipeline_id",
        "seed",
        "reason",
        "app_version",
    ),
}

#: Типы колонок Parquet: без них pandas получит строки вместо чисел.
_PARQUET_TYPES: dict[str, dict[str, str]] = {
    "metrics": {"id": "int64", "value": "float64", "t_ns": "int64"},
    "events": {"id": "int64", "t_ns": "int64"},
    "runs": {"seed": "int64"},
}

#: Допустимые форматы.
ExportFormat = Literal["csv", "parquet"]


@dataclass(slots=True)
class ExportResult:
    """Итог выгрузки."""

    path: str
    table: str
    rows: int
    format: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "table": self.table,
            "rows": self.rows,
            "format": self.format,
            "bytes": self.bytes,
        }


def rows_for_table(
    store: RunStore,
    table: str,
    *,
    run_id: str | None = None,
    name: str | None = None,
    prefix: str | None = None,
    since_ns: int | None = None,
    until_ns: int | None = None,
    limit: int = 1_000_000,
) -> list[dict[str, Any]]:
    """Ряды для экспорта: те же фильтры, что у табличного просмотра."""
    if table == "metrics":
        return store.metrics(
            run_id,
            name=name,
            prefix=prefix,
            since_ns=since_ns,
            until_ns=until_ns,
            limit=limit,
        )
    if table == "events":
        return store.events(
            run_id,
            event_type=name,
            since_ns=since_ns,
            until_ns=until_ns,
            limit=limit,
        )
    if table == "runs":
        return store.list_runs(limit=limit)
    raise ValueError(f"неизвестная таблица экспорта: {table!r}")


def _flatten(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Приводит строки к плоским колонкам экспорта."""
    flattened: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for column in EXPORT_COLUMNS[table]:
            if column == "tags_json":
                item[column] = json.dumps(row.get("tags", {}), ensure_ascii=False, sort_keys=True)
            elif column == "payload_json":
                item[column] = json.dumps(
                    row.get("payload", {}), ensure_ascii=False, sort_keys=True, default=str
                )
            elif column == "config_json":
                item[column] = json.dumps(
                    row.get("config", {}), ensure_ascii=False, sort_keys=True, default=str
                )
            elif column in row:
                item[column] = row[column]
            else:
                item[column] = None
        flattened.append(item)
    return flattened


def _write_csv(columns: tuple[str, ...], rows: list[dict[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
    temporary.replace(path)
    return len(rows)


def _write_parquet(
    table: str, columns: tuple[str, ...], rows: list[dict[str, Any]], path: Path
) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    types = _PARQUET_TYPES.get(table, {})
    arrays = []
    for column in columns:
        values = [row.get(column) for row in rows]
        kind = types.get(column)
        if kind == "int64":
            arrays.append(
                pa.array([None if item is None else int(item) for item in values], pa.int64())
            )
        elif kind == "float64":
            arrays.append(
                pa.array([None if item is None else float(item) for item in values], pa.float64())
            )
        else:
            arrays.append(pa.array(values, pa.string()))
    table_object = pa.Table.from_arrays(arrays, names=list(columns))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    pq.write_table(table_object, temporary, compression="snappy")
    temporary.replace(path)
    return len(rows)


def export_rows(
    table: str,
    rows: list[dict[str, Any]],
    path: Path | str,
    *,
    fmt: ExportFormat | None = None,
) -> ExportResult:
    """Пишет готовые ряды в CSV или Parquet.

    Формат определяется расширением, если не задан явно. Запись идёт через
    временный файл: недописанная выгрузка не выглядит готовой.
    """
    if table not in EXPORT_COLUMNS:
        raise ValueError(f"неизвестная таблица экспорта: {table!r}")
    target = Path(path)
    chosen: str = fmt or ("parquet" if target.suffix.lower() == ".parquet" else "csv")
    columns = EXPORT_COLUMNS[table]
    flattened = _flatten(table, rows)
    if chosen == "parquet":
        count = _write_parquet(table, columns, flattened, target)
    elif chosen == "csv":
        count = _write_csv(columns, flattened, target)
    else:  # pragma: no cover - защита от опечатки в формате
        raise ValueError(f"неизвестный формат экспорта: {chosen!r}")
    return ExportResult(
        path=str(target),
        table=table,
        rows=count,
        format=chosen,
        bytes=target.stat().st_size,
    )


def export_table(
    store: RunStore,
    table: str,
    path: Path | str,
    *,
    fmt: ExportFormat | None = None,
    **filters: Any,
) -> ExportResult:
    """Читает ряды из журнала и выгружает их в файл."""
    rows = rows_for_table(store, table, **filters)
    return export_rows(table, rows, path, fmt=fmt)
