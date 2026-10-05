"""Create local QBT runtime profiles and install them through the orchestrator."""

import ast
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shlex
import secrets
import shutil
import tempfile

from jinja2 import Environment, FileSystemLoader
from jnpr.junos import Device
from jnpr.junos.utils.config import Config
import yaml

from lib.qbt.network import ADDRESSES
from lib.qbt.runtime_builder import build_qbt_onbox
from lib.qbt.transport import install_transport


BASE = Path(__file__).resolve().parents[2]
TARGET_DEVICES = ("EVO1", "EVO2")
DEFAULT_ROUTER_INVENTORY = BASE / "config/inventory/input/lab_vmm.yaml"
CA_NAME = "QBT_EVO"
KEYCHAIN = "QKD_QBT_EVO"
RUNTIME_NAME = "qbt_onbox.py"
OLD_QBT_HELPERS = ("qbt_runtime_core.py", "qbt_etsi_client.py", "qbt_etsi_probe.py")
INTERFACE_PATTERN = re.compile(r"(?:et|xe|ge)-\d+/\d+/\d+(?:\.\d+)?")


def _consistent_value(values, label):
    values = [value for value in values if value is not None and value != ""]
    if not values or any(value != values[0] for value in values[1:]):
        raise ValueError(f"Router inventory has missing or conflicting {label}")
    return values[0]


def load_target_devices(inventory_path=None):
    inventory_path = Path(inventory_path or DEFAULT_ROUTER_INVENTORY)
    try:
        inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"Unable to load router inventory {inventory_path}: {error}") from error
    if not isinstance(inventory, dict) or not isinstance(inventory.get("devices"), list):
        raise ValueError(
            "Router inventory must be a source input with a devices list; "
            "generated config/runtime files are not valid inputs"
        )
    raw_devices = {}
    for record in inventory["devices"]:
        if not isinstance(record, dict) or not record.get("name"):
            raise ValueError("Router inventory device entries must be named mappings")
        name = record["name"]
        if name in TARGET_DEVICES:
            if name in raw_devices:
                raise ValueError(f"Router inventory contains duplicate device {name}")
            raw_devices[name] = record
    missing = [name for name in TARGET_DEVICES if name not in raw_devices]
    if missing:
        raise ValueError(f"Router inventory is missing target devices: {', '.join(missing)}")

    pair_links = [
        link for link in inventory.get("links", [])
        if isinstance(link, dict)
        and {link.get("node_a"), link.get("node_b")} == set(TARGET_DEVICES)
        and link.get("macsec", True) is not False
    ]
    if len(pair_links) != 1:
        raise ValueError("Router inventory must define exactly one MACsec link between EVO1 and EVO2")
    pair_link = pair_links[0]
    if not pair_link.get("id"):
        raise ValueError("Router inventory EVO1/EVO2 link must have an id")

    devices = {}
    for name in TARGET_DEVICES:
        record = raw_devices[name]
        if record.get("evo") is False or record.get("managed") is False:
            raise ValueError(f"Router inventory marks {name} as unmanaged or non-EVO")

        try:
            host_ip = ipaddress.ip_address(str(record.get("ip")))
        except ValueError as error:
            raise ValueError(f"Router inventory has an invalid management IP for {name}") from error
        if host_ip.version != 4 or host_ip.is_unspecified or host_ip.is_loopback:
            raise ValueError(f"Router inventory requires a concrete IPv4 management IP for {name}")

        qkd = record.get("qkd") or {}
        local_sae = _consistent_value(
            (record.get("sae_id"), qkd.get("sae_id")),
            f"{name} SAE identity",
        )
        peer = "EVO2" if name == "EVO1" else "EVO1"
        if pair_link["node_a"] == name:
            interface = pair_link.get("interface_a")
            peer_interface = pair_link.get("interface_b")
            role = "master"
        else:
            interface = pair_link.get("interface_b")
            peer_interface = pair_link.get("interface_a")
            role = "slave"
        if (
            not isinstance(interface, str)
            or not INTERFACE_PATTERN.fullmatch(interface)
            or not isinstance(peer_interface, str)
            or not INTERFACE_PATTERN.fullmatch(peer_interface)
            or role not in ("master", "slave")
            or not pair_link.get("id")
        ):
            raise ValueError(f"Router inventory has an incomplete or invalid MACsec link for {name}")
        configured_interfaces = record.get("interfaces")
        if configured_interfaces and interface not in configured_interfaces:
            raise ValueError(f"{name} MACsec interface is absent from its inventory interface list")

        devices[name] = {
            "name": name,
            "ip": str(host_ip),
            "sae_id": str(local_sae),
            "kme_ip": ADDRESSES[name][0],
            "kme_port": 443,
            "link": {
                "id": pair_link["id"],
                "type": pair_link.get("type", "link"),
                "macsec": pair_link.get("macsec", True),
                "role": role,
                "interface": interface,
                "peer": peer,
                "peer_ip": str(
                    ipaddress.IPv4Address(str(raw_devices[peer].get("ip")))
                ),
                "peer_interface": peer_interface,
                "peer_sae": _consistent_value(
                    (
                        raw_devices[peer].get("sae_id"),
                        (raw_devices[peer].get("qkd") or {}).get("sae_id"),
                    ),
                    f"{peer} SAE identity",
                ),
            },
        }

    if devices["EVO1"]["ip"] == devices["EVO2"]["ip"]:
        raise ValueError("EVO1 and EVO2 must have distinct management IP addresses")
    if devices["EVO1"]["sae_id"] == devices["EVO2"]["sae_id"]:
        raise ValueError("EVO1 and EVO2 must have distinct SAE identities")

    for name, peer in (("EVO1", "EVO2"), ("EVO2", "EVO1")):
        link = devices[name]["link"]
        peer_link = devices[peer]["link"]
        if (
            link.get("peer") != peer
            or link.get("peer_ip") != devices[peer]["ip"]
            or link.get("peer_sae") != devices[peer]["sae_id"]
            or link.get("peer_interface") != peer_link.get("interface")
            or link.get("id") != peer_link.get("id")
            or link.get("role") == peer_link.get("role")
        ):
            raise ValueError(f"Router inventory has inconsistent reciprocal EVO1/EVO2 links")
    return devices


