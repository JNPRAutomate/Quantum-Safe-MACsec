"""tools/generate_lab_config.py must never modify anything that already exists.

Every output gets a new timestamped name, the input inventory and
inventory_base.yaml are only read, and passwords never reach a YAML file.
"""

import stat
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import generate_lab_config as glc  # noqa: E402

SOURCE_INVENTORY = ROOT / "config" / "inventory" / "input" / "lab_vmm.yaml"
SOURCE_BASE = ROOT / "config" / "inventory" / "inventory_base.yaml"
STAMP = "20261001T120000Z"


@pytest.fixture
def workspace(tmp_path):
    inventory = tmp_path / "lab_vmm.yaml"
    inventory.write_bytes(SOURCE_INVENTORY.read_bytes())
    base = tmp_path / "inventory_base.yaml"
    base.write_bytes(SOURCE_BASE.read_bytes())
    return tmp_path, inventory, base


def _generate(tmp_path, inventory, base, **kwargs):
    return glc.generate(
        inventory,
        inventory_out=tmp_path / "out" / "lab_vmm.yaml",
        kme_out=tmp_path / "out" / "kme.yaml",
        env_out=tmp_path / "out" / "lab_vmm.env",
        inventory_base_path=base,
        stamp=STAMP,
        **kwargs,
    )


def test_outputs_are_new_timestamped_files_and_inputs_are_untouched(workspace):
    tmp_path, inventory, base = workspace
    before = {path: path.read_bytes() for path in (inventory, base)}

    inventory_path, kme_path, env_path, _ = _generate(tmp_path, inventory, base)

    assert inventory_path.name == f"lab_vmm_{STAMP}.yaml"
    assert kme_path.name == f"kme_{STAMP}.yaml"
    assert env_path.name == f"lab_vmm_{STAMP}.env"
    for path in before:
        assert path.read_bytes() == before[path]
    generated = yaml.safe_load(inventory_path.read_text())
    assert {link["id"] for link in generated["links"]} == {
        link["id"] for link in yaml.safe_load(SOURCE_INVENTORY.read_text())["links"]
    }


def test_existing_outputs_are_never_overwritten(workspace):
    tmp_path, inventory, base = workspace
    inventory_path, _, _, _ = _generate(tmp_path, inventory, base)
    content = inventory_path.read_bytes()

    with pytest.raises(FileExistsError):
        _generate(tmp_path, inventory, base)
    assert inventory_path.read_bytes() == content


def test_output_path_equal_to_input_still_gets_a_new_name(workspace):
    tmp_path, inventory, base = workspace
    before = inventory.read_bytes()

    inventory_path, _, _, _ = glc.generate(
        inventory,
        inventory_out=inventory,
        kme_out=tmp_path / "kme.yaml",
        env_out=tmp_path / "lab.env",
        inventory_base_path=base,
        stamp=STAMP,
    )

    assert inventory_path != inventory
    assert inventory.read_bytes() == before


def test_passwords_only_in_private_env_file(workspace, monkeypatch):
    tmp_path, inventory, base = workspace
    monkeypatch.setattr(glc.getpass, "getpass", lambda prompt: "example-secret")
    before = base.read_bytes()

    inventory_path, kme_path, env_path, _ = _generate(tmp_path, inventory, base, prompt_secrets=True)

    assert "example-secret" in env_path.read_text()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert "example-secret" not in inventory_path.read_text()
    assert "example-secret" not in kme_path.read_text()
    assert base.read_bytes() == before


def test_inventory_base_differences_are_reported_not_written(workspace):
    tmp_path, inventory, base = workspace
    data = yaml.safe_load(base.read_text())
    data["secrets"]["default_user"] = "someone_else"
    base.write_text(yaml.safe_dump(data))
    before = base.read_bytes()

    suggestions = glc.inventory_base_suggestions(base, {"default_user": "labuser", "default_password": "x"}, {})

    assert suggestions == ["secrets.default_user: someone_else -> labuser"]
    assert base.read_bytes() == before


def test_init_spec_is_timestamped(tmp_path, capsys):
    target = tmp_path / "spec.yaml"
    target.write_text("existing: true\n")

    assert glc.main(["--init-spec", str(target)]) == 0

    assert target.read_text() == "existing: true\n"
    written = [path for path in tmp_path.iterdir() if path.name.startswith("spec_")]
    assert len(written) == 1 and written[0].suffix == ".yaml"
    assert "Wrote starter spec" in capsys.readouterr().out
