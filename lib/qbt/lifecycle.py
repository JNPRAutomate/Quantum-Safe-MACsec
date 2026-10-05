"""Fail-closed checks for persistent QBT EVO containers and MACsec acceptance."""

import json
import re
import shlex
import time
from collections import Counter
import uuid

import paramiko
from scp import SCPException

from lib.qbt.deployment import KME_ENVIRONMENT, OWNER, render_profile
from lib.qbt.network import ADDRESSES, OOB_NETWORK, attach_networks
from lib.qbt.runtime import admin_command, admin_script_command


REMOTE_ROOT = "/var/db/qbt"
LICENSE_STATE_FILES = ("data/license.storage", "data/license.lic")
REQUIRED_FILES = (
    "secrets/machine-id",
    "secrets/master-key-id",
    "secrets/master-key-bytes",
    "secrets/kme-license",
    "license-staging/license.machine",
)
SENSITIVE_ENVIRONMENT = re.compile(
    r"(?:PASSWORD|TOKEN|SECRET|LICENSE|MASTER_KEY|PRIVATE_KEY|API_KEY|ACCESS_KEY|CAK)",
    re.IGNORECASE,
)
EXPECTED_NETWORKS = ("jnpr_cntrz_net", OOB_NETWORK)
MACSEC_CA = "QBT_EVO"
MANAGEMENT_ADDRESSES = {"EVO1": "10.38.97.218", "EVO2": "10.38.97.228"}


def run_private(client, command, *, timeout=180):
    """Run a command whose output or error may contain licence material."""
    _, stdout, stderr = client.exec_command(
        "/bin/sh -c " + shlex.quote(command), timeout=timeout
    )
    output = stdout.read().decode("utf-8", errors="replace")
    stderr.read()
    status = stdout.channel.recv_exit_status()
    if status:
        raise LifecycleError(
            f"Private QBT command failed ({status}); output withheld"
        )
    return output.strip()


class LifecycleError(RuntimeError):
    """A QBT lifecycle precondition or verification failed."""


def _device_root(device):
    if device not in ("EVO1", "EVO2"):
        raise LifecycleError("Lifecycle operations are limited to EVO1 and EVO2")
    return f"{REMOTE_ROOT}/{device.lower()}"


def classify_license_status(output, today=None):
    """Classify validity without inferring feature entitlements."""
    normalized = output.casefold()
    missing_markers = (
        "no valid license found",
        "no license found",
        "license not found",
        "license not activated",
        "activation required",
    )
    invalid_markers = (
        "invalid license",
        "license expired",
        "expired license",
        "license is expired",
    )
    if not output.strip():
        return "unknown"
    if any(marker in normalized for marker in missing_markers):
        return "missing"
    if any(marker in normalized for marker in invalid_markers):
        return "invalid"
    host_id = _host_id_from_status(output)
    expiry = re.search(
        r"(?:expir(?:y|ation|es)(?:\s+(?:date|at|on))?|valid\s+until)"
        r"\s*[:=]?\s*(\d{4}-\d{2}-\d{2})",
        output,
        re.IGNORECASE,
    )
    if not host_id or not expiry:
        return "unknown"
    try:
        from datetime import date, datetime, timezone

        expiry_date = date.fromisoformat(expiry.group(1))
    except ValueError:
        return "unknown"
    if today is None:
        today = datetime.now(timezone.utc).date()
    return "invalid" if expiry_date <= today else "active"


def _host_id_from_status(output):
    match = re.search(
        r"\bhost[\s_-]*id\s*[:=]\s*([^\s,;]+)", output, re.IGNORECASE
    )
    if match:
        return match.group(1)
    standalone = output.strip()
    return standalone if re.fullmatch(r"[A-Za-z0-9:-]+", standalone) else None


