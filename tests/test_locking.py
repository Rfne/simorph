"""Тесты блокировки проекта (G1-04)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from simorph.core.errors import ProjectLockedError, StaleLockError
from simorph.core.locking import (
    LockInfo,
    ProjectLock,
    is_stale,
    lock_path_for,
    process_alive,
    read_lock,
)


@pytest.mark.unit
def test_acquire_creates_lock_file(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    lock = ProjectLock(project)
    lock.acquire()

    assert lock.held
    info = read_lock(lock_path_for(project))
    assert info is not None
    assert info.pid == os.getpid()

    lock.release()
    assert not lock_path_for(project).exists()


@pytest.mark.unit
def test_second_instance_is_locked_out(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    first = ProjectLock(project)
    first.acquire()
    try:
        second = ProjectLock(project)
        with pytest.raises(ProjectLockedError) as exc_info:
            second.acquire()
        assert exc_info.value.holder
        assert exc_info.value.holder["pid"] == os.getpid()
    finally:
        first.release()


@pytest.mark.unit
def test_stale_lock_requires_confirmation(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    lock_file = lock_path_for(project)
    lock_file.write_text(
        json.dumps({"pid": 999_999_999, "host": "", "user": "", "created_at": ""}),
        encoding="utf-8",
    )

    lock = ProjectLock(project)
    with pytest.raises(StaleLockError):
        lock.acquire()
    assert lock_file.exists()


@pytest.mark.unit
def test_stale_lock_can_be_taken_over(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    lock_file = lock_path_for(project)
    lock_file.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")

    lock = ProjectLock(project)
    lock.acquire(takeover_stale=True)
    try:
        info = read_lock(lock_file)
        assert info is not None
        assert info.pid == os.getpid()
    finally:
        lock.release()


@pytest.mark.unit
def test_context_manager_releases(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    with ProjectLock(project) as lock:
        assert lock.held
        assert lock_path_for(project).exists()
    assert not lock_path_for(project).exists()


@pytest.mark.unit
def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = ProjectLock(tmp_path / "project.json")
    lock.acquire()
    lock.release()
    lock.release()
    assert not lock.held


@pytest.mark.unit
def test_foreign_host_lock_is_not_stale() -> None:
    info = LockInfo(pid=999_999_999, host="other-host", user="u", created_at="")
    assert is_stale(info, local_host="this-host") is False


@pytest.mark.unit
def test_lock_without_pid_is_stale() -> None:
    assert is_stale(LockInfo(pid=0, host="", user="", created_at="")) is True


@pytest.mark.unit
def test_read_lock_of_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_lock(tmp_path / "absent.lock") is None


@pytest.mark.unit
def test_read_lock_of_broken_file_is_tolerant(tmp_path: Path) -> None:
    path = tmp_path / "broken.lock"
    path.write_text("не json", encoding="utf-8")
    info = read_lock(path)
    assert info is not None
    assert info.pid == 0


@pytest.mark.unit
def test_current_process_is_alive() -> None:
    assert process_alive(os.getpid()) is True


@pytest.mark.unit
def test_lock_info_description_contains_pid() -> None:
    info = LockInfo(pid=1234, host="h", user="u", created_at="")
    assert "pid=1234" in info.description