def build_link(name, devices):
    device = devices[name]
    inventory_link = device["link"]
    peer = devices[inventory_link["peer"]]
    # These aliases are the QBT MACsec profile already configured on both EVOs.
    return {
        "id": inventory_link["id"],
        "type": inventory_link.get("type", "link"),
        "macsec": inventory_link.get("macsec", True),
        "role": inventory_link["role"],
        "interface": inventory_link["interface"],
        "peer": inventory_link["peer"],
        "peer_ip": peer["ip"],
        "peer_interface": inventory_link["peer_interface"],
        "peer_sae": inventory_link["peer_sae"],
        "ca_name": CA_NAME,
        "ca_names": [CA_NAME],
        "keychain_name": KEYCHAIN,
        "peer_kme_ip": peer["kme_ip"],
        "peer_kme_port": peer["kme_port"],
        "local_sae": device["sae_id"],
    }


def _load_inventory_base():
    return yaml.safe_load(
        (BASE / "config/inventory/inventory_base.yaml").read_text(encoding="utf-8")
    )


def create_runtime_profiles(runtime_root=None, inventory_path=None):
    devices = load_target_devices(inventory_path)
    runtime_root = Path(runtime_root or BASE / "config/runtime").resolve()
    artifact = build_qbt_onbox(BASE / "artifacts/qbt_onbox.py")
    policy = yaml.safe_load(
        (BASE / "config/inventory/qkd_policy.yaml").read_text(encoding="utf-8")
    )["qkd_policy"]
    inventory = _load_inventory_base()
    secrets_config = inventory["secrets"]
    script_user = secrets_config["script_user"]
    rpc_key = secrets_config["rpc_ssh_key_name"]
    env = Environment(
        loader=FileSystemLoader(BASE / "config/templates/qbt"),
        autoescape=False,
        keep_trailing_newline=True,
    )
    results = {}
    for name in TARGET_DEVICES:
        local_sae = devices[name]["sae_id"]
        links = [build_link(name, devices)]
        context = {
            "device_name": name,
            "local_sae": local_sae,
            "kme_ip": devices[name]["kme_ip"],
            "kme_port": devices[name]["kme_port"],
            "qkd_policy": policy,
            "links": links,
            "script_user": script_user,
            "rpc_ssh_key": f"/var/home/{script_user}/.ssh/{rpc_key}",
            "script_user_class": secrets_config["script_user_class"],
        }
        directory = runtime_root / name
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, directory / RUNTIME_NAME)
        (directory / "qbt_onbox_config.json").write_text(
            env.get_template("qbt_onbox_config.json.j2").render(**context)
        )
        (directory / "qbt_onbox_inventory.json").write_text(
            env.get_template("qbt_onbox_inventory.json.j2").render(**context)
        )
        (directory / "qbt_onbox_config.json").chmod(0o640)
        (directory / "qbt_onbox_inventory.json").chmod(0o640)
        results[name] = directory
    return validate_runtime_profiles(runtime_root, inventory_path)


