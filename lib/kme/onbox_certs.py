"""Install orchestrator-generated PKI into existing EVO KME containers."""

from __future__ import annotations

import ipaddress
import shlex
import subprocess
import tempfile
from pathlib import Path

from cryptography import x509

from lib.common.config import load_runtime_devices, load_runtime_pki_profile
from lib.qkd.identity import qkd_orchestrator_private_key, qkd_script_user
from lib.qkd.pki_self_signed import build_unique_kme_inventory


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "etsi14-kme"
CERT_DIR = "/var/db/etsi14-kme"


def _device_kme_ip(device: dict) -> str:
    value = device.get("kme_ip")
    if value is None and isinstance(device.get("kme"), dict):
        value = device["kme"].get("ip")
    if value is None:
        raise ValueError("Missing runtime KME IP")
    return str(ipaddress.ip_address(value))


def certificate_plans(devices: dict) -> list[tuple[str, str, dict[str, Path]]]:
    profile = load_runtime_pki_profile()["pki"]["profile"]
    if profile != "self_signed":
        raise ValueError("install-onbox-certs requires the self_signed PKI profile")
    managed = [device for device in devices.values() if device.get("managed") is not False]
    kmes = {item["ip"]: item["name"] for item in build_unique_kme_inventory(list(devices.values()))}
    pki = REPO_ROOT / "certs" / "self_signed"
    plans = []
    for device in managed:
        address = str(ipaddress.ip_address(device["ip"]))
        kme_ip = _device_kme_ip(device)
        name = kmes[kme_ip]
        sources = {
            "root.crt": pki / "offbox_rootCA.crt",
            "server.crt": pki / "kme" / f"{name}.crt",
            "server.key": pki / "kme" / f"{name}.key",
        }
        for source in sources.values():
            if not source.is_file():
                raise FileNotFoundError(f"Missing generated PKI file: {source}. Run QKD create first.")
        cert = x509.load_pem_x509_certificate(sources["server.crt"].read_bytes())
        addresses = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(kme_ip) not in addresses.get_values_for_type(x509.IPAddress):
            raise ValueError(f"{name}: certificate SAN does not contain {kme_ip}")
        plans.append((address, name, sources))
    return plans


def run_install_onbox_certs(dry_run: bool = False) -> None:
    plans = certificate_plans(load_runtime_devices())
    identity = str(qkd_orchestrator_private_key())
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-i", identity,
               "-o", "IdentitiesOnly=yes"]

    def execute(command: list[str]) -> None:
        print("->", shlex.join(command))
        if not dry_run:
            subprocess.run(command, check=True, timeout=120)

    for address, name, sources in plans:
        target = f"{qkd_script_user()}@{address}"
        stage = f"/var/tmp/etsi14-certs-{name}"

        def shell(command: str) -> None:
            execute(["ssh", *options, target, f'start shell command "{command}"'])

        shell(f"umask 077; mkdir -p {stage}; chmod 700 {stage}")
        with tempfile.TemporaryDirectory(prefix="etsi14-certs-") as temp:
            paths = []
            for filename, source in sources.items():
                path = Path(temp) / filename
                path.write_bytes(source.read_bytes())
                path.chmod(0o600)
                paths.append(str(path))
            execute(["scp", "-O", *options, *paths, f"{target}:{stage}/"])
        shell(
            f"docker run --rm --network none --entrypoint /bin/sh "
            f"-v {stage}:/input:ro -v {CERT_DIR}:/target etsi-kme:local "
            "'-ec' 'cp /input/root.crt /input/server.crt /input/server.key /target/; "
            "chmod 600 /target/server.key; chmod 644 /target/root.crt /target/server.crt'"
        )
        shell(f"docker restart {CONTAINER}")
        shell(f"rm {stage}/root.crt {stage}/server.crt {stage}/server.key; rmdir {stage}")
        shell(f"docker inspect {CONTAINER} --format '{{{{.State.Status}}}}'")
