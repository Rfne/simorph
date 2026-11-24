"""Общие фикстуры тестов simorph."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from simorph.core.factory import new_project
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import save_project
from simorph.settings import ApplicationSettings

# Qt должен узнать про offscreen до первого импорта виджетов.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: Момент времени, зафиксированный в тестах: без него хэш не воспроизводим.
FIXED_MOMENT = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)

#: Идентификатор проекта из эталонного файла.
FIXED_PROJECT_ID = UUID("3f1c2a9e-7b1d-4c2a-9e6f-1a2b3c4d5e6f")


@pytest.fixture
def fixed_moment() -> datetime:
    """Фиксированное время создания проекта."""
    return FIXED_MOMENT


@pytest.fixture
def project_document(fixed_moment: datetime) -> ProjectDocument:
    """Новый проект со всеми семью разделами и предсказуемым временем."""
    return new_project(
        "test",
        seed=42,
        project_id=FIXED_PROJECT_ID,
        moment=fixed_moment,
    )


@pytest.fixture
def project_dir(tmp_path: Path, project_document: ProjectDocument) -> Iterator[Path]:
    """Временный каталог с сохранённым ``project.json``."""
    save_project(project_document, tmp_path / "project.json", make_backup=False)
    yield tmp_path


@pytest.fixture
def project_path(project_dir: Path) -> Path:
    """Путь к сохранённому проекту."""
    return project_dir / "project.json"


@pytest.fixture
def isolated_settings(tmp_path: Path) -> ApplicationSettings:
    """Настройки в отдельном ini-файле: тесты не трогают настройки машины."""
    from PySide6.QtCore import QSettings

    file = tmp_path / "settings.ini"
    settings = QSettings(str(file), QSettings.Format.IniFormat)
    settings.clear()
    return ApplicationSettings(settings)


#: Идентификатор прогона в тестовом журнале.
REPORT_RUN_ID = "sim-report"

#: Хэш проекта в тестовом журнале.
REPORT_PROJECT_HASH = "sha256:" + "0" * 64


def seed_report_database(database: Path, *, run_id: str = REPORT_RUN_ID) -> Path:
    """Журнал с прогоном, метриками, ступенями и артефактом для отчёта."""
    from simorph.core.logs import ArtifactRow, EventRow, MetricRow, RunRecord, open_store

    with open_store(database) as store:
        store.start_run(
            RunRecord(
                run_id=run_id,
                kind="simulation",
                status="finished",
                started_at="2026-11-20T10:00:00Z",
                finished_at="2026-11-20T10:00:02Z",
                project_hash=REPORT_PROJECT_HASH,
                mission_id="M-01",
                seed=20261120,
                reason="миссия завершена",
                config={"duration_s": 2.0, "scale": 1.0},
                app_version="0.13.0",
            )
        )
        store.write_metrics(
            run_id,
            [
                MetricRow("k", 1.0, 1_000_000, "ratio"),
                MetricRow("k", 1.4, 2_000_000, "ratio"),
                MetricRow("k.p50", 1.2, 2_000_000, "ratio"),
                MetricRow("k.p95", 1.4, 2_000_000, "ratio"),
                MetricRow("k.max", 1.5, 2_000_000, "ratio"),
                MetricRow("k.share_over_plan", 0.5, 2_000_000, "ratio"),
                MetricRow("stage.sensor.plan_ms", 10.0, 1_000_000, "ms"),
                MetricRow("stage.sensor.actual_ms", 9.0, 1_000_000, "ms"),
                MetricRow("stage.sensor.actual_ms", 12.0, 2_000_000, "ms"),
                MetricRow("stage.npu.plan_ms", 4.0, 1_000_000, "ms"),
                MetricRow("stage.npu.actual_ms", 3.0, 1_000_000, "ms"),
                MetricRow("frames.pushed", 240.0, 2_000_000, "frames"),
                MetricRow("frames.consumed", 240.0, 2_000_000, "frames"),
                MetricRow("frames.dropped", 0.0, 2_000_000, "frames"),
                MetricRow("queue_depth", 0.0, 1_000_000, "frames"),
                MetricRow("queue_depth", 2.0, 2_000_000, "frames"),
                MetricRow("queue_wait_ms", 0.5, 2_000_000, "ms"),
                MetricRow("interval.wall_time_ms", 1000.0, 1_000_000, "ms"),
                MetricRow("interval.wall_time_ms", 1400.0, 2_000_000, "ms"),
                MetricRow("eval.map50", 0.64, 2_000_000, None),
                MetricRow("eval.latency_p95_ms", 380.0, 2_000_000, "ms"),
            ],
        )
        store.write_events(run_id, [EventRow(1_000_000, "simulation", "run_started")])
        store.add_artifacts(
            run_id,
            [ArtifactRow("onnx", "models/det_kp.onnx", "sha256:" + "1" * 64, 1024)],
        )
    return database


@pytest.fixture
def report_database(tmp_path: Path) -> Path:
    """Журнал с одним прогоном: основа тестов отчёта и дашборда."""
    return seed_report_database(tmp_path / "run.sqlite")
