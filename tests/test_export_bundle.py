"""Сборка пакета для борта: файлы, манифест, скрипты и суммы (T11-06, G11-05)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simorph.core.errors import ProjectError
from simorph.core.package import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    PackageConfig,
    build_package,
    check_toolchain,
    convert_model,
    load_manifest,
    package_id_for,
    verify_checksums,
    verify_manifest,
    verify_package,
)


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Модель, конвейер и калибровка для пакета."""
    model = tmp_path / "det_kp.onnx"
    model.write_bytes(b"onnx-stub")
    pipeline = tmp_path / "pipe.json"
    pipeline.write_text(json.dumps({"pipeline_id": "PIPE-CMP-01"}), encoding="utf-8")
    calibration = tmp_path / "temp_v4.json"
    calibration.write_text("{}", encoding="utf-8")
    return model, pipeline, calibration


@pytest.mark.integration
@pytest.mark.parametrize("target", ["rk3588", "a733"])
def test_build_package_for_both_boards(
    inputs: tuple[Path, Path, Path], tmp_path: Path, target: str
) -> None:
    model, pipeline, calibration = inputs
    destination = tmp_path / target

    report = build_package(
        destination,
        PackageConfig(
            target=target,
            models=[model],
            pipeline=pipeline,
            calibration=calibration,
            run_id="9c5960cbbdd74dbe9a45aa0d5d5cb2fa",
            project_hash="sha256:" + "2" * 64,
        ),
    )

    assert report.ok, report.issues
    assert report.package_id == f"PKG-9c5960cb-{target}"
    assert (destination / MANIFEST_NAME).is_file()
    assert (destination / CHECKSUMS_NAME).is_file()
    assert (destination / "INSTALL.md").is_file()
    assert (destination / "models" / "det_kp.onnx").is_file()
    assert (destination / "pipelines" / "pipe.json").is_file()
    assert (destination / "calib" / "temp_v4.json").is_file()
    expected_script = "convert_rknn.py" if target == "rk3588" else "convert_nbg.py"
    assert (destination / "scripts" / expected_script).is_file()

    manifest = load_manifest(destination / MANIFEST_NAME)
    assert manifest.target == target
    assert manifest.run_id == "9c5960cbbdd74dbe9a45aa0d5d5cb2fa"
    assert manifest.models[0].format == "onnx"
    assert manifest.models[0].role == "source"
    assert manifest.calibration == "calib/temp_v4.json"
    assert manifest.manifest_hash == report.manifest_hash
    assert any("конвертация" in item for item in report.warnings)


@pytest.mark.integration
def test_package_checksums_and_manifest_verify(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    model, pipeline, _calibration = inputs
    destination = tmp_path / "pkg"
    build_package(destination, PackageConfig(target="rk3588", models=[model], pipeline=pipeline))

    manifest_check, checksum_check = verify_package(destination)

    assert manifest_check.ok, manifest_check.issues
    assert checksum_check.ok, checksum_check.issues
    assert checksum_check.entries >= 5


@pytest.mark.integration
def test_checksums_detect_tampering(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    model, pipeline, _calibration = inputs
    destination = tmp_path / "pkg"
    build_package(destination, PackageConfig(target="rk3588", models=[model], pipeline=pipeline))
    (destination / "models" / "det_kp.onnx").write_bytes(b"tampered")

    check = verify_checksums(destination)

    assert not check.ok
    assert any("не совпадает" in item for item in check.issues)


@pytest.mark.integration
def test_checksums_detect_unlisted_file(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    model, pipeline, _calibration = inputs
    destination = tmp_path / "pkg"
    build_package(destination, PackageConfig(target="rk3588", models=[model], pipeline=pipeline))
    (destination / "extra.txt").write_text("лишний", encoding="utf-8")

    check = verify_checksums(destination)

    assert not check.ok
    assert any("не учтён" in item for item in check.issues)


@pytest.mark.integration
def test_package_is_deterministic(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    model, pipeline, _calibration = inputs
    hashes = []
    for index in range(2):
        report = build_package(
            tmp_path / f"pkg{index}",
            PackageConfig(
                target="a733",
                models=[model],
                pipeline=pipeline,
                created_at="2026-11-20T00:00:00Z",
            ),
        )
        hashes.append(report.manifest_hash)

    assert hashes[0] == hashes[1]
    assert hashes[0].startswith("sha256:")


@pytest.mark.integration
def test_package_requires_model(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="модель"):
        build_package(tmp_path / "pkg", PackageConfig(target="rk3588"))


@pytest.mark.integration
def test_missing_input_file_is_data_error(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    model, _pipeline, _calibration = inputs
    with pytest.raises(ProjectError, match="не найден"):
        build_package(
            tmp_path / "pkg",
            PackageConfig(target="rk3588", models=[model], pipeline=tmp_path / "nope.json"),
        )


@pytest.mark.integration
def test_generated_pipeline_is_marked(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    model, _pipeline, _calibration = inputs
    destination = tmp_path / "pkg"

    report = build_package(destination, PackageConfig(target="rk3588", models=[model]))

    pipeline = json.loads((destination / "pipelines" / "pipe_nn_rk3588.json").read_text("utf-8"))
    assert pipeline["generated"] is True
    assert pipeline["target"] == "rk3588"
    assert report.manifest is not None
    assert report.manifest.pipeline == "pipelines/pipe_nn_rk3588.json"


@pytest.mark.integration
def test_package_id_for_run() -> None:
    assert package_id_for("rk3588", "9c5960cbbdd74dbe9a45aa0d5d5cb2fa") == "PKG-9c5960cb-rk3588"
    assert package_id_for("a733", None) == "PKG-local000-a733"


@pytest.mark.unit
def test_conversion_without_toolchain_is_reported(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    model, _pipeline, _calibration = inputs
    status = check_toolchain("rk3588")

    report = convert_model(model, tmp_path / "det_kp.rknn", "rk3588")

    assert isinstance(status.available, bool)
    assert report.available == status.available
    if not status.available:
        assert report.ok is False
        assert "недоступен" in report.reason
        assert report.to_dict()["status"] == "unavailable"


@pytest.mark.integration
def test_build_with_convert_does_not_fail_without_toolchain(
    inputs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    model, pipeline, _calibration = inputs
    if check_toolchain("a733").available:  # pragma: no cover - зависит от машины
        pytest.skip("на машине установлен ACUITY: ветка «нет тулчейна» не проверяется")

    report = build_package(
        tmp_path / "pkg",
        PackageConfig(target="a733", models=[model], pipeline=pipeline, convert=True),
    )

    assert report.ok, report.issues
    assert report.conversions
    assert report.conversions[0].ok is False
    assert "недоступен" in report.conversions[0].reason


@pytest.mark.unit
@pytest.mark.parametrize("target", ["rk3588", "a733"])
def test_package_scripts_are_standalone(target: str) -> None:
    from simorph.core.package.scripts import SCRIPT_NAMES, script_for

    text = script_for(target)

    assert SCRIPT_NAMES[target] in text or "convert" in text
    assert "--check" in text
    assert "simorph" not in text
    assert "ImportError" in text or "shutil.which" in text


@pytest.mark.unit
def test_manifest_verify_requires_manifest(tmp_path: Path) -> None:
    check = verify_manifest(tmp_path / MANIFEST_NAME, root=tmp_path)

    assert not check.ok
    assert "не найден" in check.issues[0]
