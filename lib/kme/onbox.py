"""Inventory-driven embedded KME deployment backend for Junos EVO routers."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from jnpr.junos import Device
from jnpr.junos.utils.scp import SCP

from lib.common.config import load_runtime_devices
from lib.kme.onbox_certs import certificate_plans, run_install_onbox_certs
from lib.kme.onbox_network import network_command, run_configure_onbox_network


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "kme" / "onbox.yaml"
CONTAINER_NAME = "etsi14-kme"
CERT_DIR = "/var/db/etsi14-kme"


def _device_kme_ip(device: dict[str, Any]) -> str:
    value = device.get("kme_ip")
    if value is None and isinstance(device.get("kme"), dict):
        value = device["kme"].get("ip")
    if value is None:
        raise ValueError("Missing runtime KME IP")
    return str(ipaddress.IPv4Address(value))


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = REPO_ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"Missing on-box config: {p}")
    import yaml

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid on-box config: {p}")
    return data


def _shell(router: Device, command: str) -> str:
    result = router.rpc.request_shell_execute(command="/bin/sh -c " + shlex.quote(command))
    return "".join(result.itertext()).strip()


def _remote_json(router: Device, command: str) -> Any:
    output = _shell(router, command)
    return json.loads(output)


def _image_archive(image: str) -> Path:
    path = Path(tempfile.gettempdir()) / f"{image.replace('/', '_').replace(':', '_')}.tar"
    if not path.exists():
        subprocess.run(["docker", "save", "-o", str(path), image], check=True)
    return path


def _ensure_remote_image(router: Device, image: str) -> str:
    local = _remote_json(router, f"docker image inspect {shlex.quote(image)}")
    if local:
        return str(local[0]["Id"])
    archive = _image_archive(image)
    with SCP(router, timeout=600) as scp:
        scp.put(str(archive), remote_path=f"/var/tmp/{archive.name}")
    _shell(router, f"docker load -i /var/tmp/{archive.name} && rm -f /var/tmp/{archive.name}")
    loaded = _remote_json(router, f"docker image inspect {shlex.quote(image)}")
    return str(loaded[0]["Id"])


def _env_file(config: dict[str, Any], target: dict[str, Any], temp_dir: Path) -> Path:
    db = config["database"]
    credentials = Path(db["credentials_file"]).expanduser()
    if not credentials.is_absolute():
        credentials = REPO_ROOT / credentials
    if not credentials.exists():
        raise FileNotFoundError(f"Missing database credentials file: {credentials}")
    values: dict[str, str] = {}
    for line in credentials.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"Invalid line in credentials file: {line!r}")
        values[key] = value
    user = values.get("POSTGRES_USER")
    password = values.get("POSTGRES_PASSWORD")
    if not user or not password:
        raise ValueError("Database credentials file must define POSTGRES_USER and POSTGRES_PASSWORD")
    db_url = (
        f"postgres://{quote(user, safe='')}:{quote(password, safe='')}@"
        f"{db['host']}:{int(db['port'])}/{quote(db['db_name'], safe='')}"
    )
    env_file = temp_dir / "kme.env"
    env_file.write_text(
        "\n".join(
            [
                f"ETSI_014_REF_IMPL_DB_URL={db_url}",
                "ETSI_014_REF_IMPL_IP_ADDR=0.0.0.0",
                f"ETSI_014_REF_IMPL_PORT_NUM={int(target['kme_port'])}",
                f"ETSI_014_REF_IMPL_NUM_WORKER_THREADS={int(config.get('kme', {}).get('worker_threads', 2))}",
                "ETSI_014_REF_IMPL_TLS_CERT=/certs/server.crt",
                "ETSI_014_REF_IMPL_TLS_PRIVATE_KEY=/certs/server.key",
                "ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    return env_file


def _ensure_router_container(router: Device, target: dict[str, Any], image: str, image_id: str, env_file: Path) -> None:
    container = CONTAINER_NAME
    network = target["metadata"]["kme_onbox"]["network"]
    ip = _device_kme_ip(target)
    existing = _remote_json(router, f"docker inspect {shlex.quote(container)}") if _shell(router, f"docker ps -aq --filter name=^/{container}$") else None
    needs_create = True
    if existing:
        state = existing[0]
        env = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in state["Config"]["Env"] if "=" in item}
        network_info = state["NetworkSettings"]["Networks"].get(network, {})
        mounts = state.get("Mounts", [])
        if (
            state["Image"] == image_id
            and env.get("ETSI_014_REF_IMPL_DB_URL")
            and env.get("ETSI_014_REF_IMPL_PORT_NUM") == str(target["kme_port"])
            and network_info.get("IPAddress") == ip
            and any(m.get("Source") == CERT_DIR and m.get("Destination") == "/certs" and m.get("RW") is False for m in mounts)
        ):
            needs_create = False
            if state["State"]["Status"] != "running":
                _shell(router, f"docker start {container}")
            return
        _shell(router, f"docker rm -f {container}")
    if needs_create:
        with SCP(router, timeout=600) as scp:
            scp.put(str(env_file), remote_path="/var/tmp/kme.env")
        _shell(
            router,
            " ".join(
                [
                    "docker run -d",
                    f"--name {container}",
                    "--restart unless-stopped",
                    f"--network {network}",
                    f"--ip {ip}",
                    "--env-file /var/tmp/kme.env",
                    f"-v {CERT_DIR}:/certs:ro",
                    f"{shlex.quote(image)}",
                ]
            ),
        )
        _shell(router, "rm -f /var/tmp/kme.env")


def _verify_mtls(target: dict[str, Any]) -> None:
    kme_ip = _device_kme_ip(target)
    port = int(target["kme"]["port"])
    sae_id = target["qkd"]["sae_id"]
    pki = REPO_ROOT / "certs" / "self_signed"
    crt = pki / sae_id / f"{sae_id}.crt"
    key = pki / sae_id / f"{sae_id}.key"
    root = pki / "offbox_rootCA.crt"
    url = f"https://{kme_ip}:{port}/api/v1/keys/{sae_id}/status"
    response = requests.get(url, cert=(str(crt), str(key)), verify=str(root), timeout=8, proxies={"https": "", "http": ""})
    response.raise_for_status()
    if not isinstance(response.json().get("stored_key_count"), int):
        raise ValueError(f"Unexpected ETSI response from {kme_ip}")


def run_onbox_deploy(config_path: str | Path = DEFAULT_CONFIG, dry_run: bool = False) -> None:
    config = load_config(config_path)
    devices = load_runtime_devices()
    plans = certificate_plans(devices)
    planned_ips = {address: (name, sources) for address, name, sources in plans}
    if dry_run:
        for name, device in devices.items():
            if device.get("managed") is False:
                continue
            metadata = device.get("metadata", {}).get("kme_onbox")
            if not metadata:
                raise ValueError(f"[{name}] Missing metadata.kme_onbox")
            print(
                f"[DRY-RUN] {name}: network={metadata['network']} "
                f"host_access_ip={metadata['host_access_ip']} kme_ip={_device_kme_ip(device)} "
                f"image={config['docker']['image']}"
            )
        print(f"[DRY-RUN] {len(planned_ips)} managed routers would receive containers")
        return

    # Make sure host access is present before container installation.
    run_configure_onbox_network(dry_run=False)

    image = config["docker"]["image"]
    image_id = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], check=True, capture_output=True, text=True).stdout.strip()
    if not image_id:
        raise RuntimeError(f"Local Docker image not available: {image}")

    for name, device in devices.items():
        if device.get("managed") is False:
            continue
        metadata = device.get("metadata", {}).get("kme_onbox")
        if not isinstance(metadata, dict):
            raise ValueError(f"[{name}] Missing metadata.kme_onbox")
        auth = device.get("auth", {}) or {}
        if not auth.get("username") or not auth.get("password"):
            raise ValueError(f"[{name}] Missing runtime transport credentials")
        target = {
            "name": name,
            "ip": device["ip"],
            "kme": device["kme"],
            "qkd": device["qkd"],
            "kme_port": device["kme_port"],
            "metadata": device["metadata"],
        }
        print(f"[{name}] connecting to {device['ip']}")
        with Device(host=device["ip"], user=auth["username"], passwd=auth["password"], port=830, gather_facts=False, timeout=45) as router:
            _shell(router, "uname -m >/dev/null")
            stage = Path(tempfile.mkdtemp(prefix=f"etsi14-{name.lower()}-"))
            env_file = _env_file(config, device, stage)
            try:
                # Ensure image is present locally on the router
                remote_image = _shell(router, f"docker image inspect {shlex.quote(image)} --format '{{{{.Id}}}}' 2>/dev/null || true")
                if remote_image != image_id:
                    archive = Path(tempfile.gettempdir()) / f"{image.replace('/', '_').replace(':', '_')}.tar"
                    if not archive.exists():
                        subprocess.run(["docker", "save", "-o", str(archive), image], check=True)
                    with SCP(router, timeout=600) as scp:
                        scp.put(str(archive), remote_path=f"/var/tmp/{archive.name}")
                    _shell(router, f"docker load -i /var/tmp/{archive.name} && rm -f /var/tmp/{archive.name}")
                # Create or refresh the container
                _ensure_router_container(router, device, image, image_id, env_file)
            finally:
                try:
                    for item in stage.iterdir():
                        item.unlink()
                    stage.rmdir()
                except Exception:
                    pass
    # Certificates are installed after container creation so the bind-mounted
    # /var/db/etsi14-kme directory exists on all target routers before restart.
    run_install_onbox_certs(dry_run=False)
    for name, device in devices.items():
        if device.get("managed") is False:
            continue
        _verify_mtls(device)
        print(f"[{name}] on-box KME running and responding to mTLS")
    print(f"[OK] on-box KME deployment complete for {len(planned_ips)} routers")