def _read_remote_file_digests(client, device, run, require_license_state):
    root = _device_root(device)
    required = [root + "/" + relative for relative in REQUIRED_FILES]
    optional = [root + "/" + relative for relative in LICENSE_STATE_FILES]
    commands = ["set -eu"]
    for path in required:
        quoted = shlex.quote(path)
        commands.append(f"test ! -L {quoted} && test -f {quoted} && test -s {quoted}")
        commands.append("sha256sum " + quoted)
    for path in optional:
        quoted = shlex.quote(path)
        commands.append(
            f"if test -e {quoted}; then "
            f"test ! -L {quoted} && test -f {quoted} && test -s {quoted}; "
            f"sha256sum {quoted}; fi"
        )
    output = run(client, "; ".join(commands))
    result = {}
    allowed = set(required + optional)
    for line in output.splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", fields[0]):
            raise LifecycleError(f"{device}: malformed persistent-file fingerprint")
        path = fields[1].lstrip("*")
        if path not in allowed:
            raise LifecycleError(f"{device}: unexpected persistent-file fingerprint path")
        result[path[len(root) + 1:]] = fields[0].lower()
    missing = [path for path in REQUIRED_FILES if path not in result]
    if missing:
        raise LifecycleError(f"{device}: required identity or licence input is missing")
    if require_license_state and not any(
        path in result for path in LICENSE_STATE_FILES
    ):
        raise LifecycleError(f"{device}: persistent active licence state is missing")
    return result


def _read_license_status(client, container, device, expected_host_id, run_private):
    output = run_private(
        client, admin_command(container, "license", "status"), timeout=60
    )
    state = classify_license_status(output)
    if state == "unknown":
        raise LifecycleError(
            f"{device}: QBT licence status does not establish validity or absence"
        )
    status_host_id = _host_id_from_status(output)
    if status_host_id and status_host_id.casefold() != expected_host_id.casefold():
        raise LifecycleError(f"{device}: licence is associated with a different host ID")
    if state == "active" and not status_host_id:
        raise LifecycleError(f"{device}: active licence status omits its host ID")
    feature_status = (
        "unreported (QBT says 'No feature in file')"
        if "no feature in file" in output.casefold()
        else "not independently verified"
    )
    return {"state": state, "feature_entitlements": feature_status}


def _environment_map(entries):
    environment = {}
    for item in entries or []:
        if not isinstance(item, str) or "=" not in item:
            raise LifecycleError("Container image has an unsupported environment entry")
        key, value = item.split("=", 1)
        if not key:
            raise LifecycleError("Container image has an invalid environment variable")
        if key not in KME_ENVIRONMENT and SENSITIVE_ENVIRONMENT.search(key):
            file_reference = key.endswith(("_FILE", "_PATH")) and value.startswith("/")
            if not file_reference:
                raise LifecycleError(
                    "Container has inline secret environment data; refusing lifecycle change"
                )
        environment[key] = value
    return environment


def _parse_environment(config, image):
    environment = _environment_map(config.get("Env"))
    expected = _environment_map((image.get("Config") or {}).get("Env"))
    expected.update(KME_ENVIRONMENT)
    if environment != expected:
        raise LifecycleError("Container environment differs from the QBT profile")
    return environment


def _validate_mounts(info, device):
    root = _device_root(device)
    expected = {
        "/var/lib/qbt-kme": (root + "/data", True),
        "/run/secrets": (root + "/secrets", False),
        "/etc/machine-id": (root + "/secrets/machine-id", False),
        "/run/license-staging": (root + "/license-staging", False),
    }
    mounts = info.get("Mounts")
    if not isinstance(mounts, list):
        raise LifecycleError(f"{device}: Docker did not report container mounts")
    observed = {}
    for mount in mounts:
        destination = mount.get("Destination")
        if destination in observed:
            raise LifecycleError(f"{device}: duplicate bind mount destination")
        if mount.get("Type") == "tmpfs" and destination in ("/tmp", "/run"):
            continue
        if mount.get("Type") != "bind" or destination not in expected:
            raise LifecycleError(f"{device}: unexpected Docker mount")
        observed[destination] = mount
    if set(observed) != set(expected):
        raise LifecycleError(f"{device}: persistent data/identity bind mount is missing")
    normalized = {}
    for destination, (source, writable) in expected.items():
        mount = observed[destination]
        if mount.get("Source") != source or bool(mount.get("RW")) != writable:
            raise LifecycleError(f"{device}: persistent mount source or mode differs")
        normalized[destination] = {"source": source, "writable": writable}
    return normalized


