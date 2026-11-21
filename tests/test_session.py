"""Тесты жизненного цикла проекта в оболочке (T2-06, G1-04 UI, G2-04)."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from simorph.core.access import write_value
from simorph.core.errors import ProjectReadOnlyError, StaleLockError
from simorph.core.locking import lock_path_for, read_lock
from simorph.core.repository import load_project
from simorph.settings import ApplicationSettings
from simorph.ui.session import ProjectSession, autosave_path_for


@pytest.fixture
def session(isolated_settings: ApplicationSettings) -> ProjectSession:
    return ProjectSession(isolated_settings)


@pytest.mark.integration
def test_open_acquires_lock_and_remembers_path(
    session: ProjectSession,
    project_path: Path,
    isolated_settings: ApplicationSettings,
) -> None:
    opened = session.open(project_path)

    assert opened.read_only is False
    assert opened.document.name == "test"
    assert session.path == project_path
    assert lock_path_for(project_path).exists()
    assert project_path in isolated_settings.recent_projects
    assert isolated_settings.last_dir == project_path.parent


@pytest.mark.integration
def test_second_session_opens_read_only(
    session: ProjectSession,
    project_path: Path,
    isolated_settings: ApplicationSettings,
) -> None:
    session.open(project_path)
    second = ProjectSession(isolated_settings)

    opened = second.open(project_path)

    assert opened.read_only is True
    assert second.read_only is True
    assert second.lock_holder is not None
    assert second.lock_holder.pid == os.getpid()


@pytest.mark.integration
def test_read_only_session_cannot_save(
    session: ProjectSession,
    project_path: Path,
    isolated_settings: ApplicationSettings,
) -> None:
    session.open(project_path)
    second = ProjectSession(isolated_settings)
    opened = second.open(project_path)

    with pytest.raises(ProjectReadOnlyError):
        second.save(opened.document)


@pytest.mark.integration
def test_stale_lock_requires_confirmation(session: ProjectSession, project_path: Path) -> None:
    lock = lock_path_for(project_path)
    lock.write_text(
        json.dumps({"pid": 0, "host": socket.gethostname(), "user": "", "created_at": ""}),
        encoding="utf-8",
    )

    with pytest.raises(StaleLockError):
        session.open(project_path)

    opened = session.open(project_path, takeover_stale=True)

    assert opened.read_only is False
    holder = read_lock(lock)
    assert holder is not None
    assert holder.pid == os.getpid()


@pytest.mark.integration
def test_save_writes_new_path_and_moves_lock(
    session: ProjectSession, project_path: Path, tmp_path: Path
) -> None:
    opened = session.open(project_path)
    modified = write_value(opened.document, "world.seed", 7)
    target = tmp_path / "nested" / "project.json"

    saved = session.save(modified, path=target)

    assert saved == target
    assert load_project(target).document.world.seed == 7
    assert session.path == target
    assert not lock_path_for(project_path).exists()
    assert lock_path_for(target).exists()


@pytest.mark.integration
def test_autosave_does_not_touch_source_and_is_recoverable(
    session: ProjectSession, project_path: Path
) -> None:
    opened = session.open(project_path)
    modified = write_value(opened.document, "world.seed", 99)

    written = session.autosave(modified)

    assert written == autosave_path_for(project_path)
    assert load_project(project_path).document.world.seed == 42
    assert session.has_recovery()
    recovered = session.recover()
    assert recovered is not None
    assert recovered.world.seed == 99

    session.discard_recovery()
    assert not session.has_recovery()


@pytest.mark.integration
def test_close_releases_lock(session: ProjectSession, project_path: Path) -> None:
    session.open(project_path)

    session.close()

    assert session.path is None
    assert not lock_path_for(project_path).exists()
