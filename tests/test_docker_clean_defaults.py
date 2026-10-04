from types import SimpleNamespace

import yaml

from lib.docker.qkd import docker_clean


def test_runtime_clean_retains_shared_docker_targets(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime_docker"
    runtime.mkdir()
    (runtime / "devices.yaml").write_text(yaml.safe_dump({
        "devices": {"EVO1": {"ip": "192.0.2.1", "evo": True,
                             "phiotx": {"container": "phiotx01"}}},
    }))
    defaults = {
        "image": "phiotx:test", "host_data_base": "/var/db",
        "oob_network": {"name": "phiotx_oob"},
        "internal_network": {"name": "jnpr_cntrz_net"},
    }
    monkeypatch.setattr(docker_clean, "DOCKER_RUNTIME_DIR", runtime)
    monkeypatch.setattr(docker_clean, "load_clean_inventory", lambda _: (
        tmp_path / "inventory.yaml",
        {"EVO1": {"_phiotx_defaults": defaults, "phiotx": {"container": "phiotx01"}}},
    ))
    monkeypatch.setattr(docker_clean, "load_inventory_base", lambda: {})
    captured = []
    monkeypatch.setattr(docker_clean, "clean_device",
                        lambda name, device, **kwargs: captured.append(device) or True)
    monkeypatch.setattr(docker_clean, "clean_runtime", lambda: None)
    monkeypatch.setattr(docker_clean, "clean_certs", lambda: None)
    args = SimpleNamespace(
        local_only=False, pki=True, full_macsec=True, inventory="inventory.yaml",
        username="root", password="test", only=None,
    )
    docker_clean.handle_clean(args)
    assert captured[0]["_phiotx_defaults"] == defaults
    assert captured[0]["phiotx"]["container"] == "phiotx01"
