"""Offline safety tests for the greenfield PhioTX Docker bootstrap.

These tests do not contact routers. They verify the local decisions that must
be correct before the orchestrator mutates an EVO router: licence capacity and
allocation, manual asset handling, bootstrap validation, lifecycle ordering,
and cleanup after licence-transfer failures.
"""

from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import shlex
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qkd_docker_orchestrator as orchestrator
from lib.docker.qkd import docker_bootstrap_assets as bootstrap_assets
from lib.docker.qkd import docker_clean
from lib.docker.qkd import docker_phiotx_lifecycle as lifecycle
from lib.docker.qkd import docker_provisioning as provisioning


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


def test_bootstrap_rejects_multiple_zip_bundles_without_explicit_selection(
    tmp_path, monkeypatch
):
    (tmp_path / "phiotx-lab.zip").write_bytes(b"lab-bundle")
    (tmp_path / "phiotx-production.zip").write_bytes(b"production-bundle")
    monkeypatch.setattr(orchestrator, "DEFAULT_BOOTSTRAP_DIR", tmp_path)

    with pytest.raises(
        ValueError,
        match="Expected exactly one customer-supplied PhioTX ZIP.*--bundle",
    ):
        orchestrator.resolve_bootstrap_bundle(None, interactive=False)


def test_bundle_name_is_resolved_inside_the_docker_drop_directory(
    tmp_path, monkeypatch
):
    bundle = tmp_path / "hpe.zip"
    bundle.write_bytes(b"customer-supplied")
    monkeypatch.setattr(orchestrator, "DEFAULT_BOOTSTRAP_DIR", tmp_path)

    selected = orchestrator.resolve_bootstrap_bundle(
        "hpe.zip",
        interactive=False,
    )

    assert selected == bundle.resolve()


