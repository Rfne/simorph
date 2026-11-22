"""Хранилище журнала прогонов: запись батчами и чтение с фильтрами (T9-02…T9-05).

Запись идёт батчами внутри транзакции на батч: 10^5 рядов не превращаются
в 10^5 отдельных коммитов, а незавершённый батч откатывается целиком.
Поэтому после аварийного завершения база остаётся согласованной: либо
батч есть, либо его нет, а завершённые прогоны не теряются.

Чтение возвращает словари, а не модели: просмотр и экспорт работают
с одними и теми же рядами, а JSON-поля разбираются один раз. Фильтры
строятся параметризованно — имя метрики и интервал времени не склеиваются
в SQL-строку.

Прогон, оставшийся в статусе ``running`` после падения процесса,
переводится в ``interrupted`` при открытии: это не «тихое» исправление,
а явное состояние, которое видно в списке.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simorph.core.logs.records import (
    AnnotationRow,
    ArtifactRow,
    EventRow,
    MetricRow,
    RunBundle,
    RunRecord,
    utc_now,
)
from simorph.core.logs.schema import (
    connect,
    create_schema,
    pragma,
    read_schema_version,
    table_names,
)

__all__ = ["RunStore", "open_store"]

#: Размер батча по умолчанию.
DEFAULT_BATCH_SIZE: int = 5000

#: Предел строк выборки по умолчанию.
DEFAULT_LIMIT: int = 1000


def _loads(value: Any) -> Any:
    """Разбирает JSON-поле; испорченное значение возвращается как строка."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return str(value)


