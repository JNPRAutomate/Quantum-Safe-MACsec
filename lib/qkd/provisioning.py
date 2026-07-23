from jnpr.junos import Device
from jnpr.junos.utils.config import Config
from jnpr.junos.utils.scp import SCP

import yaml
import time
import subprocess
from pathlib import Path

import logging

from lib.qkd.rendering import build_device_config
from lib.common.settings import CONFIG
from lib.common.settings import PKI
from lib.common.settings import QKD
from lib.common.config import load_inventory, load_platform
from lib.common.config import load_runtime_pki_profile
from lib.common.config import load_runtime_qkd_policy
from jinja2 import Environment, FileSystemLoader


# ----------------------------------------
# PATHS
# ----------------------------------------

# <repo>/lib/qkd/provisioning.py
# parents[0] = <repo>/lib/qkd
# parents[1] = <repo>/lib
# parents[2] = <repo>
BASE_DIR = Path(__file__).resolve().parents[2]

CONFIG_DIR = BASE_DIR / CONFIG["inventory_dir"]
RUNTIME_DIR = BASE_DIR / CONFIG["runtime_dir"]
PLATFORM_DIR = CONFIG_DIR / "platforms"


def resolve_certs_dir():
    canonical = BASE_DIR / "certs"
    if canonical.exists():
        return canonical

    configured = BASE_DIR / CONFIG.get("certs_dir", "certs")
    if configured.exists():
        return configured

    return canonical


CERTS_DIR = resolve_certs_dir()


def render_common_template(template_name, context):
    templates_dir = BASE_DIR / "config" / "templates" / "common"
    env = Environment(loader=FileSystemLoader(templates_dir))
    template = env.get_template(template_name)
    return template.render(**context)


# --------------------------
# DEBUG CONTROL
# --------------------------

DEBUG = False


