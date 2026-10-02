from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qkd_docker_orchestrator as orchestrator
from lib.docker.qkd import docker_bootstrap_assets as bootstrap_assets
from lib.docker.qkd import docker_phiotx_lifecycle as lifecycle


def _devices():
    return {
        "EVO1": {"name": "EVO1", "managed": True},
        "EVO2": {"name": "EVO2", "managed": True},
    }


def test_license_capacity_limits_selected_router_count(tmp_path):
    (tmp_path / "only-one.lic").write_text("license-one", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="Licence capacity is 1.*inventory contains 2 managed EVO routers",
    ):
        orchestrator.resolve_license_assignments(
            _devices(),
            license_dir=str(tmp_path),
            required=True,
            interactive=False,
        )


def test_license_directory_assigns_one_unique_file_per_router(tmp_path):
    first = tmp_path / "01.lic"
    second = tmp_path / "02.lic"
    first.write_text("license-one", encoding="utf-8")
    second.write_text("license-two", encoding="utf-8")

    assignments = orchestrator.resolve_license_assignments(
        _devices(),
        license_dir=str(tmp_path),
        required=True,
        interactive=False,
    )

    assert assignments == {
        "EVO1": first.resolve(),
        "EVO2": second.resolve(),
    }


def test_only_filter_keeps_fleet_wide_license_mapping_stable(tmp_path):
    first = tmp_path / "01.lic"
    second = tmp_path / "02.lic"
    first.write_text("license-one", encoding="utf-8")
    second.write_text("license-two", encoding="utf-8")

    assignments = orchestrator.resolve_license_assignments(
        _devices(),
        license_dir=str(tmp_path),
        only=["EVO2"],
        required=True,
        interactive=False,
    )

    assert assignments["EVO1"] == first.resolve()
    assert assignments["EVO2"] == second.resolve()


def test_same_license_file_cannot_be_assigned_twice(tmp_path):
    license_path = tmp_path / "shared.lic"
    license_path.write_text("license", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be assigned twice"):
        orchestrator.resolve_license_assignments(
            _devices(),
            spec=[
                f"EVO1={license_path}",
                f"EVO2={license_path}",
            ],
            required=True,
            interactive=False,
        )


def test_image_archive_can_be_selected_interactively(tmp_path):
    archive = tmp_path / "phiotx.tar.gz"
    archive.write_bytes(b"vendor-image")

    selected = orchestrator.resolve_local_image_archive(
        None,
        {},
        input_fn=lambda _prompt: str(archive),
        interactive=True,
    )

    assert selected == archive.resolve()


def test_single_zip_in_docker_drop_directory_is_selected(tmp_path, monkeypatch):
    bundle = tmp_path / "phiotx.zip"
    bundle.write_bytes(b"customer-supplied")
    monkeypatch.setattr(orchestrator, "DEFAULT_BOOTSTRAP_DIR", tmp_path)

    selected = orchestrator.resolve_bootstrap_bundle(
        None,
        interactive=False,
    )

    assert selected == bundle.resolve()


def test_bootstrap_bundle_requires_manual_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "DEFAULT_BOOTSTRAP_DIR", tmp_path)

    with pytest.raises(
        FileNotFoundError,
        match="Upload it manually.*never downloads vendor images",
    ):
        orchestrator.resolve_bootstrap_bundle(None, interactive=False)


def test_vendor_zip_preparation_finds_image_checksum_and_licenses(
    tmp_path, monkeypatch
):
    image = tmp_path / "phiotx.tar.gz"
    with tarfile.open(image, "w:gz") as archive:
        payload = b"[]"
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    checksum = tmp_path / "phiotx.tar.gz.sha256"
    checksum.write_text(f"{digest}  phiotx.tar.gz\n", encoding="utf-8")
    first = tmp_path / "01.lic"
    second = tmp_path / "02.lic"
    first.write_text("license-one", encoding="utf-8")
    second.write_text("license-two", encoding="utf-8")

    bundle = tmp_path / "customer.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for path in (image, checksum, first, second):
            archive.write(path, arcname=path.name)

    stage = tmp_path / "stage"
    monkeypatch.setattr(bootstrap_assets, "BUNDLE_STAGE", stage)
    prepared = bootstrap_assets.prepare_bootstrap_bundle(bundle)
    try:
        assert prepared["image_archive"].name == "phiotx.tar.gz"
        assert len(prepared["license_files"]) == 2
        assert prepared["extracted_dir"] == stage
    finally:
        bootstrap_assets.cleanup_bootstrap_bundle(prepared)

    assert not stage.exists()


