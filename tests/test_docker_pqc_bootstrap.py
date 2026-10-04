"""Offline regression tests for fleet-wide PhioTX PQC initialization."""

from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.docker.qkd import docker_phiotx_lifecycle as lifecycle

KEM = "ML-KEM-1024"


@pytest.mark.parametrize("source_override", [False, True])
@pytest.mark.parametrize("only", [None, ["EVO1", "EVO2"]])
def test_fleet_installs_transport_for_every_selected_router(pqc_fleet, monkeypatch, source_override, only):
    state = pqc_fleet
    installed = []
    if only:
        state.keypairs.add("phiotx03")
    if source_override:
        for device in state.devices.values():
            device["phiotx"]["source_ip"] = "9.1.1.1"
    else:
        state.phiotx["internal_network"] = {"gateway": "9.1.1.1"}
    monkeypatch.setattr(
        lifecycle, "install_transport",
        lambda device, *_args: installed.append(device["name"]),
    )
    lifecycle.phiotx_up(state.devices, state.phiotx, only=only)
    assert installed == (only or ["EVO1", "EVO2", "EVO3"])


def test_dry_run_does_not_install_transport(pqc_fleet, monkeypatch):
    state = pqc_fleet
    state.phiotx["internal_network"] = {"gateway": "9.1.1.1"}
    installed = []
    monkeypatch.setattr(lifecycle, "install_transport", lambda *_args: installed.append(True))
    lifecycle.phiotx_up(state.devices, state.phiotx, dry_run=True)
    assert installed == []


@pytest.fixture
def pqc_fleet(tmp_path, monkeypatch):
    devices = {
        f"EVO{index}": {
            "name": f"EVO{index}",
            "ip": f"192.0.2.{index}",
            "phiotx": {
                "container": f"phiotx{index:02}",
                "internal_ip": f"9.1.1.{index + 9}",
                "oob_ip": f"198.51.100.{index}",
                "etsi_client": f"sae-{index:03}",
                "peers": [
                    f"EVO{other}" for other in range(1, 4) if other != index
                ],
            },
        }
        for index in range(1, 4)
    }
    state = SimpleNamespace(
        devices=devices,
        phiotx={"pqc": KEM, "peer_port": 9002},
        keypairs=set(),
        public_keys=set(),
        events=[],
        commands=[],
        failure=None,
        save_generated_key=True,
        save_imported_key=True,
    )

    def status(container):
        rows = ["PQC pub keys:"]
        for peer in devices.values():
            name = peer["phiotx"]["container"]
            if name != container:
                detail = "present" if (container, name) in state.public_keys else "missing"
                rows.append(f"  '{name}', {detail}, KEM '{KEM}'")
        detail = "present" if container in state.keypairs else "missing"
        rows.extend(["", f"  '{KEM}', {detail}"])
        return "\n".join(rows)

    def shell(_device, command, **_kwargs):
        state.commands.append(command)
        parts = shlex.split(command)
        assert parts[:2] == ["docker", "exec"]
        container = parts[2]
        args = parts[3:]
        if args == ["tx_status", "-pqc"]:
            return SimpleNamespace(returncode=0, stdout=status(container), stderr="")
        assert "-y" not in args
        if args[0] == "tx_generate_pqc_keypair":
            assert args == ["tx_generate_pqc_keypair", "-m", KEM]
            state.events.append(("generate", container))
            if state.failure == "generate":
                return SimpleNamespace(returncode=1, stdout="generation failed", stderr="")
            if state.save_generated_key:
                state.keypairs.add(container)
        else:
            assert args[0] == "tx_get_pqc_public_key"
            peer = args[args.index("-p") + 1]
            assert args == ["tx_get_pqc_public_key", "-p", peer, "-m", KEM]
            state.events.append(("fetch", container, peer))
            if state.failure == "fetch" or peer not in state.keypairs:
                return SimpleNamespace(
                    returncode=1,
                    stdout=f"Error: no PQC pub key for KEM '{KEM}', peer '{peer}'",
                    stderr="",
                )
            if state.save_imported_key:
                state.public_keys.add((container, peer))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def install_layers(device, _settings, paths, *, dry_run=False):
        if lifecycle.LAYER_ETSI in paths:
            etsi_layer = yaml.safe_load(paths[lifecycle.LAYER_ETSI].read_text())
            method = etsi_layer["etsi_service"]["keygen_method"]
            if method.startswith("pqc("):
                assert all(
                    (device["phiotx"]["container"], peer["phiotx"]["container"])
                    in state.public_keys
                    for peer in state.devices.values()
                    if peer != device
                )
                state.events.append(("application-pqc", device["phiotx"]["container"]))
        if lifecycle.LAYER_PEER not in paths:
            return True
        peer_layer = yaml.safe_load(paths[lifecycle.LAYER_PEER].read_text())
        enabled = any(peer.get("pqc") for peer in peer_layer["peers"])
        state.events.append(("layer", device["phiotx"]["container"], enabled, dry_run))
        if enabled and not dry_run:
            container = device["phiotx"]["container"]
            assert container in state.keypairs
            assert all(
                (container, peer["name"]) in state.public_keys
                for peer in peer_layer["peers"]
            )
        return True

    monkeypatch.setattr(lifecycle, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lifecycle, "pyez_shell_cmd", shell)
    for helper in (
        "ensure_host_dirs",
        "ensure_image",
        "ensure_oob_network",
        "install_license",
        "install_pki",
    ):
        monkeypatch.setattr(lifecycle, helper, lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        lifecycle,
        "start_container",
        lambda device, *_args: state.events.append(
            ("start", device["phiotx"]["container"])
        ),
    )
    monkeypatch.setattr(lifecycle, "install_layers", install_layers)
    monkeypatch.setattr(
        lifecycle, "verify_node", lambda device, *_args: {"device": device["name"]}
    )
    return state