def validate_runtime_profiles(runtime_root=None, inventory_path=None):
    devices = load_target_devices(inventory_path)
    runtime_root = Path(runtime_root or BASE / "config/runtime")
    policy = yaml.safe_load(
        (BASE / "config/inventory/qkd_policy.yaml").read_text(encoding="utf-8")
    )["qkd_policy"]
    result = {}
    for name in TARGET_DEVICES:
        directory = runtime_root / name
        script = directory / RUNTIME_NAME
        config = json.loads((directory / "qbt_onbox_config.json").read_text(encoding="utf-8"))
        onbox_inventory = json.loads(
            (directory / "qbt_onbox_inventory.json").read_text(encoding="utf-8")
        )
        if script.is_symlink() or not script.is_file():
            raise ValueError(f"{name}: missing or non-self-contained on-box script")
        script_source = script.read_text(encoding="utf-8")
        if "from qbt_etsi_client import" in script_source or "qbt_runtime_core.py" in script_source:
            raise ValueError(f"{name}: QBT on-box script depends on a separate Python helper")
        try:
            ast.parse(script_source, filename=str(script))
        except SyntaxError as error:
            raise ValueError(f"{name}: generated QBT on-box script has invalid Python syntax") from error
        link = build_link(name, devices)
        if config.get("links") != [link] or onbox_inventory.get("links") != [link]:
            raise ValueError(f"{name}: generated peer/link configuration is stale")
        expected = devices[name]
        if (
            config.get("device_name") != name
            or config.get("local_sae") != expected["sae_id"]
            or onbox_inventory.get("local_sae") != expected["sae_id"]
        ):
            raise ValueError(f"{name}: SAE identity mismatch across sidecars")
        if (
            config.get("kme_ip") != expected["kme_ip"]
            or config.get("kme_port") != expected["kme_port"]
            or onbox_inventory.get("kme_ip") != expected["kme_ip"]
            or onbox_inventory.get("kme_port") != expected["kme_port"]
        ):
            raise ValueError(f"{name}: local KME endpoint differs from router inventory")
        if config.get("qkd_policy") != policy:
            raise ValueError(f"{name}: runtime policy differs from config/inventory/qkd_policy.yaml")
        if (
            policy.get("execution_interval_seconds") != 60
            or policy.get("key_activation_interval_seconds") != 300
            or policy.get("key_batch_size") != 4
            or policy.get("max_installed_keys") != 4
        ):
            raise ValueError("QBT runtime requires 60-second ticks, 300-second spacing and four slots")
        result[name] = directory
    return result


