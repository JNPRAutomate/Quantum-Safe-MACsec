from pathlib import Path

import pytest
import yaml

from lib.qbt.hierarchical import prepare_hierarchical


def test_hierarchical_generation_retry_and_tamper(tmp_path):
    base = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((base / "config/pki/hierarchical_ca.yml").read_text())
    config["pki"]["crypto"]["key_size"] = 2048
    profile = tmp_path / "profile.yml"
    profile.write_text(yaml.safe_dump(config))
    directory = prepare_hierarchical(tmp_path / "pki", profile)
    original = (directory / "evo1.key").read_bytes()
    prepare_hierarchical(directory, profile)
    assert (directory / "evo1.key").read_bytes() == original
    assert len((directory / "ca.pem").read_text().split("-----BEGIN CERTIFICATE-----")) == 5
    (directory / "evo1.key").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        prepare_hierarchical(directory, profile)


def test_incomplete_hierarchy_fails_closed(tmp_path):
    base = Path(__file__).resolve().parents[1]
    (tmp_path / "existing.key").write_text("preserve")
    with pytest.raises(ValueError, match="incomplete"):
        prepare_hierarchical(tmp_path, base / "config/pki/hierarchical_ca.yml")


def test_rotate_keeps_previous_hierarchy_and_creates_a_new_one(tmp_path):
    base = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((base / "config/pki/hierarchical_ca.yml").read_text())
    config["pki"]["crypto"]["key_size"] = 2048
    profile = tmp_path / "profile.yml"
    profile.write_text(yaml.safe_dump(config))
    directory = prepare_hierarchical(tmp_path / "pki", profile)
    old_key = (directory / "evo1.key").read_bytes()

    prepare_hierarchical(directory, profile, rotate=True)

    assert (directory / "evo1.key").read_bytes() != old_key
    superseded = list(tmp_path.glob("pki.superseded-*"))
    assert len(superseded) == 1
    assert (superseded[0] / "evo1.key").read_bytes() == old_key
