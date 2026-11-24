"""Конфигурация дашборда: ``dashboard.yaml`` (T11-09, TRD 5.12).

Конфигурация отделяет «что показывать» от кода панели: список прогонов,
секции, пороги, адрес сервера. Файл читается и правится вне приложения,
поэтому один и тот же источник дашборда работает для разных проектов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from simorph.core.errors import ProjectError

__all__ = [
    "DASHBOARD_NAME",
    "DashboardConfig",
    "load_dashboard_config",
]

#: Имя файла конфигурации рядом с источником дашборда.
DASHBOARD_NAME: Final = "dash_run.yaml"

#: Секции, которые умеет показывать дашборд.
DASHBOARD_SECTIONS: Final[tuple[str, ...]] = ("metrics", "plots", "conclusions", "scale")


@dataclass(slots=True)
class DashboardConfig:
    """Что показать на дашборде и где его поднять."""

    title: str = "Дашборд прогонов"
    runs: list[str] = field(default_factory=list)
    limit: int = 20
    sections: list[str] = field(default_factory=lambda: list(DASHBOARD_SECTIONS))
    thresholds: dict[str, float] = field(
        default_factory=lambda: {"inference_p95_ms": 400.0, "k_p95": 1.5}
    )
    host: str = "127.0.0.1"
    port: int = 5006
    open_browser: bool = True

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("заголовок дашборда не может быть пустым")
        unknown = [item for item in self.sections if item not in DASHBOARD_SECTIONS]
        if unknown:
            raise ValueError(f"неизвестные секции дашборда: {', '.join(unknown)}")
        if not self.sections:
            raise ValueError("нужна хотя бы одна секция дашборда")
        if self.host not in ("127.0.0.1", "localhost"):
            raise ValueError("дашборд поднимается только на локальном адресе")
        if not 0 < int(self.port) < 65536:
            raise ValueError(f"недопустимый порт дашборда: {self.port}")
        if int(self.limit) < 1:
            raise ValueError("предел прогонов должен быть положительным")

    @property
    def has(self) -> set[str]:
        return set(self.sections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "runs": list(self.runs),
            "limit": self.limit,
            "sections": list(self.sections),
            "thresholds": dict(self.thresholds),
            "host": self.host,
            "port": self.port,
            "open_browser": self.open_browser,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DashboardConfig:
        return cls(
            title=str(payload.get("title", "Дашборд прогонов")),
            runs=[str(item) for item in payload.get("runs", [])],
            limit=int(payload.get("limit", 20)),
            sections=[str(item) for item in payload.get("sections", DASHBOARD_SECTIONS)],
            thresholds={
                str(key): float(value)
                for key, value in dict(payload.get("thresholds") or {}).items()
            },
            host=str(payload.get("host", "127.0.0.1")),
            port=int(payload.get("port", 5006)),
            open_browser=bool(payload.get("open_browser", True)),
        )


def load_dashboard_config(path: Path | str | None) -> DashboardConfig:
    """Читает ``dash_*.yaml``; без файла — конфигурация по умолчанию."""
    if path is None:
        return DashboardConfig()
    target = Path(path)
    if not target.is_file():
        raise ProjectError(f"конфигурация дашборда не найдена: {target}")
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProjectError(f"конфигурация дашборда не является YAML: {exc}") from exc
    if payload is None:
        return DashboardConfig()
    if not isinstance(payload, dict):
        raise ProjectError("конфигурация дашборда должна быть словарём")
    try:
        return DashboardConfig.from_dict(payload)
    except ValueError as exc:
        raise ProjectError(f"конфигурация дашборда некорректна: {exc}") from exc
