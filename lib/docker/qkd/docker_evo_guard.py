"""
EVO and Docker admission control for the PhioTX workflow.

qkd_docker_orchestrator.py runs the KME inside the router itself, so it is only
valid against Junos EVO platforms whose Docker daemon is reachable. This module
is the single gate that enforces that contract; every other module in
lib/docker/qkd assumes the gate has already passed.

Nothing here mutates router state: all probes are read-only.
"""

from lib.docker.qkd.docker_identity import (
    CommandResult,
    device_name,
    normalize_device,
    normalize_devices,
    platform_name,
    pyez_cli_cmd,
    pyez_shell_cmd,
)


# Junos EVO advertises itself in "show version" with an EVO build string, for
# example "26.2R1.7-EVOI20260619095247-evo-builder-1". Classic Junos never
# contains these markers.
EVO_VERSION_MARKERS = ("-EVO", "EVO-", "evo-builder", "Junos OS Evolved")

# Platform families that ship an EVO image. The guard still requires a live
# "show version" match; this list only catches obvious inventory mistakes
# before any session is opened.
EVO_CAPABLE_PLATFORMS = ("ptx", "qfx", "acx", "evo")


class EvoAdmissionError(RuntimeError):
    """Raised when a device is not a Docker-enabled Junos EVO router."""


def _fail(name, reason, result=None):
    detail = ""
    if isinstance(result, CommandResult):
        detail = f"\nstdout={result.stdout}\nstderr={result.stderr}"
    raise EvoAdmissionError(f"{name}: {reason}{detail}")


def is_evo_version_text(text):
    """Return True when "show version" output belongs to a Junos EVO image."""
    if not text:
        return False
    haystack = str(text)
    return any(marker in haystack for marker in EVO_VERSION_MARKERS)


def check_inventory_is_evo(device):
    """
    Cheap, offline inventory check.

    The inventory must not silently carry a classic-Junos device into a
    workflow that installs containers on the router.
    """
    device = normalize_device(device)
    name = device_name(device)

    if device.get("evo") is False:
        _fail(name, "inventory marks evo: false; the Docker workflow is EVO-only")

    platform = platform_name(device)
    if platform and platform not in EVO_CAPABLE_PLATFORMS:
        _fail(
            name,
            f"platform {platform!r} has no Junos EVO image; "
            f"expected one of {', '.join(EVO_CAPABLE_PLATFORMS)}",
        )

    if device.get("docker") is False:
        _fail(name, "inventory marks docker: false; the Docker workflow requires Docker")

    return True


def check_evo_version(device, timeout=60):
    """
    Confirm with "show version" that the router actually runs Junos EVO.

    Returns the matched version line so the caller can log exactly what was
    accepted.
    """
    device = normalize_device(device)
    name = device_name(device)

    result = pyez_cli_cmd(device, "show version", timeout=timeout)
    if result.returncode != 0:
        _fail(name, "cannot run 'show version'", result)

    if not is_evo_version_text(result.stdout):
        _fail(
            name,
            "'show version' does not report a Junos EVO image; "
            "qkd_docker_orchestrator.py refuses non-EVO platforms",
            result,
        )

    version_line = next(
        (
            line.strip()
            for line in result.stdout.splitlines()
            if is_evo_version_text(line)
        ),
        result.stdout.strip().splitlines()[0] if result.stdout.strip() else "",
    )

    return version_line


def check_docker_enabled(device, timeout=60):
    """
    Confirm the EVO host exposes a usable Docker daemon.

    "docker info" is preferred over "docker ps" because it fails explicitly
    when the daemon socket exists but the service is down.
    """
    device = normalize_device(device)
    name = device_name(device)

    result = pyez_shell_cmd(
        device,
        "docker info --format '{{.ServerVersion}}|{{.Driver}}'",
        timeout=timeout,
    )

    if result.returncode != 0 or "|" not in result.stdout:
        _fail(
            name,
            "Docker daemon is not usable on this EVO host "
            "(expected 'docker info' to report a server version)",
            result,
        )

    line = next(
        (l.strip() for l in result.stdout.splitlines() if "|" in l),
        "",
    )
    server_version, _, storage_driver = line.partition("|")

    return {
        "server_version": server_version.strip(),
        "storage_driver": storage_driver.strip(),
    }


def check_required_networks(device, networks, timeout=60):
    """
    Confirm the named Docker networks already exist on the EVO host.

    jnpr_cntrz_net is created by Junos EVO itself and must never be created or
    modified by this workflow, so a missing network is a hard error rather than
    something to repair.
    """
    device = normalize_device(device)
    name = device_name(device)

    missing = []
    for network in networks:
        result = pyez_shell_cmd(
            device,
            f"docker network inspect {network} --format '{{{{.Name}}}}'",
            timeout=timeout,
        )
        if result.returncode != 0 or network not in result.stdout:
            missing.append(network)

    if missing:
        _fail(
            name,
            f"required Docker network(s) missing: {', '.join(missing)}",
        )

    return True


def admit_device(device, required_networks=None, timeout=60):
    """
    Run the full admission sequence for one device.

    Order matters: the offline inventory check runs first so an obviously wrong
    inventory fails before any NETCONF session is opened.
    """
    device = normalize_device(device)
    name = device_name(device)

    check_inventory_is_evo(device)
    version_line = check_evo_version(device, timeout=timeout)
    docker_info = check_docker_enabled(device, timeout=timeout)

    if required_networks:
        check_required_networks(device, required_networks, timeout=timeout)

    report = {
        "name": name,
        "evo_version": version_line,
        "docker_server_version": docker_info["server_version"],
        "docker_storage_driver": docker_info["storage_driver"],
    }

    print(
        f"[OK] {name} admitted: EVO={version_line} "
        f"docker={docker_info['server_version']} "
        f"storage={docker_info['storage_driver']}"
    )

    return report


def admit_devices(devices, required_networks=None, timeout=60):
    """
    Admit every device, collecting all failures before raising.

    Reporting every offending router in one pass avoids a slow
    fix-one-rerun-discover-the-next loop on multi-node labs.
    """
    reports = {}
    failures = []

    for device in normalize_devices(devices):
        name = device_name(device)
        try:
            reports[name] = admit_device(
                device,
                required_networks=required_networks,
                timeout=timeout,
            )
        except EvoAdmissionError as error:
            failures.append(str(error))
        except Exception as error:
            failures.append(f"{name}: unexpected admission failure: {error}")

    if failures:
        raise EvoAdmissionError(
            "Docker/PhioTX admission failed; the workflow is restricted to "
            "Docker-enabled Junos EVO routers.\n  - "
            + "\n  - ".join(failures)
        )

    return reports
