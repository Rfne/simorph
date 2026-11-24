"""Манифест пакета и целевые профили (T11-05, G11-04, TST-UNT-09)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simorph.core.errors import ProjectError
from simorph.core.package import (
    MANIFEST_NAME,
    PACKAGE_FORMAT,
    Budgets,
    ModelEntry,
    PackageManifest,
    SocSpec,
    file_hash,
    load_manifest,
    manifest_hash,
    profile_for,
    verify_manifest,
)


def _example_manifest(
    *,
    created_at: str = "2026-11-20T10:00:00Z",
    models: list[dict[str, object]] | None = None,
    pipeline: str = "pipelines/pipe.json",
) -> dict[str, object]:
    """Минимальный манифест по TRD 8.4 для проверок схемы."""
    return {
        "format": PACKAGE_FORMAT,
        "package_id": "PKG-00000000-rk3588",
        "run_id": "run-1",
        "target": "rk3588",
        "soc": {"cpu": "4x A76 + 4x A55", "npu_tops": 6.0, "toolchain": "RKNN-Toolkit2"},
        "pipeline": pipeline,
        "models": models or [{"name": "models/det.onnx", "hash": "sha256:" + "0" * 64}],
        "calibration": None,
        "budgets": {"inference_p95_ms": 400.0, "memory_mb": 512.0},
        "checksums": "checksums.sha256",
        "created_at": created_at,
    }


@pytest.mark.unit
def test_profiles_cover_both_boards() -> None:
    rk3588 = profile_for("rk3588")
    a733 = profile_for("A733")

    assert rk3588.cpu == "4x A76 + 4x A55"
    assert rk3588.npu_tops == 6.0
    assert rk3588.toolchain == "RKNN-Toolkit2"
    assert rk3588.model_format == "rknn"
    assert a733.cpu == "2x A76 + 6x A55"
    assert a733.npu_tops == 3.0
    assert a733.model_format == "nbg"
    assert rk3588.budgets == {"inference_p95_ms": 400.0, "memory_mb": 512.0}


@pytest.mark.unit
def test_unknown_target_is_data_error() -> None:
    with pytest.raises(ProjectError, match="неизвестный целевой борт"):
        profile_for("orangepi")


@pytest.mark.unit
def test_file_hash_format(tmp_path: Path) -> None:
    target = tmp_path / "model.onnx"
    target.write_bytes(b"onnx")

    digest = file_hash(target)

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


@pytest.mark.unit
def test_manifest_rejects_bad_hash() -> None:
    with pytest.raises(ValueError, match="sha256"):
        ModelEntry(name="models/a.onnx", hash="md5:123")


@pytest.mark.unit
def test_manifest_rejects_duplicate_models() -> None:
    entry = ModelEntry(name="models/a.onnx", hash="sha256:" + "0" * 64)

    with pytest.raises(ValueError, match="повторяются"):
        PackageManifest(
            package_id="PKG-1",
            target="rk3588",
            soc=SocSpec(cpu="4x A76 + 4x A55", npu_tops=6.0, toolchain="RKNN-Toolkit2"),
            pipeline="pipelines/pipe.json",
            models=[entry, entry],
        )


@pytest.mark.unit
def test_manifest_rejects_unknown_fields() -> None:
    payload = _example_manifest()
    payload["extra"] = "не по схеме"

    with pytest.raises(ValueError):
        PackageManifest.model_validate(payload)


@pytest.mark.unit
def test_manifest_hash_ignores_creation_time() -> None:
    first = PackageManifest.model_validate(_example_manifest(created_at="2026-11-20T10:00:00Z"))
    second = PackageManifest.model_validate(_example_manifest(created_at="2026-11-21T10:00:00Z"))

    assert first.computed_hash() == second.computed_hash()
    assert first.seal().manifest_hash == second.seal().manifest_hash


@pytest.mark.unit
def test_manifest_hash_is_stable_across_dict_order() -> None:
    payload = _example_manifest()
    reordered = dict(reversed(list(payload.items())))

    assert manifest_hash(payload) == manifest_hash(reordered)


@pytest.mark.unit
def test_manifest_round_trip_yaml() -> None:
    manifest = PackageManifest.model_validate(_example_manifest()).seal()

    text = manifest.to_yaml()
    restored = PackageManifest.model_validate(yaml.safe_load(text))

    assert restored.manifest_hash == manifest.manifest_hash
    assert restored.payload() == manifest.payload()


@pytest.mark.unit
def test_budgets_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Budgets(inference_p95_ms=0.0)
    with pytest.raises(ValueError):
        Budgets(memory_mb=-1.0)


@pytest.mark.unit
def test_check_profile_detects_mismatch() -> None:
    payload = _example_manifest()
    payload["soc"] = {"cpu": "4x A55", "npu_tops": 1.0, "toolchain": "RKNN-Toolkit2"}
    manifest = PackageManifest.model_validate(payload)

    issues = manifest.check_profile()

    assert any("cpu" in item for item in issues)
    assert any("npu_tops" in item for item in issues)


@pytest.mark.unit
def test_verify_manifest_ok(tmp_path: Path) -> None:
    model = tmp_path / "models" / "det.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"onnx")
    pipeline = tmp_path / "pipelines" / "pipe.json"
    pipeline.parent.mkdir(parents=True)
    pipeline.write_text(json.dumps({"pipeline_id": "P-1"}), encoding="utf-8")

    payload = _example_manifest(
        models=[{"name": "models/det.onnx", "hash": file_hash(model)}],
        pipeline="pipelines/pipe.json",
    )
    manifest = PackageManifest.model_validate(payload).seal()
    (tmp_path / MANIFEST_NAME).write_text(manifest.to_yaml(), encoding="utf-8")

    check = verify_manifest(tmp_path / MANIFEST_NAME, root=tmp_path)

    assert check.ok, check.issues
    assert check.checked_files == 2
    assert "ok" in check.summary()


@pytest.mark.unit
def test_verify_manifest_detects_tampered_file(tmp_path: Path) -> None:
    model = tmp_path / "models" / "det.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"onnx")
    payload = _example_manifest(models=[{"name": "models/det.onnx", "hash": file_hash(model)}])
    manifest = PackageManifest.model_validate(payload).seal()
    (tmp_path / MANIFEST_NAME).write_text(manifest.to_yaml(), encoding="utf-8")
    model.write_bytes(b"tampered")

    check = verify_manifest(tmp_path / MANIFEST_NAME, root=tmp_path)

    assert not check.ok
    assert any("хэш файла" in item for item in check.issues)


@pytest.mark.unit
def test_verify_manifest_reports_missing_file_and_bad_yaml(tmp_path: Path) -> None:
    missing = verify_manifest(tmp_path / "nope.yaml")
    assert not missing.ok
    assert "не найден" in missing.issues[0]

    broken = tmp_path / "broken.yaml"
    broken.write_text("target: [", encoding="utf-8")
    assert not verify_manifest(broken).ok

    wrong = tmp_path / "wrong.yaml"
    wrong.write_text("target: rk3588\n", encoding="utf-8")
    assert not verify_manifest(wrong).ok


@pytest.mark.unit
def test_load_manifest_errors(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="не найден"):
        load_manifest(tmp_path / "manifest.yaml")

    bad = tmp_path / "manifest.yaml"
    bad.write_text("package_id: x\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="не соответствует схеме"):
        load_manifest(bad)