def _validate_container(info, device, image_ref, image):
    container = "qbt-" + device.lower()
    if info.get("Name") != "/" + container:
        raise LifecycleError(f"{device}: container name mismatch")
    image_id = image.get("Id")
    if info.get("Image") != image_id:
        raise LifecycleError(f"{device}: container image differs from the loaded image")
    config = info.get("Config") or {}
    labels = config.get("Labels") or {}
    if (
        labels.get("io.qbt.lab.owner") != OWNER
        or labels.get("io.qbt.lab.device") != device.lower()
    ):
        raise LifecycleError(f"{device}: container ownership labels do not match")
    if config.get("Image") not in (image_ref, image_id):
        raise LifecycleError(f"{device}: container image reference differs")
    host = info.get("HostConfig") or {}
    restart = host.get("RestartPolicy") or {}
    if (
        host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is True
        or host.get("AutoRemove") is True
        or restart.get("Name") != "unless-stopped"
        or host.get("PortBindings")
    ):
        raise LifecycleError(f"{device}: container security or restart profile differs")
    if config.get("WorkingDir") != "/var/lib/qbt-kme" or config.get("Cmd") != ["-f", "run"]:
        raise LifecycleError(f"{device}: container command or working directory differs")
    if not any(value.casefold() == "all" for value in host.get("CapDrop") or []):
        raise LifecycleError(f"{device}: container does not drop all capabilities")
    if not any(
        value.casefold() == "net_bind_service" for value in host.get("CapAdd") or []
    ):
        raise LifecycleError(f"{device}: container lacks the approved bind-service capability")
    if not any(
        value in ("no-new-privileges:true", "no-new-privileges")
        for value in host.get("SecurityOpt") or []
    ):
        raise LifecycleError(f"{device}: container lacks no-new-privileges")
    if not {"/tmp", "/run"}.issubset((host.get("Tmpfs") or {}).keys()):
        raise LifecycleError(f"{device}: container tmpfs profile differs")
    if config.get("Tty") or config.get("OpenStdin"):
        raise LifecycleError(f"{device}: interactive container is not supported")
    return _parse_environment(config, image)


def _network_state(info, device, required):
    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    if required:
        if set(networks) != set(EXPECTED_NETWORKS):
            raise LifecycleError(f"{device}: expected ETSI and OOB networks are not attached")
        expected = {
            "jnpr_cntrz_net": ADDRESSES[device][0],
            OOB_NETWORK: ADDRESSES[device][1],
        }
        for network, address in expected.items():
            if networks[network].get("IPAddress") != address:
                raise LifecycleError(f"{device}: unexpected address on {network}")
        return expected
    if not set(networks).issubset(set(EXPECTED_NETWORKS) | {"none"}):
        raise LifecycleError(f"{device}: unexpected network on the new container")
    return {
        network: entry.get("IPAddress")
        for network, entry in networks.items()
        if network in EXPECTED_NETWORKS
    }


def preflight_device(
    client,
    device,
    run,
    run_private,
    image_ref,
    *,
    require_networks=True,
    allow_inactive_license=False,
):
    container = "qbt-" + device.lower()
    info_list = json.loads(run(client, "docker inspect " + shlex.quote(container)))
    if not isinstance(info_list, list) or len(info_list) != 1:
        raise LifecycleError(f"{device}: Docker inspect did not return one container")
    info = info_list[0]
    if (info.get("State") or {}).get("Status") != "running":
        raise LifecycleError(f"{device}: QBT container is not running")
    image_list = json.loads(run(client, "docker image inspect " + shlex.quote(image_ref)))
    if not isinstance(image_list, list) or len(image_list) != 1:
        raise LifecycleError(f"{device}: loaded QBT image cannot be inspected")
    image = image_list[0]
    if image.get("Architecture") != "amd64" or image.get("Os") != "linux":
        raise LifecycleError(f"{device}: QBT image must be linux/amd64")
    environment = _validate_container(info, device, image_ref, image)
    mounts = _validate_mounts(info, device)
    network_addresses = _network_state(info, device, require_networks)
    root = _device_root(device)
    directories = (root, root + "/data", root + "/secrets", root + "/license-staging")
    run(
        client,
        "set -eu; "
        + "; ".join(
            "test ! -L "
            + shlex.quote(path)
            + " && test -d "
            + shlex.quote(path)
            for path in directories
        ),
    )
    host_id_output = run_private(
        client, admin_command(container, "license", "host-id"), timeout=60
    )
    host_id = _host_id_from_status(host_id_output)
    machine_id = run(client, "cat " + shlex.quote(root + "/secrets/machine-id")).strip()
    if not host_id or not machine_id or machine_id.casefold() != host_id.casefold():
        raise LifecycleError(f"{device}: QBT host ID differs from persistent machine-id")
    license_status = _read_license_status(
        client, container, device, host_id, run_private
    )
    file_digests = _read_remote_file_digests(
        client,
        device,
        run,
        require_license_state=(license_status["state"] == "active"),
    )
    if not allow_inactive_license and license_status["state"] != "active":
        raise LifecycleError(
            f"{device}: QBT licence state is {license_status['state']}; "
            "do not remove or recreate this container"
        )
    return {
        "device": device,
        "container": container,
        "container_id": info["Id"],
        "image_id": image["Id"],
        "image_ref": image_ref,
        "environment": environment,
        "mounts": mounts,
        "network_addresses": network_addresses,
        "file_digests": file_digests,
        "host_id": host_id,
        "license_status": license_status,
        "inspect": info,
    }


