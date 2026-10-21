from pathlib import Path

import yaml

from lib.kme import onbox_clean


def test_onbox_clean_dry_run_targets_managed_devices_and_database(tmp_path: Path, monkeypatch, capsys):
    config = {
        "ssh": {"local": True},
        "paths": {"project_dir": "/root/etsi_kme_docker_etsi14_kme"},
        "docker": {"compose_file": "config/kme/onbox-postgres-compose.yaml"},
    }
    config_path = tmp_path / "onbox.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(
        onbox_clean,
        "load_runtime_devices",
        lambda: {
            "EVO1": {
                "ip": "10.38.97.218",
                "auth": {"username": "root", "password": "unused"},
                "metadata": {"kme_onbox": {"network": "evokme_oob"}},
            },
            "EVO2": {
                "ip": "10.38.97.228",
                "auth": {"username": "root", "password": "unused"},
                "metadata": {"kme_onbox": {"network": "evokme_oob"}},
            },
            "UNMANAGED": {"managed": False},
        },
    )
    calls = []
    monkeypatch.setattr(
        onbox_clean,
        "remote_run",
        lambda config, command, **kwargs: calls.append((command, kwargs)),
    )

    onbox_clean.run_onbox_clean(config_path, dry_run=True)

    output = capsys.readouterr().out
    assert "EVO1" in output
    assert "EVO2" in output
    assert "UNMANAGED" not in output
    assert "--volumes" in output
    assert calls[0][1]["dry_run"] is True


def test_onbox_clean_requires_force_for_live_cleanup(tmp_path: Path):
    config_path = tmp_path / "onbox.yaml"
    config_path.write_text("{}\n", encoding="utf-8")

    try:
        onbox_clean.run_onbox_clean(config_path)
    except RuntimeError as exc:
        assert "--force" in str(exc)
    else:
        raise AssertionError("Expected live cleanup to require --force")