from pathlib import Path

import yaml

from lib.kme import db_init


def test_remote_run_uses_local_shell_when_configured(monkeypatch):
    calls = []

    def fake_run_local(command, **kwargs):
        calls.append((command, kwargs))
        return None

    monkeypatch.setattr(db_init, "run_local", fake_run_local)

    db_init.remote_run({"ssh": {"local": True}}, "docker ps", dry_run=True)

    assert calls[0][0] == ["/bin/sh", "-c", "docker ps"]
    assert calls[0][1]["dry_run"] is True


def test_db_init_recreates_local_volume_before_schema(tmp_path: Path, monkeypatch):
    config = {
        "ssh": {"local": True},
        "paths": {"project_dir": "/tmp/kme-db"},
        "docker": {"compose_file": "compose.yaml"},
        "database": {
            "service_name": "qkd-postgres",
            "container_name": "etsi14-postgres",
            "username": "db_user",
            "db_name": "key_store",
            "volume_name": "etsi14-postgres-data",
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        db_init,
        "remote_run",
        lambda config, command, **kwargs: calls.append((command, kwargs.get("input_text"))),
    )

    db_init.run_db_init(
        config_path=config_path,
        dry_run=True,
        recreate=True,
        recreate_volume=True,
        content_type="TEXT",
    )

    assert "down --volumes" in calls[0][0]
    assert "up -d qkd-postgres" in calls[1][0]
    assert "DROP TABLE IF EXISTS keys" in calls[3][1]