def preflight_pair(
    clients,
    run,
    run_private,
    image_ref,
    *,
    require_networks=True,
    allow_inactive_license=False,
):
    if set(clients) != {"EVO1", "EVO2"}:
        raise LifecycleError("QBT preflight requires EVO1 and EVO2")
    result = {
        device: preflight_device(
            clients[device],
            device,
            run,
            run_private,
            image_ref,
            require_networks=require_networks,
            allow_inactive_license=allow_inactive_license,
        )
        for device in ("EVO1", "EVO2")
    }
    if result["EVO1"]["image_id"] != result["EVO2"]["image_id"]:
        raise LifecycleError("EVO1 and EVO2 do not use the same QBT image")
    return result


def _wait_for_replacement_preflight(
    client, device, run, run_private, image_ref, *, timeout=120, interval=2
):
    deadline = time.monotonic() + timeout
    retryable = (
        "container is not running",
        "private qbt command failed",
        "qbt licence status does not establish validity or absence",
    )
    while True:
        try:
            return preflight_device(
                client,
                device,
                run,
                run_private,
                image_ref,
            )
        except LifecycleError as error:
            if not any(message in str(error).casefold() for message in retryable):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LifecycleError(
                    f"{device}: replacement did not become ready with its active licence"
                ) from error
            time.sleep(min(interval, remaining))


def public_summary(snapshot):
    return {
        "device": snapshot["device"],
        "container": snapshot["container"],
        "container_id_prefix": snapshot["container_id"][:12],
        "image_id": snapshot["image_id"],
        "license_state": snapshot["license_status"]["state"],
        "feature_entitlements": snapshot["license_status"]["feature_entitlements"],
        "network_addresses": snapshot["network_addresses"],
        "persistent_identity_files_verified": True,
    }


def activate_offline_license(client, device, run, run_private):
    """Activate only an explicitly missing licence; never replace an active one."""
    container = "qbt-" + device.lower()
    root = _device_root(device)
    host_id_output = run_private(
        client, admin_command(container, "license", "host-id"), timeout=60
    )
    host_id = _host_id_from_status(host_id_output)
    machine_id = run(client, "cat " + shlex.quote(root + "/secrets/machine-id")).strip()
    if not host_id or machine_id.casefold() != host_id.casefold():
        raise LifecycleError(f"{device}: machine-id does not match the QBT host ID")
    identity_before = _read_remote_file_digests(
        client, device, run, require_license_state=False
    )
    output = run_private(
        client, admin_command(container, "license", "status"), timeout=60
    )
    if classify_license_status(output) != "missing":
        raise LifecycleError(
            f"{device}: activation is allowed only when QBT explicitly reports no licence"
        )
    if _host_id_from_status(output) not in (None, host_id):
        raise LifecycleError(f"{device}: missing licence status reports a different host ID")
    if any(path in identity_before for path in LICENSE_STATE_FILES):
        raise LifecycleError(
            f"{device}: licence storage already exists; refusing a second activation"
        )
    command = admin_script_command(
        container,
        'exec qbt-kme license activate offline '
        '"$(cat /run/secrets/kme-license)" /run/license-staging/license.machine',
    )
    run_private(client, command, timeout=120)
    status = run_private(
        client, admin_command(container, "license", "status"), timeout=60
    )
    status_host_id = _host_id_from_status(status)
    if (
        classify_license_status(status) != "active"
        or not status_host_id
        or status_host_id.casefold() != host_id.casefold()
    ):
        raise LifecycleError(
            f"{device}: offline activation could not be verified; output withheld"
        )
    identity_after = _read_remote_file_digests(client, device, run)
    if any(
        identity_after.get(path) != identity_before.get(path)
        for path in REQUIRED_FILES
    ):
        raise LifecycleError(f"{device}: activation changed persistent identity inputs")
    return {
        "device": device,
        "host_id_matches_machine_id": True,
        "license_state": "active",
        "license_state_persisted": True,
        "feature_entitlements": (
            "unreported (QBT says 'No feature in file')"
            if "no feature in file" in status.casefold()
            else "not independently verified"
        ),
    }


