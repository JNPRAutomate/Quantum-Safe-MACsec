"""Offline PKI checks using real OpenSSL; no routers or customer assets."""

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qkd_docker_orchestrator as orchestrator
from lib.docker.qkd import docker_pki_external as pki


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(pki, "RUNTIME_DIR", tmp_path / "runtime_docker")
    devices = {}
    for index in (1, 2):
        devices[f"EVO{index}"] = {
            "name": f"EVO{index}",
            "managed": True,
            "qkd": {"sae_id": f"sae-00{index}"},
            "phiotx": {
                "container": f"phiotx0{index}",
                "internal_ip": f"9.1.1.{index + 9}",
                "oob_ip": f"10.38.112.{index + 9}",
                "etsi_client": f"sae-00{index}",
            },
        }
    phiotx = {
        "ca": {
            "dir": str(tmp_path / "linuxCA" / "phiotx"),
            "ca_key_bits": 2048,
            "key_bits": 2048,
            "ca_days": 30,
            "days": 7,
        }
    }
    return devices, phiotx


def _pki_snapshot(ca_dir):
    return {
        str(path.relative_to(ca_dir)): path.read_bytes()
        for folder, suffix in (("private", "*.key"), ("certs", "*.crt"))
        for path in (ca_dir / folder).glob(suffix)
    }


def _verify_staged_material(outputs):
    for name, bundle in outputs.items():
        ca = x509.load_pem_x509_certificate(bundle["ca"].read_bytes())
        assert set(bundle["identities"]) == {"qxc", "etsi", "sae"}
        index = int(name[-1])
        expected_names = {
            "qxc": f"phiotx0{index}",
            "etsi": f"phiotx0{index}-etsi",
            "sae": f"sae-00{index}",
        }
        for role, identity in bundle["identities"].items():
            cert = x509.load_pem_x509_certificate(identity["crt"].read_bytes())
            cert.verify_directly_issued_by(ca)
            key = serialization.load_pem_private_key(
                identity["key"].read_bytes(), password=None
            )
            assert key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ) == cert.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            assert cert.subject.get_attributes_for_oid(
                x509.NameOID.COMMON_NAME
            )[0].value == expected_names[role]
            assert identity["key"].stat().st_mode & 0o777 == 0o600
            if role in ("qxc", "etsi"):
                sans = cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
                assert set(str(ip) for ip in sans.get_values_for_type(
                    x509.IPAddress
                )) == {f"9.1.1.{index + 9}", f"10.38.112.{index + 9}"}


def test_fresh_bootstrap_regenerates_ca_and_all_six_evo_certificates(
    lab, monkeypatch, capsys
):
    devices, phiotx = lab
    pki.build_external_pki(devices, phiotx)
    ca_dir = Path(phiotx["ca"]["dir"])
    config = ca_dir / "openssl.cnf"
    config.write_text(
        config.read_text(encoding="utf-8").split("[ phiotx_etsi ]", 1)[0],
        encoding="utf-8",
    )

    def reject_reuse(*_args):
        pytest.fail("Fresh bootstrap must never check old certificates for reuse")

    monkeypatch.setattr(pki, "certificate_is_usable", reject_reuse)
    capsys.readouterr()
    for _ in range(2):
        before = _pki_snapshot(ca_dir)
        before_config = config.read_bytes()
        assert len(before) == 14  # One CA plus six leaves, each with a key.
        old_ca = x509.load_pem_x509_certificate(
            before["certs/phiotx-lab-ca.crt"]
        )

        outputs = pki.build_external_pki(devices, phiotx, fresh=True)

        after = _pki_snapshot(ca_dir)
        assert set(after) == set(before)
        assert all(after[path] != data for path, data in before.items())
        new_ca = x509.load_pem_x509_certificate(
            after["certs/phiotx-lab-ca.crt"]
        )
        assert new_ca.fingerprint(hashes.SHA256()) != old_ca.fingerprint(
            hashes.SHA256()
        )
        backups = list(ca_dir.parent.glob("phiotx.backup-*"))
        matching = [path for path in backups if _pki_snapshot(path) == before]
        assert len(matching) == 1
        assert (matching[0] / "openssl.cnf").read_bytes() == before_config
        assert not Path(f"{ca_dir}.bootstrap.lock").exists()
        assert not list(ca_dir.parent.glob("phiotx.bootstrap-*"))
        _verify_staged_material(outputs)

    assert len(list(ca_dir.parent.glob("phiotx.backup-*"))) == 2
    assert "[SKIP]" not in capsys.readouterr().out


