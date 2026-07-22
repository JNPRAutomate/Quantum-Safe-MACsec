#!/usr/bin/env python3

from lib.common.settings import QKD, PKI
from lib.common.config import load_runtime_pki_profile, load_runtime_qkd_policy, load_inventory_base
from jnpr.junos import Device
from jnpr.junos.utils.config import Config
import json
import subprocess
import shlex
import re
import time


# -------------------------------------------------
# Basic helpers
# -------------------------------------------------


def qkd_script_user():
    return QKD.get("SCRIPT_USER", "admin")


def qkd_deploy_user():
    return QKD.get("DEPLOY_USER", "root")


def qkd_ssh_home():
    return f"{QKD.get('SSH_HOME_BASE', '/var/home')}/{qkd_script_user()}"


def qkd_ssh_dir():
    return f"{qkd_ssh_home()}/.ssh"


def qkd_ssh_private_key():
    return f"{qkd_ssh_dir()}/{QKD.get('SSH_KEY_NAME', 'qkd_id_ed25519')}"


def qkd_ssh_public_key():
    return f"{qkd_ssh_private_key()}.pub"


def qkd_authorized_keys():
    return f"{qkd_ssh_dir()}/authorized_keys"


def qkd_peer_cmd_user(device=None):
    if isinstance(device, dict):
        value = device.get("peer_cmd_user")
        if value:
            return str(value)
    return str(QKD.get("PEER_CMD_USER", "etsi_peer_view"))


def qkd_peer_cmd_class():
    return str(QKD.get("PEER_CMD_CLASS", "read-only"))


def qkd_peer_cmd_ssh_private_key():
    return f"{qkd_ssh_dir()}/{QKD.get('PEER_CMD_SSH_KEY_NAME', QKD.get('SSH_KEY_NAME', 'qkd_id_ed25519'))}"


def qkd_peer_cmd_ssh_public_key():
    return f"{qkd_peer_cmd_ssh_private_key()}.pub"


def qkd_peer_cmd_authorized_keys(device=None):
    peer_user = qkd_peer_cmd_user(device)
    return f"{QKD.get('SSH_HOME_BASE', '/var/home')}/{peer_user}/.ssh/authorized_keys"


def qkd_remote_op_script():
    return QKD.get(
        "REMOTE_OP_SCRIPT_PATH",
        f"{QKD.get('OP_SCRIPT_DIR', '/var/db/scripts/op')}/{QKD.get('SCRIPT_NAME', 'qkd_onbox.py')}",
    )


def qkd_remote_event_script():
    return (
        f"{QKD.get('EVENT_SCRIPT_DIR', '/var/db/scripts/event')}/"
        f"{QKD.get('SCRIPT_NAME', 'qkd_onbox.py')}"
    )


def qkd_remote_tmp_dir():
    return QKD.get("REMOTE_TMP_DIR", "/var/tmp")


def qkd_onbox_config_dir():
    return QKD.get("ONBOX_CONFIG_DIR", QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op"))


def qkd_remote_config_json():
    return f"{qkd_onbox_config_dir()}/{QKD.get('ONBOX_CONFIG_JSON_NAME', 'qkd_onbox_config.json')}"


def qkd_remote_inventory_json():
    return f"{qkd_onbox_config_dir()}/{QKD.get('ONBOX_INVENTORY_JSON_NAME', 'qkd_onbox_inventory.json')}"


def qkd_remote_cert_dir():
    return PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")


def device_host(device):
    if device.get("mgmt_ip"):
        return device["mgmt_ip"]
    if device.get("ip"):
        return device["ip"]
    if device.get("host"):
        return device["host"]
    raise KeyError(f"Device {device.get('name', '<unknown>')} has no mgmt_ip/ip/host field")


def normalize_device(device, name=None):
    if not isinstance(device, dict):
        raise TypeError(f"Invalid device record: expected dict, got {type(device)}")
    d = dict(device)
    if name and "name" not in d:
        d["name"] = name
    if "name" not in d:
        raise KeyError(f"Device record missing name and no inventory key was provided: {d}")
    return d


def normalize_devices(devices):
    if isinstance(devices, dict):
        return [normalize_device(device, name=name) for name, device in devices.items()]
    if isinstance(devices, list):
        return [normalize_device(device) for device in devices]
    raise TypeError(f"Invalid devices type: expected dict or list, got {type(devices)}")


def device_name(device):
    if not isinstance(device, dict):
        raise TypeError(f"Invalid device record: expected dict, got {type(device)}")
    name = device.get("name")
    if not name:
        raise KeyError(f"Device record missing logical name: {device}")
    return name


def validate_device_record(device):
    for key in ["name", "ip", "auth"]:
        if key not in device:
            raise KeyError(f"Device record missing required field '{key}': {device}")
    auth = device.get("auth") or {}
    if "username" not in auth:
        raise KeyError(f"Device {device['name']} missing auth.username")
    if "password" not in auth:
        raise KeyError(f"Device {device['name']} missing auth.password")


def platform_name(device):
    return str(device.get("platform", "")).lower()


def platform_is_legacy_qfx(device):
    return platform_name(device) in ["qfx", "vqfx"]


def junos_cli_quote(value):
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    return f'"{value}"'


# -------------------------------------------------
# Result / PyEZ helpers
# -------------------------------------------------


class CommandResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def validate_verbose():
    return bool(QKD.get("VALIDATE_VERBOSE", False))


def print_if_verbose(text):
    if validate_verbose() and text:
        print(text)


def print_device_output(name, text):
    if not text:
        return
    for raw_line in str(text).splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        print(f"[INFO][{name}] {line}")


def rpc_output_to_text(rsp):
    if rsp is None:
        return ""
    chunks = []
    try:
        for elem in rsp.iter():
            if elem.text:
                chunks.append(elem.text)
            if elem.tail:
                chunks.append(elem.tail)
        text = "".join(chunks).strip()
        if text:
            return text
    except Exception:
        pass
    try:
        if rsp.text:
            return rsp.text.strip()
    except Exception:
        pass
    return str(rsp)


def shell_output_has_error(text, include_failed_marker=True):
    if not text:
        return False
    markers = [
        "Permission denied",
        "Operation not permitted",
        "Command not found",
        "Undefined variable",
        "Illegal variable name",
        "Unmatched",
        "Syntax error",
        "No such file or directory",
        "cannot",
        "error:",
    ]
    if include_failed_marker:
        # Some valid JSON status payloads include values like "ENC_FAILED".
        markers.append("failed")
    low = text.lower()
    for marker in markers:
        if marker.lower() in low:
            return True
    return False


def pyez_shell_cmd(device, command, timeout=60, include_failed_marker=True):
    device = normalize_device(device)
    name = device_name(device)
    host = device_host(device)
    candidates = []

    auth = device.get("auth") or {}
    user = auth.get("username")
    passwd = auth.get("password")
    if user and passwd:
        candidates.append((str(user), str(passwd), "device.auth"))

    try:
        base = load_inventory_base()
        secrets = base.get("secrets", {}) if isinstance(base, dict) else {}
        if not isinstance(secrets, dict):
            secrets = {}

        bootstrap_user = secrets.get("bootstrap_user") or secrets.get("deploy_user")
        bootstrap_password = (
            secrets.get("bootstrap_password")
            or secrets.get("deploy_password")
            or secrets.get("root_password")
        )
        if bootstrap_user and bootstrap_password:
            candidates.append((str(bootstrap_user), str(bootstrap_password), "inventory_base.bootstrap/deploy"))

        script_user = secrets.get("script_user") or secrets.get("default_user")
        script_password = (
            secrets.get("script_password")
            or secrets.get("admin_password")
            or secrets.get("default_password")
        )
        if script_user and script_password:
            candidates.append((str(script_user), str(script_password), "inventory_base.script/default"))
    except Exception:
        pass

    deduped_candidates = []
    seen = set()
    for cand_user, cand_password, source in candidates:
        key = (cand_user, cand_password)
        if key in seen:
            continue
        seen.add(key)
        deduped_candidates.append((cand_user, cand_password, source))

    if not deduped_candidates:
        return CommandResult(1, "", f"missing auth.username/auth.password for device {name}")

    last_error = None
    for cand_user, cand_password, source in deduped_candidates:
        for port in (830, 22):
            dev = Device(host=host, user=cand_user, passwd=cand_password, port=port, timeout=timeout)
            try:
                dev.open()
                rsp = dev.rpc.request_shell_execute(command=command)
                text = rpc_output_to_text(rsp)
                has_error = shell_output_has_error(text, include_failed_marker=include_failed_marker)
                return CommandResult(1 if has_error else 0, text.strip(), "")
            except Exception as exc:
                last_error = exc
            finally:
                try:
                    dev.close()
                except Exception:
                    pass

    return CommandResult(1, "", str(last_error))


def ssh_deploy_cmd(device, command, timeout=30, include_failed_marker=True):
    return pyez_shell_cmd(
        device=device,
        command=command,
        timeout=timeout,
        include_failed_marker=include_failed_marker,
    )


def ssh_cmd(device, command, user, timeout=30):
    host = device_host(device)
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
    deploy_key = device.get("orchestrator_ssh_key") or device.get("deploy_ssh_key")
    if deploy_key:
        cmd.extend(["-i", deploy_key, "-o", "IdentitiesOnly=yes"])
    cmd.extend([f"{user}@{host}", command])
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)


