"""Миграции схемы проекта (T1-03, TRD 5.11).

Миграция — детерминированная функция ``vN → vN+1`` над сырым словарём.
Она не знает про модели, поэтому может починить файл, который ещё не
проходит валидацию. Повторный вызов на актуальной версии ничего не меняет.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from simorph.core.errors import MigrationError, UnsupportedVersionError
from simorph.core.schema_version import (
    CURRENT_VERSION,
    is_current,
    is_from_future,
    is_known,
)

#: Функция миграции: получает документ и возвращает новый.
MigrationFunc = Callable[[dict[str, Any]], dict[str, Any]]

#: Один шаг: версия-источник → (версия-приёмник, функция).
MigrationStep = tuple[str, MigrationFunc]

#: Цепочки миграций: раздел → версия-источник → шаг.
MIGRATIONS: dict[str, dict[str, MigrationStep]] = {}

_SECTION_ID_FIELDS: tuple[tuple[str, str], ...] = (
    ("agents", "agent_id"),
    ("exercises", "exercise_id"),
    ("pipelines", "pipeline_id"),
    ("reports", "report_id"),
)


def register(section: str, source: str, target: str, func: MigrationFunc) -> None:
    """Регистрирует шаг миграции ``source → target``."""
    MIGRATIONS.setdefault(section, {})[source] = (target, func)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Итог миграции: новый документ и список применённых шагов."""

    payload: dict[str, Any]
    applied: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _migrate_section(item: Any, id_field: str) -> None:
    """Дописывает ``schema_version`` и переименовывает ``id`` в ``*_id``."""
    if not isinstance(item, dict):
        return
    item.setdefault("schema_version", CURRENT_VERSION)
    if id_field not in item and "id" in item:
        item[id_field] = item.pop("id")


def project_0_9_to_1_0(data: dict[str, Any]) -> dict[str, Any]:
    """Проект формата 0.9 → 1.0: единое поле версии и идентификаторы разделов."""
    migrated: dict[str, Any] = dict(data)
    migrated.pop("version", None)
    migrated["schema_version"] = CURRENT_VERSION

    for key in ("profile", "world"):
        section = migrated.get(key)
        if isinstance(section, dict):
            section.setdefault("schema_version", CURRENT_VERSION)

    for key, id_field in _SECTION_ID_FIELDS:
        items = migrated.get(key)
        if isinstance(items, list):
            for item in items:
                _migrate_section(item, id_field)

    migrated.setdefault("transport", {"mode": "inproc", "domain_id": 0})
    migrated.setdefault("settings", {"theme": "dark", "language": "ru", "offline": True})

    moment = _now_iso()
    migrated.setdefault("created_at", moment)
    if "updated_at" not in migrated:
        migrated["updated_at"] = migrated["created_at"]

    return migrated


register("project", "0.9", CURRENT_VERSION, project_0_9_to_1_0)


def payload_version(payload: dict[str, Any]) -> str:
    """Возвращает версию документа или отказывает, если её нельзя определить."""
    raw = payload.get("schema_version", payload.get("version"))
    if not isinstance(raw, str) or not raw.strip():
        raise UnsupportedVersionError("в документе нет поля 'schema_version'", version="unknown")
    return raw.strip()


def plan_migration(section: str, version: str) -> list[tuple[str, str, MigrationFunc]]:
    """Строит цепочку шагов от ``version`` до текущей версии.

    Raises:
        UnsupportedVersionError: версия новее текущей или неизвестна.
        MigrationError: для версии нет зарегистрированного шага.
    """
    if is_from_future(version):
        raise UnsupportedVersionError(
            f"версия {version} новее поддерживаемой {CURRENT_VERSION}: обновите приложение",
            version=version,
        )
    if not is_known(version):
        raise UnsupportedVersionError(f"неизвестная версия схемы: {version}", version=version)

    steps: list[tuple[str, str, MigrationFunc]] = []
    current = version
    guard = 0
    while not is_current(current):
        guard += 1
        if guard > 64:  # pragma: no cover - защита от зацикливания реестра
            raise MigrationError(f"слишком длинная цепочка миграций от {version}")
        step = MIGRATIONS.get(section, {}).get(current)
        if step is None:
            raise MigrationError(f"нет миграции {section}:{current} → следующая версия")
        following, func = step
        steps.append((current, following, func))
        current = following
    return steps


def migrate_payload(
    payload: dict[str, Any],
    *,
    section: str = "project",
) -> MigrationResult:
    """Применяет цепочку миграций к документу.

    Если версия уже текущая, документ возвращается без изменений —
    повторная миграция не меняет файл.
    """
    version = payload_version(payload)
    if is_current(version):
        return MigrationResult(payload=payload, applied=[])

    steps = plan_migration(section, version)
    migrated = payload
    applied: list[str] = []
    for source, target, func in steps:
        migrated = func(migrated)
        applied.append(f"{section}:{source}→{target}")

    if payload_version(migrated) != CURRENT_VERSION:  # pragma: no cover - защита реестра
        raise MigrationError("цепочка миграций не привела к текущей версии")
    return MigrationResult(payload=migrated, applied=applied)
