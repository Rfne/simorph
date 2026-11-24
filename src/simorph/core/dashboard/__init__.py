"""Дашборд прогона на Panel: данные, конфигурация, панель и сервер (этап 11).

Пакет отделён от Qt: дашборд читает журнал, строит панель и поднимает
локальный сервер. Panel импортируется лениво — ``core`` остаётся лёгким
для CLI и тестов, которым нужны только данные и конфигурация.
"""

from __future__ import annotations

from simorph.core.dashboard.app import build_dashboard, dashboard_summary, latex_kappa
from simorph.core.dashboard.config import (
    DASHBOARD_NAME,
    DASHBOARD_SECTIONS,
    DashboardConfig,
    load_dashboard_config,
)
from simorph.core.dashboard.data import (
    DashboardData,
    DashboardRun,
    load_dashboard_data,
)
from simorph.core.dashboard.server import LOCAL_ADDRESS, dashboard_url, serve_dashboard

__all__ = [
    "DASHBOARD_NAME",
    "DASHBOARD_SECTIONS",
    "LOCAL_ADDRESS",
    "DashboardConfig",
    "DashboardData",
    "DashboardRun",
    "build_dashboard",
    "dashboard_summary",
    "dashboard_url",
    "latex_kappa",
    "load_dashboard_config",
    "load_dashboard_data",
    "serve_dashboard",
]