def test_unknown_bundle_name_reports_every_searched_location(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(orchestrator, "DEFAULT_BOOTSTRAP_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match="Searched:.*never downloads"):
        orchestrator.resolve_bootstrap_bundle("missing.zip", interactive=False)


def test_operator_can_choose_among_several_bundles_at_runtime(
    tmp_path, monkeypatch
):
    first = tmp_path / "hpe.zip"
    second = tmp_path / "other-vendor.zip"
    first.write_bytes(b"hpe-bundle")
    second.write_bytes(b"other-bundle")
    monkeypatch.setattr(orchestrator, "DEFAULT_BOOTSTRAP_DIR", tmp_path)

    by_number = orchestrator.resolve_bootstrap_bundle(
        None,
        input_fn=lambda _prompt: "1",
        interactive=True,
    )
    by_name = orchestrator.resolve_bootstrap_bundle(
        None,
        input_fn=lambda _prompt: "other-vendor.zip",
        interactive=True,
    )

    assert by_number == first.resolve()
    assert by_name == second.resolve()


def test_bootstrap_bundle_requires_manual_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "DEFAULT_BOOTSTRAP_DIR", tmp_path)

    with pytest.raises(
        FileNotFoundError,
        match="Upload it manually.*never downloads vendor images",
    ):
        orchestrator.resolve_bootstrap_bundle(None, interactive=False)


def test_clean_loads_the_selected_evo_inventory(tmp_path):
    inventory = tmp_path / "evo.yaml"
    inventory.write_text(
        """
phiotx:
  image: phiotx:test
devices:
  - name: EVO1
    evo: true
    ip: 192.0.2.1
  - name: EVO2
    evo: true
    ip: 192.0.2.2
""",
        encoding="utf-8",
    )

    path, devices = docker_clean.load_clean_inventory(inventory)

    assert path == inventory.resolve()
    assert sorted(devices) == ["EVO1", "EVO2"]


def test_clean_uses_cli_credentials_with_inventory_before_local_cleanup(
    tmp_path, monkeypatch
):
    inventory = tmp_path / "evo.yaml"
    inventory.write_text(
        """
phiotx:
  image: phiotx:test
devices:
  - name: EVO1
    evo: true
    ip: 192.0.2.1
""",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime_docker"
    events = []

    monkeypatch.setattr(docker_clean, "DOCKER_RUNTIME_DIR", runtime)
    monkeypatch.setattr(docker_clean, "load_inventory_base", lambda: {})
    monkeypatch.setattr(
        docker_clean,
        "clean_device",
        lambda name, device, full_macsec=False: (
            events.append(
                (
                    "remote",
                    name,
                    device["auth"]["username"],
                    device["auth"]["password"],
                )
            )
            or True
        ),
    )
    monkeypatch.setattr(
        docker_clean,
        "clean_runtime",
        lambda: events.append(("local",)),
    )

    docker_clean.handle_clean(
        SimpleNamespace(
            local_only=False,
            pki=False,
            full_macsec=False,
            continue_on_failure=False,
            inventory=str(inventory),
            username="root",
            password="secret",
        )
    )

    assert events == [
        ("remote", "EVO1", "root", "secret"),
        ("local",),
    ]


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


@pytest.fixture
def vendor_pki_install(tmp_path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"test-ca")
    identities = {}
    for role, store in (("qxc", "qxc"), ("etsi", "etsi"), ("sae", None)):
        key = tmp_path / f"{role}.key"
        crt = tmp_path / f"{role}.crt"
        key.write_bytes(f"{role}-key".encode("ascii"))
        crt.write_bytes(f"{role}-certificate".encode("ascii"))
        identities[role] = {"store": store, "key": key, "crt": crt}
    state = SimpleNamespace(
        bundle={"ca": ca, "identities": identities},
        host_files={},
        container_files={},
        ca_copies=[],
        installed=[],
        fail_store=None,
    )

    def push_files(_device, transfers):
        for source, destination in transfers:
            state.host_files[destination] = Path(source).read_bytes()

    def docker(_device, command, _what, **_kwargs):
        operation, source, target = shlex.split(command)
        assert operation == "cp"
        container, _, destination = target.partition(":")
        assert container == "phiotx01"
        state.container_files[destination] = state.host_files[source]
        if destination == "/tmp/pki/ca.pem":
            state.ca_copies.append(state.container_files[destination])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def run(_device, command, _what, **_kwargs):
        parts = shlex.split(command)
        if parts[:2] == ["rm", "-f"]:
            for path in parts[2:]:
                state.host_files.pop(path, None)
        else:
            assert parts[:2] == ["mkdir", "-p"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def execute(_device, container, command, _what, **_kwargs):
        assert container == "phiotx01"
        parts = shlex.split(command)
        if parts[0] == "tx_install_private_key":
            key_path = parts[parts.index("-key") + 1]
            state.container_files.pop(key_path)
        elif parts[0] == "tx_install_crt":
            store = parts[parts.index("-pki") + 1]
            ca_path = parts[parts.index("-ca") + 1]
            crt_path = parts[parts.index("-crt") + 1]
            assert state.container_files[ca_path] == b"test-ca"
            assert state.container_files[crt_path] == f"{store}-certificate".encode("ascii")
            if store == state.fail_store:
                raise lifecycle.PhiotxLifecycleError(f"Simulated {store} install failure")
            state.container_files.pop(ca_path)
            state.container_files.pop(crt_path)
            state.installed.append(store)
        elif parts == ["rm", "-rf", "/tmp/pki"]:
            state.container_files.clear()
        else:
            assert parts == ["mkdir", "-p", "/tmp/pki"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(lifecycle, "_push_files", push_files)
    monkeypatch.setattr(lifecycle, "_docker", docker)
    monkeypatch.setattr(lifecycle, "_run", run)
    monkeypatch.setattr(lifecycle, "_exec", execute)
    return state


def test_pki_replenishes_ca_input_consumed_by_each_vendor_install(
    vendor_pki_install,
):
    state = vendor_pki_install

    assert lifecycle.install_pki(
        {"name": "EVO1"}, {"container": "phiotx01"}, state.bundle
    )

    assert state.installed == ["qxc", "etsi"]
    assert state.ca_copies == [b"test-ca", b"test-ca"]
    assert state.host_files == {}
    assert state.container_files == {}
    assert state.bundle["ca"].read_bytes() == b"test-ca"


@pytest.mark.parametrize("store", ["qxc", "etsi"])
def test_pki_install_failure_cleans_host_and_container_staging(
    vendor_pki_install, store
):
    state = vendor_pki_install
    state.fail_store = store

    with pytest.raises(lifecycle.PhiotxLifecycleError, match=f"Simulated {store}"):
        lifecycle.install_pki(
            {"name": "EVO1"}, {"container": "phiotx01"}, state.bundle
        )

    assert state.host_files == {}
    assert state.container_files == {}
    for identity in state.bundle["identities"].values():
        assert identity["key"].is_file()
        assert identity["crt"].is_file()


def test_external_ca_cert_paths_use_staged_sae_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(provisioning, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(
        provisioning,
        "load_docker_runtime_pki_profile",
        lambda: {"pki": {"profile": "external_ca"}},
    )

    paths = provisioning.resolve_cert_paths_for_device(
        "EVO1",
        {"qkd": {"sae_id": "sae-001"}},
    )

    assert paths["cert"] == tmp_path / "EVO1" / "pki" / "sae.crt"
    assert paths["key"] == tmp_path / "EVO1" / "pki" / "sae.key"
    assert paths["ca"] == tmp_path / "EVO1" / "pki" / "ca.pem"


def test_plain_tar_upload_keeps_plain_tar_remote_name(tmp_path, monkeypatch):
    image = tmp_path / "phiotx.tar"
    image.write_bytes(b"docker-image")
    commands = []
    transfers = []

    monkeypatch.setattr(
        lifecycle,
        "_docker",
        lambda _device, args, _what, **_kwargs: SimpleNamespace(
            returncode=1 if args.startswith("image inspect") else 0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "_push_files",
        lambda _device, items: transfers.extend(items),
    )
    monkeypatch.setattr(
        lifecycle,
        "_run",
        lambda _device, command, _what, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=(
                    lifecycle._sha256_file(image)
                    if command.startswith("sha256sum")
                    else ""
                ),
                stderr="",
            )
        ),
    )

    lifecycle.ensure_image(
        {"name": "EVO1"},
        {
            "image": "phiotx:test",
            "image_archive": "/var/tmp/vendor-image.tar.gz",
        },
        local_archive=image,
    )

    assert transfers == [(image.resolve(), "/var/tmp/phiotx.tar")]
    assert "docker load -i /var/tmp/phiotx.tar" in commands
    assert not any("gzip -dc" in command for command in commands)


def test_provisioning_returns_failed_devices(monkeypatch):
    devices = {
        "EVO1": {
            "name": "EVO1",
            "platform": "ptx",
            "links": [{"peer": "EVO2"}],
        }
    }
    monkeypatch.setattr(
        provisioning,
        "load_docker_runtime_inventory",
        lambda: ({}, devices, {}),
    )
    monkeypatch.setattr(provisioning, "load_platform", lambda _platform: {})
    monkeypatch.setattr(
        provisioning,
        "build_device_config",
        lambda **_kwargs: ["set system host-name evo1"],
    )
    monkeypatch.setattr(
        provisioning,
        "push_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )

    failed = provisioning.run_provisioning(None, devices=devices)

    assert failed == ["EVO1"]
