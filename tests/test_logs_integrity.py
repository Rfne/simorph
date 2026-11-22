"""Целостность журнала после аварийного завершения процесса (T9-05, G9-05)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from simorph.core.logs import RunRecord, open_store

pytestmark = pytest.mark.integration

#: Скрипт пишет батчи и умирает без закрытия соединения: так выглядит kill.
CRASH_SCRIPT = """
import os
import sys

from simorph.core.logs import MetricRow, RunRecord, RunStore

path = sys.argv[1]
store = RunStore.open(path)
store.start_run(RunRecord(run_id="killed", kind="pipeline", pipeline_id="P-9"))
rows = [MetricRow("node.n.p95_ms", float(index), index, "ms") for index in range(20_000)]
store.write_metrics("killed", rows, batch_size=1_000)
os._exit(9)
"""


def _crash(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", CRASH_SCRIPT, str(path)],
        check=False,
        timeout=120,
        capture_output=True,
        text=True,
    )


def test_kill_during_write_keeps_database_consistent(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    completed = _crash(path)

    assert completed.returncode == 9, completed.stderr
    assert path.exists()

    with open_store(path) as store:
        integrity = store.integrity_check()
        run = store.get_run("killed")
        counts = store.counts("killed")
        names = store.metric_names("killed")

    assert integrity == "ok"
    assert run is not None
    assert run["status"] == "interrupted"
    assert run["pipeline_id"] == "P-9"
    # Батчи коммитятся целиком: ни одна завершённая порция не потеряна.
    assert counts["metrics"] == 20_000
    assert names == ["node.n.p95_ms"]


def test_reopen_after_kill_allows_new_runs(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    _crash(path)

    with open_store(path) as store:
        store.start_run(RunRecord(run_id="after", kind="simulation"))
        store.finish_run("after")
        run_ids = {item["run_id"] for item in store.list_runs()}

    assert run_ids == {"killed", "after"}