def _run_where(
    *,
    kind: str | None = None,
    status: str | None = None,
    pipeline_id: str | None = None,
    mission_id: str | None = None,
    project_hash: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> tuple[str, list[Any]]:
    """Собирает ``WHERE`` для ``runs`` из необязательных фильтров."""
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("kind", kind),
        ("status", status),
        ("pipeline_id", pipeline_id),
        ("mission_id", mission_id),
        ("project_hash", project_hash),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if since is not None:
        clauses.append("started_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("started_at <= ?")
        params.append(until)
    return (f" WHERE {' AND '.join(clauses)}" if clauses else "", params)


class RunStore:
    """Журнал прогонов в SQLite."""

    def __init__(self, path: Path | str, connection: sqlite3.Connection | None = None) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._connection = connection if connection is not None else connect(self.path)
        with self._lock:
            create_schema(self._connection)

    # --- жизненный цикл --------------------------------------------------

    @classmethod
    def open(cls, path: Path | str) -> RunStore:
        """Открывает или создаёт журнал."""
        return cls(path)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def schema_version(self) -> int:
        with self._lock:
            return read_schema_version(self._connection)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> RunStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- запись ----------------------------------------------------------

    def start_run(self, run: RunRecord) -> str:
        """Создаёт запись прогона в статусе ``running``."""
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO runs (run_id, project_hash, exercise_id, mission_id, pipeline_id,"
                " kind, seed, started_at, finished_at, status, reason, config_json,"
                " artifact_dir, app_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                run.to_row(),
            )
        return run.run_id

    def finish_run(
        self,
        run_id: str,
        status: str = "finished",
        *,
        reason: str | None = None,
        finished_at: str | None = None,
    ) -> bool:
        """Закрывает прогон: статус, причина и время завершения."""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE runs SET status = ?, reason = ?, finished_at = ? WHERE run_id = ?",
                (status, reason, finished_at or utc_now(), run_id),
            )
        return cursor.rowcount > 0

    def write_events(
        self,
        run_id: str,
        events: Sequence[EventRow],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> int:
        """Пишет события батчами; возвращает число записанных строк."""
        return self._write_rows(
            "INSERT INTO run_events (run_id, t_ns, source, type, payload_json)"
            " VALUES (?, ?, ?, ?, ?)",
            [item.to_row(run_id) for item in events],
            batch_size,
        )

    def write_metrics(
        self,
        run_id: str,
        metrics: Sequence[MetricRow],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> int:
        """Пишет ряды метрик батчами; возвращает число записанных строк."""
        return self._write_rows(
            "INSERT INTO metrics (run_id, name, value, unit, t_ns, tags_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [item.to_row(run_id) for item in metrics],
            batch_size,
        )

    def add_artifacts(self, run_id: str, artifacts: Iterable[ArtifactRow]) -> int:
        """Добавляет артефакты без дублей по ``(run_id, path, hash)``."""
        added = 0
        with self._lock, self._connection:
            for item in artifacts:
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO artifacts (run_id, kind, path, hash, size_bytes)"
                    " VALUES (?, ?, ?, ?, ?)",
                    item.to_row(run_id),
                )
                added += max(0, cursor.rowcount)
        return added

    def add_annotations(self, run_id: str, annotations: Iterable[AnnotationRow]) -> int:
        """Добавляет разметку кадров."""
        rows = [item.to_row(run_id) for item in annotations]
        if not rows:
            return 0
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT INTO annotations (run_id, frame_id, class_id, bbox_json,"
                " keypoints_json, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def annotate(self, run_id: str, annotation: AnnotationRow) -> int:
        """Одна запись разметки."""
        return self.add_annotations(run_id, [annotation])

    def record(self, bundle: RunBundle, *, batch_size: int = DEFAULT_BATCH_SIZE) -> dict[str, int]:
        """Пишет прогон целиком: запись, события, метрики, артефакты, разметку.

        Порядок важен: ``runs`` первым, потому что на него ссылаются
        внешние ключи остальных таблиц.
        """
        self.start_run(bundle.run)
        written = {
            "events": self.write_events(bundle.run.run_id, bundle.events, batch_size=batch_size),
            "metrics": self.write_metrics(bundle.run.run_id, bundle.metrics, batch_size=batch_size),
            "artifacts": self.add_artifacts(bundle.run.run_id, bundle.artifacts),
            "annotations": self.add_annotations(bundle.run.run_id, bundle.annotations),
        }
        self.finish_run(bundle.run.run_id, bundle.run.status, reason=bundle.run.reason)
        return written

    def _write_rows(
        self,
        statement: str,
        rows: Sequence[tuple[Any, ...]],
        batch_size: int,
    ) -> int:
        if not rows:
            return 0
        size = max(1, int(batch_size))
        written = 0
        with self._lock:
            for start in range(0, len(rows), size):
                chunk = rows[start : start + size]
                with self._connection:
                    self._connection.executemany(statement, chunk)
                written += len(chunk)
        return written

    # --- чтение ----------------------------------------------------------

    def list_runs(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        pipeline_id: str | None = None,
        mission_id: str | None = None,
        project_hash: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
        offset: int = 0,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Список прогонов с фильтрами и сортировкой по времени старта."""
        where, params = _run_where(
            kind=kind,
            status=status,
            pipeline_id=pipeline_id,
            mission_id=mission_id,
            project_hash=project_hash,
            since=since,
            until=until,
        )
        order = "DESC" if newest_first else "ASC"
        params.extend([max(0, int(limit)), max(0, int(offset))])
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM runs{where} ORDER BY started_at {order}, run_id LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._run_dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return None if row is None else self._run_dict(row)

    def count_runs(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        pipeline_id: str | None = None,
        mission_id: str | None = None,
        project_hash: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> int:
        """Число прогонов с теми же фильтрами, что и у ``list_runs``."""
        where, params = _run_where(
            kind=kind,
            status=status,
            pipeline_id=pipeline_id,
            mission_id=mission_id,
            project_hash=project_hash,
            since=since,
            until=until,
        )
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS value FROM runs{where}", params
            ).fetchone()
        return int(row["value"]) if row is not None else 0

    def events(
        self,
        run_id: str | None = None,
        *,
        event_type: str | None = None,
        source: str | None = None,
        since_ns: int | None = None,
        until_ns: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """События с фильтрами по прогону, типу, источнику и интервалу."""
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("run_id", run_id), ("type", event_type), ("source", source)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since_ns is not None:
            clauses.append("t_ns >= ?")
            params.append(int(since_ns))
        if until_ns is not None:
            clauses.append("t_ns <= ?")
            params.append(int(until_ns))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([max(0, int(limit)), max(0, int(offset))])
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM run_events{where} ORDER BY t_ns, id LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "t_ns": row["t_ns"],
                "source": row["source"],
                "type": row["type"],
                "payload": _loads(row["payload_json"]),
            }
            for row in rows
        ]

    def metrics(
        self,
        run_id: str | None = None,
        *,
        name: str | None = None,
        prefix: str | None = None,
        since_ns: int | None = None,
        until_ns: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Ряды метрик с фильтрами по прогону, имени и интервалу."""
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if name is not None:
            clauses.append("name = ?")
            params.append(name)
        if prefix is not None:
            clauses.append("name LIKE ?")
            params.append(f"{prefix}%")
        if since_ns is not None:
            clauses.append("t_ns >= ?")
            params.append(int(since_ns))
        if until_ns is not None:
            clauses.append("t_ns <= ?")
            params.append(int(until_ns))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([max(0, int(limit)), max(0, int(offset))])
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM metrics{where} ORDER BY t_ns, id LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "name": row["name"],
                "value": row["value"],
                "unit": row["unit"],
                "t_ns": row["t_ns"],
                "tags": _loads(row["tags_json"]),
            }
            for row in rows
        ]

    def metric_names(self, run_id: str | None = None) -> list[str]:
        """Различные имена метрик, упорядоченные по алфавиту."""
        params: list[Any] = []
        where = ""
        if run_id is not None:
            where = " WHERE run_id = ?"
            params.append(run_id)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT DISTINCT name FROM metrics{where} ORDER BY name", params
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def artifacts(self, run_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if run_id is not None:
            where = " WHERE run_id = ?"
            params.append(run_id)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM artifacts{where} ORDER BY id", params
            ).fetchall()
        return [dict(row) for row in rows]

    def annotations(self, run_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if run_id is not None:
            where = " WHERE run_id = ?"
            params.append(run_id)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM annotations{where} ORDER BY id", params
            ).fetchall()
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "frame_id": row["frame_id"],
                "class_id": row["class_id"],
                "bbox": _loads(row["bbox_json"]),
                "keypoints": _loads(row["keypoints_json"]),
                "source": row["source"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def counts(self, run_id: str | None = None) -> dict[str, int]:
        """Число строк по таблицам: нужно отчёту и проверкам полноты."""
        result: dict[str, int] = {}
        with self._lock:
            for table in ("runs", "run_events", "metrics", "artifacts", "annotations"):
                if run_id is None:
                    row = self._connection.execute(
                        f"SELECT COUNT(*) AS value FROM {table}"
                    ).fetchone()
                else:
                    row = self._connection.execute(
                        f"SELECT COUNT(*) AS value FROM {table} WHERE run_id = ?", (run_id,)
                    ).fetchone()
                result[table] = int(row["value"]) if row is not None else 0
        return result

    # --- обслуживание ----------------------------------------------------

    def recover_incomplete(self) -> list[str]:
        """Помечает ``running`` как ``interrupted``: процесс умер раньше финала."""
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT run_id FROM runs WHERE status = 'running'"
            ).fetchall()
            ids = [str(row["run_id"]) for row in rows]
            if ids:
                self._connection.execute(
                    "UPDATE runs SET status = 'interrupted', reason = COALESCE(reason, ?),"
                    " finished_at = COALESCE(finished_at, ?) WHERE status = 'running'",
                    ("прогон не завершён: процесс прерван", utc_now()),
                )
        return ids

    def prune(
        self,
        *,
        before: str | None = None,
        keep_last: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Удаляет старые прогоны; артефакты удаляются каскадом.

        ``keep_last`` сохраняет указанное число самых свежих прогонов,
        ``before`` удаляет всё, что старше указанного момента. Оба условия
        можно совмещать: удаляется пересечение.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT run_id, started_at FROM runs ORDER BY started_at DESC"
            ).fetchall()
            doomed: list[str] = []
            for index, row in enumerate(rows):
                too_old = before is not None and str(row["started_at"]) < before
                too_many = keep_last is not None and index >= max(0, int(keep_last))
                if too_old or too_many:
                    doomed.append(str(row["run_id"]))
            if doomed and not dry_run:
                with self._connection:
                    self._connection.executemany(
                        "DELETE FROM runs WHERE run_id = ?", [(item,) for item in doomed]
                    )
        return {"removed": 0 if dry_run else len(doomed), "candidates": doomed, "dry_run": dry_run}

    def integrity_check(self) -> str:
        """Результат ``PRAGMA integrity_check``: ``ok`` или описание порчи."""
        with self._lock:
            return str(pragma(self._connection, "integrity_check"))

    def tables(self) -> list[str]:
        with self._lock:
            return table_names(self._connection)

    def stats(self) -> dict[str, Any]:
        """Сводка журнала: путь, версия схемы, режим WAL и число строк."""
        with self._lock:
            return {
                "path": str(self.path),
                "schema_version": read_schema_version(self._connection),
                "journal_mode": str(pragma(self._connection, "journal_mode")),
                "foreign_keys": int(pragma(self._connection, "foreign_keys") or 0),
                "counts": self.counts(),
            }

    # --- служебное -------------------------------------------------------

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["config"] = _loads(payload.pop("config_json", "{}"))
        return payload


def open_store(path: Path | str, *, recover: bool = True) -> RunStore:
    """Открывает журнал и помечает незавершённые прогоны прерванными."""
    store = RunStore.open(path)
    if recover:
        store.recover_incomplete()
    return store


def parse_moment(value: str | None) -> str | None:
    """Нормализует момент времени для фильтра ``before``/``since``.

    Принимает ``YYYY-MM-DD`` и полный ISO-момент: короткая дата означает
    начало суток UTC, чтобы сравнение строк оставалось корректным.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) == 10:
        datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
        return f"{text}T00:00:00Z"
    return text