def test_application_pqc_activates_after_fleet_exchange(pqc_fleet):
    state = pqc_fleet
    state.phiotx["keygen_mode"] = "pqc"
    state.phiotx["etsi"] = {"keygen_method": "pqc(ML-KEM-1024)"}
    lifecycle.phiotx_up(state.devices, state.phiotx)
    applications = [event for event in state.events if event[0] == "application-pqc"]
    assert len(applications) == 3
    first_application = next(i for i, event in enumerate(state.events) if event[0] == "application-pqc")
    assert all(i < first_application for i, event in enumerate(state.events) if event[0] == "fetch")


def test_missing_status_generates_and_verifies_the_local_keypair(pqc_fleet):
    state = pqc_fleet
    device = state.devices["EVO1"]
    settings = lifecycle.node_settings("EVO1", device, state.phiotx)

    assert lifecycle.ensure_pqc_keypair(device, settings, state.phiotx)

    assert state.keypairs == {"phiotx01"}
    assert state.events == [("generate", "phiotx01")]
    assert state.commands.count("docker exec phiotx01 tx_status -pqc") == 2


def test_existing_local_keypair_is_reused_when_peer_public_keys_are_missing(pqc_fleet):
    state = pqc_fleet
    state.keypairs.add("phiotx01")
    device = state.devices["EVO1"]
    settings = lifecycle.node_settings("EVO1", device, state.phiotx)

    assert lifecycle.ensure_pqc_keypair(device, settings, state.phiotx)

    assert not state.events


def test_successful_generation_without_a_stored_keypair_is_rejected(pqc_fleet):
    state = pqc_fleet
    state.save_generated_key = False
    device = state.devices["EVO1"]
    settings = lifecycle.node_settings("EVO1", device, state.phiotx)

    with pytest.raises(lifecycle.PhiotxLifecycleError, match="not present after generation"):
        lifecycle.ensure_pqc_keypair(device, settings, state.phiotx)


def test_successful_fetch_without_a_stored_public_key_is_rejected(pqc_fleet):
    state = pqc_fleet
    state.keypairs.update(("phiotx01", "phiotx02", "phiotx03"))
    state.save_imported_key = False
    device = state.devices["EVO1"]
    settings = lifecycle.node_settings("EVO1", device, state.phiotx)
    settings["peers"] = [{"name": "phiotx02"}]

    with pytest.raises(lifecycle.PhiotxLifecycleError, match="not present after import"):
        lifecycle.setup_pqc(device, settings, state.phiotx)


def test_fleet_generates_all_pairs_and_imports_all_keys_before_pqc_activation(pqc_fleet):
    state = pqc_fleet

    reports = lifecycle.phiotx_up(state.devices, state.phiotx)

    generations = [i for i, event in enumerate(state.events) if event[0] == "generate"]
    fetches = [i for i, event in enumerate(state.events) if event[0] == "fetch"]
    activations = [
        i for i, event in enumerate(state.events)
        if event[0] == "layer" and event[2]
    ]
    starts = [i for i, event in enumerate(state.events) if event[0] == "start"]
    assert len(generations) == 3
    assert len(fetches) == 6
    assert len(activations) == 3
    assert max(starts) < min(generations)
    assert max(generations) < min(fetches)
    assert max(fetches) < min(activations)
    assert len([event for event in state.events if event[0] == "layer" and not event[2]]) == 3
    assert set(reports) == set(state.devices)
    assert state.phiotx["pqc"] == KEM


def test_ready_fleet_retry_preserves_pqc_and_reuses_keypairs(pqc_fleet):
    state = pqc_fleet
    lifecycle.phiotx_up(state.devices, state.phiotx)
    state.events.clear()

    lifecycle.phiotx_up(state.devices, state.phiotx)

    assert not any(event[0] == "generate" for event in state.events)
    assert all(event[2] for event in state.events if event[0] == "layer")


@pytest.mark.parametrize("failure", ["generate", "fetch"])
def test_pqc_failures_stop_before_overlay_activation(pqc_fleet, failure):
    state = pqc_fleet
    state.failure = failure

    with pytest.raises(lifecycle.PhiotxLifecycleError):
        lifecycle.phiotx_up(state.devices, state.phiotx)

    assert not any(event[0] == "layer" and event[2] for event in state.events)


def test_dry_run_previews_pqc_without_generating_or_importing_keys(pqc_fleet):
    state = pqc_fleet

    lifecycle.phiotx_up(state.devices, state.phiotx, dry_run=True)

    assert not state.commands
    assert all(
        event[2] and event[3] for event in state.events if event[0] == "layer"
    )


def test_status_collection_uses_supported_hive_peer_command(monkeypatch):
    commands = []
    monkeypatch.setattr(
        lifecycle,
        "_exec",
        lambda _device, _container, command, *_args, **_kwargs: (
            commands.append(command) or SimpleNamespace(stdout="status")
        ),
    )
    monkeypatch.setattr(
        lifecycle, "_run", lambda *_args, **_kwargs: SimpleNamespace(stdout="listeners")
    )
    report = lifecycle.verify_node(
        {"name": "EVO1"}, {"container": "phiotx01"}, {"pqc": KEM}
    )

    assert "txh -P -c no" in commands
    assert "tx_status -peers" not in commands
    assert report["peers"] == "status"
