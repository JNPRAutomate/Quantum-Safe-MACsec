"""
PhioTX container lifecycle inside Junos EVO routers.

This module owns everything that happens inside the router's Docker daemon:
image load, persistent storage, networks, container creation, licence, PKI
installation, configuration layers, and the ML-KEM post-quantum overlay on the
peer/Hive channel.

All of it scales with the inventory: the peer mesh, the listeners, and the ETSI
clients are derived from the devices and links actually present, so adding an
EVO router to config/inventory/input/docker_evo_lab.yaml is sufficient.

Every device reached here has already passed docker_evo_guard.admit_device().
"""

import hashlib
import re
import shlex
import time
from pathlib import Path

import yaml
from jnpr.junos import Device
from jnpr.junos.utils.scp import SCP

from lib.common.settings import CONFIG
from lib.docker.qkd.docker_paths import DOCKER_RUNTIME_DIR
from lib.docker.qkd.docker_identity import (
    device_host,
    device_name,
    normalize_device,
    pyez_shell_cmd,
)
from lib.docker.qkd.docker_etsi_transport import install_transport
from lib.docker.qkd.docker_onbox_builder import _device_kme_source_ip


BASE_DIR = Path(__file__).resolve().parents[3]
RUNTIME_DIR = DOCKER_RUNTIME_DIR

# Staging directory inside the router. Everything placed here is removed again
# once it has been installed into the container.
REMOTE_STAGING = "/var/tmp/phiotx_stage"

# Layer names, in installation order. Lower numbers are evaluated first by
# PhioTX, so the base layer must always be installed before the peer and ETSI
# layers that depend on it.
LAYER_BASE = "100-zero-touch-base"
LAYER_PEER = "600-peer-hive"
LAYER_ETSI = "700-etsi-bulk"
LAYER_KEY_FETCH = "650-key-fetch"


class PhiotxLifecycleError(RuntimeError):
    """Raised when a PhioTX container operation fails on an EVO router."""


# ---------------------------------------------------------------------------
# Remote execution helpers
# ---------------------------------------------------------------------------


