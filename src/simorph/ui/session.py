"""Жизненный цикл проекта в оболочке (T2-06).

Сессия знает путь к файлу, держит блокировку записи на всё время работы
с проектом и ведёт автосохранение. Автосохранение пишет отдельный файл
``project.json.autosave``, поэтому исходник не перезаписывается: если
процесс убили, при следующем открытии предлагается восстановление.

Блокировка живёт столько же, сколько открытый проект: так второй экземпляр
приложения видит занятость и открывает проект только для чтения (G1-04).
Если процесс завершился аварийно, блокировка остаётся, но считается
устаревшей и снимается только по явному подтверждению.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from simorph.core.canonical import compute_hash, dump_document
from simorph.core.errors import (
    ProjectError,
    ProjectLockedError,
    ProjectReadOnlyError,
    StaleLockError,
)
from simorph.core.locking import LockInfo, ProjectLock, lock_path_for, read_lock
from simorph.core.models.project import ProjectDocument
from simorph.core.repository import load_project, save_project, write_text_atomic
from simorph.settings import ApplicationSettings

__all__ = ["AUTOSAVE_SUFFIX", "OpenedProject", "ProjectSession", "autosave_path_for"]

#: Суффикс файла автосохранения.
AUTOSAVE_SUFFIX = ".autosave"


def autosave_path_for(project_path: Path) -> Path:
    """Путь файла автосохранения рядом с проектом."""
    return project_path.with_name(project_path.name + AUTOSAVE_SUFFIX)


@dataclass(slots=True)
class OpenedProject:
    """Результат открытия: документ и сведения о файле."""

    document: ProjectDocument
    path: Path
    migrations: list[str]
    recovery_available: bool
    read_only: bool = False
    lock_holder: LockInfo | None = None


class ProjectSession:
    """Открытие, сохранение и автосохранение проекта."""

    def __init__(self, settings: ApplicationSettings | None = None) -> None:
        self._settings = settings if settings is not None else ApplicationSettings()
        self._path: Path | None = None
        self._autosave_path: Path | None = None
        self._lock: ProjectLock | None = None
        self._read_only = False
        self._holder: LockInfo | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def title(self) -> str:
        return self._path.name if self._path is not None else "без имени"

    @property
    def settings(self) -> ApplicationSettings:
        return self._settings

    @property
    def read_only(self) -> bool:
        """Проект занят другим экземпляром и открыт только для чтения."""
        return self._read_only

    @property
    def lock_holder(self) -> LockInfo | None:
        """Сведения о владельце блокировки, если проект занят."""
        return self._holder

    def open(
        self,
        path: Path,
        *,
        takeover_stale: bool = False,
        read_only: bool = False,
    ) -> OpenedProject:
        """Открывает проект, применяя миграции и захватывая блокировку.

        Args:
            path: файл проекта.
            takeover_stale: снять устаревшую блокировку без дополнительного
                подтверждения (пользователь уже согласился).
            read_only: открыть заведомо без права записи.

        Raises:
            StaleLockError: блокировка осталась от завершившегося процесса
                и требует подтверждения.
        """
        loaded = load_project(path)
        self._release_lock()
        self._path = path
        self._autosave_path = autosave_path_for(path)
        self._read_only = False
        self._holder = None
        self._settings.add_recent(path)
        self._settings.last_dir = path.parent

        if read_only:
            self._read_only = True
            self._holder = read_lock(lock_path_for(path))
        else:
            self._acquire_lock(takeover_stale=takeover_stale)

        return OpenedProject(
            document=loaded.document,
            path=path,
            migrations=loaded.applied_migrations,
            recovery_available=self.has_recovery(),
            read_only=self._read_only,
            lock_holder=self._holder,
        )

    def _acquire_lock(self, *, takeover_stale: bool) -> None:
        """Берёт блокировку на время сеанса; занятость делает проект read-only."""
        if self._path is None:
            return
        lock = ProjectLock(self._path)
        try:
            lock.acquire(takeover_stale=takeover_stale)
        except StaleLockError:
            raise
        except ProjectLockedError as exc:
            self._read_only = True
            self._holder = LockInfo.parse(exc.holder)
            return
        # StaleLockError не перехватываем: снятие требует подтверждения.
        self._lock = lock
        self._read_only = False
        self._holder = None

    def _release_lock(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def close(self) -> None:
        """Освобождает проект и снимает блокировку."""
        self._release_lock()
        self._path = None
        self._autosave_path = None
        self._read_only = False
        self._holder = None

    def attach(self, path: Path, document: ProjectDocument) -> None:
        """Связывает сессию с новым проектом, ещё не записанным на диск."""
        self._release_lock()
        self._path = path
        self._autosave_path = autosave_path_for(path)
        self._read_only = False
        self._holder = None
        self._settings.add_recent(path)
        self._settings.last_dir = path.parent
        self._discard_recovery()
        _ = document

    def detach(self) -> None:
        """Отвязывает сессию от файла: новый проект ещё не сохранён."""
        self.close()

    def save(self, document: ProjectDocument, *, path: Path | None = None) -> Path:
        """Сохраняет документ под блокировкой, делая резервную копию."""
        if self._read_only:
            raise ProjectReadOnlyError(
                "проект открыт только для чтения: его занял другой экземпляр"
            )
        target = path or self._path
        if target is None:
            raise ProjectError("не задан путь сохранения")

        if self._lock is not None and self._lock.held and self._lock.path == lock_path_for(target):
            save_project(document, target, make_backup=True)
        else:
            lock = ProjectLock(target)
            lock.acquire(takeover_stale=True)
            try:
                save_project(document, target, make_backup=True)
            except BaseException:
                lock.release()
                raise
            self._release_lock()
            self._lock = lock

        self._path = target
        self._autosave_path = autosave_path_for(target)
        self._settings.add_recent(target)
        self._settings.last_dir = target.parent
        self._discard_recovery()
        return target

    # --- автосохранение -------------------------------------------------

    def has_recovery(self) -> bool:
        """Есть ли файл автосохранения, оставшийся от прошлого запуска."""
        return self._autosave_path is not None and self._autosave_path.exists()

    def autosave(self, document: ProjectDocument) -> Path | None:
        """Пишет автосохранение, не трогая исходный файл."""
        if self._autosave_path is None:
            return None
        write_text_atomic(self._autosave_path, dump_document(document), make_backup=False)
        return self._autosave_path

    def recover(self) -> ProjectDocument | None:
        """Читает документ из автосохранения, если он есть."""
        if self._autosave_path is None or not self._autosave_path.exists():
            return None
        return load_project(self._autosave_path, migrate=False).document

    def _discard_recovery(self) -> None:
        if self._autosave_path is not None:
            self._autosave_path.unlink(missing_ok=True)

    def discard_recovery(self) -> None:
        """Удаляет файл автосохранения (после восстановления или отказа)."""
        self._discard_recovery()

    def project_hash(self, document: ProjectDocument) -> str:
        """Хэш текущего документа — для статус-бара и журнала."""
        return compute_hash(document)
