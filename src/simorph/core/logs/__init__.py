"""Журнал прогонов: схема, запись, чтение, импорт отчётов и экспорт (этап 9).

Пакет не зависит от Qt: те же функции используют CLI ``simorph runs``,
вкладка «Журналы» и тесты. Прогон симуляции и прогон конвейера
раскладываются в одни и те же таблицы, поэтому просмотр, фильтры и
экспорт работают одинаково для обоих видов.

Журнал — отдельный файл ``run.sqlite`` рядом с проектом: прогоны
неизменяемы и растут быстрее документа, а ``project.json`` остаётся
компактным и переносимым.
"""

from __future__ import annotations

from simorph.core.logs.export import (
    EXPORT_COLUMNS,
    ExportFormat,
    ExportResult,
    export_rows,
    export_table,
    rows_for_table,
)
from simorph.core.logs.ingest import (
    DEFAULT_LOG_NAME,
    default_log_path,
    record_pipeline,
    record_simulation,
    record_to_file,
)
from simorph.core.logs.records import (
    RUN_KINDS,
    RUN_STATUSES,
    AnnotationRow,
    ArtifactRow,
    EventRow,
    MetricRow,
    RunBundle,
    RunRecord,
    pipeline_bundle,
    simulation_bundle,
    utc_now,
)
from simorph.core.logs.schema import (
    CURRENT_SCHEMA_VERSION,
    EVENT_TABLE_COLUMNS,
    METRIC_TABLE_COLUMNS,
    SCHEMA_SQL,
    connect,
    create_schema,
    read_schema_version,
    table_names,
)
from simorph.core.logs.store import DEFAULT_BATCH_SIZE, RunStore, open_store, parse_moment

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_LOG_NAME",
    "EVENT_TABLE_COLUMNS",
    "EXPORT_COLUMNS",
    "METRIC_TABLE_COLUMNS",
    "RUN_KINDS",
    "RUN_STATUSES",
    "SCHEMA_SQL",
    "AnnotationRow",
    "ArtifactRow",
    "EventRow",
    "ExportFormat",
    "ExportResult",
    "MetricRow",
    "RunBundle",
    "RunRecord",
    "RunStore",
    "connect",
    "create_schema",
    "default_log_path",
    "export_rows",
    "export_table",
    "open_store",
    "parse_moment",
    "pipeline_bundle",
    "read_schema_version",
    "record_pipeline",
    "record_simulation",
    "record_to_file",
    "rows_for_table",
    "simulation_bundle",
    "table_names",
    "utc_now",
]
