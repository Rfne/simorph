"""Блокировка проекта файлом ``.lock`` (T1-04, G1-04).

Блокировка нужна, чтобы два экземпляра приложения не перезаписывали один
файл. Создание идёт атомарно (``O_CREAT | O_EXCL``), поэтому гонки нет.
Если владелец блокировки завершился аварийно, блокировка считается
устаревшей и снимается только по явному подтверждению.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simorph.core.errors import ProjectLockedError, StaleLockError

#: Суффикс файла блокировки.
LOCK_SUFFIX = ".lock"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if sys.platform == "win32":  # pragma: no cover - ветка Windows

    def _process_alive_impl(pid: int) -> bool:
        """Windows: ждём объект процесса, не блокируясь."""
        import ctypes

        synchronize = 0x00100000
        still_active = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.WaitForSingleObject(handle, 0) == still_active)
        finally:
            kernel32.CloseHandle(handle)

else:  # pragma: no cover - ветка POSIX

    def _process_alive_impl(pid: int) -> bool:
        """POSIX: сигнал 0 проверяет существование процесса."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def process_alive(pid: int) -> bool:
    """Проверяет, существует ли процесс с данным ``pid`` на этой машине.

    Реализация выбирается по ``sys.platform`` на уровне модуля: только так
    mypy отбрасывает недоступную платформе ветку, и типы сходятся одинаково
    на Windows и на Linux.
    """
    if pid <= 0:
        return False
    return _process_alive_impl(pid)


@dataclass(frozen=True, slots=True)
class LockInfo:
    """Содержимое файла блокировки."""

    pid: int
    host: str
    user: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "host": self.host,
            "user": self.user,
            "created_at": self.created_at,
        }

    @classmethod
    def parse(cls, payload: object) -> LockInfo:
        """Разбирает содержимое блокировки, подставляя безопасные значения."""
        if not isinstance(payload, dict):
            return cls(pid=0, host="", user="", created_at="")
        raw_pid = payload.get("pid", 0)
        pid = raw_pid if isinstance(raw_pid, int) else 0
        return cls(
            pid=pid,
            host=str(payload.get("host", "")),
            user=str(payload.get("user", "")),
            created_at=str(payload.get("created_at", "")),
        )

    @property
    def description(self) -> str:
        """Короткое описание для сообщения об ошибке."""
        return f"pid={self.pid}, host={self.host or '?'}, user={self.user or '?'}"


def lock_path_for(project_path: Path) -> Path:
    """Путь к файлу блокировки рядом с проектом."""
    return project_path.with_name(project_path.name + LOCK_SUFFIX)


def read_lock(path: Path) -> LockInfo | None:
    """Читает блокировку; ``None``, если файла нет."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return LockInfo(pid=0, host="", user="", created_at="")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return LockInfo(pid=0, host="", user="", created_at="")
    return LockInfo.parse(payload)


def is_stale(info: LockInfo, *, local_host: str | None = None) -> bool:
    """Блокировка устарела, если её процесс на этой машине уже не жив."""
    host = local_host if local_host is not None else socket.gethostname()
    if info.host and info.host != host:
        return False
    if info.pid <= 0:
        return True
    return not process_alive(info.pid)


class ProjectLock:
    """Контекстный менеджер блокировки проекта."""

    def __init__(self, project_path: Path) -> None:
        self._path = lock_path_for(project_path)
        self._held = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self, *, takeover_stale: bool = False) -> None:
        """Захватывает блокировку.

        Args:
            takeover_stale: снять устаревшую блокировку, оставленную
                завершившимся процессом.

        Raises:
            ProjectLockedError: проект занят живым экземпляром.
            StaleLockError: блокировка устарела, но подтверждения нет.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = LockInfo(
            pid=os.getpid(),
            host=socket.gethostname(),
            user=_user_name(),
            created_at=_now_iso(),
        )
        for attempt in range(2):
            try:
                handle = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                info = read_lock(self._path) or LockInfo(0, "", "", "")
                if not is_stale(info):
                    raise ProjectLockedError(
                        f"проект занят: {info.description}", holder=info.to_dict()
                    ) from None
                if not takeover_stale:
                    raise StaleLockError(
                        f"устаревшая блокировка: {info.description}", holder=info.to_dict()
                    ) from None
                self._path.unlink(missing_ok=True)
                if attempt == 1:  # pragma: no cover - гонка двух снимающих
                    raise ProjectLockedError("не удалось снять устаревшую блокировку") from None
                continue
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload.to_dict(), stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._held = True
            return

    def release(self) -> None:
        """Снимает блокировку, если она принадлежит этому процессу."""
        if not self._held:
            return
        self._path.unlink(missing_ok=True)
        self._held = False

    def __enter__(self) -> ProjectLock:
        if not self._held:
            self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def _user_name() -> str:
    """Имя пользователя для диагностики занятости."""
    for variable in ("USERNAME", "USER", "LOGNAME"):
        value = os.environ.get(variable)
        if value:
            return value
    return ""