def _event_command(sae_id, interface, script_user):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", script_user):
        raise ValueError("Invalid QBT on-box script user")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", sae_id):
        raise ValueError("Invalid QBT SAE identity")
    if not re.fullmatch(r"(?:et|xe|ge)-\d+/\d+/\d+(?:\.\d+)?", interface):
        raise ValueError("Invalid MACsec interface")
    safe_interface = interface.replace("/", "_")
    prefix = (
        f"/var/home/{script_user}/logs/"
        f"qbt_debug_{sae_id}_{safe_interface}.log"
    )
    return (
        "set -eu; found=0; "
        f"for file in {prefix}*; do "
        'if [ -f "$file" ]; then found=1; '
        "if grep -hE 'MKA KEY CONFIRMED|SAK_ROLLOVER|KEYCHAIN INSTALL OK' \"$file\"; "
        "then :; else code=$?; if [ \"$code\" -ne 1 ]; then exit \"$code\"; fi; fi; "
        "fi; done; "
        'if [ "$found" -eq 0 ]; then printf "NO_QBT_ROTATION_LOG\\n"; fi'
    )


def parse_rotation_events(output):
    events = []
    for line in output.splitlines():
        if "MKA KEY CONFIRMED" in line:
            kind = "confirmed"
        elif "SAK_ROLLOVER" in line:
            kind = "rollover"
        elif "KEYCHAIN INSTALL OK" in line:
            kind = "keychain"
        else:
            continue
        fields = dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)", line))
        events.append({
            "line": line.strip(),
            "kind": kind,
            "key_id": fields.get("key_id", "").lower(),
            "latest_sak_an": fields.get("latest_sak_an"),
            "previous_sak_an": fields.get("previous_sak_an"),
        })
    return events


def _target_interface_block(output, interface):
    lines = []
    active = False
    for line in output.splitlines():
        stripped = line.strip()
        marker, separator, value = stripped.partition(":")
        if separator and marker.casefold() == "interface name":
            if active:
                break
            active = value.strip() == interface
        if active:
            lines.append(stripped)
    return lines


def parse_macsec_inuse(output, interface, ca_name=MACSEC_CA):
    block = _target_interface_block(output, interface)
    current_ca = None
    current_status = None
    for line in block:
        marker, separator, value = line.partition(":")
        if separator and marker.casefold() == "ca name":
            if current_ca == ca_name and current_status == "inuse":
                return True
            current_ca = value.strip()
            current_status = None
        status = re.search(r"(?:^|\s)Status:\s*([^\s]+)", line, re.IGNORECASE)
        if status:
            current_status = status.group(1).casefold()
    return current_ca == ca_name and current_status == "inuse"


def parse_mka_secured(output, interface):
    block = _target_interface_block(output, interface)
    state = None
    suspended = None
    for line in block:
        marker, separator, value = line.partition(":")
        if not separator:
            continue
        if marker.casefold() == "interface state":
            state = value.strip().casefold()
        elif marker.casefold() == "mka suspended":
            suspended = value.strip().casefold()
    return (
        state is not None
        and state.startswith("secured")
        and suspended is not None
        and suspended.startswith(("0", "no", "false"))
    )


def _fresh_events(events, baseline):
    seen = Counter()
    for event in events:
        seen[event["line"]] += 1
        if seen[event["line"]] > baseline[event["line"]]:
            yield event


