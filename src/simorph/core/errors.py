"""Ошибки ядра проекта.

Иерархия нужна, чтобы CLI различал ошибку данных (код 1) и внутренний сбой
(код 2): всё, что наследуется от :class:`ProjectError`, — это ошибка данных.
"""

from __future__ import annotations

from simorph.core.issues import Issue


class SimorphError(Exception):
    """Базовый класс ошибок приложения."""


class ProjectError(SimorphError):
    """Ошибка данных проекта: файла, схемы, ссылок или версии."""


class ProjectNotFoundError(ProjectError):
    """Файл проекта не существует."""


class ProjectFormatError(ProjectError):
    """Файл не является корректным JSON-документом."""


class ProjectReadOnlyError(ProjectError):
    """Попытка записи без права записи (проект занят другим экземпляром)."""


class ProjectLockedError(ProjectError):
    """Проект заблокирован другим экземпляром приложения."""

    def __init__(self, message: str, *, holder: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.holder = holder or {}


class StaleLockError(ProjectLockedError):
    """Блокировка осталась от завершившегося процесса и требует подтверждения."""


class UnsupportedVersionError(ProjectError):
    """Версия схемы неизвестна или новее поддерживаемой."""

    def __init__(self, message: str, *, version: str) -> None:
        super().__init__(message)
        self.version = version


class MigrationError(ProjectError):
    """Цепочка миграций не может быть построена."""


class ProjectValidationError(ProjectError):
    """Документ не прошёл валидацию."""

    def __init__(self, message: str, *, issues: list[Issue]) -> None:
        super().__init__(message)
        self.issues = issues
