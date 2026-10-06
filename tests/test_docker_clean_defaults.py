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


def test_parse_scripts_using_users_finds_event_and_op_references():
    display_set_output = "\n".join(
        [
            "set event-options event-script file old_timer.py python-script-user etsi_user",
            "set system scripts op file old_status.py python-script-user etsi_peer_view",
            "set event-options event-script file unrelated.py python-script-user root",
        ]
    )

    event_scripts, op_scripts = docker_clean.parse_scripts_using_users(
        display_set_output,
        target_users={"etsi_user", "etsi_peer_view"},
    )

    assert event_scripts == {"old_timer.py": {"etsi_user"}}
    assert op_scripts == {"old_status.py": {"etsi_peer_view"}}


def test_parse_scripts_using_users_handles_quoted_filenames_and_deduplicates():
    display_set_output = "\n".join(
        [
            'set event-options event-script file "old timer.py" python-script-user etsi_user',
            'set event-options event-script file "old timer.py" python-script-user etsi_user',
        ]
    )

    event_scripts, op_scripts = docker_clean.parse_scripts_using_users(
        display_set_output,
        target_users={"etsi_user"},
    )

    assert event_scripts == {"old timer.py": {"etsi_user"}}
    assert op_scripts == {}


def test_cleanup_plan_preserves_users_referenced_by_unmanaged_scripts():
    event_scripts = {
        "phiotx_qkd_onbox.py": {"etsi_user"},
        "site_health_check.py": {"etsi_user"},
    }
    op_scripts = {"phiotx_qkd_onbox.py": {"etsi_user"}}

    event_to_delete, op_to_delete, preserved_users = (
        docker_clean.plan_managed_script_cleanup(
            event_scripts,
            op_scripts,
            managed_scripts={"phiotx_qkd_onbox.py", "qkd_onbox.py"},
        )
    )

    assert event_to_delete == ["phiotx_qkd_onbox.py"]
    assert op_to_delete == ["phiotx_qkd_onbox.py"]
    assert preserved_users == {"etsi_user"}
