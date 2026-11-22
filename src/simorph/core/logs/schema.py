"""Схема SQLite-журнала прогонов (T9-01, TRD 5.9).

Журнал — отдельный файл ``run.sqlite`` рядом с рабочим местом, а не часть
``project.json``: прогоны неизменяемы и растут быстрее документа, поэтому
переносимый проект остаётся компактным, а журнал можно архивировать
и очищать отдельно.

Схема создаётся идемпотентно: ``CREATE TABLE IF NOT EXISTS`` плюс запись
версии в ``settings``. Версия нужна, чтобы миграции применялись один раз
и только к старым базам; шаг миграции — детерминированная функция
``vN → vN+1``, как и у проекта.

Режим WAL и ``synchronous=NORMAL`` выбраны осознанно: запись идёт
батчами, а после аварийного завершения база открывается и не требует
ручного ремонта — незавершённая транзакция откатывается сама.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "METRIC_TABLE_COLUMNS",
    "SCHEMA_SQL",
    "connect",
    "read_schema_version",
    "write_schema_version",
]

#: Версия схемы журнала.
CURRENT_SCHEMA_VERSION: Final[int] = 1

#: Колонки таблицы метрик в порядке экспорта.
METRIC_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "run_id",
    "name",
    "value",
    "unit",
    "t_ns",
    "tags_json",
)

#: Колонки таблицы событий в порядке экспорта.
EVENT_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "run_id",
    "t_ns",
    "source",
    "type",
    "payload_json",
)

#: DDL схемы. Порядок операторов важен: сначала родительские таблицы.
SCHEMA_SQL: Final[tuple[str, ...]] = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id        TEXT PRIMARY KEY,
        project_hash  TEXT,
        exercise_id   TEXT,
        mission_id    TEXT,
        pipeline_id   TEXT,
        kind          TEXT NOT NULL,
        seed          INTEGER,
        started_at    TEXT NOT NULL,
        finished_at   TEXT,
        status        TEXT NOT NULL,
        reason        TEXT,
        config_json   TEXT NOT NULL DEFAULT '{}',
        artifact_dir  TEXT,
        app_version   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        t_ns          INTEGER NOT NULL,
        source        TEXT NOT NULL,
        type          TEXT NOT NULL,
        payload_json  TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metrics (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        value         REAL,
        unit          TEXT,
        t_ns          INTEGER NOT NULL,
        tags_json     TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        kind          TEXT NOT NULL,
        path          TEXT NOT NULL,
        hash          TEXT,
        size_bytes    INTEGER,
        UNIQUE (run_id, path, hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS annotations (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        frame_id       INTEGER,
        class_id       TEXT,
        bbox_json      TEXT NOT NULL DEFAULT '[]',
        keypoints_json TEXT NOT NULL DEFAULT '[]',
        source         TEXT NOT NULL DEFAULT 'user',
        created_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_metrics_run ON metrics (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics (name)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_t ON metrics (t_ns)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_run_name_t ON metrics (run_id, name, t_ns)",
    "CREATE INDEX IF NOT EXISTS idx_events_run ON run_events (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_t ON run_events (t_ns)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON run_events (type)",
    "CREATE INDEX IF NOT EXISTS idx_events_run_t ON run_events (run_id, t_ns)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_annotations_run ON annotations (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs (started_at)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status)",
)


def connect(path: Path | str) -> sqlite3.Connection:
    """Открывает базу с WAL, внешними ключами и созданной схемой.

    ``check_same_thread=False`` нужен вкладке журналов: чтение идёт из
    рабочего потока, а запись — из потока прогона. Один ``RunStore``
    сериализует доступ сам, поэтому соединение остаётся единственным.
    """
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), check_same_thread=False, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    """Создаёт таблицы, индексы и записывает версию схемы."""
    with connection:
        for statement in SCHEMA_SQL:
            connection.execute(statement)
        write_schema_version(connection, CURRENT_SCHEMA_VERSION)


def read_schema_version(connection: sqlite3.Connection) -> int:
    """Версия схемы из ``settings``; отсутствие записи — 0."""
    row = connection.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def write_schema_version(connection: sqlite3.Connection, version: int) -> None:
    """Записывает версию схемы."""
    connection.execute(
        "INSERT INTO settings (key, value) VALUES ('schema_version', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(int(version)),),
    )


def table_names(connection: sqlite3.Connection) -> list[str]:
    """Имена таблиц схемы: нужны тестам и диагностике."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def pragma(connection: sqlite3.Connection, name: str) -> Any:
    """Значение ``PRAGMA``; обёртка нужна для тестов и отчёта о состоянии."""
    row = connection.execute(f"PRAGMA {name}").fetchone()
    return None if row is None else row[0]
