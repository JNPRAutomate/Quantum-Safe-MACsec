"""QBT adapter for the repository's dual hierarchical CA generator."""

import hashlib
import json
from pathlib import Path

import yaml
from lib.qkd import pki_hierarchical as existing
from lib.qbt.network import ADDRESSES


def validate(directory, manifest):
    for name, digest in manifest["files"].items():
        path = directory / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Hierarchical PKI file changed: {name}")
    for domain, names in (("kme", ("evo1", "evo2")), ("juniper", ("sae-001", "sae-002"))):
        root = existing.load_certificate(directory / f"{domain}_pki/root_ca/{domain}-root-ca.crt")
        issuing = existing.load_certificate(directory / f"{domain}_pki/issuing_ca/{domain}-issuing-ca.crt")
        now = existing.now_utc()
        for cert in (root, issuing):
            if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                raise ValueError(f"{domain}: CA outside validity interval")
        leaves = []
        for name in names:
            cert = existing.load_certificate(directory / f"{domain}_pki/certs/{name}/{name}.crt")
            key = existing.load_private_key(directory / f"{domain}_pki/certs/{name}/{name}.key")
            if cert.public_key().public_numbers() != key.public_key().public_numbers():
                raise ValueError(f"{name}: key mismatch")
            now = existing.now_utc()
            if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                raise ValueError(f"{name}: certificate expired or not yet valid")
            leaves.append(cert)
        existing.verify_tree(root, issuing, leaves)


def prepare_hierarchical(directory, config_path):
    directory = Path(directory).resolve()
    config = yaml.safe_load(Path(config_path).read_text())
    config_digest = hashlib.sha256(Path(config_path).read_bytes()).hexdigest()
    marker = directory / "manifest.json"
    if directory.exists() and any(directory.iterdir()):
        if not marker.is_file():
            raise ValueError("Existing incomplete hierarchical PKI; refusing regeneration")
        manifest = json.loads(marker.read_text())
        if manifest["config_digest"] != config_digest:
            raise ValueError("Hierarchical profile changed; explicit rotation required")
        validate(directory, manifest)
        return directory
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    config["pki"]["output_dir"] = str(directory)
    profiles = existing.load_all_profiles(config)
    results = {}
    for domain, names in (("kme", ("evo1", "evo2")), ("juniper", ("sae-001", "sae-002"))):
        leaves = []
        for index, name in enumerate(names):
            leaves.append({
                "name": name, "common_name": name, "dns": [name],
                "ip": ADDRESSES["EVO" + str(index + 1)][0] if domain == "kme" else
                ("10.38.97.218" if index == 0 else "10.38.97.228"),
            })
        results[domain] = existing.build_domain(
            domain, config["pki"][domain + "_domain"], config, profiles, leaves
        )
        existing.verify_tree(
            results[domain]["root_cert"], results[domain]["issuing_cert"],
            results[domain]["leaf_certs"],
        )
    existing.build_trust_exchange(config, results["kme"], results["juniper"])
    # QBT imports both trust domains; each SAE uses the KME trust chain.
    ca_bundle = (
        results["kme"]["ca_chain_path"].read_bytes()
        + results["juniper"]["ca_chain_path"].read_bytes()
    )
    (directory / "ca.pem").write_bytes(ca_bundle)
    for domain, names in (("kme", ("evo1", "evo2")), ("juniper", ("sae-001", "sae-002"))):
        for name in names:
            source = directory / f"{domain}_pki/certs/{name}"
            (directory / (name + ".key")).write_bytes((source / (name + ".key")).read_bytes())
            (directory / (name + ".pem")).write_bytes((source / (name + ".chain.crt")).read_bytes())
    files = {}
    for path in directory.rglob("*"):
        if path.is_file():
            if path.suffix == ".key":
                path.chmod(0o600)
            files[str(path.relative_to(directory))] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"config_digest": config_digest, "files": files}
    marker.write_text(json.dumps(manifest, indent=2) + "\n")
    validate(directory, manifest)
    return directory
