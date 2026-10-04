import copy
import json

import pytest

import qkd_docker_orchestrator as orchestrator
from lib.docker.qkd.docker_keygen import resolve_keygen, save_keygen
from lib.docker.qkd.docker_phiotx_lifecycle import render_etsi_layer


def config():
    return {"pqc": "ML-KEM-1024", "etsi": {"keygen_method": "bulk", "port": 443}}


def test_bulk_preserves_original_etsi_payload():
    original = config()
    snapshot = copy.deepcopy(original)
    settings = {"internal_ip": "9.1.1.10", "sae_id": "sae-001"}
    assert render_etsi_layer(settings, resolve_keygen(original, "bulk")) == render_etsi_layer(settings, original)
    assert original == snapshot


@pytest.mark.parametrize("command", ["create", "bootstrap", "deploy", "phiotx-up"])
@pytest.mark.parametrize("mode", ["bulk", "pqc", "hybrid"])
def test_parser_mode_selection(command, mode):
    assert orchestrator.build_parser().parse_args([command, "--keygen-mode", mode]).keygen_mode == mode


def test_pqc_sets_service_and_client_without_fallback():
    selected = resolve_keygen(config(), "pqc")
    payload = render_etsi_layer({"internal_ip": "9.1.1.10", "sae_id": "sae-001"}, selected)
    assert payload["etsi_service"]["keygen_method"] == "pqc(ML-KEM-1024)"
    assert payload["clients"][0]["keygen_method"] == "pqc(ML-KEM-1024)"


def test_mode_persistence_and_override(tmp_path):
    path = tmp_path / "keygen_mode.json"
    save_keygen(resolve_keygen(config(), "pqc"), path)
    assert json.loads(path.read_text())["mode"] == "pqc"
    assert resolve_keygen(config(), state_path=path)["keygen_mode"] == "pqc"
    assert resolve_keygen(config(), "bulk", path)["keygen_mode"] == "bulk"
    assert resolve_keygen({**config(), "keygen_mode": "bulk"}, state_path=path)["keygen_mode"] == "bulk"


def test_invalid_method_is_not_silently_bulk():
    with pytest.raises(ValueError, match="Unsupported"):
        resolve_keygen({"etsi": {"keygen_method": ["pqc", "bulk"]}})


def test_hybrid_without_qkd_configuration_fails_before_mutation():
    with pytest.raises(ValueError, match="qkd_simulator"):
        resolve_keygen(config(), "hybrid")


def test_pqc_without_peer_provisioning_fails():
    with pytest.raises(ValueError, match="peer provisioning"):
        resolve_keygen({"keygen_algorithm": "ML-KEM-1024"}, "pqc")


def test_create_propagates_cli_mode_to_every_generated_surface(tmp_path, monkeypatch):
    devices = {"EVO1": {"name": "EVO1"}}
    monkeypatch.setattr(orchestrator, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(orchestrator, "resolve_inventory_path", lambda _: tmp_path / "lab.yaml")
    monkeypatch.setattr(orchestrator, "load_docker_inventory",
                        lambda _: {"phiotx": config(), "devices": list(devices.values())})
    monkeypatch.setattr(orchestrator, "build_full_inventory", lambda **kwargs: None)
    monkeypatch.setattr(orchestrator, "build_runtime_qkd_policy", lambda **kwargs: None)
    monkeypatch.setattr(orchestrator, "load_qkd_policy_template", lambda: {})
    monkeypatch.setattr(orchestrator, "load_docker_runtime_devices", lambda: devices)
    generated = []
    monkeypatch.setattr(orchestrator, "build_onbox_artifacts",
                        lambda records, phiotx: generated.append(phiotx))
    monkeypatch.setattr(orchestrator, "build_layers",
                        lambda name, device, records, phiotx: generated.append(phiotx))
    args = orchestrator.build_parser().parse_args(["create", "--skip-pki", "--keygen-mode", "pqc"])
    assert orchestrator.cmd_create(args) == 0
    assert len(generated) == 2
    assert all(value["etsi"]["keygen_method"] == "pqc(ML-KEM-1024)" for value in generated)
    assert json.loads((tmp_path / "keygen_mode.json").read_text())["mode"] == "pqc"