def _run(device, command, what, timeout=120, allow_fail=False):
    name = device_name(device)
    result = pyez_shell_cmd(device, command, timeout=timeout)

    if result.returncode != 0 and not allow_fail:
        raise PhiotxLifecycleError(
            f"{name}: {what} failed\ncommand={command}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

    return result


def _docker(device, args, what, timeout=120, allow_fail=False):
    return _run(device, f"docker {args}", what, timeout=timeout, allow_fail=allow_fail)


def _exec(device, container, args, what, timeout=120, allow_fail=False):
    return _docker(
        device,
        f"exec {shlex.quote(container)} {args}",
        what,
        timeout=timeout,
        allow_fail=allow_fail,
    )


def _push_files(device, transfers):
    """Copy local files to the router over SCP."""
    device = normalize_device(device)
    name = device_name(device)
    auth = device.get("auth") or {}

    dev = Device(
        host=device_host(device),
        user=auth.get("username"),
        passwd=auth.get("password"),
        port=830,
        timeout=120,
    )

    try:
        dev.open()
        with SCP(dev) as scp:
            for local_path, remote_path in transfers:
                print(f"[{name}] SCP {local_path} -> {remote_path}")
                scp.put(str(local_path), remote_path=remote_path)
    except Exception as error:
        raise PhiotxLifecycleError(f"{name}: SCP transfer failed: {error}") from error
    finally:
        try:
            dev.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Inventory resolution
# ---------------------------------------------------------------------------


def node_settings(name, device, phiotx):
    """Flatten the inventory into the values this module needs."""
    node = device.get("phiotx") or {}
    container = node.get("container")

    if not container:
        raise PhiotxLifecycleError(f"Device {name} has no phiotx.container")

    internal_ip = node.get("internal_ip") or (device.get("kme") or {}).get("ip")
    oob_ip = node.get("oob_ip")

    if not internal_ip or not oob_ip:
        raise PhiotxLifecycleError(
            f"Device {name} needs both phiotx.internal_ip and phiotx.oob_ip"
        )

    host_base = str(phiotx.get("host_data_base", "/var/db")).rstrip("/")

    return {
        "container": container,
        "internal_ip": str(internal_ip),
        "oob_ip": str(oob_ip),
        "sae_id": str(
            node.get("etsi_client") or (device.get("qkd") or {}).get("sae_id") or ""
        ),
        "host_dir": f"{host_base}/{container}",
        "data_dir": f"{host_base}/{container}/data",
        "logs_dir": f"{host_base}/{container}/logs",
    }


def resolve_peers(name, device, devices, phiotx):
    """
    Build the PhioTX peer list for one router.

    Peers are taken from the explicit phiotx.peers list when present. Otherwise
    they are derived from the MACsec links, which keeps the Hive mesh and the
    MACsec topology consistent for any number of routers. The legacy singular
    phiotx.peer key is still honoured.
    """
    node = device.get("phiotx") or {}

    explicit = node.get("peers")
    if explicit is None and node.get("peer"):
        explicit = [node["peer"]]

    # Map container name -> owning device so either form can be referenced.
    by_container = {}
    for other_name, other in devices.items():
        other_node = other.get("phiotx") or {}
        if other_node.get("container"):
            by_container[other_node["container"]] = (other_name, other)

    peer_device_names = []

    if explicit:
        for entry in explicit:
            key = entry.get("name") if isinstance(entry, dict) else entry
            key = str(key)
            if key in devices:
                peer_device_names.append(key)
            elif key in by_container:
                peer_device_names.append(by_container[key][0])
            else:
                raise PhiotxLifecycleError(
                    f"Device {name} references unknown PhioTX peer {key!r}"
                )
    else:
        for link in device.get("links", []) or []:
            peer = link.get("peer")
            if peer and peer in devices and peer != name:
                peer_device_names.append(peer)

    peers = []
    seen = set()

    for peer_name in peer_device_names:
        if peer_name in seen or peer_name == name:
            continue
        seen.add(peer_name)

        peer_settings = node_settings(peer_name, devices[peer_name], phiotx)
        peers.append(
            {
                "name": peer_settings["container"],
                "addr": peer_settings["oob_ip"],
                "port": int(phiotx.get("peer_port", 9002)),
                "pki": "qxc",
                "role": "classic",
            }
        )

    if not peers:
        raise PhiotxLifecycleError(
            f"Device {name} has no PhioTX peers; a Hive node needs at least one"
        )

    return peers


# ---------------------------------------------------------------------------
# Layer rendering
# ---------------------------------------------------------------------------


def render_base_layer(settings):
    return {"name": settings["container"]}


def render_peer_layer(settings, peers, phiotx):
    """
    Render the peer/Hive layer.

    The PQC value must be a concrete KEM name such as ML-KEM-1024. The vendor
    sample ships the placeholder "shared_key", which PhioTX rejects at commit
    time with "missing PQC shared key".
    """
    peer_port = int(phiotx.get("peer_port", 9002))
    pqc = phiotx.get("pqc")

    rendered_peers = []
    for peer in peers:
        entry = dict(peer)
        if pqc:
            entry["pqc"] = pqc
        rendered_peers.append(entry)

    return {
        "name": settings["container"],
        "tx_service": {
            "port": peer_port,
            "listen": [f"{settings['oob_ip']}:{peer_port}"],
            "pki": "qxc",
            "tls_verbose": "on",
        },
        "peers": rendered_peers,
    }


def render_etsi_layer(settings, phiotx):
    """Render the local ETSI QKD 014 service and its single on-box client."""
    etsi = phiotx.get("etsi") or {}
    port = int(etsi.get("port", 443))
    keygen_method = etsi.get("keygen_method", "bulk")
    src_ip = etsi.get("src_ip", "9.1.1.1/32")
    client_addr = str(src_ip).split("/")[0]

    return {
        "etsi_service": {
            "port": port,
            "listen": [f"{settings['internal_ip']}:{port}"],
            "pki": "etsi",
            "validate_client": bool(etsi.get("validate_client", True)),
            "tls_verbose": "on",
            "src_ip": [src_ip],
            "keygen_method": keygen_method,
        },
        "clients": [
            {
                "name": settings["sae_id"],
                "addr": client_addr,
                "pki": "etsi",
                "keygen_method": keygen_method,
            }
        ],
    }


def build_layers(name, device, devices, phiotx):
    """Render all layers for one router and write them under config/runtime."""
    settings = node_settings(name, device, phiotx)
    peers = resolve_peers(name, device, devices, phiotx)

    layers = {
        LAYER_BASE: render_base_layer(settings),
        LAYER_PEER: render_peer_layer(settings, peers, phiotx),
        LAYER_ETSI: render_etsi_layer(settings, phiotx),
    }
    if phiotx.get("keygen_mode") == "hybrid":
        from lib.docker.qkd.docker_key_fetch import render_key_fetch
        layers[LAYER_KEY_FETCH] = render_key_fetch(name, device, devices, phiotx)

    out_dir = RUNTIME_DIR / name / "phiotx"
    out_dir.mkdir(parents=True, exist_ok=True)

    written = {}
    for layer_name, payload in layers.items():
        path = out_dir / f"{layer_name}.yaml"
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        written[layer_name] = path

    print(f"[OK] rendered {len(written)} PhioTX layer(s) for {name} in {out_dir}")
    return written


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------


def container_exists(device, container):
    result = _docker(
        device,
        f"ps -a --filter name=^{shlex.quote(container)}$ --format '{{{{.Names}}}}'",
        "container lookup",
        allow_fail=True,
    )
    return container in (result.stdout or "")


def ensure_host_dirs(device, settings):
    """
    Create the persistent store with restrictive permissions.

    /var/db is used because it survives reboot and upgrade on EVO. Mode 0700
    keeps PhioTX key material unreadable by other local accounts.
    """
    _run(
        device,
        f"mkdir -p {shlex.quote(settings['data_dir'])} "
        f"{shlex.quote(settings['logs_dir'])} && "
        f"chmod 700 {shlex.quote(settings['host_dir'])}",
        "persistent store creation",
    )
    return True


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_image(device, phiotx, local_archive=None):
    """
    Load the PhioTX image when absent.

    When ``local_archive`` is supplied, the orchestrator uploads it to the
    remote ``phiotx.image_archive`` path and verifies SHA-256 before loading.
    Otherwise the archive must already exist on the EVO.
    """
    image = phiotx["image"]

    present = _docker(
        device,
        f"image inspect {shlex.quote(image)} --format '{{{{.Id}}}}'",
        "image lookup",
        allow_fail=True,
    )
    if present.returncode == 0 and "sha256:" in (present.stdout or ""):
        print(f"[SKIP] image {image} already loaded")
        return False

    archive = phiotx.get("image_archive")
    if not archive:
        raise PhiotxLifecycleError(
            f"Image {image} is absent and phiotx.image_archive is not set"
        )

    uploaded = False
    if local_archive is not None:
        local_archive = Path(local_archive).expanduser().resolve()
        if not local_archive.is_file():
            raise PhiotxLifecycleError(
                f"Local PhioTX image archive not found: {local_archive}"
            )

        remote_parent = str(Path(archive).parent)
        archive = str(Path(remote_parent) / local_archive.name)
        _run(
            device,
            f"mkdir -p {shlex.quote(remote_parent)}",
            "image staging directory",
        )
        _push_files(device, [(local_archive, archive)])
        uploaded = True

    else:
        _run(
            device,
            f"test -f {shlex.quote(archive)}",
            f"image archive lookup ({archive}); upload it with scp -O or pass "
            "--image-archive to the orchestrator",
        )

    try:
        if uploaded:
            expected = _sha256_file(local_archive)
            remote_digest = _run(
                device,
                f"sha256sum {shlex.quote(archive)} | awk '{{print $1}}'",
                "uploaded image checksum",
                timeout=300,
            )
            actual = (remote_digest.stdout or "").strip().splitlines()
            actual = actual[-1].strip() if actual else ""
            if actual != expected:
                raise PhiotxLifecycleError(
                    f"{device_name(device)}: uploaded image checksum mismatch: "
                    f"expected {expected}, received {actual or '<empty>'}"
                )
            print(
                f"[OK] uploaded and verified image archive for "
                f"{device_name(device)}"
            )

        if str(archive).lower().endswith((".gz", ".tgz")):
            load_command = f"gzip -dc {shlex.quote(archive)} | docker load"
        else:
            load_command = f"docker load -i {shlex.quote(archive)}"

        _run(device, load_command, "image load", timeout=900)
    finally:
        if uploaded:
            _run(
                device,
                f"rm -f {shlex.quote(archive)}",
                "uploaded image cleanup",
                allow_fail=True,
            )

    print(f"[OK] loaded image {image}")
    return True


def ensure_oob_network(device, phiotx):
    """
    Create the OOB macvlan network used for peer/Hive traffic.

    ipvlan and macvlan-on-eth0 are both rejected by the EVO kernel; macvlan in
    bridge mode on vmb0 is the only validated combination.
    """
    network = phiotx.get("oob_network") or {}
    name = network.get("name")

    if not name:
        raise PhiotxLifecycleError("phiotx.oob_network.name is required")

    present = _docker(
        device,
        f"network inspect {shlex.quote(name)} --format '{{{{.Name}}}}'",
        "OOB network lookup",
        allow_fail=True,
    )
    if present.returncode == 0 and name in (present.stdout or ""):
        print(f"[SKIP] Docker network {name} already exists")
        return False

    _docker(
        device,
        f"network create -d {shlex.quote(network.get('driver', 'macvlan'))} "
        f"--subnet {shlex.quote(network['subnet'])} "
        f"--gateway {shlex.quote(network['gateway'])} "
        f"-o parent={shlex.quote(network.get('parent', 'vmb0'))} "
        f"-o macvlan_mode={shlex.quote(network.get('macvlan_mode', 'bridge'))} "
        f"{shlex.quote(name)}",
        "OOB network creation",
    )
    print(f"[OK] created Docker network {name}")
    return True


def start_container(device, settings, phiotx):
    """
    Create and start the PhioTX container, then attach the internal bridge.

    "--cpus" is deliberately not used: the EVO kernel rejects the CFS quota
    write during OCI container creation and the container never starts.
    "--cpu-shares" provides relative weighting without that quota.
    """
    container = settings["container"]

    if container_exists(device, container):
        print(f"[SKIP] container {container} already exists")
        _docker(device, f"start {shlex.quote(container)}", "container start", allow_fail=True)
        return False

    oob = (phiotx.get("oob_network") or {}).get("name")

    _docker(
        device,
        "run -d "
        f"--name {shlex.quote(container)} "
        f"--hostname {shlex.quote(container)} "
        f"--restart {shlex.quote(str(phiotx.get('restart_policy', 'unless-stopped')))} "
        f"--cpu-shares {int(phiotx.get('cpu_shares', 1024))} "
        f"--memory {shlex.quote(str(phiotx.get('memory', '512m')))} "
        f"--network {shlex.quote(oob)} "
        f"--ip {shlex.quote(settings['oob_ip'])} "
        "-e CAF_APP_PERSISTENT_DIR=/data "
        f"-e PHIOTX_NODE_NAME={shlex.quote(container)} "
        "-e PHIOTX_LAYER_PERSIST=yes "
        f"-v {shlex.quote(settings['data_dir'])}:/data "
        f"{shlex.quote(phiotx['image'])}",
        "container creation",
        timeout=300,
    )

    internal = phiotx.get("internal_network") or {}
    internal_name = internal.get("name")
    if not internal_name:
        raise PhiotxLifecycleError("phiotx.internal_network.name is required")

    # jnpr_cntrz_net is created and owned by Junos EVO; only attach to it.
    _docker(
        device,
        f"network connect --ip {shlex.quote(settings['internal_ip'])} "
        f"{shlex.quote(internal_name)} {shlex.quote(container)}",
        "internal network attachment",
    )

    print(
        f"[OK] started {container} "
        f"oob={settings['oob_ip']} internal={settings['internal_ip']}"
    )
    return True


# ---------------------------------------------------------------------------
# In-container provisioning
# ---------------------------------------------------------------------------


def install_license(device, settings, license_path):
    """Install or replace the node-unique PhioTX licence."""
    container = settings["container"]
    remote = f"{settings['data_dir']}/.license-install.lic"
    expected = _sha256_file(license_path)

    _run(device, f"mkdir -p {shlex.quote(settings['data_dir'])}", "licence staging")
    try:
        _push_files(device, [(license_path, remote)])
        remote_digest = _run(
            device,
            f"sha256sum {shlex.quote(remote)} | awk '{{print $1}}'",
            "licence checksum",
        )
        actual = (remote_digest.stdout or "").strip().splitlines()
        actual = actual[-1].strip() if actual else ""
        if actual != expected:
            raise PhiotxLifecycleError(
                f"{device_name(device)}: licence checksum mismatch for {container}"
            )

        _exec(
            device,
            container,
            "tx_install_license -f /data/.license-install.lic",
            "licence install",
        )
        _exec(device, container, "tx_status -license", "licence verification")
    finally:
        _run(device, f"rm -f {shlex.quote(remote)}", "licence cleanup")

    print(f"[OK] licence installed on {container}")
    return True


def install_pki(device, settings, pki_bundle):
    """
    Install the external CA trust material into the container PKI stores.

    tx_install_private_key and tx_install_crt are used instead of a read-only
    bind mount so PhioTX owns the material in its own stores. In -y mode both
    commands securely wipe and unlink the source file inside the container.
    """
    container = settings["container"]
    ca_local = pki_bundle["ca"]
    identities = pki_bundle["identities"]

    _run(device, f"mkdir -p {shlex.quote(REMOTE_STAGING)}", "staging directory")
    _exec(device, container, "mkdir -p /tmp/pki", "container staging directory")

    ca_remote = f"{REMOTE_STAGING}/ca.pem"
    try:
        _push_files(device, [(ca_local, ca_remote)])
        for role, identity in identities.items():
            store = identity.get("store")
            if not store:
                # The SAE client identity belongs to the router, not the container.
                continue

            key_remote = f"{REMOTE_STAGING}/{role}.key"
            crt_remote = f"{REMOTE_STAGING}/{role}.crt"
            try:
                _push_files(
                    device,
                    [(identity["key"], key_remote), (identity["crt"], crt_remote)],
                )
                _docker(
                    device,
                    f"cp {shlex.quote(key_remote)} {shlex.quote(container)}:/tmp/pki/private.key",
                    f"{role} key copy",
                )
                _docker(
                    device,
                    f"cp {shlex.quote(crt_remote)} {shlex.quote(container)}:/tmp/pki/this.crt",
                    f"{role} certificate copy",
                )
                _exec(
                    device,
                    container,
                    f"tx_install_private_key -pki {shlex.quote(store)} "
                    "-key /tmp/pki/private.key -f -y",
                    f"{role} private key install",
                )
                # tx_install_crt -y consumes the CA input along with the leaf.
                _docker(
                    device,
                    f"cp {shlex.quote(ca_remote)} {shlex.quote(container)}:/tmp/pki/ca.pem",
                    f"{role} CA copy",
                )
                _exec(
                    device,
                    container,
                    f"tx_install_crt -pki {shlex.quote(store)} "
                    "-crt /tmp/pki/this.crt -ca /tmp/pki/ca.pem -f -y",
                    f"{role} certificate install",
                )
            finally:
                _run(
                    device,
                    f"rm -f {shlex.quote(key_remote)} {shlex.quote(crt_remote)}",
                    f"{role} staging cleanup",
                )
            print(f"[OK] installed PKI store {store} on {container}")
    finally:
        try:
            _run(device, f"rm -f {shlex.quote(ca_remote)}", "CA staging cleanup")
        finally:
            _exec(device, container, "rm -rf /tmp/pki", "container staging cleanup")

    return True


def install_layers(device, settings, layer_paths, dry_run=False):
    """
    Install configuration layers with tx_install_cf.

    tx_install_cf without -y is a dry run, so each layer is validated before it
    is committed. A rejected dry run aborts the deployment rather than leaving
    a half-configured node.
    """
    container = settings["container"]
    _run(device, f"mkdir -p {shlex.quote(REMOTE_STAGING)}", "staging directory")

    for layer_name in (LAYER_BASE, LAYER_PEER, LAYER_KEY_FETCH, LAYER_ETSI):
        local_path = layer_paths.get(layer_name)
        if not local_path:
            continue

        remote = f"{REMOTE_STAGING}/{layer_name}.yaml"
        _push_files(device, [(local_path, remote)])
        _docker(
            device,
            f"cp {shlex.quote(remote)} {shlex.quote(container)}:/tmp/{layer_name}.yaml",
            f"{layer_name} copy",
        )

        _exec(
            device,
            container,
            f"tx_install_cf -layer {shlex.quote(layer_name)} /tmp/{layer_name}.yaml",
            f"{layer_name} dry run",
        )

        if dry_run:
            print(f"[DRY-RUN] {container}: {layer_name} validated, not committed")
        else:
            _exec(
                device,
                container,
                f"tx_install_cf -y -layer {shlex.quote(layer_name)} /tmp/{layer_name}.yaml",
                f"{layer_name} commit",
            )
            print(f"[OK] committed layer {layer_name} on {container}")

        _exec(
            device,
            container,
            f"rm -f /tmp/{layer_name}.yaml",
            "container staging cleanup",
            allow_fail=True,
        )
        _run(device, f"rm -f {shlex.quote(remote)}", "staging cleanup", allow_fail=True)

    return True


def _pqc_key_present(status, name, kem=None):
    prefix = f"'{name}',"
    for line in status.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        detail = line[len(prefix):].strip()
        if kem is None:
            if "KEM '" in detail:
                continue
        elif f"KEM '{kem}'" not in detail:
            continue
        return bool(detail) and not detail.lower().startswith("missing")
    return False


def ensure_pqc_keypair(device, settings, phiotx):
    """Generate a missing local keypair without rotating an existing one."""
    kem = phiotx.get("pqc")
    if not kem:
        print("[SKIP] no phiotx.pqc configured")
        return False

    container = settings["container"]
    status = _exec(device, container, "tx_status -pqc", "PQC keypair status")
    if _pqc_key_present(status.stdout or "", kem):
        print(f"[SKIP] {kem} keypair already present on {container}")
        return True

    _exec(
        device,
        container,
        f"tx_generate_pqc_keypair -m {shlex.quote(kem)}",
        "PQC keypair generation",
    )
    status = _exec(device, container, "tx_status -pqc", "PQC keypair verification")
    if not _pqc_key_present(status.stdout or "", kem):
        raise PhiotxLifecycleError(
            f"{device_name(device)}: {kem} keypair is not present after generation "
            f"on {container}\nstdout={status.stdout}"
        )
    print(f"[OK] generated and verified {kem} keypair on {container}")
    return True


def setup_pqc(device, settings, phiotx, *, prepare_keypair=True):
    """
    Import peer public keys over the authenticated TLS channel.

    Fleet bring-up prepares every local keypair before calling this function.
    Private keys remain in the persistent PhioTX database.
    """
    kem = phiotx.get("pqc")
    if not kem:
        print("[SKIP] no phiotx.pqc configured")
        return False

    container = settings["container"]
    if prepare_keypair:
        ensure_pqc_keypair(device, settings, phiotx)

    for peer in settings.get("peers", []):
        _exec(
            device,
            container,
            f"tx_get_pqc_public_key -p {shlex.quote(peer['name'])} "
            f"-m {shlex.quote(kem)}",
            f"PQC public key fetch from {peer['name']}",
        )
        status = _exec(
            device, container, "tx_status -pqc", "PQC public key verification"
        )
        if not _pqc_key_present(status.stdout or "", peer["name"], kem):
            raise PhiotxLifecycleError(
                f"{device_name(device)}: {kem} public key of {peer['name']} "
                f"is not present after import on {container}\nstdout={status.stdout}"
            )
        print(f"[OK] imported {kem} public key of {peer['name']} on {container}")

    return True


def verify_node(device, settings, phiotx):
    """Read back the state an operator would check by hand."""
    container = settings["container"]
    report = {}

    for label, command in (
        ("layers", "tx_install_cf -list"),
        ("pki_qxc", "tx_status -pki qxc"),
        ("pki_etsi", "tx_status -pki etsi"),
        ("pqc", "tx_status -pqc"),
        ("peers", "txh -P -c no"),
    ):
        result = _exec(device, container, command, label, allow_fail=True)
        report[label] = (result.stdout or "").strip()

    listeners = _run(
        device,
        f"docker exec {shlex.quote(container)} ss -ltn || true",
        "listener check",
        allow_fail=True,
    )
    report["listeners"] = (listeners.stdout or "").strip()

    print(f"[OK] collected PhioTX status from {container}")
    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def phiotx_up(
    devices,
    phiotx,
    pki_bundles=None,
    licenses=None,
    image_archive=None,
    require_licenses=False,
    require_pki=False,
    dry_run=False,
    only=None,
):
    """
    Bring up PhioTX on every selected EVO router.

    Returns a per-device report. The sequence is intentionally strict: storage,
    image, network, container, licence, PKI, layers, PQC, verification. Each
    step depends on the previous one having succeeded.
    """
    pki_bundles = pki_bundles or {}
    licenses = licenses or {}
    selected = {
        name: device
        for name, device in devices.items()
        if device.get("managed") is not False and (not only or name in set(only))
    }

    if not selected:
        raise PhiotxLifecycleError("No managed EVO devices selected")
    if phiotx.get("keygen_mode") == "hybrid":
        from lib.docker.qkd.docker_key_fetch import render_key_fetch
        for name, device in selected.items():
            render_key_fetch(name, device, devices, phiotx)
            if "qkd" not in (pki_bundles.get(name, {}).get("identities") or {}):
                raise PhiotxLifecycleError(f"Missing QKD client PKI for {name}")

    missing_licenses = sorted(set(selected) - set(licenses))
    if require_licenses and missing_licenses:
        raise PhiotxLifecycleError(
            "A unique PhioTX licence is required for every selected EVO router; "
            f"missing: {', '.join(missing_licenses)}"
        )

    missing_pki = sorted(set(selected) - set(pki_bundles))
    if require_pki and missing_pki:
        raise PhiotxLifecycleError(
            "PKI material is required for every selected EVO router; "
            f"missing: {', '.join(missing_pki)}. Run create without --skip-pki."
        )

    reports = {}
    prepared = {}

    # Phase 1: make every node available before configuring cross-node PQC.
    for name, device in selected.items():
        print(f"\n=== PhioTX bring-up on {name} ===")

        settings = node_settings(name, device, phiotx)
        settings["peers"] = resolve_peers(name, device, devices, phiotx)
        prepared[name] = (device, settings)

        ensure_host_dirs(device, settings)
        ensure_image(device, phiotx, local_archive=image_archive)
        ensure_oob_network(device, phiotx)
        start_container(device, settings, phiotx)
        if not dry_run and _device_kme_source_ip(device, phiotx):
            install_transport(
                device, phiotx, RUNTIME_DIR / name / "transport",
                lambda command: _run(device, command, "ETSI transport setup").stdout,
                lambda transfers: _push_files(device, transfers),
            )
            print(f"[OK] {name}: local ETSI socket helper ready")

        if name in licenses:
            install_license(device, settings, licenses[name])
        else:
            print(f"[WARN] no licence supplied for {name}; skipping licence install")

        if name in pki_bundles:
            install_pki(device, settings, pki_bundles[name])
        else:
            print(f"[WARN] no PKI bundle for {name}; skipping PKI install")

        layer_config = phiotx
        kem = phiotx.get("pqc")
        if kem and not dry_run:
            status = _exec(
                device, settings["container"], "tx_status -pqc", "PQC readiness"
            )
            ready = _pqc_key_present(status.stdout or "", kem) and all(
                _pqc_key_present(status.stdout or "", peer["name"], kem)
                for peer in settings["peers"]
            )
            if not ready:
                layer_config = {**phiotx, "pqc": None}
                print(f"[PLAN] {settings['container']}: stage TLS before PQC activation")

        layer_paths = build_layers(name, device, devices, layer_config)
        if phiotx.get("keygen_mode") in ("pqc", "hybrid"):
            # Application KEM generation must not start before the fleet has
            # exchanged its public keys. No temporary bulk fallback is added.
            layer_paths = {
                key: value for key, value in layer_paths.items()
                if key not in (LAYER_ETSI, LAYER_KEY_FETCH)
            }
        install_layers(device, settings, layer_paths, dry_run=dry_run)

    # Every peer must have a local keypair before any public-key fetch.
    if not dry_run and phiotx.get("pqc"):
        for name, (device, settings) in prepared.items():
            print(f"\n=== PhioTX PQC keypair preparation on {name} ===")
            ensure_pqc_keypair(device, settings, phiotx)

        for name, (device, settings) in prepared.items():
            print(f"\n=== PhioTX PQC public-key exchange on {name} ===")
            setup_pqc(device, settings, phiotx, prepare_keypair=False)

        for name, (device, settings) in prepared.items():
            print(f"\n=== PhioTX PQC activation on {name} ===")
            layer_paths = build_layers(name, device, devices, phiotx)
            install_layers(
                device, settings, {LAYER_PEER: layer_paths[LAYER_PEER]}
            )

    if phiotx.get("keygen_mode") in ("pqc", "hybrid"):
        if phiotx["keygen_mode"] == "hybrid":
            for name, (device, settings) in prepared.items():
                layer_paths = build_layers(name, device, devices, phiotx)
                install_layers(
                    device, settings,
                    {LAYER_KEY_FETCH: layer_paths[LAYER_KEY_FETCH]},
                    dry_run=dry_run,
                )
            if not dry_run:
                wait_for_qkd_pool(prepared)
        for name, (device, settings) in prepared.items():
            layer_paths = build_layers(name, device, devices, phiotx)
            install_layers(
                device, settings, {LAYER_ETSI: layer_paths[LAYER_ETSI]},
                dry_run=dry_run,
            )

    for name, (device, settings) in prepared.items():
        reports[name] = verify_node(device, settings, phiotx)

    return reports


def wait_for_qkd_pool(prepared, timeout=90):
    deadline = time.monotonic() + timeout
    waiting = set(prepared)
    while waiting:
        for name in list(waiting):
            device, settings = prepared[name]
            result = _exec(device, settings["container"], "txh -Q -c no", "QKD pool readiness")
            if re.search(r"Q Pool:\s*[1-9][0-9]*", result.stdout or ""):
                waiting.remove(name)
        if not waiting:
            return
        if time.monotonic() >= deadline:
            raise PhiotxLifecycleError(
                "No pre-fetched QKD material before hybrid activation on: "
                + ", ".join(sorted(waiting))
            )
        time.sleep(3)
