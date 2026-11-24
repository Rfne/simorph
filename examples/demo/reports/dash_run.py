"""Источник дашборда прогонов: `panel serve reports/dash_run.py`.

Файл читается и правится вне приложения: меняйте заголовок, секции и пороги
в `dash_run.yaml` рядом или прямо здесь. Данные — только из журнала
`../../run.sqlite` (путь можно переопределить переменной SIMORPH_LOG).

Запуск из приложения: `simorph dashboard examples/demo/project.json`.
Запуск вручную: `panel serve examples/demo/reports/dash_run.py --show`.
"""

from __future__ import annotations

import os
from pathlib import Path

from simorph.core.dashboard import (
    DashboardConfig,
    build_dashboard,
    load_dashboard_config,
    load_dashboard_data,
)

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "dash_run.yaml"
PROJECT = HERE.parent / "project.json"

config: DashboardConfig = load_dashboard_config(CONFIG_PATH)
data = load_dashboard_data(
    Path(os.environ.get("SIMORPH_LOG", PROJECT)),
    run_ids=list(config.runs),
    limit=config.limit,
)

build_dashboard(data, config).servable()