def validate_pki_bundle(pki):
    pki = Path(pki).resolve(strict=True)
    if not pki.is_dir():
        raise ValueError(f"QBT PKI path is not a directory: {pki}")
    required = (
        "ca.pem",
        "sae-001.pem",
        "sae-001.key",
        "sae-002.pem",
        "sae-002.key",
    )
    missing = [filename for filename in required if not (pki / filename).is_file()]
    if missing:
        raise ValueError(f"QBT PKI bundle is missing: {', '.join(missing)}")
    return pki


def _read_junos_state(dev):
    security = dev.rpc.get_config(
        filter_xml="<configuration><security><macsec/><authentication-key-chains/></security></configuration>"
    )
    associations = {
        node.findtext("name")
        for node in security.xpath(".//macsec/connectivity-association")
        if node.findtext("name")
    }
    keychains = {
        node.findtext("name")
        for node in security.xpath(".//authentication-key-chains/key-chain")
        if node.findtext("name")
    }
    key0 = bool(security.xpath(
        ".//authentication-key-chains/key-chain[name=$name]/key[name='0']",
        name=KEYCHAIN,
    ))
    seed_names = security.xpath(
        ".//authentication-key-chains/key-chain[name=$name]/key[name='0']/key-name/text()",
        name=KEYCHAIN,
    )
    return associations, keychains, key0, (seed_names[0] if seed_names else None)


def _peer_public_key(client, run, script_user, key_name):
    key_path = f"/var/home/{script_user}/.ssh/{key_name}"
    run(
        client,
        f"mkdir -p /var/home/{script_user}/.ssh /var/home/{script_user}/qbt-state "
        f"/var/home/{script_user}/logs && "
        f"chown {script_user} /var/home/{script_user}/.ssh "
        f"/var/home/{script_user}/qbt-state /var/home/{script_user}/logs && "
        f"chmod 700 /var/home/{script_user}/.ssh /var/home/{script_user}/qbt-state && "
        f"if test ! -s {key_path}; then "
        f"su -s /bin/sh {script_user} -c 'ssh-keygen -t ed25519 -N \"\" -f {key_path}'; fi && "
        f"test -s {key_path}.pub"
    )
    return run(client, f"cat {key_path}.pub")


def _op_python_files(client, run):
    return run(
        client,
        "for f in /var/db/scripts/op/*.py; do "
        "test -f \"$f\" && basename \"$f\"; done | sort",
    ).splitlines()


def _junos_commands(public_key, seed, policy, script_user, script_user_class, interface):
    interval = int(policy["execution_interval_seconds"])
    commands = [
        "set system scripts language python3",
        f"set system scripts op file {RUNTIME_NAME}",
        f"set event-options event-script file {RUNTIME_NAME} python-script-user {script_user}",
        "set event-options generate-event QBT_TIMER time-interval " + str(interval),
        "set event-options policy QBT_POLICY events QBT_TIMER",
        f"set event-options policy QBT_POLICY then event-script {RUNTIME_NAME}",
        f"set system login user {script_user} class {script_user_class}",
        f'set system login user {script_user} authentication ssh-ed25519 "{public_key.strip()}"',
        f"set security macsec connectivity-association {CA_NAME} cipher-suite gcm-aes-xpn-256",
        f"set security macsec connectivity-association {CA_NAME} security-mode static-cak",
        f"set security macsec connectivity-association {CA_NAME} pre-shared-key-chain {KEYCHAIN}",
        f"set security macsec interfaces {interface} connectivity-association {CA_NAME}",
    ]
    if seed:
        seed_name, seed_value = seed
        commands.extend([
            f"set security authentication-key-chains key-chain {KEYCHAIN} key 0 key-name "
            + seed_name,
            f'set security authentication-key-chains key-chain {KEYCHAIN} key 0 secret "{seed_value}"',
            f"set security authentication-key-chains key-chain {KEYCHAIN} key 0 start-time 2026-1-1.00:01",
        ])
    return commands