def deploy_auth_user(device):
    device = normalize_device(device)
    auth = device.get("auth") or {}
    return str(auth.get("username") or "")


def ssh_script_user_onbox_cmd(device, command, timeout=30, include_failed_marker=True):
    device = normalize_device(device)
    script_user = qkd_script_user()
    deploy_user = deploy_auth_user(device)
    key_path = qkd_ssh_private_key()

    # Fast-path: when deploy auth user already matches script_user, run directly.
    # This avoids unnecessary localhost SSH dependency on authorized_keys ownership.
    if deploy_user == script_user:
        if command.startswith("op "):
            direct_cmd = "cli -c " + shlex.quote(command)
        else:
            direct_cmd = command
        return ssh_deploy_cmd(
            device=device,
            command=direct_cmd,
            timeout=timeout,
            include_failed_marker=include_failed_marker,
        )

    if command.startswith("op "):
        remote_payload = command
    elif platform_is_legacy_qfx(device):
        remote_payload = command
    else:
        remote_payload = "start shell command " + junos_cli_quote(command)

    remote_cmd = (
        f"ssh -i {key_path} "
        f"-o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=no "
        f"-o BatchMode=yes "
        f"{script_user}@127.0.0.1 "
        f"{shlex.quote(remote_payload)}"
    )
    if validate_verbose():
        print("REMOTE_CMD=", remote_cmd)
    return ssh_deploy_cmd(
        device=device,
        command=remote_cmd,
        timeout=timeout,
        include_failed_marker=include_failed_marker,
    )


# -------------------------------------------------
# Identity plan
# -------------------------------------------------


def check_validation_plan():
    print("=== QKD validation plan ===")
    print(f"deploy_user_fallback = {qkd_deploy_user()}")
    print(f"script_user          = {qkd_script_user()}")
    print(f"ssh_home             = {qkd_ssh_home()}")
    print(f"ssh_dir              = {qkd_ssh_dir()}")
    print(f"ssh_key              = {qkd_ssh_private_key()}")
    print(f"ssh_pub              = {qkd_ssh_public_key()}")
    print(f"authorized_keys      = {qkd_authorized_keys()}")
    print(f"peer_key_sync_target = {qkd_script_user()}")
    print(f"peer_cmd_user        = {qkd_peer_cmd_user()}")
    print(f"peer_cmd_class       = {qkd_peer_cmd_class()}")
    print(f"peer_cmd_ssh_key     = {qkd_peer_cmd_ssh_private_key()}")
    print(f"peer_cmd_ssh_pub     = {qkd_peer_cmd_ssh_public_key()}")
    print(f"peer_cmd_auth_keys   = {qkd_peer_cmd_authorized_keys()}")
    print(f"op_script_path       = {qkd_remote_op_script()}")
    print(f"cert_dir             = {qkd_remote_cert_dir()}")
    print(f"log_file             = {QKD.get('LOG_FILE', '/var/home/macsec_user/qkd-state/logs/qkd_debug.log')}")
    print(f"runtime_tmp_dir      = {qkd_remote_tmp_dir()}")


# -------------------------------------------------
# Common checks
# -------------------------------------------------


def check_deploy_user_access(device):
    device = normalize_device(device)
    name = device_name(device)
    auth = device.get("auth") or {}
    deploy_user = auth.get("username", "<missing>")
    result = ssh_deploy_cmd(device, "whoami; id; uname -a", timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            f"PyEZ deploy access failed on {name}\n"
            f"deploy_user={deploy_user}\n"
            f"host={device_host(device)}\n"
            f"stdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )
    print(f"[OK] deploy user access on {name}: {deploy_user}")
    print_if_verbose(result.stdout)


def check_script_user_exists(device):
    device = normalize_device(device)
    name = device_name(device)
    script_user = qkd_script_user()
    host = device_host(device)
    result = ssh_deploy_cmd(device, f"id {script_user}", timeout=30)
    stdout = str(result.stdout or "")
    stderr = str(result.stderr or "")
    if result.returncode != 0:
        stderr_lower = stderr.lower()
        if (
            "connecttimeouterror" in stderr_lower
            or "timed out" in stderr_lower
            or "timeout" in stderr_lower
            or "no route to host" in stderr_lower
            or "connection refused" in stderr_lower
            or "connection reset" in stderr_lower
            or "unable to connect" in stderr_lower
        ):
            raise RuntimeError(
                f"Cannot validate SCRIPT_USER on {name}: device unreachable or SSH/PyEZ timeout.\n"
                f"device={name}\n"
                f"host={host}\n"
                f"script_user={script_user}\n"
                f"hint=Check reachability, routing/VPN/helper VM, firewall, and SSH service.\n"
                f"stdout={stdout}\n"
                f"stderr={stderr}"
            )
        raise RuntimeError(
            f"SCRIPT_USER validation failed on {name}: user '{script_user}' was not found or cannot be queried.\n"
            f"device={name}\n"
            f"host={host}\n"
            f"script_user={script_user}\n"
            f"stdout={stdout}\n"
            f"stderr={stderr}"
        )
    print(f"[OK] script user on {name}: {script_user} exists")
    print_if_verbose(stdout)


def check_script_user_home_simple(device):
    device = normalize_device(device)
    name = device_name(device)
    ssh_home = qkd_ssh_home()
    ssh_dir = qkd_ssh_dir()
    cmd = (
        f"echo ### home; "
        f"ls -ld {ssh_home}; "
        f"echo ### ssh-dir; "
        f"mkdir -p {ssh_dir}; "
        f"test -d {ssh_dir}; "
        f"ls -ld {ssh_dir}"
    )
    result = ssh_deploy_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"SCRIPT_USER home/.ssh check failed on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] home/.ssh dir on {name}: {ssh_dir}")
    print_if_verbose(result.stdout)


def check_script_dirs_simple(device):
    device = normalize_device(device)
    name = device_name(device)
    op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
    event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")
    cert_dir = qkd_remote_cert_dir()
    runtime_tmp_dir = qkd_remote_tmp_dir()
    cmd = (
        f"echo ### qkd-script-dirs; "
        f"mkdir -p {op_script_dir}; "
        f"mkdir -p {event_script_dir}; "
        f"mkdir -p {cert_dir}; "
        f"ls -ld {runtime_tmp_dir}; "
        f"ls -ld {op_script_dir}; "
        f"ls -ld {event_script_dir}; "
        f"ls -ld {cert_dir}"
    )
    result = ssh_deploy_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"script directory check failed on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] script dirs on {name}: {op_script_dir} {event_script_dir} {cert_dir}")
    print_if_verbose(result.stdout)