def test_phiotx_up_rejects_missing_licenses_before_router_mutation(monkeypatch):
    touched = []
    monkeypatch.setattr(
        lifecycle,
        "ensure_host_dirs",
        lambda *_args, **_kwargs: touched.append("host-dirs"),
    )

    with pytest.raises(lifecycle.PhiotxLifecycleError, match="missing: EVO2"):
        lifecycle.phiotx_up(
            _devices(),
            {},
            licenses={"EVO1": Path("/tmp/evo1.lic")},
            require_licenses=True,
        )

    assert touched == []


def test_phiotx_up_waits_for_all_nodes_before_pqc(monkeypatch):
    events = []
    devices = _devices()
    settings = {
        name: {"container": name.lower(), "peers": []}
        for name in devices
    }

    monkeypatch.setattr(
        lifecycle,
        "node_settings",
        lambda name, _device, _phiotx: dict(settings[name]),
    )
    monkeypatch.setattr(
        lifecycle,
        "resolve_peers",
        lambda *_args, **_kwargs: [{"name": "peer"}],
    )
    monkeypatch.setattr(
        lifecycle,
        "ensure_host_dirs",
        lambda device, *_args: events.append(("dirs", device["name"])),
    )
    monkeypatch.setattr(
        lifecycle,
        "ensure_image",
        lambda device, *_args, **_kwargs: events.append(("image", device["name"])),
    )
    monkeypatch.setattr(
        lifecycle,
        "ensure_oob_network",
        lambda device, *_args: events.append(("network", device["name"])),
    )
    monkeypatch.setattr(
        lifecycle,
        "start_container",
        lambda device, *_args: events.append(("start", device["name"])),
    )
    monkeypatch.setattr(lifecycle, "install_license", lambda *_args: True)
    monkeypatch.setattr(lifecycle, "install_pki", lambda *_args: True)
    monkeypatch.setattr(
        lifecycle,
        "build_layers",
        lambda *_args: {},
    )
    monkeypatch.setattr(lifecycle, "install_layers", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        lifecycle,
        "setup_pqc",
        lambda device, *_args: events.append(("pqc", device["name"])),
    )
    monkeypatch.setattr(
        lifecycle,
        "verify_node",
        lambda device, *_args: {"device": device["name"]},
    )

    reports = lifecycle.phiotx_up(
        devices,
        {},
        licenses={name: Path(f"/tmp/{name}.lic") for name in devices},
        pki_bundles={name: {"ca": "ca"} for name in devices},
        require_licenses=True,
        require_pki=True,
    )

    last_start = max(index for index, event in enumerate(events) if event[0] == "start")
    first_pqc = min(index for index, event in enumerate(events) if event[0] == "pqc")
    assert first_pqc > last_start
    assert reports == {
        "EVO1": {"device": "EVO1"},
        "EVO2": {"device": "EVO2"},
    }


def test_license_install_uses_persistent_data_path_and_cleans_up(
    tmp_path, monkeypatch
):
    license_path = tmp_path / "node.lic"
    license_path.write_text("node-license", encoding="utf-8")
    commands = []

    monkeypatch.setattr(lifecycle, "_push_files", lambda _device, transfers: None)
    monkeypatch.setattr(
        lifecycle,
        "_run",
        lambda _device, command, _what, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=(
                    lifecycle._sha256_file(license_path)
                    if command.startswith("sha256sum")
                    else ""
                ),
                stderr="",
            )
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "_exec",
        lambda _device, _container, command, _what, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    lifecycle.install_license(
        {"name": "EVO1"},
        {
            "container": "phiotx01",
            "data_dir": "/var/db/phiotx01/data",
        },
        license_path,
    )

    assert "tx_install_license /data/.license-install.lic" in commands
    assert "tx_status -license" in commands
    assert any(
        command == "rm -f /var/db/phiotx01/data/.license-install.lic"
        for command in commands
    )


def test_license_checksum_failure_still_cleans_remote_file(
    tmp_path, monkeypatch
):
    license_path = tmp_path / "node.lic"
    license_path.write_text("node-license", encoding="utf-8")
    commands = []

    monkeypatch.setattr(lifecycle, "_push_files", lambda _device, _transfers: None)
    monkeypatch.setattr(
        lifecycle,
        "_run",
        lambda _device, command, _what, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout="wrong-digest" if command.startswith("sha256sum") else "",
                stderr="",
            )
        ),
    )

    with pytest.raises(
        lifecycle.PhiotxLifecycleError,
        match="licence checksum mismatch",
    ):
        lifecycle.install_license(
            {"name": "EVO1"},
            {
                "container": "phiotx01",
                "data_dir": "/var/db/phiotx01/data",
            },
            license_path,
        )

    assert commands[-1] == "rm -f /var/db/phiotx01/data/.license-install.lic"
