"""Тесты CLI проекта: new, validate, migrate, hash (T1-07, G1-01…G1-07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.cli.exit_codes import ExitCode
from simorph.cli.main import main
from simorph.core.canonical import compute_hash
from simorph.core.locking import ProjectLock, lock_path_for
from simorph.core.repository import load_project


@pytest.mark.integration
def test_new_creates_valid_project(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "project.json"
    code = main(["project", "new", str(target), "--name", "demo", "--json"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["schema_version"] == "1.0"
    assert target.exists()


@pytest.mark.integration
def test_new_project_has_all_sections(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "project.json"
    main(["project", "new", str(target), "--json"])
    capfd.readouterr()

    document = load_project(target).document
    sections = document.sections()
    assert set(sections) == {
        "project",
        "profile",
        "world",
        "agent",
        "exercise",
        "pipeline",
        "report",
    }
    assert document.agents
    assert document.exercises
    assert document.pipelines
    assert document.reports


@pytest.mark.integration
def test_new_project_passes_validate(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "project.json"
    main(["project", "new", str(target)])
    capfd.readouterr()

    code = main(["project", "validate", str(target), "--json"])
    out, _ = capfd.readouterr()
    payload = json.loads(out)

    assert code == int(ExitCode.OK)
    assert payload["issues"] == []


@pytest.mark.integration
def test_new_refuses_to_overwrite(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "project.json"
    main(["project", "new", str(target)])
    capfd.readouterr()

    code = main(["project", "new", str(target)])
    _, err = capfd.readouterr()
    assert code == int(ExitCode.DATA_ERROR)
    assert "уже существует" in err


@pytest.mark.integration
def test_new_force_overwrites(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "project.json"
    main(["project", "new", str(target), "--name", "first"])
    capfd.readouterr()

    code = main(["project", "new", str(target), "--name", "second", "--force"])
    capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert load_project(target).document.name == "second"


@pytest.mark.integration
def test_validate_reports_errors_with_paths(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    payload = json.loads(project_path.read_text(encoding="utf-8"))
    payload["exercises"][0]["world_id"] = "W-99"
    project_path.write_text(json.dumps(payload), encoding="utf-8")

    code = main(["project", "validate", str(project_path), "--json"])
    out, _ = capfd.readouterr()
    report = json.loads(out)

    assert code == int(ExitCode.DATA_ERROR)
    assert report["status"] == "error"
    assert any(item["path"] == "exercises.0.world_id" for item in report["issues"])


@pytest.mark.integration
def test_validate_missing_file_is_data_error(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = main(["project", "validate", str(tmp_path / "absent.json"), "--json"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert json.loads(out)["status"] == "error"


@pytest.mark.integration
def test_validate_broken_json_is_data_error(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{не json", encoding="utf-8")

    code = main(["project", "validate", str(target)])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "JSON" in err


@pytest.mark.integration
def test_hash_is_stable_across_runs(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    first = main(["project", "hash", str(project_path)])
    out_first, _ = capfd.readouterr()

    second = main(["project", "hash", str(project_path)])
    out_second, _ = capfd.readouterr()

    assert first == int(ExitCode.OK)
    assert second == int(ExitCode.OK)
    assert out_first.strip() == out_second.strip()


@pytest.mark.integration
def test_hash_matches_library(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    main(["project", "hash", str(project_path), "--json"])
    out, _ = capfd.readouterr()
    reported = json.loads(out)["hash"]

    document = load_project(project_path).document
    assert reported == compute_hash(document)


@pytest.mark.integration
def test_migrate_legacy_file_creates_backup(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "legacy.json"
    target.write_text(
        json.dumps(
            {
                "version": "0.9",
                "project_id": "3f1c2a9e-7b1d-4c2a-9e6f-1a2b3c4d5e6f",
                "name": "legacy",
                "profile": {"parameters": []},
                "world": {"world_id": "W-01", "seed": 42, "terrain": {"size_m": [10, 10]}},
                "agents": [],
                "exercises": [],
                "pipelines": [],
                "reports": [],
            }
        ),
        encoding="utf-8",
    )
    original = target.read_text(encoding="utf-8")

    code = main(["project", "migrate", str(target), "--json"])
    out, _ = capfd.readouterr()
    result = json.loads(out)

    assert code == int(ExitCode.OK)
    assert result["applied"] == ["project:0.9→1.0"]
    assert result["backup"]
    assert Path(result["backup"]).read_text(encoding="utf-8") == original
    assert load_project(target).document.schema_version == "1.0"


@pytest.mark.integration
def test_migrate_is_idempotent(project_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    first = main(["project", "migrate", str(project_path), "--json"])
    capfd.readouterr()
    before = project_path.read_bytes()

    second = main(["project", "migrate", str(project_path), "--json"])
    out, _ = capfd.readouterr()

    assert first == int(ExitCode.OK)
    assert second == int(ExitCode.OK)
    assert json.loads(out)["applied"] == []
    assert project_path.read_bytes() == before


@pytest.mark.integration
def test_migrate_dry_run_does_not_write(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "legacy.json"
    target.write_text(
        json.dumps({"version": "0.9", "project_id": "x", "name": "l"}), encoding="utf-8"
    )
    before = target.read_bytes()

    code = main(["project", "migrate", str(target), "--dry-run", "--json"])
    out, _ = capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert json.loads(out)["applied"] == ["project:0.9→1.0"]
    assert target.read_bytes() == before


@pytest.mark.integration
def test_migrate_future_version_is_data_error(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "future.json"
    target.write_text(json.dumps({"schema_version": "9.0", "name": "x"}), encoding="utf-8")

    code = main(["project", "migrate", str(target)])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "новее" in err


@pytest.mark.integration
def test_project_without_action_is_data_error(capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["project"])
    _, err = capfd.readouterr()
    assert code == int(ExitCode.DATA_ERROR)
    assert "new" in err or "действие" in err


@pytest.mark.integration
def test_new_accepts_directory_path(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["project", "new", str(tmp_path)])
    capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert (tmp_path / "project.json").exists()


@pytest.mark.integration
def test_new_releases_lock_after_write(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "project.json"
    main(["project", "new", str(target)])
    capfd.readouterr()

    assert not lock_path_for(target).exists()


@pytest.mark.integration
def test_new_is_blocked_by_live_lock(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "project.json"
    holder = ProjectLock(target)
    holder.acquire()
    try:
        code = main(["project", "new", str(target), "--json"])
        out, _ = capfd.readouterr()
        report = json.loads(out)

        assert code == int(ExitCode.DATA_ERROR)
        assert report["status"] == "error"
        assert "занят" in report["error"]
        assert not target.exists()
    finally:
        holder.release()


@pytest.mark.integration
def test_new_requires_confirmation_for_stale_lock(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "project.json"
    lock_path_for(target).write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")

    code = main(["project", "new", str(target)])
    _, err = capfd.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert "--takeover-lock" in err
    assert not target.exists()


@pytest.mark.integration
def test_new_takeover_lock_replaces_stale_file(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "project.json"
    lock_path_for(target).write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")

    code = main(["project", "new", str(target), "--takeover-lock"])
    capfd.readouterr()

    assert code == int(ExitCode.OK)
    assert target.exists()
    assert not lock_path_for(target).exists()


@pytest.mark.integration
def test_migrate_is_blocked_by_live_lock(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "legacy.json"
    target.write_text(
        json.dumps({"version": "0.9", "project_id": "x", "name": "l"}), encoding="utf-8"
    )
    before = target.read_bytes()

    holder = ProjectLock(target)
    holder.acquire()
    try:
        code = main(["project", "migrate", str(target)])
        _, err = capfd.readouterr()

        assert code == int(ExitCode.DATA_ERROR)
        assert "занят" in err
        assert target.read_bytes() == before
    finally:
        holder.release()


@pytest.mark.integration
def test_read_only_commands_ignore_lock(
    project_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    holder = ProjectLock(project_path)
    holder.acquire()
    try:
        validate_code = main(["project", "validate", str(project_path), "--json"])
        capfd.readouterr()
        hash_code = main(["project", "hash", str(project_path), "--json"])
        capfd.readouterr()

        assert validate_code == int(ExitCode.OK)
        assert hash_code == int(ExitCode.OK)
    finally:
        holder.release()
