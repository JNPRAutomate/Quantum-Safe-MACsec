import json
from pathlib import Path

import pytest

import qbt_orchestrator as qbt
from lib.qbt import macsec
from lib.qbt.runtime_builder import qbt_onbox_path


INVENTORY = Path(__file__).resolve().parents[1] / "config/inventory/input/lab_vmm.yaml"


def use_standalone_fixture(monkeypatch, tmp_path):
    artifact = tmp_path / "qbt_onbox.py"
    artifact.write_text("print('standalone')\n", encoding="utf-8")
    monkeypatch.setattr(macsec, "qbt_onbox_path", lambda: artifact)


def test_qbt_runtime_uses_qbt_debug_log_prefix():
    source = qbt_onbox_path().read_text(encoding="utf-8")
    assert 'link_log_file = f"{LOG_DIR}/qbt_debug_' in source
    assert "qkd_debug_" not in source
    assert 'EARLY_SCRIPT_VERSION = "qbt_ver1.0"' in source
    assert 'version=ver3.3.4.1' not in source
    assert "if len(cak_name) not in (32, 62, 64):" in source
    assert not any(line.rstrip(" \t") != line for line in source.splitlines())


def test_source_inventory_selects_only_evo_pair():
    devices = macsec.load_target_devices(INVENTORY)

    assert set(devices) == {"EVO1", "EVO2"}
    assert devices["EVO1"]["ip"] == "10.38.97.218"
    assert devices["EVO2"]["ip"] == "10.38.97.228"
    assert devices["EVO1"]["sae_id"] == "sae-001"
    assert devices["EVO2"]["sae_id"] == "sae-002"
    assert devices["EVO1"]["link"]["interface"] == "et-0/0/1"
    assert devices["EVO2"]["link"]["interface"] == "et-0/0/1"
    assert devices["EVO1"]["link"]["peer"] == "EVO2"
    assert devices["EVO2"]["link"]["peer"] == "EVO1"
    assert devices["EVO1"]["link"]["role"] == "master"
    assert devices["EVO2"]["link"]["role"] == "slave"
    assert devices["EVO1"]["kme_ip"] == "9.1.1.10"
    assert devices["EVO2"]["kme_ip"] == "9.1.1.11"


def test_generated_runtime_inventory_is_not_accepted_as_input(tmp_path):
    generated = tmp_path / "devices.yaml"
    generated.write_text("devices:\n  EVO1: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="generated config/runtime files are not valid inputs"):
        macsec.load_target_devices(generated)


def test_python_script_user_is_configured_only_for_event_script():
    commands = macsec._junos_commands(
        "ssh-ed25519 AAAA fixture",
        None,
        {"execution_interval_seconds": 60},
        "etsi_user",
        "super-user",
        "et-0/0/1",
    )

    assert "set system scripts op file qbt_onbox.py" in commands
    assert (
        "set event-options event-script file qbt_onbox.py "
        "python-script-user etsi_user"
    ) in commands
    assert not any(
        command.startswith("set system scripts op file ")
        and "python-script-user" in command
        for command in commands
    )


def test_seed_key_name_matches_the_runtime_derivation():
    import ast
    import hashlib

    source = (Path(__file__).resolve().parents[1] / "artifacts/qbt_onbox.py").read_text()
    wanted = {"ckn_from_key_id", "bootstrap_seed_key_id"}
    functions = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"hashlib": hashlib}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "qbt_onbox", "exec"), namespace)

    assert macsec.bootstrap_seed_key_name() == namespace["ckn_from_key_id"](
        namespace["bootstrap_seed_key_id"]("QKD_QBT_EVO", 0)
    )


def test_reset_commands_remove_only_the_qbt_macsec_objects():
    assert macsec._reset_commands("et-0/0/1") == [
        "delete security macsec interfaces et-0/0/1",
        "delete security macsec connectivity-association QBT_EVO",
        "delete security authentication-key-chains key-chain QKD_QBT_EVO",
    ]


def test_park_runtime_state_moves_files_and_never_deletes():
    seen = []

    def fake_run(client, command):
        seen.append(command)
        return "/var/home/etsi_user/qbt-state/reset-backup-1"

    macsec._park_runtime_state(object(), fake_run, "etsi_user")

    assert "mv " in seen[0] and "rm " not in seen[0]
    assert "qkd_db_*.json" in seen[0]


def test_runtime_profiles_are_derived_from_inventory(monkeypatch, tmp_path):
    use_standalone_fixture(monkeypatch, tmp_path)
    runtime_root = tmp_path / "runtime"

    profiles = macsec.create_runtime_profiles(
        runtime_root=runtime_root,
        inventory_path=INVENTORY,
    )

    assert set(profiles) == {"EVO1", "EVO2"}
    assert {directory.name for directory in runtime_root.iterdir()} == {"EVO1", "EVO2"}
    evo1_config = json.loads(
        (profiles["EVO1"] / "qbt_onbox_config.json").read_text(encoding="utf-8")
    )
    evo2_inventory = json.loads(
        (profiles["EVO2"] / "qbt_onbox_inventory.json").read_text(encoding="utf-8")
    )
    assert evo1_config["local_sae"] == "sae-001"
    assert evo1_config["kme_ip"] == "9.1.1.10"
    assert evo1_config["kme_port"] == 443
    assert evo1_config["links"][0]["id"] == "EVO1-EVO2"
    assert evo1_config["links"][0]["peer_ip"] == "10.38.97.228"
    assert evo1_config["links"][0]["peer_sae"] == "sae-002"
    assert evo1_config["links"][0]["ca_name"] == macsec.CA_NAME
    assert evo1_config["links"][0]["keychain_name"] == macsec.KEYCHAIN
    assert evo2_inventory["local_sae"] == "sae-002"
    assert evo2_inventory["kme_ip"] == "9.1.1.11"
    assert evo2_inventory["links"][0]["peer_ip"] == "10.38.97.218"


def test_create_command_needs_no_router_credentials(monkeypatch, tmp_path, capsys):
    use_standalone_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(qbt, "connect", lambda *_args: pytest.fail("create must not connect"))

    assert qbt.main([
        "create",
        "--router-inventory",
        str(INVENTORY),
        "--runtime-root",
        str(tmp_path / "runtime"),
    ]) == 0
    assert "[OK] EVO1" in capsys.readouterr().out


def test_deploy_dry_run_validates_pair_without_router_credentials(
    monkeypatch, tmp_path, capsys
):
    use_standalone_fixture(monkeypatch, tmp_path)
    runtime_root = tmp_path / "runtime"
    macsec.create_runtime_profiles(runtime_root, INVENTORY)
    pki = tmp_path / "pki"
    pki.mkdir()
    for name in ("ca.pem", "sae-001.pem", "sae-001.key", "sae-002.pem", "sae-002.key"):
        (pki / name).write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(qbt, "connect", lambda *_args: pytest.fail("dry-run must not connect"))

    assert qbt.main([
        "deploy",
        "--pki-dir",
        str(pki),
        "--router-inventory",
        str(INVENTORY),
        "--runtime-root",
        str(runtime_root),
        "--dry-run",
    ]) == 0
    assert "No remote connection made" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["create", "deploy"])
def test_runtime_commands_reject_partial_pair(command):
    with pytest.raises(SystemExit) as error:
        qbt.main([command, "--only", "EVO1"])
    assert error.value.code == 2
