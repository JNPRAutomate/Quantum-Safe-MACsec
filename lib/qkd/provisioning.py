from jnpr.junos import Device
from jnpr.junos.utils.config import Config
from jnpr.junos.utils.scp import SCP
from jnpr.junos.exception import ConfigLoadError

import yaml
import time
import subprocess
import hashlib
import shlex
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
ONBOX_SCRIPT_NAME = "qkd_onbox.py"


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


def _is_config_db_lock_error(exc):
    text = str(exc).lower()
    return (
        "configuration database locked by" in text
        or "configuration database locked" in text
        or "exclusive [edit]" in text
    )


def _lock_error_hint(exc):
    text = str(exc)
    for line in text.splitlines():
        if "configuration database locked by" in line.lower():
            return line.strip()
    return "configuration database locked"


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

    return (
        ("re0" in low and "re1" in low)
        or ("routing engine 0" in low and "routing engine 1" in low)
        or ("slot 0:" in low and "slot 1:" in low)
    )


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
    Ensure qkd_onbox.py exists on both routing engines before commit synchronize.
    """
    op_script_dir = "/var/db/scripts/op"
    event_script_dir = "/var/db/scripts/event"

    op_script = f"{op_script_dir}/{script_name}"
    event_script = f"{event_script_dir}/{script_name}"

    # Ensure local RE has target script files before trying to copy them.
    run_shell(
        dev,
        (
            f"mkdir -p {op_script_dir} {event_script_dir}; "
            f"chmod 755 {event_script} {op_script} 2>/dev/null || true"
        ),
        name=name,
        strict=False,
    )

    if not has_dual_re(dev, name):
        print(f"[{name}] Single RE detected - script sync skipped")
        return

    print(f"[{name}] Dual-RE detected - syncing QKD scripts to peer RE")

    for path in (event_script, op_script):
        copy_file_to_other_re(dev, name, path)

    # Ask Junos to push scripts too. Ignore failure here; file copy above is the primary sync.
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

        Behavior:
        - Single-RE devices: normal commit only.
        - Dual-RE devices: commit synchronize.
        - If dual-RE synchronize fails due script propagation, sync scripts and retry once.
        - If synchronize still fails due remote RE commit/connectivity issues,
            fall back to local commit so deploy can progress on active RE.
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

        if sync and dual_re and (
            "event script missing" in low
            or "remote commit-configuration failed" in low
        ):
            print(f"[{name}] commit synchronize failed; syncing scripts to peer RE and retrying once")
            sync_qkd_scripts_dual_re(dev, name, ONBOX_SCRIPT_NAME)
            try:
                cu.commit(sync=True)
                return
            except Exception as retry_exc:
                retry_text = str(retry_exc)
                retry_low = retry_text.lower()
                remote_re_sync_failure = (
                    "remote commit-configuration failed" in retry_low
                    or "could not connect to re1" in retry_low
                    or "cannot connect to other re" in retry_low
                )
                if remote_re_sync_failure:
                    print(f"[{name}] WARN commit synchronize still failing on peer RE; falling back to local commit")
                    cu.commit()
                    return
                raise

        print(f"[{name}] COMMIT FAILED")
        print(text)
        raise


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


def push_certs(dev, name, device):
    remote_dir = PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")
    files = resolve_cert_paths_for_device(name, device)

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

    if DEBUG:
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

    with SCP(dev, progress=progress) as scp:
        for local_file in (local_cert, local_key, local_ca):
            remote_file = f"{remote_dir}/{local_file.name}"
            if DEBUG:
                print(f"[{name}] SCP {local_file} -> {remote_file}")
            scp.put(str(local_file), remote_path=remote_file)

    verify_cmd = (
        f"chmod 644 {remote_dir}/{local_cert.name}; "
        f"chmod 600 {remote_dir}/{local_key.name}; "
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

    print(f"[{name}] Certs copied OK {local_cert.name}, {local_key.name}, {local_ca.name}")


# --------------------------
# rollback candidate
# --------------------------


def rollback_candidate(dev, name):
    cu = Config(dev)
    try:
        cu.rollback(rb_id=0)
        print(f"[{name}] Candidate rollback 0 complete")
    except Exception as exc:
        print(f"[{name}] Candidate rollback 0 warning: {exc}")


# ----------------------------------------
# QKD SCRIPT CONFIG
# ----------------------------------------


def configure_qkd_scripts(dev, name, base):
    script_name = ONBOX_SCRIPT_NAME
    secrets = base.get("secrets", {})
    script_user = secrets.get("script_user") or secrets.get("default_user") or "admin"
    runtime_policy = load_runtime_qkd_policy()
    qkd_policy = runtime_policy.get("qkd_policy", {}) if isinstance(runtime_policy, dict) else {}
    rotation_interval_seconds = int(qkd_policy.get("interval_seconds", 60))

    rollback_candidate(dev, name)

    print(f"[{name}] Rendering event/op templates")
    print(f"[{name}] Using script_user={script_user}")

    context = {
        "script_name": script_name,
        "script_user": script_user,
        "rotation_interval_seconds": rotation_interval_seconds,
    }

    event_cfg = render_common_template("event.j2", context)
    op_cfg = render_common_template("op_script.j2", context)
    full_cfg = event_cfg + "\n" + op_cfg

    print(f"[{name}] Applying QKD script config")

    # Only dual-RE devices need script sync before commit synchronize.
    if has_dual_re(dev, name):
        sync_qkd_scripts_dual_re(dev, name, script_name)

    with Config(dev) as cu:
        cu.load(full_cfg, format="set", merge=False)
        commit_safely(dev, cu, name, sync=True)

    print(f"[{name}] QKD scripts event and op configured OK")


def apply_peer_ssh_authorized_keys_config(dev, device_name, device_dict, all_devices_dict, base):
    from lib.qkd.identity import (
        collect_script_user_public_keys,
        qkd_script_user,
        qkd_authorized_keys,
        qkd_ssh_dir,
        ssh_deploy_cmd,
    )

    secrets = base.get("secrets", {}) if isinstance(base, dict) else {}
    if not isinstance(secrets, dict):
        secrets = {}

    peer_cmd_user = secrets.get("script_user") or secrets.get("default_user") or qkd_script_user()
    all_devices_list = [all_devices_dict[name] for name in sorted(all_devices_dict.keys())]

    try:
        pub_keys = collect_script_user_public_keys(all_devices_list)
    except Exception as exc:
        print(f"[{device_name}] WARN failed to collect peer SSH keys: {exc}")
        return

    # Keep all runtime peers mutually trusted even if inventory links omit
    # explicit peer names (some topologies provide only peer_ip).
    source_names = sorted(pub_keys.keys())

    if not source_names:
        print(f"[{device_name}] No peer sources for SSH authorized-keys sync")
        return

    key_lines = []

    for source_name in source_names:
        pub_key = pub_keys.get(source_name)
        if not pub_key:
            print(f"[{device_name}] WARN missing peer SSH key from {source_name}")
            continue
        parts = pub_key.strip().split()
        if len(parts) < 2:
            print(f"[{device_name}] WARN malformed peer SSH key from {source_name}: {pub_key}")
            continue
        key_type = parts[0]
        key_blob = parts[1]
        key_comment = " ".join(parts[2:]).strip() if len(parts) > 2 else ""
        if not key_comment:
            key_comment = f"{peer_cmd_user}@{source_name}"

        # Junos expects the SSH public key in full format inside the key payload:
        #   <key-type> <base64> <comment>
        key_payload = f"{key_type} {key_blob} {key_comment}"
        # Keep only a canonical SSH public key line for file-based synchronization.
        key_lines.append(key_payload)

    if not key_lines:
        print(f"[{device_name}] No valid peer SSH keys to configure")
        return

    auth_path = qkd_authorized_keys()
    ssh_dir = qkd_ssh_dir()
    cmd_parts = [
        f"mkdir -p {ssh_dir}",
        f"touch {auth_path}",
    ]
    for key_line in key_lines:
        quoted_key = shlex.quote(key_line)
        cmd_parts.append(f"grep -q -F {quoted_key} {auth_path} || echo {quoted_key} >> {auth_path}")
    cmd_parts.extend(
        [
            f"chmod 600 {auth_path}",
            f"echo AUTHORIZED_KEYS_SYNC_OK user={peer_cmd_user} target={device_name} key_count={len(key_lines)}",
        ]
    )
    sync_cmd = "; ".join(cmd_parts)

    print(f"[{device_name}] Applying peer SSH authorized_keys sync")
    result = ssh_deploy_cmd(device_dict, sync_cmd, timeout=60, include_failed_marker=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"peer SSH authorized_keys sync failed on {device_name}\n"
            f"stdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )
    print(f"[{device_name}] Peer SSH authorized_keys synchronized OK")


# ----------------------------------------
# PUSH CONFIG
# ----------------------------------------


def push_config(device_name, device, commands, base, devices_dict=None):
    def _bootstrap_key_name(keychain_name, key_index):
        seed = f"{keychain_name}:bootstrap:key-name:{key_index}"
        return hashlib.sha256(seed.encode()).hexdigest()

    def _bootstrap_secret(keychain_name, key_index):
        seed = f"{keychain_name}:bootstrap:secret:{key_index}"
        return hashlib.sha256(seed.encode()).hexdigest()

    def _bootstrap_start_time():
        return "2026-01-01.00:01"

    def _ensure_keychain_prereqs(cmds):
        refs = []
        existing = set()

        for line in cmds:
            s = (line or "").strip()
            if not s or s.startswith("#"):
                continue
            existing.add(s)
            marker = " pre-shared-key-chain "
            if s.startswith("set security macsec connectivity-association ") and marker in s:
                kc = s.split(marker, 1)[1].strip()
                if kc and kc not in refs:
                    refs.append(kc)

        extras = []
        for keychain_name in refs:
            required = [
                f"set security authentication-key-chains key-chain {keychain_name}",
                f"set security authentication-key-chains key-chain {keychain_name} key 1 key-name {_bootstrap_key_name(keychain_name, 1)}",
                f"set security authentication-key-chains key-chain {keychain_name} key 1 secret \"{_bootstrap_secret(keychain_name, 1)}\"",
                f"set security authentication-key-chains key-chain {keychain_name} key 1 start-time {_bootstrap_start_time()}",
                f"delete security authentication-key-chains key-chain {keychain_name} key 0",
                f"delete security authentication-key-chains key-chain {keychain_name} key 2",
                f"delete security authentication-key-chains key-chain {keychain_name} key 3",
                f"delete security authentication-key-chains key-chain {keychain_name} key 4",
                f"delete security authentication-key-chains key-chain {keychain_name} key 5",
            ]
            for req in required:
                if req not in existing:
                    extras.append(req)
                    existing.add(req)

        return cmds + extras

    commands = _ensure_keychain_prereqs(commands)

    dev = Device(
        host=device["ip"],
        user=device["auth"]["username"],
        passwd=device["auth"]["password"],
        port=830,
    )

    try:
        dev.open()

        try:
            dev.rpc.cli("file make-directory /var/db/scripts/certs")
        except Exception:
            pass

        push_certs(dev, device_name, device)
        configure_qkd_scripts(dev, device_name, base)
        if devices_dict:
            try:
                apply_peer_ssh_authorized_keys_config(dev, device_name, device, devices_dict, base)
            except Exception as exc:
                print(f"[{device_name}] WARN failed to apply peer SSH authorized-keys config: {exc}")

        # Do not rollback again here; configure_qkd_scripts() already starts from a clean candidate.
        max_lock_retries = 6
        lock_retry_delay_s = 5
        for attempt in range(1, max_lock_retries + 2):
            try:
                with Config(dev) as cu:
                    for cmd in commands:
                        cmd = cmd.strip()
                        if not cmd or cmd.startswith("#"):
                            continue
                        cu.load(
                            cmd,
                            format="set",
                            ignore_warning=["statement not found"],
                        )

                    if cu.diff():
                        print(f"[{device_name}] Applying config")
                        commit_safely(dev, cu, device_name, sync=True)
                        print(f"[{device_name}] Commit OK")
                    else:
                        print(f"[{device_name}] No changes")
                break
            except ConfigLoadError as exc:
                if _is_config_db_lock_error(exc) and attempt <= max_lock_retries:
                    print(
                        f"[{device_name}] WARN candidate config DB lock detected ({_lock_error_hint(exc)}). "
                        f"Retry {attempt}/{max_lock_retries} in {lock_retry_delay_s}s..."
                    )
                    time.sleep(lock_retry_delay_s)
                    continue
                raise
            except Exception as exc:
                if _is_config_db_lock_error(exc) and attempt <= max_lock_retries:
                    print(
                        f"[{device_name}] WARN config DB lock during apply ({_lock_error_hint(exc)}). "
                        f"Retry {attempt}/{max_lock_retries} in {lock_retry_delay_s}s..."
                    )
                    time.sleep(lock_retry_delay_s)
                    continue
                raise

    finally:
        dev.close()

    time.sleep(2)


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


def run_provisioning(log, dry_run=False, preview=False, ssh_key=None, debug=False, verbose=0, devices=None):
    global DEBUG
    DEBUG = bool(debug) or int(verbose or 0) > 0

    base, runtime_devices, topology = load_inventory()
    if devices is None:
        devices = runtime_devices

    for name, device in devices.items():
        if should_skip_device(name, device):
            continue

        platform_cfg = load_platform(device["platform"])
        macsec = device.get("macsec", {})

        if "cak" in macsec and "ckn" in macsec:
            print(f"[{name}] STATIC MACsec detected")
            commands = build_macsec_static(device, platform_cfg)
        else:
            commands = build_device_config(
                device_name=name,
                device=device,
                platform=platform_cfg,
                base=base,
                topology=topology,
            )

        if preview:
            print(f"\n=== {name} ===")
            print("\n".join(commands))
            continue

        if dry_run:
            print(f"[{name}] dry-run -> skipping push")
            continue

        push_config(name, device, commands, base, devices)