def test_create_reuses_valid_material_without_replacing_the_ca(lab, capsys):
    devices, phiotx = lab
    pki.build_external_pki(devices, phiotx)
    ca_dir = Path(phiotx["ca"]["dir"])
    before = _pki_snapshot(ca_dir)
    capsys.readouterr()

    outputs = pki.build_external_pki(devices, phiotx)

    assert _pki_snapshot(ca_dir) == before
    assert not list(ca_dir.parent.glob("phiotx.backup-*"))
    assert capsys.readouterr().out.count("[SKIP]") == 6
    _verify_staged_material(outputs)


def test_failed_fresh_issuance_preserves_the_active_ca(lab, monkeypatch):
    devices, phiotx = lab
    ca_dir = Path(phiotx["ca"]["dir"])
    ca_dir.mkdir(parents=True)
    marker = ca_dir / "existing-ca-state"
    marker.write_text("preserve this CA", encoding="utf-8")

    def fail_issuance(*_args, **_kwargs):
        raise pki.ExternalCaError("Simulated leaf issuance failure")

    monkeypatch.setattr(pki, "issue_certificate", fail_issuance)
    with pytest.raises(pki.ExternalCaError, match="Simulated leaf issuance failure"):
        pki.build_external_pki(devices, phiotx, fresh=True)

    assert marker.read_text(encoding="utf-8") == "preserve this CA"
    assert list(ca_dir.iterdir()) == [marker]
    assert not list(ca_dir.parent.glob("phiotx.backup-*"))
    assert not Path(f"{ca_dir}.bootstrap.lock").exists()
    assert not pki.RUNTIME_DIR.exists()


def test_fresh_bootstrap_rejects_partial_fleet_before_ca_changes(lab):
    devices, phiotx = lab

    with pytest.raises(pki.ExternalCaError, match="must include every managed EVO"):
        pki.build_external_pki(devices, phiotx, only=["EVO1"], fresh=True)

    assert not Path(phiotx["ca"]["dir"]).parent.exists()


def test_fresh_bootstrap_does_not_follow_an_existing_ca_symlink(
    lab, tmp_path
):
    devices, phiotx = lab
    target = tmp_path / "manual-ca"
    target.mkdir()
    ca_dir = Path(phiotx["ca"]["dir"])
    ca_dir.parent.mkdir()
    ca_dir.symlink_to(target, target_is_directory=True)

    with pytest.raises(pki.ExternalCaError, match="Refusing to replace a symlink"):
        pki.build_external_pki(devices, phiotx, fresh=True)

    assert ca_dir.is_symlink()
    assert not list(target.iterdir())
    assert not Path(f"{ca_dir}.bootstrap.lock").exists()


def test_bootstrap_requests_fresh_pki_without_force_flag(lab, monkeypatch):
    devices, phiotx = lab
    args = SimpleNamespace(
        inventory="docker_evo_lab.yaml",
        skip_pki=False,
        only=None,
        image_archive="image.tar.gz",
        username="root",
        license=None,
        license_dir="licences",
        force_pki=False,
    )
    calls = []
    monkeypatch.setattr(
        orchestrator, "resolve_inventory_path", lambda _value: Path("evo.yaml")
    )
    monkeypatch.setattr(
        orchestrator,
        "load_docker_inventory",
        lambda _path: {"phiotx": phiotx, "devices": list(devices.values())},
    )
    monkeypatch.setattr(
        orchestrator, "resolve_local_image_archive",
        lambda *_args: Path("image.tar.gz"),
    )
    monkeypatch.setattr(
        orchestrator, "resolve_license_assignments", lambda *_args, **_kwargs: {}
    )

    def create(args, *, fresh_pki=False):
        calls.append((fresh_pki, args.force_pki))
        raise RuntimeError("stop before router access")

    monkeypatch.setattr(orchestrator, "cmd_create", create)
    with pytest.raises(RuntimeError, match="stop before router access"):
        orchestrator._run_greenfield_bootstrap(args)

    assert calls == [(True, False)]