def check_runtime_cleanup_simple(device):
    device = normalize_device(device)
    name = device_name(device)
    script_user = str(QKD.get("SCRIPT_USER", "macsec_user"))
    ssh_home_base = str(QKD.get("SSH_HOME_BASE", "/var/home"))
    state_dir = f"{ssh_home_base}/{script_user}/qkd-state"
    logs_dir = f"{state_dir}/logs"
    cmd = (
        "echo ### qkd-runtime-cleanup; "
        f"mkdir -p {logs_dir}; "
        f"chflags nouchg,noschg {state_dir}/qkd_db_*.json 2>/dev/null; "
        f"chflags nouchg,noschg {state_dir}/qkd_db_*.json.*.tmp 2>/dev/null; "
        f"chflags nouchg,noschg {state_dir}/qkd_onbox_* 2>/dev/null; "
        f"chflags nouchg,noschg {logs_dir}/qkd_debug*.log 2>/dev/null; "
        "chflags nouchg,noschg /var/tmp/qkd_db_*.json; "
        "chflags nouchg,noschg /var/tmp/qkd_db_*.json.*.tmp; "
        "chflags nouchg,noschg /var/tmp/qkd_debug*.log; "
        "chflags nouchg,noschg /var/tmp/qkd_onbox_*; "
        f"rm -f {state_dir}/qkd_db_*.json; "
        f"rm -f {state_dir}/qkd_db_*.json.*.tmp; "
        f"rm -rf {state_dir}/qkd_onbox_*; "
        f"rm -f {logs_dir}/qkd_debug*.log; "
        "rm -f /var/tmp/qkd_db_*.json; "
        "rm -f /var/tmp/qkd_db_*.json.*.tmp; "
        "rm -f /var/tmp/qkd_debug*.log; "
        "rm -rf /var/tmp/qkd_onbox_*; "
        "echo ### qkd-runtime-cleanup-done"
    )
    result = ssh_deploy_cmd(device, cmd, timeout=60)
    if result.returncode != 0:
        print(f"[WARN] runtime cleanup had non-fatal output on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
    else:
        print(f"[OK] runtime state cleared on {name}: {state_dir}")
    print_if_verbose(result.stdout)


def check_shipment_preload_artifacts(device):
    device = normalize_device(device)
    name = device_name(device)
    op_path = qkd_remote_op_script()
    event_path = qkd_remote_event_script()
    config_path = qkd_remote_config_json()
    inventory_path = qkd_remote_inventory_json()

    cmd = (
        f"ls -l {op_path}; "
        f"ls -l {event_path}; "
        f"ls -l {config_path}; "
        f"ls -l {inventory_path}; "
        f"grep -n -F '\"enabled\": false' {config_path}; "
        f"grep -n -F '\"enabled\": false' {inventory_path}"
    )

    result = ssh_deploy_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            f"shipment preload artifact check failed on {name}\n"
            f"stdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )

    print(f"[OK] shipment preload artifacts detected on {name}")
    print_if_verbose(result.stdout)


def check_remote_certs(device):
    device = normalize_device(device)
    name = device_name(device)
    sae = (device.get("qkd") or {}).get("sae_id") or device.get("local_sae") or device.get("sae") or device.get("sae_id")
    if not sae:
        raise RuntimeError(f"cannot validate remote certs on {name}: missing SAE ID")
    remote_cert_dir = qkd_remote_cert_dir()
    expected = [f"{remote_cert_dir}/{sae}.crt", f"{remote_cert_dir}/{sae}.key", f"{remote_cert_dir}/offbox_rootCA.crt"]
    cmd = "; ".join([f"test -s {path} && echo OK:{path} || echo MISSING:{path}" for path in expected])
    result = ssh_deploy_cmd(device, cmd, timeout=30)
    missing = [line for line in (result.stdout or "").splitlines() if line.startswith("MISSING:")]
    if missing:
        raise RuntimeError(f"remote cert validation failed on {name}\n" + "\n".join(missing))
    print(f"[OK] remote certs on {name}: {sae}.crt {sae}.key offbox_rootCA.crt")


# -------------------------------------------------
# Non-legacy / ACX / MX stronger checks
# -------------------------------------------------


def check_script_user_ssh_identity(device):
    device = normalize_device(device)
    name = device_name(device)
    script_user = qkd_script_user()
    ssh_dir = qkd_ssh_dir()
    key_path = qkd_ssh_private_key()
    pub_path = qkd_ssh_public_key()
    peer_key_path = qkd_peer_cmd_ssh_private_key()
    peer_pub_path = qkd_peer_cmd_ssh_public_key()
    key_type = str(QKD.get("SSH_KEY_TYPE", "ed25519")).strip().lower()
    key_bits = int(QKD.get("SSH_KEY_BITS", 4096))
    key_comment = QKD.get("SSH_KEY_COMMENT", "qkd-orchestrator")

    if key_type == "rsa":
        keygen_cmd = f"ssh-keygen -t rsa -b {key_bits} -N \"\" -C \"{key_comment}\" -f {key_path}"
    elif key_type == "ed25519":
        keygen_cmd = f"ssh-keygen -t ed25519 -N \"\" -C \"{key_comment}\" -f {key_path}"
    else:
        raise ValueError(f"Unsupported SSH_KEY_TYPE={key_type}. Expected 'ed25519' or 'rsa'.")

    def keygen_cmd_for(path):
        if key_type == "rsa":
            return f"ssh-keygen -t rsa -b {key_bits} -N \"\" -C \"{key_comment}\" -f {path}"
        return f"ssh-keygen -t ed25519 -N \"\" -C \"{key_comment}\" -f {path}"

    def load_rotation_thresholds():
        script_threshold = 30 * 24 * 3600
        peer_threshold = 3600
        try:
            policy = load_runtime_qkd_policy()
            qkd_policy = policy.get("qkd_policy", {}) if isinstance(policy, dict) else {}
            script_threshold = int(qkd_policy.get("script_user_rotation_seconds", script_threshold))
            peer_threshold = int(qkd_policy.get("peer_cmd_rotation_seconds", peer_threshold))
        except Exception:
            pass
        return max(script_threshold, 0), max(peer_threshold, 0)

    def remote_file_age_seconds(path):
        # Calculate file age using stat + date + expr (Junos-compatible)
        # Backticks work on Junos, $() does not
        # Platform-aware: Junos uses stat -f, Linux uses stat -c
        platform = device.get("platform", "").lower()
        
        if platform in ("mx", "qfx"):
            # Junos FreeBSD: use stat -f '%m' with backticks and expr
            cmd = f"expr `date +%s` - `stat -f '%m' {path}`"
        else:
            # Linux/ACX: use stat -c '%Y' with backticks and expr
            cmd = f"expr `date +%s` - `stat -c '%Y' {path}`"
        
        result = ssh_deploy_cmd(device, cmd, timeout=20, include_failed_marker=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"failed to read SSH key age on {name} path={path}\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        values = re.findall(r"-?\d+", result.stdout or "")
        if not values:
            raise RuntimeError(
                f"invalid SSH key age output on {name} path={path}\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        return int(values[-1])

    # Pre-deploy SSH identity check: keys must exist, be readable, and have size > 0
    # Key generation happens in bootstrap, not here. This function only verifies.
    cmd_main = (
        f"mkdir -p {ssh_dir}; "
        f"test -f {key_path}; "
        f"test -r {key_path}; "
        f"test -s {key_path}; "
        f"test -f {pub_path}; "
        f"test -s {pub_path}; "
        f"test -f {peer_key_path}; "
        f"test -r {peer_key_path}; "
        f"test -s {peer_key_path}; "
        f"test -f {peer_pub_path}; "
        f"test -s {peer_pub_path}; "
        f"ls -ld {ssh_dir}; "
        f"ls -l {key_path}; "
        f"ls -l {pub_path}; "
        f"ls -l {peer_key_path}; "
        f"ls -l {peer_pub_path}"
    )
    
    result = ssh_deploy_cmd(device, cmd_main, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"SSH identity check failed on {name}. "
            f"Keys missing or not readable. Run bootstrap first.\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    print(f"[OK] SSH identity on {name}: {key_path} {peer_key_path}")
    print_if_verbose(result.stdout)

    script_rotation_s, peer_rotation_s = load_rotation_thresholds()
    key_thresholds = {key_path: script_rotation_s}
    if peer_key_path in key_thresholds:
        key_thresholds[peer_key_path] = min(key_thresholds[peer_key_path], peer_rotation_s)
    else:
        key_thresholds[peer_key_path] = peer_rotation_s

    rotate_paths = []
    for path, threshold in key_thresholds.items():
        if threshold <= 0:
            continue
        age_seconds = remote_file_age_seconds(path)
        if age_seconds >= threshold:
            rotate_paths.append((path, age_seconds, threshold))

    for path, age_seconds, threshold in rotate_paths:
        print(
            f"[WARN] SSH key rotation due on {name}: key={path} age_seconds={age_seconds} threshold_seconds={threshold}"
        )
        pub = f"{path}.pub"
        rotate_cmd = (
            f"rm -f {path} {pub}; "
            f"{keygen_cmd_for(path)}; "
            f"chmod 600 {path}; "
            f"chmod 644 {pub}; "
            f"ls -l {path}; "
            f"ls -l {pub}"
        )
        rotate_result = ssh_deploy_cmd(device, rotate_cmd, timeout=60)
        if rotate_result.returncode != 0:
            raise RuntimeError(
                f"SSH key rotation failed on {name} key={path}\n"
                f"stdout={rotate_result.stdout}\n"
                f"stderr={rotate_result.stderr}"
            )
        print(f"[OK] SSH key rotated on {name}: key={path}")
        print_if_verbose(rotate_result.stdout)


def check_script_user_authorized_keys(device):
    device = normalize_device(device)
    name = device_name(device)
    script_user = qkd_script_user()
    deploy_user = deploy_auth_user(device)
    ssh_dir = qkd_ssh_dir()
    pub_path = qkd_ssh_public_key()
    auth_path = qkd_authorized_keys()

    if deploy_user == script_user:
        cmd = (
            f"mkdir -p {ssh_dir}; "
            f"test -s {pub_path}; "
            f"ls -l {pub_path}; "
            f"ls -l {auth_path} || true"
        )
        result = ssh_deploy_cmd(device, cmd, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"authorized_keys precheck failed on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
        print(f"[OK] authorized_keys on {name}: mutation skipped (deploy_user={deploy_user} == script_user)")
        print_if_verbose(result.stdout)
        return

    cmd = (
        f"mkdir -p {ssh_dir}; "
        f"test -s {pub_path}; "
        f"touch {auth_path}; "
        f"grep -q -F -f {pub_path} {auth_path} || cat {pub_path} >> {auth_path}; "
        f"test -s {auth_path}; "
        f"ls -l {auth_path}"
    )
    result = ssh_deploy_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"authorized_keys check failed on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] authorized_keys on {name}: {auth_path}")
    print_if_verbose(result.stdout)


def check_script_user_can_read_private_key(device):
    device = normalize_device(device)
    name = device_name(device)
    script_user = qkd_script_user()
    key_path = qkd_ssh_private_key()
    peer_key_path = qkd_peer_cmd_ssh_private_key()
    cmd = f"whoami; test -r {key_path}; ls -l {key_path}; echo PRIVATE_KEY_READABLE_OK user={script_user} key={key_path}"
    if peer_key_path != key_path:
        cmd += f"; test -r {peer_key_path}; ls -l {peer_key_path}; echo PRIVATE_KEY_READABLE_OK user={script_user} key={peer_key_path}"
    result = ssh_script_user_onbox_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"SCRIPT_USER cannot read private key on {name} as {script_user}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] private key readable on {name}: {key_path}")
    print_if_verbose(result.stdout)


def check_script_user_atomic_write(device):
    device = normalize_device(device)
    name = device_name(device)
    test_path = "/var/tmp/qkd_identity_write_test.json"
    tmp_path = "/var/tmp/qkd_identity_write_test.json.tmp"
    cmd = f"echo old > {test_path}; echo new > {tmp_path}; mv {tmp_path} {test_path}; cat {test_path}; ls -l {test_path}; rm -f {test_path}"
    result = ssh_script_user_onbox_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"SCRIPT_USER atomic write failed on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] atomic write on {name}: {tmp_path} -> {test_path}")
    print_if_verbose(result.stdout)


def remove_ssh_keys_from_junos_config(device, username):
    """
    Remove all SSH keys from Junos configuration for a user.
    This is necessary because Junos SSH daemon prioritizes config keys over authorized_keys file.
    
    Args:
        device: Device to connect to (PyEZ Device object)
        username: Username (e.g., 'etsi_peer_view', 'macsec_user')
    
    Returns:
        True if successful or no changes needed
    
    Raises:
        RuntimeError if deletion fails
    """
    target_name = device_name(device)
    try:
        host = device_host(device)
        auth = device.get("auth") or {}
        user = auth.get("username")
        password = auth.get("password")
        
        if not user or not password:
            raise RuntimeError(f"missing auth for removing SSH keys on {target_name}")
        
        dev = Device(host=host, user=user, passwd=password, port=22, gather_facts=False)
        dev.open()
        try:
            # Delete ALL ssh-ed25519 keys for this user from Junos config
            # This ensures Junos SSH daemon will use authorized_keys file, not config keys
            delete_cmd = f"delete system login user {username} authentication ssh-ed25519"
            print(f"[DEBUG] removing Junos config SSH keys for {username} on {target_name}...")
            
            with Config(dev, mode='dynamic') as cu:
                cu.load(f"delete system login user {username} authentication ssh-ed25519", format='set')
                cu.commit()
            
            print(f"[OK] removed Junos config SSH keys for {username} on {target_name}")
            return True
        finally:
            try:
                dev.close()
            except Exception:
                pass
    except Exception as exc:
        # If user doesn't have SSH keys in config, this is OK (not an error)
        if "unknown hierarchy" in str(exc).lower():
            print(f"[OK] no SSH keys in config for {username} on {target_name} (not configured)")
            return True
        print(f"[WARN] failed to remove SSH keys from config for {username} on {target_name}: {exc}")
        # Continue anyway - authorized_keys file might still work
        return True


def write_ssh_authorized_keys(device, username, pub_keys_list):
    """
    Write SSH public keys to a user's authorized_keys file using Junos RPC with privileges.
    
    Args:
        device: Device to connect to (PyEZ Device object)
        username: Username (e.g., 'etsi_peer_view', 'macsec_user')
        pub_keys_list: List of public key strings to write
    
    Uses dev.rpc.request_shell_execute (Junos RPC with system privileges).
    Uses printf | dd (not >) for writing - shell redirect doesn't work in Junos RPC.
    """
    target_name = device_name(device)
    home_dir = f"/var/home/{username}"
    ssh_dir = f"{home_dir}/.ssh"
    auth_keys_file = f"{ssh_dir}/authorized_keys"
    
    # Determine correct group for chown (etsi_peer_view uses wheel, macsec_user uses staff)
    if username == "etsi_peer_view":
        group = "wheel"
    elif username == "macsec_user":
        group = "staff"
    else:
        group = "wheel"  # default
    
    # Create content string with all keys joined by newlines
    keys_content = "\n".join(pub_keys_list)
    
    # Build shell command to write SSH keys
    # Use multiple echo statements to append each key (more reliable in Junos RPC context)
    # First, create directory and remove old file
    shell_cmd = f"mkdir -p {ssh_dir}; rm -f {auth_keys_file}; "
    
    # Append each public key using echo >>
    for key_line in pub_keys_list:
        # Escape single quotes in the key for shell safety
        safe_key = key_line.replace("'", "'\\''")
        shell_cmd += f"echo '{safe_key}' >> {auth_keys_file}; "
    
    # Set correct permissions and ownership
    shell_cmd += (
        f"chown {username}:{group} {ssh_dir} {auth_keys_file}; "
        f"chmod 700 {ssh_dir}; "
        f"chmod 600 {auth_keys_file}; "
        f"echo '[VERIFY] File size and perms:'; ls -lh {auth_keys_file}; "
        f"echo '[VERIFY] Line count:'; wc -l {auth_keys_file}"
    )
    
    # Execute via Junos RPC with system privileges
    try:
        # Connect to device if needed
        host = device_host(device)
        auth = device.get("auth") or {}
        user = auth.get("username")
        password = auth.get("password")
        
        print(f"[DEBUG] write_ssh_authorized_keys: target={target_name} user={username} keys={len(pub_keys_list)} host={host}")
        
        if not user or not password:
            raise RuntimeError(f"missing auth for SSH key setup on {target_name}")
        
        dev = Device(host=host, user=user, passwd=password, port=22, gather_facts=False)
        dev.open()
        try:
            print(f"[DEBUG] executing shell command on {target_name}...")
            # Use Junos RPC request_shell_execute (same as bootstrap)
            result = dev.rpc.request_shell_execute(command=shell_cmd)
            print(f"[DEBUG] RPC returned")
            # RPC returns XML element, extract text properly
            if result is not None:
                # Get text from XML element - result is ElementTree element
                output = ""
                if hasattr(result, 'text') and result.text:
                    output = result.text.strip()
                elif hasattr(result, 'itertext'):
                    output = "".join(result.itertext()).strip()
                else:
                    # Fallback: convert to string and look for actual content
                    output_str = str(result)
                    if "<output>" in output_str:
                        # Extract text between XML tags
                        import re
                        match = re.search(r'<output>(.*?)</output>', output_str, re.DOTALL)
                        if match:
                            output = match.group(1).strip()
                
                if output:
                    # Show all output lines for verification
                    output_lines = output.split('\n')
                    for line in output_lines:
                        if line.strip():
                            print(f"[DEBUG] >>> {line}")
                    
                    # Check for success indicators
                    if "rw-------" in output and f"{username}" in output:
                        print(f"[OK] SSH authorized_keys written for {username} on {target_name} ({len(pub_keys_list)} keys) - VERIFIED")
                        return True
                    
                    # Check for errors
                    if "error" in output.lower() or "failed" in output.lower():
                        raise RuntimeError(f"shell execution error: {output}")
                    
                    print(f"[OK] SSH authorized_keys written for {username} on {target_name} ({len(pub_keys_list)} keys)")
                    return True
                else:
                    # No output but command executed - likely success
                    print(f"[DEBUG] RPC command executed successfully (no output)")
                    print(f"[OK] SSH authorized_keys written for {username} on {target_name} ({len(pub_keys_list)} keys)")
                    return True
            else:
                print(f"[WARN] SSH authorized_keys write on {target_name} - RPC returned None")
            return True
        finally:
            try:
                dev.close()
            except Exception:
                pass
    except Exception as exc:
        print(f"[ERROR] SSH key write failed for {username} on {target_name}: {exc}")
        raise


def collect_script_user_public_keys(devices):
    devices = normalize_devices(devices)
    pub_keys = {}
    pub_path = qkd_peer_cmd_ssh_public_key()
    for device in devices:
        name = device_name(device)

        # Always read peer_cmd SSH public key from filesystem (not Junos config),
        # to ensure we get fresh keys after rotation, not stale config values.
        result = ssh_deploy_cmd(device, f"cat {pub_path}", timeout=20)
        if result.returncode != 0:
            raise RuntimeError(f"failed to read peer command public key on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
        
        key = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ssh-rsa ") or line.startswith("ssh-ed25519 ") or line.startswith("ecdsa-sha2-"):
                key = line
                break

        if not key:
            raise RuntimeError(
                f"invalid peer command public key on {name} path={pub_path}\n"
                f"raw_output={result.stdout}"
            )

        pub_keys[name] = key
    return pub_keys


def install_peer_authorized_keys(devices):
    devices = normalize_devices(devices)
    pub_keys = collect_script_user_public_keys(devices)
    device_names = {device_name(d) for d in devices}
    max_attempts = 5
    retry_wait_seconds = [2, 4, 8, 12]

    print("\n" + "="*70)
    print("PHASE 1: etsi_peer_view SSH key synchronization (peer verification)")
    print("="*70)
    print("[*] Synchronizing etsi_peer_view SSH keys to authorized_keys...\n")

    def is_config_locked_error(exc):
        text = str(exc or "").lower()
        return (
            "configuration database locked by" in text
            or "exclusive [edit]" in text
        )

    def is_statement_not_found_warning(exc):
        text = str(exc or "").lower()
        return "statement not found" in text

    def linked_peer_sources(target_device):
        """
        Return source device names that are expected to open peer SSH sessions
        towards target_device. We scope authorized keys to direct topology peers
        instead of installing keys from the entire fleet.
        """
        sources = set()
        for link in target_device.get("links", []) or []:
            if not isinstance(link, dict):
                continue
            peer = link.get("peer")
            if peer and peer in device_names:
                sources.add(str(peer))
        return sources

    def parse_public_key(line):
        key_line = (line or "").strip()
        parts = key_line.split()
        if len(parts) < 2:
            return None, None
        key_type = parts[0].strip()
        if key_type.startswith("ssh-") or key_type.startswith("ecdsa-"):
            return key_type, key_line
        return None, None

    def collect_configured_peer_keys(device, peer_user):
        result = ssh_deploy_cmd(
            device,
            f"cli -c 'show configuration system login user {peer_user} | display set'",
            timeout=20,
            include_failed_marker=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"failed to read configured peer SSH keys target={device_name(device)} peer_cmd_user={peer_user}\n"
                f"stdout={result.stdout}\n"
                f"stderr={result.stderr}"
            )

        configured = []
        seen = set()
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            match = re.search(
                r'^set system login user \S+ authentication (ssh-[^ ]+|ecdsa-[^ ]+) "([^"]+)"$',
                line,
            )
            if not match:
                continue
            key_type = match.group(1).strip()
            key_line = match.group(2).strip()
            marker = (key_type, key_line)
            if marker in seen:
                continue
            seen.add(marker)
            configured.append(marker)

        return configured

    synced_targets = []
    failed_targets = []

    for device in devices:
        target = device_name(device)
        sync_target_user = qkd_peer_cmd_user()  # etsi_peer_view
        
        source_names = linked_peer_sources(device)
        source_names.add(target)  # Always include device itself
        if not source_names:
            print(f"[WARN] no linked peer sources found for target={target}; peer authorized_keys will be empty")

        # Collect desired keys as list of pub_key strings
        desired_pub_keys = []
        for source_name in sorted(source_names):
            pub_key = pub_keys.get(source_name)
            if not pub_key:
                raise RuntimeError(
                    f"missing peer public key source={source_name} target={target}; run deploy/bootstrap on source first"
                )
            desired_pub_keys.append(pub_key)
        
        sources_label = ', '.join(sorted(source_names)) if source_names else '(none)'
        print(
            f"[INFO] peer SSH key sync target={target} user={sync_target_user} "
            f"keys={len(desired_pub_keys)} sources={sources_label}"
        )
        
        try:
            # Step 1: Remove SSH keys from Junos config (they override authorized_keys)
            remove_ssh_keys_from_junos_config(device, sync_target_user)
            
            # Step 2: Write SSH keys to authorized_keys file
            write_ssh_authorized_keys(device, sync_target_user, desired_pub_keys)
            synced_targets.append(target)
        except Exception as exc:
            failed_targets.append((target, str(exc)))

    print("\n" + "-"*70)
    print("=== PHASE 1 Summary: etsi_peer_view SSH key sync ===")
    print("-"*70)
    print(f"Result: {'OK' if not failed_targets else 'FAILED'}")
    print(f"Synced targets: {len(synced_targets)}")
    if failed_targets:
        print(f"Failed targets: {len(failed_targets)}")
        for target, error in failed_targets:
            print(f"- {target}: {error}")
        raise RuntimeError(
            f"failed to configure peer SSH keys on: {', '.join(t for t, _ in failed_targets)}"
        )

    # PHASE 2: Synchronize macsec_user SSH public keys (qkd_id_ed25519.pub) to Junos config
    # Same Junos config approach as Phase 1, but for macsec_user instead of etsi_peer_view
    print("\n" + "="*70)
    print("PHASE 2: macsec_user SSH key synchronization (key installation)")
    print("="*70)
    print("[*] Synchronizing macsec_user SSH keys to authorized_keys...\n")
    
    # Reset counters for Phase 2
    synced_targets = []
    failed_targets = []
    
    # Collect macsec_user SSH public keys (qkd_id_ed25519.pub)
    script_pub_keys = {}
    script_pub_path = qkd_ssh_public_key()
    for device in devices:
        name = device_name(device)
        result = ssh_deploy_cmd(device, f"cat {script_pub_path}", timeout=20, include_failed_marker=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"failed to read macsec_user SSH public key on {name} path={script_pub_path}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        key = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ssh-rsa ") or line.startswith("ssh-ed25519 ") or line.startswith("ecdsa-sha2-"):
                key = line
                break
        if not key:
            raise RuntimeError(
                f"invalid macsec_user SSH public key on {name} path={script_pub_path}\nraw={result.stdout}"
            )
        script_pub_keys[name] = key
    
    # PHASE 2 (continued): Synchronize macsec_user SSH public keys to authorized_keys
    sync_user = qkd_script_user()  # macsec_user
    
    for device in devices:
        target = device_name(device)
        
        # Build desired keys: peers + self
        peer_sources = set()
        for link in device.get("links", []) or []:
            if not isinstance(link, dict):
                continue
            peer = link.get("peer")
            if peer and peer in device_names:
                peer_sources.add(str(peer))
        peer_sources.add(target)  # Include device itself
        
        # Collect desired keys as list of pub_key strings
        desired_pub_keys = []
        for source_name in sorted(peer_sources):
            pub_key = script_pub_keys.get(source_name)
            if not pub_key:
                raise RuntimeError(
                    f"missing macsec_user SSH public key source={source_name} target={target}"
                )
            desired_pub_keys.append(pub_key)
        
        sources_label = ', '.join(sorted(peer_sources)) if peer_sources else '(none)'
        print(
            f"[INFO] macsec_user SSH key sync target={target} user={sync_user} "
            f"keys={len(desired_pub_keys)} sources={sources_label}"
        )
        
        try:
            write_ssh_authorized_keys(device, sync_user, desired_pub_keys)
            synced_targets.append(target)
        except Exception as exc:
            failed_targets.append((target, str(exc)))

    print("\n" + "-"*70)
    print("=== PHASE 2 Summary: macsec_user SSH key sync ===")
    print("-"*70)
    print(f"Result: {'OK' if not failed_targets else 'FAILED'}")
    print(f"Synced targets: {len(synced_targets)}")
    if failed_targets:
        print(f"Failed targets: {len(failed_targets)}")
        for target, error in failed_targets:
            print(f"- {target}: {error}")
        raise RuntimeError(
            f"failed to configure macsec_user SSH keys on: {', '.join(t for t, _ in failed_targets)}"
        )


def check_peer_ssh_from_device(device):
    device = normalize_device(device)
    name = device_name(device)
    peer_user = qkd_peer_cmd_user(device)
    key_path = qkd_peer_cmd_ssh_private_key()
    known_hosts = f"{qkd_ssh_dir()}/known_hosts"

    for link in device.get("links", []):
        peer_ip = link.get("peer_ip")
        if not peer_ip:
            print(f"[WARN] skipping peer SSH check device={name} reason=missing_peer_ip")
            continue

        peer_payload = "show system uptime"

        cmd = (
            f"ssh -i {key_path} "
            f"-o IdentitiesOnly=yes "
            f"-o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile={known_hosts} "
            f"-o BatchMode=yes "
            f"-o ConnectTimeout=2 "
            f"-o ServerAliveInterval=2 "
            f"-o ServerAliveCountMax=1 "
            f"-o LogLevel=ERROR "
            f"{peer_user}@{peer_ip} "
            f"{shlex.quote(peer_payload)}"
        )
        result = ssh_script_user_onbox_cmd(device, cmd, timeout=8)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        combined = f"{stdout}\n{stderr}"
        combined_low = combined.lower()

        # Success markers in uptime output (Junos MX/ACX style)
        uptime_markers = ["current time:", "system booted:", "protocols started:", "last configured:"]
        has_uptime_content = any(m in combined_low for m in uptime_markers)

        hard_fail_markers = [
            "permission denied",
            "publickey,password",
            "authentication failed",
            "no such identity",
            "bad permissions",
            "private key",
        ]
        is_auth_fail = any(m in combined_low for m in hard_fail_markers)

        if is_auth_fail:
            raise RuntimeError(
                f"peer SSH authentication failed from {name} to {peer_ip} as {peer_user}\n"
                f"stdout={stdout}\n"
                f"stderr={stderr}"
            )

        # Accept if uptime content is present regardless of non-fatal XML warnings
        # (e.g. "error: invalid xml tag" from Junos ACX command-handler is harmless)
        if has_uptime_content or (stdout.strip() and "error:" not in combined_low):
            print(f"[OK] peer SSH {name} -> {peer_ip} as {peer_user}")
            print_if_verbose(stdout)
            continue

        if "rpctimeouterror" in combined_low or "timeout" in combined_low:
            print(
                f"[WARN] peer reachability check timed out: {name} -> {peer_ip} as {peer_user}; "
                "manual SSH may still be valid"
            )
            print_if_verbose(stdout)
            print_if_verbose(stderr)
            continue

        raise RuntimeError(
            f"peer SSH command failed from {name} to {peer_ip} as {peer_user}\n"
            f"stdout={stdout}\n"
            f"stderr={stderr}"
        )


# -------------------------------------------------
# Postdeploy checks
# -------------------------------------------------


def check_event_script_path(device):
    device = normalize_device(device)
    name = device_name(device)
    path = qkd_remote_event_script()
    result = ssh_deploy_cmd(device, f"ls -l {path}", timeout=20)
    if result.returncode != 0:
        raise RuntimeError(f"qkd_onbox.py missing on {name} at {path}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] event script exists on {name}: {path}")
    print_if_verbose(result.stdout)


def check_event_script_permissions(device):
    device = normalize_device(device)
    name = device_name(device)
    path = qkd_remote_event_script()
    mode = QKD.get("ONBOX_SCRIPT_MODE", "0555")
    result = ssh_deploy_cmd(device, f"chmod {mode} {path}; ls -l {path}", timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"event qkd_onbox.py permission check failed on {name}\npath={path}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] event script permissions set: {path} mode={mode}")
    print_if_verbose(result.stdout)


def check_op_script_path(device):
    device = normalize_device(device)
    name = device_name(device)
    path = qkd_remote_op_script()
    result = ssh_deploy_cmd(device, f"ls -l {path}", timeout=20)
    if result.returncode != 0:
        raise RuntimeError(f"qkd_onbox.py missing on {name} at {path}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] op script exists on {name}: {path}")
    print_if_verbose(result.stdout)


def check_op_script_permissions(device):
    device = normalize_device(device)
    name = device_name(device)
    path = qkd_remote_op_script()
    mode = QKD.get("ONBOX_SCRIPT_MODE", "0555")
    result = ssh_deploy_cmd(device, f"chmod {mode} {path}; ls -l {path}", timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"qkd_onbox.py permission check failed on {name}\npath={path}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] op script permissions set: {path} mode={mode}")


def check_onbox_json_permissions(device):
    device = normalize_device(device)
    name = device_name(device)
    mode = QKD.get("ONBOX_JSON_MODE", "0664")
    config_path = qkd_remote_config_json()
    inventory_path = qkd_remote_inventory_json()
    cmd = f"chmod {mode} {config_path} {inventory_path}; ls -l {config_path}; ls -l {inventory_path}"
    result = ssh_deploy_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            f"onbox JSON permission check failed on {name}\n"
            f"config_path={config_path}\n"
            f"inventory_path={inventory_path}\n"
            f"stdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )
    print(f"[OK] onbox JSON permissions set on {name}: mode={mode}")
    print_if_verbose(result.stdout)


def check_system_scripts_python3(device):
    device = normalize_device(device)
    name = device_name(device)
    cmd = 'cli -c "show configuration system scripts | display set"'
    result = ssh_deploy_cmd(device, cmd, timeout=20)
    if "set system scripts language python3" not in result.stdout:
        raise RuntimeError(f"system scripts python3 not configured on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] system scripts python3 configured on {name}")
    print_if_verbose(result.stdout)


def check_event_options_script_user(device):
    device = normalize_device(device)
    name = device_name(device)
    script_user = qkd_script_user()
    script_name = QKD["SCRIPT_NAME"]
    cmd = 'cli -c "show configuration event-options event-script | display set"'
    result = ssh_deploy_cmd(device, cmd, timeout=30)
    if script_name not in result.stdout or f"python-script-user {script_user}" not in result.stdout:
        raise RuntimeError(f"event-options python-script-user mismatch on {name}\nexpected script_user={script_user}\nstdout={result.stdout}\nstderr={result.stderr}")
    print(f"[OK] event script user configured on {name}: {script_user}")
    print_if_verbose(result.stdout)


def grep_remote_literal(device, literal, path, timeout=20):
    cmd = f"grep -n -F {shlex.quote(str(literal))} {path}"
    return ssh_deploy_cmd(device, cmd, timeout=timeout)


def check_onbox_embedded_config(device):
    device = normalize_device(device)
    name = device_name(device)
    config_path = qkd_remote_config_json()
    inventory_path = qkd_remote_inventory_json()

    for path in (config_path, inventory_path):
        result = ssh_deploy_cmd(device, f"ls -l {path}", timeout=20)
        if result.returncode != 0:
            raise RuntimeError(
                f"external onbox JSON missing on {name}\npath={path}\nstdout={result.stdout}\nstderr={result.stderr}"
            )

    script_user = qkd_script_user()
    expected_key = qkd_ssh_private_key()
    peer_cmd_user = qkd_peer_cmd_user(device)
    expected_peer_key = qkd_peer_cmd_ssh_private_key()
    checks = [
        (config_path, f'"script_user": "{script_user}"'),
        (config_path, f'"ssh_key": "{expected_key}"'),
        (config_path, f'"peer_cmd_user": "{peer_cmd_user}"'),
        (config_path, f'"peer_cmd_ssh_key": "{expected_peer_key}"'),
        (inventory_path, '"links": ['),
    ]

    failed = []
    for path, marker in checks:
        result = grep_remote_literal(device=device, literal=marker, path=path, timeout=20)
        if result.returncode != 0:
            failed.append((path, marker, result.stdout, result.stderr))
        else:
            print_if_verbose(f"[OK] on-box config check on {name}: {marker} found in {path}")

    if failed:
        lines = []
        for path, marker, stdout, stderr in failed:
            lines.append(
                f"- path={path}\n"
                f"  expected={marker}\n"
                f"  stdout={stdout}\n"
                f"  stderr={stderr}"
            )
        raise RuntimeError(
            f"on-box JSON config validation failed on {name}: expected values not found in deployed JSON\n" + "\n".join(lines)
        )

    print(
        f"[OK] on-box JSON identity on {name}: script_user={script_user} peer_cmd_user={peer_cmd_user}"
    )


def check_onbox_runtime_policy_config(device):
    device = normalize_device(device)
    name = device_name(device)
    config_path = qkd_remote_config_json()
    inventory_path = qkd_remote_inventory_json()
    runtime_pki = load_runtime_pki_profile()
    runtime_policy = load_runtime_qkd_policy()
    pki = runtime_pki.get("pki", {})
    qkd_policy = runtime_policy.get("qkd_policy", {})
    pki_profile = pki.get("profile")
    juniper_pki = pki.get("juniper", {})
    trust_bundle = juniper_pki.get("trust_bundle") or pki.get("trust_bundle")
    max_installed_keys = qkd_policy.get("max_installed_keys")
    required_markers = [
        (config_path, "qkd_policy", '"qkd_policy"'),
        (config_path, "enabled_flag", '"enabled": false'),
        (config_path, "pki_profile", f'"pki_profile": "{pki_profile}"'),
        (config_path, "max_installed_keys", f'"max_installed_keys": {int(max_installed_keys)}'),
        (inventory_path, "inventory_links", '"links": ['),
        (inventory_path, "inventory_enabled", '"enabled": false'),
    ]
    if trust_bundle:
        required_markers.append((config_path, "trust_bundle", f'"trust_bundle": "{trust_bundle}"'))
    failed = []
    for path, label, marker in required_markers:
        result = grep_remote_literal(device=device, literal=marker, path=path, timeout=20)
        if result.returncode != 0:
            failed.append((path, label, marker, result.stdout, result.stderr))
        else:
            print_if_verbose(f"[OK] on-box config check on {name}: {label} present in {path}")
    print(f"[OK] on-box runtime config on {name}: pki_profile={pki_profile} max_installed_keys={max_installed_keys} trust_bundle={'present' if trust_bundle else 'missing'}")
    if failed:
        lines = []
        for path, label, marker, stdout, stderr in failed:
            lines.append(
                f"- missing marker={label}\n"
                f"  path={path}\n"
                f"  expected={marker}\n"
                f"  stdout={stdout}\n"
                f"  stderr={stderr}"
            )
        raise RuntimeError(
            f"deployed qkd_onbox external JSON validation failed on {name}\n" + "\n".join(lines)
        )


def expected_max_installed_keys():
    runtime_policy = load_runtime_qkd_policy()
    qkd_policy = runtime_policy.get("qkd_policy", {})
    value = int(qkd_policy.get("max_installed_keys", 5))
    return 1 if value < 1 else value


def keychain_names_from_device(device):
    device = normalize_device(device)
    names = []
    for link in device.get("links", []):
        ca_names = []
        ca_name = link.get("ca_name")
        if ca_name:
            ca_names.append(ca_name)
        for ca in link.get("ca_names", []) or []:
            if ca and ca not in ca_names:
                ca_names.append(ca)
        for ca in ca_names:
            keychain = link.get("keychain_name") or f"QKD_{ca}"
            if keychain not in names:
                names.append(keychain)
    return names


def check_keychain_slot_limit(device):
    device = normalize_device(device)
    name = device_name(device)
    max_keys = expected_max_installed_keys()
    allowed_max_index = max_keys - 1
    keychains = keychain_names_from_device(device)
    if not keychains:
        print(f"[WARN] no QKD keychains found in runtime links for {name}")
        return
    cmd = 'cli -c "show configuration security authentication-key-chains | display set"'
    result = ssh_deploy_cmd(device, cmd, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"failed to read authentication-key-chains on {name}\nstdout={result.stdout}\nstderr={result.stderr}")
    output = result.stdout or ""
    violations = []
    for keychain in keychains:
        pattern = re.compile(r"set security authentication-key-chains key-chain " + re.escape(keychain) + r" key (\d+)\b")
        indexes = sorted({int(match.group(1)) for match in pattern.finditer(output)})
        overflow = [idx for idx in indexes if idx > allowed_max_index]
        if overflow:
            violations.append({"keychain": keychain, "configured_indexes": indexes, "overflow_indexes": overflow})
        else:
            #print(f"[OK] keychain slot limit on {name}: {keychain} indexes={indexes} max_allowed={allowed_max_index}")
            print(
                    f"[OK] keychain entries on {name}: "
                    f"{keychain} used_slots={indexes} "
                    f"capacity={allowed_max_index + 1}"
            )
    if violations:
        lines = []
        for item in violations:
            lines.append(f"- keychain={item['keychain']} configured_indexes={item['configured_indexes']} overflow_indexes={item['overflow_indexes']}")
        raise RuntimeError(f"QKD keychain slot limit validation failed on {name}\nmax_installed_keys={max_keys}\nallowed_indexes=0..{allowed_max_index}\nstale key slots found:\n" + "\n".join(lines))


def check_qkd_status_as_script_user(device):
    device = normalize_device(device)
    name = device_name(device)
    script_user = qkd_script_user()
    for link in device.get("links", []):
        iface = link.get("interface")
        if not iface:
            continue
        cmd = f"op qkd_onbox.py action status iface {iface}"
        result = None
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            timeout = 30 if attempt == 1 else 45
            result = ssh_script_user_onbox_cmd(
                device,
                cmd,
                timeout=timeout,
                include_failed_marker=False,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            combined = f"{stdout}\n{stderr}".lower()
            is_timeout = ("rpctimeouterror" in combined) or ("timeout" in combined)
            if result.returncode == 0:
                break
            if is_timeout and attempt < max_attempts:
                print(f"[WARN] qkd status timeout on {name} iface={iface} attempt={attempt}/{max_attempts}; retrying")
                continue
            raise RuntimeError(
                f"QKD status failed on {name} iface={iface} as {script_user}\n"
                f"stdout={stdout}\n"
                f"stderr={stderr}"
            )

        raw = ((result.stdout if result else "") or "").strip()
        try:
            status = json.loads(raw)
            ca_name = status.get("ca_name")
            keychain_name = status.get("keychain_name")
            generation = status.get("generation")
            installed_keys = status.get("installed_keys", []) or []
            health = status.get("health", {}) or {}
            degraded = bool(health.get("degraded"))
            declared_down = bool(health.get("declared_down"))
            kme_fail_count = health.get("kme_fail_count", 0)
            last_kme_error = health.get("last_kme_error")
            health_state = "DEGRADED" if degraded or declared_down or last_kme_error else "OK"
            print(f"[OK] qkd status {name} iface={iface}: ca={ca_name} keychain={keychain_name} generation={generation} installed_keys={len(installed_keys)} kme_fail_count={kme_fail_count} health={health_state}")
            print_if_verbose(raw)
        except Exception:
            print(f"[OK] qkd status {name} iface={iface}")
            print_if_verbose(raw)


def check_no_state_save_errors(device):
    device = normalize_device(device)
    name = device_name(device)
    cmd = "grep -h -E 'STATE SAVE ERROR|KEYCHAIN BOOTSTRAP STATE SAVE FAIL|Operation not permitted' /var/tmp/qkd_debug*.log 2>/dev/null || true"
    result = ssh_deploy_cmd(device, cmd, timeout=20)
    output = (result.stdout or "").strip()
    real_error_markers = ["STATE SAVE ERROR", "KEYCHAIN BOOTSTRAP STATE SAVE FAIL", "Operation not permitted"]
    matched_lines = []
    for line in output.splitlines():
        line = line.strip()
        if line and any(marker in line for marker in real_error_markers):
            matched_lines.append(line)
    if matched_lines:
        raise RuntimeError(f"STATE SAVE ERROR detected on {name}\nstdout={chr(10).join(matched_lines)}\nstderr={result.stderr}")
    print(f"[OK] no state save errors on {name}")


# -------------------------------------------------
# Validation entrypoints
# -------------------------------------------------


def validate_device_identity_predeploy(device, shipment_aware=False):
    device = normalize_device(device)
    name = device_name(device)
    validate_device_record(device)

    if platform_is_legacy_qfx(device):
        print(f"=== QKD legacy QFX pre-deploy validation: {name} ===")
        script_user_ready = True
        try:
            check_script_user_exists(device)
        except Exception:
            if shipment_aware:
                check_shipment_preload_artifacts(device)
                script_user_ready = False
                print(f"[INFO] shipment-aware predeploy: skipping script-user checks on {name} (SCRIPT_USER not ready yet)")
            else:
                raise

        if script_user_ready:
            check_script_user_home_simple(device)
            check_script_dirs_simple(device)
            check_runtime_cleanup_simple(device)
        print(f"[OK] QKD legacy QFX pre-deploy validation passed: {name}")
        return

    print(f"=== QKD pre-deploy validation: {name} ===")
    script_user_ready = True
    try:
        check_script_user_exists(device)
    except Exception:
        if shipment_aware:
            check_shipment_preload_artifacts(device)
            script_user_ready = False
            print(f"[INFO] shipment-aware predeploy: skipping script-user checks on {name} (SCRIPT_USER not ready yet)")
        else:
            raise

    if script_user_ready:
        check_script_user_home_simple(device)
        check_script_dirs_simple(device)
        check_script_user_ssh_identity(device)
        check_script_user_authorized_keys(device)
        check_runtime_cleanup_simple(device)
    print(f"[OK] QKD pre-deploy validation passed: {name}")


def validate_device_identity_postdeploy(device):
    device = normalize_device(device)
    name = device_name(device)
    validate_device_record(device)

    if platform_is_legacy_qfx(device):
        print(f"=== QKD legacy QFX post-deploy validation: {name} ===")
        print(f"[OK] QKD legacy QFX post-deploy validation passed: {name}")
        return

    print(f"=== QKD post-deploy validation: {name} ===")
    check_op_script_path(device)
    check_op_script_permissions(device)
    check_event_script_path(device)
    check_event_script_permissions(device)
    check_onbox_json_permissions(device)
    check_system_scripts_python3(device)
    check_event_options_script_user(device)
    check_onbox_embedded_config(device)
    check_onbox_runtime_policy_config(device)
    check_keychain_slot_limit(device)
    check_peer_ssh_from_device(device)
    check_qkd_status_as_script_user(device)
    check_no_state_save_errors(device)
    print(f"[OK] QKD post-deploy validation passed: {name}")


def validate_all_devices_predeploy(devices, shipment_aware=False):
    devices = normalize_devices(devices)
    check_validation_plan()
    print("")
    print("=== QKD pre-deploy validation ===")
    print(f"Devices: {len(devices)}")
    print("")
    failed = []
    for index, device in enumerate(devices, start=1):
        name = device_name(device)
        host = device_host(device)
        print(f"[{index}/{len(devices)}] {name}")
        print(f"  host        : {host}")
        print(f"  script_user : {qkd_script_user()}")
        print("")
        try:
            validate_device_identity_predeploy(device, shipment_aware=shipment_aware)
            print(f"[OK] pre-deploy validation passed: {name}")
            print("")
        except Exception as exc:
            failed.append((name, exc))
            print(f"[FAIL] pre-deploy validation failed: {name}")
            print(str(exc))
            print("")
    if failed:
        print("=== QKD pre-deploy validation summary ===")
        print("Result: FAILED")
        print(f"Failed devices: {len(failed)}")
        print("")
        for name, exc in failed:
            print(f"- {name}: {exc}")
        raise RuntimeError("QKD pre-deploy validation failed for: " + ", ".join(name for name, _ in failed))

    # Fast predeploy: do not synchronize peer authorized_keys and do not run peer SSH matrix.
    # Those checks are expensive and can be blocked by Junos banner / PyEZ shell-wrapper behavior.
    # Peer SSH can still be validated in postdeploy or manually if needed.
    print("[OK] fast predeploy complete: peer SSH matrix skipped")
    print("=== QKD pre-deploy validation complete ===")
    print("Result: OK")


def validate_all_devices_postdeploy(devices):
    devices = normalize_devices(devices)
    check_validation_plan()
    print("")
    print("=== QKD post-deploy validation ===")
    print(f"Devices: {len(devices)}")
    print("")

    try:
        base = load_inventory_base()
        secrets = base.get("secrets", {}) if isinstance(base, dict) else {}
        if not isinstance(secrets, dict):
            secrets = {}

        script_user = secrets.get("script_user") or QKD.get("SCRIPT_USER") or "admin"
        script_password = (
            secrets.get("script_password")
            or secrets.get("admin_password")
            or secrets.get("default_password")
            or None
        )

        if script_password:
            for device in devices:
                auth = device.get("auth")
                if not isinstance(auth, dict):
                    auth = {}
                    device["auth"] = auth
                auth["username"] = script_user
                auth["password"] = script_password
    except Exception:
        pass

    # Ensure script-user keys are present and rotated (if due) before syncing
    # peer authorized keys across devices.
    for device in devices:
        check_script_user_ssh_identity(device)

    # Ensure peer command keys are present via Junos login configuration before
    # running matrix SSH authentication checks.
    install_peer_authorized_keys(devices)

    failed = []
    for index, device in enumerate(devices, start=1):
        name = device_name(device)
        host = device_host(device)
        print(f"[{index}/{len(devices)}] {name}")
        print(f"  host        : {host}")
        print(f"  script_user : {qkd_script_user()}")
        print("")
        try:
            validate_device_identity_postdeploy(device)
            print(f"[OK] post-deploy validation passed: {name}")
            print("")
        except Exception as exc:
            failed.append((name, exc))
            print(f"[FAIL] post-deploy validation failed: {name}")
            print(str(exc))
            print("")
    if failed:
        print("=== QKD post-deploy validation summary ===")
        print("Result: FAILED")
        print(f"Failed devices: {len(failed)}")
        print("")
        for name, exc in failed:
            print(f"- {name}: {exc}")
        raise RuntimeError("QKD post-deploy validation failed for: " + ", ".join(name for name, _ in failed))
    print("=== QKD post-deploy validation complete ===")
    print("Result: OK")


def validate_all_devices(devices, phase="predeploy", shipment_aware=False):
    devices = normalize_devices(devices)
    if phase == "predeploy":
        validate_all_devices_predeploy(devices, shipment_aware=shipment_aware)
        return
    if phase == "postdeploy":
        validate_all_devices_postdeploy(devices)
        return
    if phase == "full":
        validate_all_devices_predeploy(devices)
        validate_all_devices_postdeploy(devices)
        return
    raise ValueError(f"unknown validate phase={phase}")


def validate_device_identity(device, phase="predeploy"):
    device = normalize_device(device)
    if phase == "predeploy":
        return validate_device_identity_predeploy(device)
    if phase == "postdeploy":
        return validate_device_identity_postdeploy(device)
    if phase == "full":
        validate_device_identity_predeploy(device)
        validate_device_identity_postdeploy(device)
        return
    raise ValueError(f"unknown validation phase={phase}")