def _rotation_evidence(events, baseline):
    rollovers = {}
    for device in ("EVO1", "EVO2"):
        fresh = list(_fresh_events(events[device], baseline[device]))
        installs = [event for event in fresh if event["kind"] == "keychain"]
        confirmed = {
            event["key_id"] for event in fresh
            if event["kind"] == "confirmed" and event["key_id"]
        }
        changed_saks = {
            event["key_id"] for event in fresh
            if event["kind"] == "rollover"
            and event["key_id"]
            and event["latest_sak_an"] is not None
            and event["previous_sak_an"] is not None
            and event["latest_sak_an"] != event["previous_sak_an"]
        }
        if not installs or not confirmed or not (confirmed & changed_saks):
            return False, f"{device}: fresh keychain/MKA/SAK rollover evidence is incomplete"
        rollovers[device] = confirmed & changed_saks
    common = rollovers["EVO1"] & rollovers["EVO2"]
    if not common:
        return False, "No shared SAK rollover Key-ID was confirmed on both EVOs"
    return True, sorted(common)[-1]


def verify_rotation(
    clients,
    run,
    devices,
    script_user,
    *,
    timeout=900,
    poll_interval=30,
    sleep=time.sleep,
):
    if timeout < 0 or poll_interval <= 0:
        raise ValueError("Rotation timeout and poll interval must be non-negative/positive")
    baselines = {}
    for device in ("EVO1", "EVO2"):
        link = devices[device]["link"]
        output = run(
            clients[device],
            _event_command(devices[device]["sae_id"], link["interface"], script_user),
        )
        baselines[device] = Counter(
            event["line"] for event in parse_rotation_events(output)
        )
    deadline = time.monotonic() + timeout
    last_reason = "fresh bilateral rollover not observed"
    while True:
        events = {}
        operational = {}
        for device in ("EVO1", "EVO2"):
            link = devices[device]["link"]
            output = run(
                clients[device],
                _event_command(devices[device]["sae_id"], link["interface"], script_user),
            )
            events[device] = parse_rotation_events(output)
            connections = run(
                clients[device],
                "cli -c " + shlex.quote("show security macsec connections"),
            )
            mka = run(
                clients[device],
                "cli -c " + shlex.quote("show security mka sessions"),
            )
            operational[device] = (
                parse_macsec_inuse(connections, link["interface"])
                and parse_mka_secured(mka, link["interface"])
            )
        complete, evidence = _rotation_evidence(events, baselines)
        if complete and all(operational.values()):
            return {
                "shared_sak_key_id": evidence,
                "macsec_inuse": operational,
                "fresh_rollovers": {
                device: sum(
                    event["kind"] == "rollover"
                    for event in _fresh_events(events[device], baselines[device])
                )
                for device in ("EVO1", "EVO2")
                },
            }
        last_reason = (
            evidence if not complete
            else "MKA Secured or MACsec in-use not confirmed on both EVOs"
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LifecycleError(f"MACsec verification timed out: {last_reason}")
        sleep(min(poll_interval, remaining))


def assert_continuity(before, after):
    fields = ("image_id", "environment", "mounts", "host_id")
    if any(before[field] != after[field] for field in fields):
        raise LifecycleError(
            f"{before['device']}: image, identity, mount or licence fingerprint changed"
        )
    for path in REQUIRED_FILES:
        if before["file_digests"].get(path) != after["file_digests"].get(path):
            raise LifecycleError(
                f"{before['device']}: persistent identity/licence inputs changed"
            )
    if before["license_status"]["state"] != after["license_status"]["state"]:
        raise LifecycleError(f"{before['device']}: licence state changed")


def assert_activation_continuity(before, after):
    fields = ("image_id", "environment", "mounts", "host_id")
    if any(before[field] != after[field] for field in fields):
        raise LifecycleError(
            f"{before['device']}: activation changed image, identity or mounts"
        )
    for path in REQUIRED_FILES:
        if before["file_digests"].get(path) != after["file_digests"].get(path):
            raise LifecycleError(
                f"{before['device']}: activation changed persistent identity inputs"
            )
    if after["license_status"]["state"] != "active":
        raise LifecycleError(f"{after['device']}: offline licence is not active")


def _container_names(client, run):
    output = run(
        client,
        shlex.join(["docker", "ps", "-a", "--format", "{{.Names}}"]),
    )
    return set(output.splitlines())


def _container_networks(client, container, run):
    inspected = json.loads(
        run(client, "docker inspect " + shlex.quote(container))
    )
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise LifecycleError(f"{container}: Docker inspect did not return one container")
    return (inspected[0].get("NetworkSettings") or {}).get("Networks") or {}


def _disconnect_lab_networks(client, container, run):
    networks = _container_networks(client, container, run)
    for network in EXPECTED_NETWORKS:
        if network in networks:
            run(
                client,
                shlex.join(["docker", "network", "disconnect", network, container]),
            )


def _restore_recreated(
    client, device, original_id, rollback_name, failed_name, network_addresses, run
):
    container = "qbt-" + device.lower()
    names = _container_names(client, run)
    retained_failed = None
    if container in names:
        inspected = json.loads(
            run(client, "docker inspect " + shlex.quote(container))
        )
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise LifecycleError(f"{device}: cannot inspect container during rollback")
        active_id = inspected[0].get("Id")
        if active_id == original_id:
            if (inspected[0].get("State") or {}).get("Status") != "running":
                run(client, "docker start " + shlex.quote(container))
            return None
        run(client, "docker stop " + shlex.quote(container))
        _disconnect_lab_networks(client, container, run)
        if failed_name in names:
            raise LifecycleError(f"{device}: rollback diagnostic name is already in use")
        run(
            client,
            shlex.join(["docker", "rename", container, failed_name]),
        )
        retained_failed = failed_name

    names = _container_names(client, run)
    if rollback_name not in names:
        raise LifecycleError(f"{device}: retained original container is unavailable")
    if container in names:
        raise LifecycleError(f"{device}: original container name is still occupied")
    run(
        client,
        shlex.join(["docker", "rename", rollback_name, container]),
    )
    attached = _container_networks(client, container, run)
    for network, address in network_addresses.items():
        if network not in attached:
            run(
                client,
                shlex.join(
                    ["docker", "network", "connect", "--ip", address, network, container]
                ),
            )
    run(client, "docker start " + shlex.quote(container))
    return retained_failed


def _progress(message):
    print(f"[recreate] {message}", flush=True)


def build_create_command(device, image_ref, name):
    """Render the approved profile as an explicit `docker create` command.

    The EVO Docker engine has no Compose plugin, and the existing containers
    were created with plain Docker, so this reproduces that profile exactly.
    """
    service = render_profile(device, MANAGEMENT_ADDRESSES[device], image_ref)[
        "services"
    ]["kme"]
    args = [
        "docker", "create", "--name", name,
        "--restart", service["restart"],
        "--network", service["network_mode"],
        "--read-only",
        "--workdir", service["working_dir"],
    ]
    for capability in service["cap_drop"]:
        args += ["--cap-drop", capability]
    for capability in service["cap_add"]:
        args += ["--cap-add", capability]
    for option in service["security_opt"]:
        args += ["--security-opt", option]
    for target in service["tmpfs"]:
        args += ["--tmpfs", target]
    for key, value in sorted(service["labels"].items()):
        args += ["--label", f"{key}={value}"]
    for key, value in sorted(service["environment"].items()):
        args += ["--env", f"{key}={value}"]
    for volume in service["volumes"]:
        mount = f"type=bind,src={volume['source']},dst={volume['target']}"
        if volume.get("read_only"):
            mount += ",readonly"
        args += ["--mount", mount]
    args.append(service["image"])
    args += service["command"]
    return shlex.join(args)


def recreate_pair(
    clients,
    before,
    run,
    run_private,
    transfer,
    probe,
    image_ref,
    verify_after,
):
    """Replace both licensed containers, retaining the originals for rollback.

    No container or persistent directory is removed. Each original is stopped,
    detached from the lab networks and renamed; the replacement is created with
    the same image, bind mounts and security profile.
    """
    _progress("1/4 checking licence, identity, image and mounts on both EVOs (read-only)")
    current = preflight_pair(clients, run, run_private, image_ref)
    operation_errors = (
        OSError,
        RuntimeError,
        EOFError,
        ValueError,
        KeyError,
        paramiko.SSHException,
        SCPException,
    )
    for device in ("EVO1", "EVO2"):
        if before[device]["container_id"] != current[device]["container_id"]:
            raise LifecycleError(f"{device}: container changed after the preflight")
        assert_continuity(before[device], current[device])

    suffix = uuid.uuid4().hex[:8]
    plans = {}
    for device in ("EVO1", "EVO2"):
        client = clients[device]
        names = _container_names(client, run)
        plans[device] = {
            "rollback_name": (
                "qbt-" + device.lower() + "-rollback-"
                + current[device]["container_id"][:12]
            ),
            "failed_name": "qbt-" + device.lower() + "-failed-" + suffix,
            "command": build_create_command(
                device, image_ref, "qbt-" + device.lower()
            ),
        }
        if plans[device]["rollback_name"] in names or plans[device]["failed_name"] in names:
            raise LifecycleError(f"{device}: rollback container name is already in use")

    _progress("2/4 plans validated; originals will be kept as rollback containers")
    transitions = []
    after = {}
    try:
        for device in ("EVO1", "EVO2"):
            client = clients[device]
            container = "qbt-" + device.lower()
            plan = plans[device]
            _progress(f"3/4 {device}: stopping the original (data stays in place)")
            old = current[device]
            transitions.append({
                "device": device,
                "original_id": old["container_id"],
                "rollback_name": plan["rollback_name"],
                "failed_name": plan["failed_name"],
                "network_addresses": old["network_addresses"],
            })
            run(client, "docker stop " + shlex.quote(container))
            stopped = json.loads(run(client, "docker inspect " + shlex.quote(container)))
            if (stopped[0].get("State") or {}).get("Status") != "exited":
                raise LifecycleError(f"{device}: original container did not stop")
            _disconnect_lab_networks(client, container, run)
            run(client, shlex.join(["docker", "rename", container, plan["rollback_name"]]))
            _progress(f"3/4 {device}: original renamed {plan['rollback_name']}; creating the replacement")
            run(client, plan["command"])
            run(client, "docker start " + shlex.quote(container))
            _progress(f"3/4 {device}: replacement started; attaching ETSI and OOB networks")
            attach_networks(client, device, run, probe)
            _progress(f"3/4 {device}: waiting for the active licence and identity checks")
            replacement = _wait_for_replacement_preflight(
                client, device, run, run_private, image_ref
            )
            assert_activation_continuity(old, replacement)
            after[device] = replacement
        _progress("4/4 final licence and identity comparison on both EVOs")
        acceptance = verify_after(clients, current)
    except operation_errors as error:
        rollback_failures = []
        retained_failed = {}
        for transition in reversed(transitions):
            device = transition["device"]
            try:
                retained_failed[device] = _restore_recreated(
                    clients[device],
                    device,
                    transition["original_id"],
                    transition["rollback_name"],
                    transition["failed_name"],
                    transition["network_addresses"],
                    run,
                )
            except operation_errors:
                rollback_failures.append(device)
        details = (
            f"Recreation failed: {error}. The original containers were retained "
            "and automatic rollback was attempted."
        )
        if rollback_failures:
            details += (
                " Rollback needs operator attention on: "
                + ", ".join(rollback_failures)
                + ". Do not remove any container or data directory."
            )
        preserved = [f"{d}={n}" for d, n in retained_failed.items() if n]
        if preserved:
            details += " Failed replacements retained as " + ", ".join(preserved) + "."
        raise LifecycleError(details) from error
    return {
        "containers": {
            device: {
                **public_summary(after[device]),
                "rollback_container": plans[device]["rollback_name"],
            }
            for device in ("EVO1", "EVO2")
        },
        "verification": acceptance,
        "original_containers_retained": True,
    }


def verify_pair(
    clients,
    run,
    run_private,
    transfer,
    image_ref,
    devices,
    script_user,
    *,
    rotation_timeout=900,
):
    before = preflight_pair(clients, run, run_private, image_ref)
    from lib.qbt.probe import paired_probe

    paired_probe(clients, run, transfer)
    rotation = verify_rotation(
        clients,
        run,
        devices,
        script_user,
        timeout=rotation_timeout,
    )
    after = preflight_pair(clients, run, run_private, image_ref)
    for device in ("EVO1", "EVO2"):
        assert_continuity(before[device], after[device])
    return {
        "docker_license": {
            device: public_summary(after[device]) for device in ("EVO1", "EVO2")
        },
        "etsi": "four paired 256-bit ENC/DEC keys verified",
        "macsec": rotation,
    }