def dbg(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")


def dbg_block(title, content):
    if DEBUG:
        print(f"\n[DEBUG] ===== {title} =====")
        print(content)
        print("[DEBUG] =====================\n")


def progress(filename, size, sent):
    if not DEBUG:
        return

    if isinstance(filename, bytes):
        filename = filename.decode()

    percent = int((sent / size) * 100)
    print(f"{filename} -> {percent}% ({sent}/{size} bytes)")


def rpc_text(rsp):
    if rsp is None:
        return ""
    try:
        return "".join(rsp.itertext()).strip()
    except Exception:
        pass
    try:
        if rsp.text:
            return rsp.text.strip()
    except Exception:
        pass
    return str(rsp).strip()


# ----------------------------------------
# DUAL RE HELPERS
# ----------------------------------------


def run_cli(dev, command, name=None, strict=False):
    """
    Execute Junos CLI command and return text.
    strict=False is intentional for RE sync helpers because file copy may fail
    on single-RE platforms or when a target RE alias is not valid.
    """
    try:
        rsp = dev.rpc.cli(command, format="text")
        out = rpc_text(rsp)
        if DEBUG and out:
            label = name or "device"
            print(f"[{label}] CLI {command}\n{out}")
        return out
    except Exception as exc:
        if strict:
            raise
        if DEBUG:
            label = name or "device"
            print(f"[{label}] CLI warning: {command}: {exc}")
        return str(exc)


def run_shell(dev, command, name=None, strict=False):
    try:
        rsp = dev.rpc.request_shell_execute(command=command)
        out = rpc_text(rsp)
        if DEBUG and out:
            label = name or "device"
            print(f"[{label}] SHELL {command}\n{out}")
        return out
    except Exception as exc:
        if strict:
            raise
        if DEBUG:
            label = name or "device"
            print(f"[{label}] SHELL warning: {command}: {exc}")
        return str(exc)


def has_dual_re(dev, name):
    """
    Detect dual RE robustly.

    MX304 output may show either:
      - Routing Engine 0 / Routing Engine 1
      - RE0 / RE1
      - Slot 0: / Slot 1:

    The previous implementation only matched re0/re1 or routing engine 0/1,
    so it missed actual MX304 dual-RE outputs that use Slot 0 / Slot 1.
    """
    out = run_cli(dev, "show chassis routing-engine", name=name, strict=False)
    low = (out or "").lower()

    re1_lines = []
    for line in low.splitlines():
        if (
            "re1" in line
            or "routing engine 1" in line
            or "slot 1:" in line
        ):
            re1_lines.append(line)

    if not re1_lines:
        return False

    absent_tokens = (
        " empty",
        "absent",
        "not present",
        "not-installed",
        "not installed",
        "not online",
    )

    for line in re1_lines:
        if not any(token in line for token in absent_tokens):
            return True

    return False


def copy_file_to_other_re(dev, name, src_path, dst_name=None):
    """
    Best-effort copy of a local file to the other Routing Engine.

    Tries both re0: and re1: targets because the active RE identity may vary.
    On single-RE systems this function should not be called.
    """
    dst_path = str(Path(src_path).parent / (dst_name or Path(src_path).name))
    ok = False

    for re_name in ("re0", "re1"):
        cmd = f"file copy {src_path} {re_name}:{dst_path}"
        out = run_cli(dev, cmd, name=name, strict=False)
        low = (out or "").lower()
        if "error" not in low and "failed" not in low and "no such" not in low:
            ok = True

    return ok


def sync_qkd_scripts_dual_re(dev, name, script_name):
    """
    Ensure qkd_onbox.py and external JSON runtime files exist on both routing
    engines before commit synchronize.
    """
    op_script_dir = "/var/db/scripts/op"
    event_script_dir = "/var/db/scripts/event"
    config_dir = QKD.get("ONBOX_CONFIG_DIR", op_script_dir)
    config_json_name = QKD.get("ONBOX_CONFIG_JSON_NAME", "qkd_onbox_config.json")
    inventory_json_name = QKD.get("ONBOX_INVENTORY_JSON_NAME", "qkd_onbox_inventory.json")
    script_mode = QKD.get("ONBOX_SCRIPT_MODE", "0555")
    json_mode = QKD.get("ONBOX_JSON_MODE", "0664")

    op_script = f"{op_script_dir}/{script_name}"
    event_script = f"{event_script_dir}/{script_name}"
    config_json = f"{config_dir}/{config_json_name}"
    inventory_json = f"{config_dir}/{inventory_json_name}"

    # Ensure local RE has required files before trying to copy them.
    run_shell(
        dev,
        (
            f"mkdir -p {op_script_dir} {event_script_dir} {config_dir}; "
            f"chmod {script_mode} {event_script} {op_script} 2>/dev/null || true; "
            f"chmod {json_mode} {config_json} {inventory_json} 2>/dev/null || true"
        ),
        name=name,
        strict=False,
    )

    if not has_dual_re(dev, name):
        print(f"[{name}] Single RE detected - script sync skipped")
        return

    print(f"[{name}] Dual-RE detected - syncing QKD scripts to peer RE")

    for path in (
        event_script,
        op_script,
        config_json,
        inventory_json,
    ):
        copy_file_to_other_re(dev, name, path)

    # Ask Junos to push scripts too only when explicitly enabled.
    # In some dual-RE environments this command can transiently contend for
    # candidate DB lock and interfere with immediate follow-up config loads.
    if bool(QKD.get("ENABLE_COMMIT_SYNCHRONIZE_SCRIPTS", False)):
        run_cli(dev, "commit synchronize scripts", name=name, strict=False)


def sync_certs_dual_re(dev, name, remote_dir, filenames):
    if not has_dual_re(dev, name):
        return

    print(f"[{name}] Dual-RE detected - syncing certs to peer RE")
    for filename in filenames:
        copy_file_to_other_re(dev, name, f"{remote_dir}/{filename}")


def commit_safely(dev, cu, name, sync=True):
    """
    Commit helper.

    Correct behavior:
    - Single-RE devices: normal commit only.
    - Dual-RE devices: commit synchronize.
    - If a dual-RE commit synchronize fails because backup RE is missing event
      scripts, sync QKD scripts to peer RE and retry once.
        - For known Junos license-gating edge case on backup RE
            ("requires 'L3 VPN (VXLAN)' license" + remote commit failure), fall back
            to local commit so deploy can continue.
        - Do not fall back to a normal commit on dual-RE after generic sync failure,
            because that hides real RE1 problems.
    """
    dual_re = has_dual_re(dev, name) if sync else False

    try:
        if sync and dual_re:
            cu.commit(sync=True)
        else:
            cu.commit()
        return
    except Exception as exc:
        text = str(exc)
        low = text.lower()

        vxlan_license_re1_sync_failure = (
            "requires 'l3 vpn (vxlan)' license" in low
            and "remote commit-configuration failed" in low
            and ("re1" in low or "other re" in low or "backup" in low)
        )

        if sync and dual_re and vxlan_license_re1_sync_failure:
            print(
                f"[{name}] commit synchronize blocked by known RE1 VXLAN license warning; "
                "falling back to local commit (no-synchronize)"
            )
            cu.commit()
            return

        if sync and dual_re and (
            "event script missing" in low
            or "remote commit-configuration failed" in low
        ):
            print(f"[{name}] commit synchronize failed; syncing scripts to peer RE and retrying once")
            sync_qkd_scripts_dual_re(dev, name, QKD.get("SCRIPT_NAME", "qkd_onbox.py"))
            cu.commit(sync=True)
            return

        print(f"[{name}] COMMIT FAILED")
        print(text)
        raise


def is_config_db_lock_error(exc):
    low = str(exc).lower()
    return (
        "configuration database locked" in low
        or "exclusive [edit]" in low
        or "private edits in use" in low
        or "try 'configure private' or 'configure exclusive'" in low
    )


def lock_retry_parameters():
    max_attempts = int(QKD.get("CONFIG_DB_LOCK_RETRY_ATTEMPTS", 5))
    base_wait_seconds = int(QKD.get("CONFIG_DB_LOCK_RETRY_WAIT_SECONDS", 2))
    if max_attempts < 1:
        max_attempts = 1
    if base_wait_seconds < 1:
        base_wait_seconds = 1
    return max_attempts, base_wait_seconds


# ----------------------------------------
# STATIC MACSEC
# ----------------------------------------


def build_macsec_static(device, platform_cfg):
    ca = device["macsec"].get("ca_name", "CA1")
    cipher = platform_cfg.get("macsec", {}).get("cipher", "gcm-aes-xpn-256")

    ckn = device["macsec"].get("ckn")
    cak = device["macsec"].get("cak")

    if not ckn or not cak:
        raise ValueError("Missing CKN/CAK")

    cmds = []

    cmds.append(f"set security macsec connectivity-association {ca} cipher-suite {cipher}")
    cmds.append(f"set security macsec connectivity-association {ca} security-mode static-cak")
    cmds.append(f"set security macsec connectivity-association {ca} replay-protect")
    cmds.append(f"set security macsec connectivity-association {ca} pre-shared-key ckn {ckn}")
    cmds.append(f"set security macsec connectivity-association {ca} pre-shared-key cak {cak}")

    local_ifaces = []

    for link in device.get("links", []):
        iface = link.get("interface")
        if iface and iface not in local_ifaces:
            local_ifaces.append(iface)

    if not local_ifaces:
        local_ifaces = device["macsec"].get("interfaces", [])

    for iface in local_ifaces:
        cmds.append(f"set security macsec interfaces {iface} connectivity-association {ca}")

    return cmds


# ----------------------------------------
# CERT HELPERS
# ----------------------------------------


def device_sae_id(device):
    qkd = device.get("qkd", {}) or {}

    for value in (
        qkd.get("sae_id"),
        device.get("local_sae"),
        device.get("sae"),
        device.get("sae_id"),
    ):
        if value:
            return value

    raise RuntimeError(
        f"Cannot resolve SAE ID for device {device.get('name', '<unknown>')}"
    )


def sae_id_aliases(sae_id):
    """Return canonical SAE ID plus a legacy separator variant for compatibility."""
    if not sae_id:
        return []

    aliases = [sae_id]

    if "-" in sae_id:
        aliases.append(sae_id.replace("-", "_"))
    elif "_" in sae_id:
        aliases.append(sae_id.replace("_", "-"))

    unique = []
    for alias in aliases:
        if alias and alias not in unique:
            unique.append(alias)
    return unique


def resolve_cert_file_paths(local_dev_dir, sae_id):
    for candidate_sae in sae_id_aliases(sae_id):
        candidate_cert = local_dev_dir / f"{candidate_sae}.crt"
        candidate_key = local_dev_dir / f"{candidate_sae}.key"
        if candidate_cert.exists() and candidate_key.exists():
            return candidate_cert, candidate_key

    return local_dev_dir / f"{sae_id}.crt", local_dev_dir / f"{sae_id}.key"


def resolve_cert_paths_for_device(name, device):
    sae_id = device_sae_id(device)
    sae_candidates = sae_id_aliases(sae_id)

    runtime_pki = load_runtime_pki_profile()
    pki = runtime_pki.get("pki", {})
    profile = pki.get("profile", "self_signed")

    if profile == "self_signed":
        profile_dir = CERTS_DIR / "self_signed"
        candidate_device_dirs = []
        for candidate_sae in sae_candidates:
            candidate_device_dirs.extend(
                [
                    profile_dir / candidate_sae,
                    profile_dir / "certs" / candidate_sae,
                ]
            )

        local_dev_dir = None
        for candidate in candidate_device_dirs:
            if candidate.exists():
                local_dev_dir = candidate
                break
        if local_dev_dir is None:
            local_dev_dir = candidate_device_dirs[0]

        local_cert, local_key = resolve_cert_file_paths(local_dev_dir, sae_id)

        local_ca_candidates = [
            profile_dir / "offbox_rootCA.crt",
            profile_dir / "trust_exchange" / "install_on_juniper" / "offbox_rootCA.crt",
        ]
        local_ca = None
        for candidate in local_ca_candidates:
            if candidate.exists():
                local_ca = candidate
                break
        if local_ca is None:
            local_ca = local_ca_candidates[0]

        return {
            "profile": profile,
            "sae_id": sae_id,
            "cert": local_cert,
            "key": local_key,
            "ca": local_ca,
        }

    if profile == "hierarchical_ca":
        profile_dir = CERTS_DIR / "hierarchical_ca"

        candidate_device_dirs = []
        for candidate_sae in sae_candidates:
            candidate_device_dirs.extend(
                [
                    profile_dir / "juniper_pki" / "certs" / candidate_sae,
                    profile_dir / "juniper" / candidate_sae,
                    profile_dir / "devices" / candidate_sae,
                    profile_dir / candidate_sae,
                ]
            )

        local_dev_dir = None
        for candidate in candidate_device_dirs:
            if candidate.exists():
                local_dev_dir = candidate
                break
        if local_dev_dir is None:
            local_dev_dir = candidate_device_dirs[0]

        local_cert, local_key = resolve_cert_file_paths(local_dev_dir, sae_id)

        juniper_pki = pki.get("juniper", {}) or {}
        trust_bundle = juniper_pki.get("trust_bundle") or pki.get("trust_bundle")

        if trust_bundle:
            local_ca = Path(trust_bundle)
            if not local_ca.is_absolute():
                local_ca = BASE_DIR / local_ca
        else:
            local_ca = (
                profile_dir
                / "trust_exchange"
                / "install_on_juniper"
                / "trusted-kme-ca-bundle.crt"
            )

        return {
            "profile": profile,
            "sae_id": sae_id,
            "cert": local_cert,
            "key": local_key,
            "ca": local_ca,
        }

    raise RuntimeError(
        f"[{name}] Unsupported PKI profile for cert deployment: {profile}"
    )


def push_certs(dev, name, device, base=None):
    remote_dir = PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")
    files = resolve_cert_paths_for_device(name, device)

    secrets = base.get("secrets", {}) if isinstance(base, dict) else {}
    if not isinstance(secrets, dict):
        secrets = {}
    runtime_user = secrets.get("script_user") or secrets.get("default_user") or "admin"
    peer_cmd_user = secrets.get("peer_cmd_user") or QKD.get("PEER_CMD_USER", "etsi_peer_view")

    profile = files["profile"]
    sae_id = files["sae_id"]
    local_cert = files["cert"]
    local_key = files["key"]
    local_ca = files["ca"]

    missing = [str(path) for path in (local_cert, local_key, local_ca) if not path.exists()]
    if missing:
        raise RuntimeError(
            f"[{name}] Missing local cert files for sae={sae_id} profile={profile}\n"
            f"CERTS_DIR={CERTS_DIR}\n"
            + "\n".join(missing)
        )

    print(f"[{name}] Copying certs profile={profile} sae={sae_id} to {remote_dir}")

    try:
        dev.rpc.request_shell_execute(
            command=(
                f"mkdir -p {remote_dir}; "
                f"chmod 755 {remote_dir}; "
                f"ls -ld {remote_dir}"
            )
        )
    except Exception as exc:
        raise RuntimeError(f"[{name}] Failed to prepare remote cert dir {remote_dir}: {exc}")

    files_to_copy = [
        (local_cert, f"{remote_dir}/{local_cert.name}"),
        (local_key,  f"{remote_dir}/{local_key.name}"),
        (local_ca,   f"{remote_dir}/{local_ca.name}"),
    ]
    filenames = ", ".join(lf.name for lf, _ in files_to_copy)
    print(f"[{name}] SCP certs -> {remote_dir}: {filenames}")
    with SCP(dev, progress=progress) as scp:
        for local_file, remote_file in files_to_copy:
            if DEBUG:
                print(f"[{name}] SCP {local_file} -> {remote_file}")
            scp.put(str(local_file), remote_path=remote_file)

    verify_cmd = (
        f"chown {runtime_user}:wheel {remote_dir}/{local_cert.name} {remote_dir}/{local_key.name} {remote_dir}/{local_ca.name}; "
        f"chmod 644 {remote_dir}/{local_cert.name}; "
        f"chmod 640 {remote_dir}/{local_key.name}; "
        f"chmod 644 {remote_dir}/{local_ca.name}; "
        f"test -s {remote_dir}/{local_cert.name} && echo OK:{remote_dir}/{local_cert.name}; "
        f"test -s {remote_dir}/{local_key.name} && echo OK:{remote_dir}/{local_key.name}; "
        f"test -s {remote_dir}/{local_ca.name} && echo OK:{remote_dir}/{local_ca.name}; "
        f"ls -l {remote_dir}"
    )

    rsp = dev.rpc.request_shell_execute(command=verify_cmd)
    output = rpc_text(rsp)

    if output:
        dbg_block(f"{name} REMOTE CERTS", output)

    required_markers = [
        f"OK:{remote_dir}/{local_cert.name}",
        f"OK:{remote_dir}/{local_key.name}",
        f"OK:{remote_dir}/{local_ca.name}",
    ]
    missing_remote = [marker for marker in required_markers if marker not in output]
    if missing_remote:
        raise RuntimeError(
            f"[{name}] Remote cert verification failed\n"
            f"expected markers={missing_remote}\n"
            f"output={output}"
        )

    sync_certs_dual_re(dev, name, remote_dir, [local_cert.name, local_key.name, local_ca.name])

    print(
        f"[{name}] Certs copied OK {local_cert.name}, {local_key.name}, {local_ca.name} "
        f"owner={runtime_user} key_mode=640 peer_cmd_user={peer_cmd_user}"
    )


# --------------------------
# rollback candidate
# --------------------------


def rollback_candidate(dev, name):
    cu = Config(dev)
    try:
        cu.rollback(rb_id=0)
        if DEBUG:
            print(f"[{name}] Candidate config cleared (rollback 0)")
    except Exception as exc:
        print(f"[{name}] WARN candidate config clear failed: {exc}")


# ----------------------------------------
# QKD SCRIPT CONFIG
# ----------------------------------------


def render_qkd_script_config(base):
    script_name = QKD.get("SCRIPT_NAME", "qkd_onbox.py")
    secrets = base.get("secrets", {})
    script_user = secrets.get("script_user") or secrets.get("default_user") or "admin"
    peer_cmd_user = secrets.get("peer_cmd_user") or QKD.get("PEER_CMD_USER", "etsi_peer_view")
    peer_cmd_class = secrets.get("peer_cmd_class") or QKD.get("PEER_CMD_CLASS", "read-only")
    runtime_policy = load_runtime_qkd_policy()
    qkd_policy = runtime_policy.get("qkd_policy", {}) if isinstance(runtime_policy, dict) else {}
    rotation_interval_seconds = int(qkd_policy.get("interval_seconds", 60))

    context = {
        "script_name": script_name,
        "script_user": script_user,
        "script_user_class": "super-user",
        "peer_cmd_user": peer_cmd_user,
        "peer_cmd_class": peer_cmd_class,
        "rotation_interval_seconds": rotation_interval_seconds,
    }

    peer_ssh_hardening_cfg = render_common_template("peer_cmd_ssh_hardening.j2", context)
    users_cfg = render_common_template("runtime_users.j2", context)
    event_cfg = render_common_template("event.j2", context)
    op_cfg = render_common_template("op_script.j2", context)
    return peer_ssh_hardening_cfg + "\n" + users_cfg + "\n" + event_cfg + "\n" + op_cfg


def configure_qkd_scripts(dev, name, base, device_dict=None, all_devices_list=None):
    script_name = QKD.get("SCRIPT_NAME", "qkd_onbox.py")
    secrets = base.get("secrets", {})
    script_user = secrets.get("script_user") or secrets.get("default_user") or "admin"

    rollback_candidate(dev, name)

    print(f"[{name}] Rendering event/op templates")
    print(f"[{name}] Using script_user={script_user}")
    full_cfg = render_qkd_script_config(base)

    print(f"[{name}] Applying QKD script config")

    # Only dual-RE devices need script sync before commit synchronize.
    if has_dual_re(dev, name):
        sync_qkd_scripts_dual_re(dev, name, script_name)

    max_attempts, base_wait_seconds = lock_retry_parameters()

    for attempt in range(1, max_attempts + 1):
        try:
            with Config(dev) as cu:
                cu.load(full_cfg, format="set", merge=False)
                commit_safely(dev, cu, name, sync=True)
            break
        except Exception as exc:
            if not is_config_db_lock_error(exc) or attempt == max_attempts:
                raise
            wait_seconds = base_wait_seconds * attempt
            print(
                f"[{name}] WARN QKD script config lock attempt={attempt}/{max_attempts} "
                f"wait={wait_seconds}s error={exc}"
            )
            time.sleep(wait_seconds)

    print(f"[{name}] QKD scripts event and op configured OK")
    
    # Apply peer SSH authorized-keys as second configuration step
    if device_dict and all_devices_list:
        try:
            apply_peer_ssh_authorized_keys_config(dev, name, device_dict, all_devices_list, base)
        except Exception as exc:
            print(f"[{name}] WARN failed to apply peer SSH authorized-keys config: {exc}")


def apply_peer_ssh_authorized_keys_config(dev, device_name, device_dict, all_devices_dict, base):
    """
    Apply peer SSH authorized-keys via Junos configuration (not filesystem manipulation).
    This runs as part of configure_qkd_scripts() so we reuse the already-open NETCONF connection.
    
    all_devices_dict: dict of name -> device
    """
    from lib.qkd.identity import collect_script_user_public_keys, qkd_peer_cmd_user
    
    secrets = base.get("secrets", {})
    peer_cmd_user = secrets.get("peer_cmd_user") or QKD.get("PEER_CMD_USER", "etsi_peer_view")
    
    # Convert devices dict to list for collect_script_user_public_keys
    all_devices_list = [all_devices_dict[name] for name in sorted(all_devices_dict.keys())]
    
    try:
        # Collect peer SSH public keys from all devices
        pub_keys = collect_script_user_public_keys(all_devices_list)
    except Exception as exc:
        print(f"[{device_name}] WARN failed to collect peer SSH keys: {exc}")
        return
    
    # Determine which peer keys this device needs
    device_names = set(all_devices_dict.keys())
    source_names = set([device_name])  # Always include self
    
    for link in device_dict.get("links", []) or []:
        peer_name = link.get("peer")
        if peer_name and peer_name in device_names:
            source_names.add(peer_name)
    
    if not source_names or len(source_names) == 1:
        print(f"[{device_name}] No peer sources for SSH authorized-keys config")
        return
    
    # Build config commands for each peer key
    config_lines = []
    for source_name in sorted(source_names):
        pub_key = pub_keys.get(source_name)
        if not pub_key:
            print(f"[{device_name}] WARN missing peer SSH key from {source_name}")
            continue
        # pub_key is full line: "ssh-ed25519 AAA... comment"
        # Junos expects the full format as the authentication value: "ssh-ed25519 <base64> <comment>"
        # Escape quotes for set command
        escaped_key = pub_key.replace('"', '\\"')
        config_lines.append(f"set system login user {peer_cmd_user} authentication \"{escaped_key}\"")
    
    if not config_lines:
        print(f"[{device_name}] No valid peer SSH keys to configure")
        return
    
    # Apply via another Config session
    max_attempts, base_wait_seconds = lock_retry_parameters()
    for attempt in range(1, max_attempts + 1):
        try:
            with Config(dev) as cu:
                for cmd in config_lines:
                    cu.load(cmd, format="set")
                if cu.diff():
                    print(f"[{device_name}] Applying peer SSH authorized-keys config")
                    commit_safely(dev, cu, device_name, sync=True)
                    print(f"[{device_name}] Peer SSH authorized-keys configured OK")
                else:
                    print(f"[{device_name}] Peer SSH authorized-keys unchanged")
            break
        except Exception as exc:
            if not is_config_db_lock_error(exc) or attempt == max_attempts:
                raise
            wait_seconds = base_wait_seconds * attempt
            print(
                f"[{device_name}] WARN peer SSH config lock attempt={attempt}/{max_attempts} "
                f"wait={wait_seconds}s"
            )
            time.sleep(wait_seconds)


# ----------------------------------------
# PUSH CONFIG
# ----------------------------------------


def push_config(device_name, device, commands, base, devices_dict=None):
    secrets = base.get("secrets", {}) if isinstance(base, dict) else {}
    if not isinstance(secrets, dict):
        secrets = {}

    auth_candidates = []

    # Prefer per-device auth first (during deploy this is set to script_user),
    # then fall back to bootstrap/deploy credentials.
    # This avoids SCP write failures when cert files/dirs are no longer writable
    # by bootstrap users (for example after ownership hardening).
    device_auth = device.get("auth", {}) if isinstance(device.get("auth"), dict) else {}
    device_user = device_auth.get("username")
    device_password = device_auth.get("password")
    if device_user and device_password:
        auth_candidates.append((str(device_user), str(device_password), "device.auth"))

    bootstrap_user = secrets.get("bootstrap_user") or secrets.get("deploy_user")
    bootstrap_password = (
        secrets.get("bootstrap_password")
        or secrets.get("deploy_password")
        or secrets.get("root_password")
    )
    if bootstrap_user and bootstrap_password:
        auth_candidates.append((str(bootstrap_user), str(bootstrap_password), "inventory_base.bootstrap/deploy"))

    script_user = secrets.get("script_user") or secrets.get("default_user")
    script_password = (
        secrets.get("script_password")
        or secrets.get("admin_password")
        or secrets.get("default_password")
    )
    if script_user and script_password:
        auth_candidates.append((str(script_user), str(script_password), "inventory_base.script/default"))

    deduped_candidates = []
    seen = set()
    for user, password, source in auth_candidates:
        key = (user, password)
        if key in seen:
            continue
        seen.add(key)
        deduped_candidates.append((user, password, source))

    if not deduped_candidates:
        raise RuntimeError(f"[{device_name}] No NETCONF credentials available for provisioning")

    dev = None
    selected_user = None
    selected_source = None
    last_exc = None

    selected_port = None

    def open_device_connection(user, password, source, preferred_ports=None):
        ports = []
        for port in (preferred_ports or []):
            if port and port not in ports:
                ports.append(port)
        for port in (830, 22):
            if port not in ports:
                ports.append(port)

        last_error = None
        for port in ports:
            candidate_dev = Device(
                host=device["ip"],
                user=user,
                passwd=password,
                port=port,
            )
            try:
                candidate_dev.open()
                return candidate_dev, port
            except Exception as exc:
                last_error = exc
                try:
                    candidate_dev.close()
                except Exception:
                    pass
        raise RuntimeError(
            f"[{device_name}] Unable to open NETCONF session user={user} source={source} last_error={last_error}"
        )

    for user, password, source in deduped_candidates:
        try:
            candidate_dev, port = open_device_connection(user, password, source)
            dev = candidate_dev
            selected_user = user
            selected_source = source
            selected_port = port
            break
        except Exception as exc:
            last_exc = exc
        if dev is not None:
            break

    if dev is None:
        raise RuntimeError(
            f"[{device_name}] NETCONF authentication failed for all credential sources "
            f"({', '.join(source for _, _, source in deduped_candidates)})\n"
            f"last_error={last_exc}"
        )

    try:
        print(
            f"[{device_name}] NETCONF auth selected: "
            f"user={selected_user} source={selected_source} port={selected_port}"
        )

        try:
            dev.rpc.cli("file make-directory /var/db/scripts/certs")
        except Exception:
            pass

        try:
            push_certs(dev, device_name, device, base)
        except Exception as exc:
            error_text = str(exc)
            can_retry_with_bootstrap = (
                selected_source == "device.auth"
                and bootstrap_user
                and bootstrap_password
                and selected_user != str(bootstrap_user)
                and "Permission denied" in error_text
            )
            if not can_retry_with_bootstrap:
                raise

            print(
                f"[{device_name}] Cert copy via {selected_user} failed with permission denied; "
                f"retrying as {bootstrap_user}"
            )
            retry_dev = None
            try:
                retry_dev, retry_port = open_device_connection(
                    str(bootstrap_user),
                    str(bootstrap_password),
                    "inventory_base.bootstrap/deploy",
                    preferred_ports=[selected_port],
                )
                print(
                    f"[{device_name}] NETCONF retry for cert copy: "
                    f"user={bootstrap_user} source=inventory_base.bootstrap/deploy port={retry_port}"
                )
                push_certs(retry_dev, device_name, device, base)
            finally:
                if retry_dev is not None:
                    try:
                        retry_dev.close()
                    except Exception:
                        pass

        configure_qkd_scripts(dev, device_name, base, device_dict=device, all_devices_list=devices_dict)

        # Do not rollback again here; configure_qkd_scripts() already starts from a clean candidate.
        max_attempts, base_wait_seconds = lock_retry_parameters()

        with Config(dev) as cu:
            for cmd in commands:
                cmd = cmd.strip()
                if not cmd or cmd.startswith("#"):
                    continue
                for attempt in range(1, max_attempts + 1):
                    try:
                        cu.load(cmd, format="set")
                        break
                    except Exception as exc:
                        if not is_config_db_lock_error(exc) or attempt == max_attempts:
                            raise
                        wait_seconds = base_wait_seconds * attempt
                        print(
                            f"[{device_name}] WARN config load lock attempt={attempt}/{max_attempts} "
                            f"wait={wait_seconds}s"
                        )
                        time.sleep(wait_seconds)

            if cu.diff():
                print(f"[{device_name}] Applying config")
                commit_safely(dev, cu, device_name, sync=True)
                print(f"[{device_name}] Commit OK")
            else:
                print(f"[{device_name}] No changes")

    finally:
        dev.close()

    time.sleep(10)


# ----------------------------------------
# TOPOLOGY / LINK HELPERS
# ----------------------------------------


def resolve_peers(devices, topology):
    peer_map = {}

    for a, b in topology.get("pairs", []) or []:
        peer_map.setdefault(a, []).append(b)
        peer_map.setdefault(b, []).append(a)

    for link in topology.get("links", []) or []:
        a = link.get("node_a")
        b = link.get("node_b")
        if a and b:
            peer_map.setdefault(a, []).append(b)
            peer_map.setdefault(b, []).append(a)

    return peer_map


def device_has_runtime_links(device):
    links = device.get("links", []) or []
    return bool(links)


def should_skip_device(name, device):
    if device.get("managed") is False:
        print(f"[{name}] unmanaged device -> skipping")
        return True

    if not device_has_runtime_links(device):
        print(f"[{name}] No runtime links -> skipping")
        return True

    return False


# ----------------------------------------
# MAIN ENGINE
# ----------------------------------------


def run_provisioning(log=None, dry_run=False, preview=False, ssh_key=None, debug=False, devices=None):
    global DEBUG
    DEBUG = debug

    base, loaded_devices, topology = load_inventory()

    # If devices are pre-filtered (from --devices flag), use those; otherwise use all loaded devices
    if devices is not None:
        devices_to_process = devices
    else:
        devices_to_process = loaded_devices

    # Count devices to process for progress tracking
    deployable_count = sum(1 for n, d in devices_to_process.items() if not should_skip_device(n, d))
    device_idx = 0

    for name, device in devices_to_process.items():
        if should_skip_device(name, device):
            continue

        device_idx += 1
        platform_cfg = load_platform(device["platform"])
        macsec = device.get("macsec", {})

        if "cak" in macsec and "ckn" in macsec:
            print(f"  [{device_idx}/{deployable_count}] {name}: Configuring STATIC MACsec")
            print(f"[{name}] STATIC MACsec detected")
            commands = build_macsec_static(device, platform_cfg)
        else:
            print(f"  [{device_idx}/{deployable_count}] {name}: Building QKD configuration...")
            commands = build_device_config(
                device_name=name,
                device=device,
                platform=platform_cfg,
                base=base,
                topology=topology,
            )

        if preview:
            print(f"\n=== {name} ===")
            print(render_qkd_script_config(base))
            print("\n".join(commands))
            continue

        if dry_run:
            print(f"[{name}] dry-run -> skipping push")
            continue

        push_config(name, device, commands, base, devices_dict=devices_to_process)
        print(f"  [{device_idx}/{deployable_count}] ✓ {name} configuration complete")