def deploy_runtime(clients, inventory_path, password, pki, runtime_root, run, transfer):
    if set(clients) != set(TARGET_DEVICES):
        raise ValueError("Runtime deployment requires both EVO1 and EVO2")
    devices = load_target_devices(inventory_path)
    hosts = {name: devices[name]["ip"] for name in TARGET_DEVICES}
    pki = validate_pki_bundle(pki)
    runtime_dirs = validate_runtime_profiles(runtime_root, inventory_path)
    inventory = _load_inventory_base()
    secrets_config = inventory["secrets"]
    script_user = secrets_config["script_user"]
    script_user_class = secrets_config["script_user_class"]
    rpc_key = secrets_config["rpc_ssh_key_name"]

    states = {}
    seed_names = {}
    for name in TARGET_DEVICES:
        with Device(host=hosts[name], user="root", passwd=password, gather_facts=False) as dev:
            associations, keychains, key0, key0_name = _read_junos_state(dev)
            if associations - {CA_NAME}:
                raise ValueError(f"{name}: unrelated MACsec associations exist")
            if keychains - {KEYCHAIN}:
                raise ValueError(f"{name}: unrelated MACsec keychains exist")
            if (CA_NAME in associations) != (KEYCHAIN in keychains):
                raise ValueError(f"{name}: partial QBT MACsec configuration; refusing repair")
            if KEYCHAIN in keychains and not key0:
                raise ValueError(f"{name}: QBT keychain has no seed key; refusing overwrite")
            states[name] = CA_NAME not in associations
            if not states[name]:
                if not key0_name:
                    raise ValueError(f"{name}: QBT seed key has no key-name; refusing deployment")
                seed_names[name] = key0_name
    if len(set(states.values())) != 1:
        raise ValueError("EVO1/EVO2 MACsec state differs; refusing partial deployment")
    if not states["EVO1"] and seed_names["EVO1"] != seed_names["EVO2"]:
        raise ValueError("EVO1/EVO2 QBT seed key names differ; refusing unsynchronized deployment")
    allowed_op_scripts = {RUNTIME_NAME, *OLD_QBT_HELPERS}
    for name in TARGET_DEVICES:
        scripts = _op_python_files(clients[name], run)
        unexpected = sorted(set(scripts) - allowed_op_scripts)
        if unexpected:
            raise ValueError(
                f"{name}: unexpected Python scripts in /var/db/scripts/op: {unexpected}; "
                "refusing partial deployment"
            )
    seed = None
    if states["EVO1"]:
        seed_value = secrets.token_hex(32)
        seed = (hashlib.sha256(bytes.fromhex(seed_value)).hexdigest(), seed_value)

    keys = {}
    for name in TARGET_DEVICES:
        keys[name] = _peer_public_key(clients[name], run, script_user, rpc_key)

    for name in TARGET_DEVICES:
        client = clients[name]
        peer = "EVO2" if name == "EVO1" else "EVO1"
        host_key = clients[peer].get_transport().get_remote_server_key()
        known = hosts[peer] + " " + host_key.get_name() + " " + host_key.get_base64() + "\n"
        local = runtime_dirs[name]
        staging = run(client, "mktemp -d /var/tmp/qbt-runtime.XXXXXX")
        try:
            for filename in (RUNTIME_NAME, "qbt_onbox_config.json", "qbt_onbox_inventory.json"):
                transfer(client, local / filename, staging + "/" + filename)
            with tempfile.TemporaryDirectory(prefix="qbt-known-hosts-") as folder:
                known_file = Path(folder) / "known_hosts"
                known_file.write_text(known)
                transfer(client, known_file, staging + "/known_hosts")
            sae = devices[name]["sae_id"]
            for source, filename in (
                (pki / "ca.pem", "qbt-ca.pem"),
                (pki / (sae + ".pem"), sae + ".crt"),
                (pki / (sae + ".key"), sae + ".key"),
            ):
                transfer(client, source, staging + "/" + filename)
            op = "/var/db/scripts/op"
            event = "/var/db/scripts/event"
            certs = "/var/db/scripts/certs"
            setup = (
                "set -eu; mkdir -p " + op + " " + event + " " + certs + "; "
                f"for old in {' '.join(OLD_QBT_HELPERS)}; do rm -f {op}/$old {event}/$old; done; "
                f"for f in {RUNTIME_NAME} qbt_onbox_config.json qbt_onbox_inventory.json; do "
                f"test ! -L {op}/$f; test ! -L {op}/$f.new; "
                f"cat {staging}/$f > {op}/$f.new; chown root:{script_user} {op}/$f.new; "
                "chmod 550 " + op + "/$f.new; mv -f " + op + "/$f.new " + op + "/$f; done; "
                f"cp {op}/{RUNTIME_NAME} {event}/{RUNTIME_NAME}; "
                f"chown root:{script_user} {event}/{RUNTIME_NAME}; chmod 550 {event}/{RUNTIME_NAME}; "
                f"chown {script_user} {staging}/known_hosts; chmod 600 {staging}/known_hosts; "
                f"cp {staging}/known_hosts /var/home/{script_user}/.ssh/known_hosts; "
                f"chown {script_user} /var/home/{script_user}/.ssh/known_hosts; "
                f"touch /var/home/{script_user}/.ssh/authorized_keys; "
                f"grep -Fxq {shlex.quote(keys[peer].strip())} "
                f"/var/home/{script_user}/.ssh/authorized_keys || "
                f"printf '%s\\n' {shlex.quote(keys[peer].strip())} "
                f">> /var/home/{script_user}/.ssh/authorized_keys; "
                f"chown {script_user} /var/home/{script_user}/.ssh/authorized_keys; "
                f"chmod 600 /var/home/{script_user}/.ssh/authorized_keys; "
                f"for f in qbt-ca.pem {sae}.crt {sae}.key; do "
                f"test ! -L {certs}/$f; cat {staging}/$f > {certs}/$f.new; "
                f"chown root:{script_user} {certs}/$f.new; chmod 640 {certs}/$f.new; "
                f"mv -f {certs}/$f.new {certs}/$f; done; "
                f"rm -f {staging}/qbt_onbox.py {staging}/qbt_onbox_config.json "
                f"{staging}/qbt_onbox_inventory.json {staging}/known_hosts "
                f"{staging}/qbt-ca.pem {staging}/{sae}.crt {staging}/{sae}.key; "
                f"rmdir {staging}"
            )
            run(client, setup)
            remaining_scripts = _op_python_files(client, run)
            if remaining_scripts != [RUNTIME_NAME]:
                raise ValueError(
                    f"{name}: expected only {RUNTIME_NAME} in /var/db/scripts/op; "
                    f"found {remaining_scripts}"
                )
        except BaseException:
            run(client, f"rm -rf {shlex.quote(staging)}")
            raise
        install_transport(
            client,
            name,
            run,
            transfer,
            script_user=script_user,
            kme_ip=devices[name]["kme_ip"],
        )

    for name in TARGET_DEVICES:
        peer = "EVO2" if name == "EVO1" else "EVO1"
        with Device(host=hosts[name], user="root", passwd=password, gather_facts=False) as dev:
            policy = json.loads(
                (runtime_dirs[name] / "qbt_onbox_config.json").read_text()
            )["qkd_policy"]
            commands = _junos_commands(
                keys[peer],
                seed if states[name] else None,
                policy,
                script_user,
                script_user_class,
                build_link(name, devices)["interface"],
            )
            with Config(dev, mode="exclusive") as cu:
                cu.load("\n".join(commands), format="set", merge=True)
                cu.commit_check()
                cu.commit(comment="QBT on-box runtime and key rotation event")
        print(
            f"{name}: installed one op script, two JSON sidecars, QBT ETSI helper, "
            "and 60-second event timer; existing QBT seed preserved"
        )
