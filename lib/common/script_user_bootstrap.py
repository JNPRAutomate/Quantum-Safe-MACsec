#!/usr/bin/env python3
"""
SCRIPT_USER bootstrap helper for the QKD/MACsec orchestrator.

Purpose
-------
Create or update the configured QKD SCRIPT_USER on managed Junos devices before
normal deploy/validation.

Important behavior
------------------
- Deploy must NOT prompt for root/deploy-user password.
- Root/deploy-user password is only requested when explicitly running this module
  with --ask-deploy-password, or when a caller explicitly passes
  prompt_for_deploy_password=True.
- If no deploy/root password is available and prompting is disabled, bootstrap is
  safely skipped. This allows normal qkd_orchestrator.py deploy to assume the
  SCRIPT_USER already exists and continue using admin credentials from runtime.

Credential model
----------------
SCRIPT_USER password:
  1. --script-password / function override
  2. secrets.script_password
  3. secrets.admin_password
  4. secrets.default_password

Deploy user:
  1. --deploy-user / function override
  2. secrets.deploy_user
  3. QKD["DEPLOY_USER"]
  4. root

Deploy/root password:
  1. --deploy-password / function override
  2. secrets.deploy_password
  3. secrets.root_password
  4. prompt ONLY if explicitly requested

Python compatibility
--------------------
Written for Python 3.8/3.9 compatibility. No modern union type syntax.
"""

from __future__ import annotations

import argparse
import getpass
import subprocess
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from jnpr.junos import Device
from jnpr.junos.utils.config import Config

try:
    import crypt  # type: ignore
except Exception:  # pragma: no cover
    crypt = None

try:
    from lib.common.settings import CONFIG, QKD
except Exception:  # pragma: no cover
    CONFIG = {
        "inventory_dir": "config/inventory",
        "runtime_dir": "config/runtime",
    }
    QKD = {
        "SCRIPT_USER": "macsec_user",
        "DEPLOY_USER": "root",
    }


# Expected location:
#   <repo>/lib/common/script_user_bootstrap.py
# parents[0] = <repo>/lib/common
# parents[1] = <repo>/lib
# parents[2] = <repo>
BASE_DIR = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError("Invalid YAML root in %s: expected mapping" % path)

    return data


def _resolve_env_placeholder(value: Any, path: str) -> Any:
    if not isinstance(value, str):
        return value

    token = value.strip()
    prefix = "${ENV:"
    suffix = "}"

    if not (token.startswith(prefix) and token.endswith(suffix)):
        return value

    env_name = token[len(prefix):-len(suffix)].strip()
    if not env_name:
        raise ValueError("Invalid empty ENV placeholder at %s" % path)

    resolved = os.getenv(env_name)
    if resolved is None:
        raise ValueError(
            "Missing required environment variable '%s' referenced at %s" % (env_name, path)
        )
    return resolved


def _resolve_env_placeholders(obj: Any, path: str = "root") -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            out[key] = _resolve_env_placeholders(value, "%s.%s" % (path, key))
        return out

    if isinstance(obj, list):
        out = []
        for idx, item in enumerate(obj):
            out.append(_resolve_env_placeholders(item, "%s[%d]" % (path, idx)))
        return out

    return _resolve_env_placeholder(obj, path)


def _inventory_base_path(repo_root: Path) -> Path:
    return repo_root / CONFIG.get("inventory_dir", "config/inventory") / "inventory_base.yaml"


def _runtime_devices_path(repo_root: Path) -> Path:
    return repo_root / CONFIG.get("runtime_dir", "config/runtime") / "devices.yaml"


def load_inventory_base(repo_root: Path) -> Dict[str, Any]:
    path = _inventory_base_path(repo_root)
    if not path.exists():
        return {}
    return _resolve_env_placeholders(_load_yaml(path), path="inventory_base")


def load_runtime_devices(repo_root: Path) -> Dict[str, Dict[str, Any]]:
    path = _runtime_devices_path(repo_root)
    data = _load_yaml(path)
    devices = data.get("devices", {})
    if not isinstance(devices, dict):
        raise ValueError("Invalid runtime devices file: expected top-level devices map")
    return devices


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def _secrets_block(inventory_base: Dict[str, Any]) -> Dict[str, Any]:
    secrets = inventory_base.get("secrets", {})
    return secrets if isinstance(secrets, dict) else {}


def get_script_user(inventory_base: Dict[str, Any], override: Optional[str] = None) -> str:
    if override:
        return override

    secrets = _secrets_block(inventory_base)
    return str(
        secrets.get("script_user")
        or QKD.get("SCRIPT_USER")
        or "admin"
    )


def get_script_password(inventory_base: Dict[str, Any], override: Optional[str] = None) -> str:
    if override is not None:
        return override

    secrets = _secrets_block(inventory_base)
    value = (
        secrets.get("script_password")
        or secrets.get("admin_password")
        or secrets.get("default_password")
    )

    if not value:
        raise ValueError(
            "Cannot determine SCRIPT_USER password. Expected one of "
            "secrets.script_password, secrets.admin_password, or secrets.default_password "
            "in inventory_base.yaml, or pass --script-password."
        )

    return str(value)


def get_deploy_user(inventory_base: Dict[str, Any], override: Optional[str] = None) -> str:
    if override:
        return override

    secrets = _secrets_block(inventory_base)
    return str(
        secrets.get("bootstrap_user")
        or secrets.get("deploy_user")
        or QKD.get("DEPLOY_USER")
        or "root"
    )


def get_deploy_password_from_config(
    inventory_base: Dict[str, Any],
    override: Optional[str] = None,
) -> Optional[str]:
    if override is not None:
        return override

    secrets = _secrets_block(inventory_base)
    value = (
        secrets.get("bootstrap_password")
        or secrets.get("deploy_password")
        or secrets.get("root_password")
    )

    return str(value) if value else None


def prompt_deploy_password_once(deploy_user: str) -> str:
    return getpass.getpass("Password for deploy user %s: " % deploy_user)


def get_peer_cmd_user(inventory_base: Dict[str, Any], override: Optional[str] = None) -> str:
    if override:
        return override

    secrets = _secrets_block(inventory_base)
    return str(secrets.get("peer_cmd_user") or QKD.get("PEER_CMD_USER") or "etsi_peer_view")


def get_peer_cmd_password(inventory_base: Dict[str, Any], override: Optional[str] = None) -> Optional[str]:
    if override is not None:
        return override

    secrets = _secrets_block(inventory_base)
    value = (
        secrets.get("peer_cmd_password")
        or None
    )
    return str(value) if value else None


def get_peer_cmd_class(inventory_base: Dict[str, Any], override: Optional[str] = None) -> str:
    if override:
        return override

    secrets = _secrets_block(inventory_base)
    return str(secrets.get("peer_cmd_class") or QKD.get("PEER_CMD_CLASS") or "read-only")


# ---------------------------------------------------------------------------
# Password encryption
# ---------------------------------------------------------------------------


def encrypted_junos_password(plain_password: str) -> str:
    """
    Generate a SHA-512 crypt password suitable for Junos encrypted-password.

    Only used when SCRIPT_USER does not exist yet.
    """
    if crypt is not None:
        try:
            salt = crypt.mksalt(crypt.METHOD_SHA512)
            encrypted = crypt.crypt(plain_password, salt)
            if encrypted:
                return encrypted
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", plain_password],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    raise RuntimeError("Unable to generate encrypted password. Install Python crypt support or openssl.")


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------


def managed_script_user_devices(devices: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}

    for name, device in devices.items():
        if not isinstance(device, dict):
            continue
        if device.get("managed") is False:
            continue
        selected[name] = device

    return selected


def filter_devices(
    devices: Dict[str, Dict[str, Any]],
    only: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    selected = managed_script_user_devices(devices)
    if not only:
        return selected

    keep = set(only)
    return {name: device for name, device in selected.items() if name in keep}


# ---------------------------------------------------------------------------
# Junos helpers
# ---------------------------------------------------------------------------


def _rpc_text(rpc_result: Any) -> str:
    if rpc_result is None:
        return ""
    text = getattr(rpc_result, "text", None)
    if text:
        return str(text)
    if hasattr(rpc_result, "itertext"):
        return "".join(rpc_result.itertext())
    return str(rpc_result)


def user_exists(dev: Device, username: str) -> bool:
    try:
        result = dev.rpc.cli(
            "show configuration system login user %s" % username,
            format="text",
        )
        text = _rpc_text(result).strip()
        if not text:
            return False
        if "user %s" % username in text:
            return True
        if "class" in text or "authentication" in text or "encrypted-password" in text:
            return True
        return False
    except Exception:
        return False


def class_exists(dev: Device, class_name: str) -> bool:
    try:
        result = dev.rpc.cli(
            "show configuration system login class %s" % class_name,
            format="text",
        )
        text = _rpc_text(result).strip()
        if not text:
            return False
        return "class %s" % class_name in text
    except Exception:
        return False


def build_set_commands(
    username: str,
    class_name: str,
    encrypted_password: Optional[str],
    user_exists: bool,
    lock_password_when_missing: bool = False,
) -> List[str]:
    commands = [
        "set system login user %s class %s" % (username, class_name),
    ]

    if not user_exists:
        if encrypted_password:
            commands.append(
                "set system login user %s authentication encrypted-password \"%s\""
                % (username, encrypted_password)
            )
        elif lock_password_when_missing:
            # Explicitly lock password-based login for key-only users.
            commands.append(
                "set system login user %s authentication encrypted-password \"*\""
                % username
            )
        else:
            raise ValueError("encrypted_password is required when creating a new user")

    return commands


def build_peer_cmd_class_commands(class_name: str, script_name: str) -> List[str]:
    # Built-in Junos classes are always available and should not be redefined.
    builtin_classes = {"super-user", "operator", "read-only", "unauthorized"}
    if class_name in builtin_classes:
        return []

    return [
        "set system login class %s permissions view" % class_name,
        "set system login class %s allow-commands \"^(quit|exit|logout)$\"" % class_name,
        "set system login class %s deny-configuration \".*\"" % class_name,
    ]


def build_ssh_fix_command(script_user: str) -> str:
    home = "/var/home/%s" % script_user
    ssh_dir = "%s/.ssh" % home
    authorized_keys = "%s/authorized_keys" % ssh_dir

    return (
        "mkdir -p {ssh_dir}; "
        "touch {authorized_keys}; "
        "chown {user} {ssh_dir} {authorized_keys}; "
        "chmod 700 {ssh_dir}; "
        "chmod 600 {authorized_keys}; "
        "ls -ld {ssh_dir}; "
        "ls -l {authorized_keys}"
    ).format(
        user=script_user,
        ssh_dir=ssh_dir,
        authorized_keys=authorized_keys,
    )


def run_shell_fix(dev: Device, name: str, script_user: str, deploy_user: str) -> bool:
    # Disabled by default: platform shell behavior and privilege model can make
    # this noisy and non-portable, while deploy/postdeploy paths install and
    # validate required peer keys independently.
    if os.getenv("QKD_ENABLE_SSH_HOME_FIX", "0").lower() not in {"1", "true", "yes"}:
        print("[%s] ssh home fix skipped for %s (set QKD_ENABLE_SSH_HOME_FIX=1 to enable)" % (name, script_user))
        return True

    if str(deploy_user).strip().lower() != "root":
        print(
            "[%s] ssh home fix skipped for %s: deploy user %s is not root"
            % (name, script_user, deploy_user)
        )
        return True

    command = build_ssh_fix_command(script_user)
    try:
        result = dev.rpc.request_shell_execute(command=command)
        text = _rpc_text(result).strip()
        if text:
            print("[%s] ssh home fix output:\n%s" % (name, text))
        else:
            print("[%s] ssh home fix completed for %s" % (name, script_user))
        return True
    except Exception as exc:
        # Non-fatal by design.
        print("[%s] WARN ssh home fix failed for %s: %s" % (name, script_user, exc))
        return True


def generate_ssh_keys_for_script_user(
    dev: Device,
    name: str,
    script_user: str,
    script_password: str,
    deploy_user: str,
    fallback_user: Optional[str] = None,
    fallback_password: Optional[str] = None,
) -> bool:
    # Generate SSH keys for script_user. Keys are needed for:
    # 1. Remote op script execution (peer SSH commands)
    # 2. Peer key rotation and synchronization
    #
    # Single consistent approach for all platforms:
    # 1. Delete old keys as deploy_user
    # 2. Create SSH dir as deploy_user
    # 3. Generate keys AS script_user (by opening a new connection with script_user creds)
    
    ssh_home = "/var/home/%s" % script_user
    ssh_dir = "%s/.ssh" % ssh_home
    key_path = "%s/qkd_id_ed25519" % ssh_dir
    pub_path = "%s.pub" % key_path
    peer_key_path = "%s/qkd_peer_cmd_ed25519" % ssh_dir
    peer_pub_path = "%s.pub" % peer_key_path
    key_comment = "qkd-orchestrator"

    def _verify_keys_present(dev_conn: Device, label: str) -> bool:
        verify_cmd = (
            "test -r {key} && "
            "test -r {pub} && "
            "test -r {pkey} && "
            "test -r {ppub} && "
            "echo __QKD_KEYS_OK__ && "
            "ls -l {key} {pub} {pkey} {ppub}"
        ).format(
            key=key_path,
            pub=pub_path,
            pkey=peer_key_path,
            ppub=peer_pub_path,
        )
        try:
            result = dev_conn.rpc.request_shell_execute(command=verify_cmd)
            text = _rpc_text(result).strip()
            if "__QKD_KEYS_OK__" in text:
                if text:
                    print("[%s] SSH key verification (%s):\n%s" % (name, label, text))
                return True
            if text:
                print("[%s] WARN SSH key verification (%s) output:\n%s" % (name, label, text))
            return False
        except Exception as exc:
            print("[%s] WARN SSH key verification failed (%s): %s" % (name, label, exc))
            return False

    def _generate_as_connection(dev_conn: Device, label: str) -> bool:
        genkey_cmd = (
            "ssh-keygen -t ed25519 -N \"\" -C \"{comment}\" -f {key}; "
            "ssh-keygen -t ed25519 -N \"\" -C \"{comment}\" -f {pkey}; "
            "chmod 600 {key} {pkey}; "
            "chmod 644 {pub} {ppub}; "
            "ls -l {key} {pub} {pkey} {ppub}"
        ).format(
            comment=key_comment,
            key=key_path,
            pub=pub_path,
            pkey=peer_key_path,
            ppub=peer_pub_path,
        )
        result = dev_conn.rpc.request_shell_execute(command=genkey_cmd)
        text = _rpc_text(result).strip()
        if text:
            print("[%s] SSH key generation (%s):\n%s" % (name, label, text))
        return _verify_keys_present(dev_conn, label)

    try:
        # Step 1: Create SSH dir as deploy_user
        mkdir_cmd = "mkdir -p %s" % ssh_dir
        dev.rpc.request_shell_execute(command=mkdir_cmd)
        
        # Step 2: Generate keys and cleanup AS script_user
        # Connect as script_user to handle keys with correct ownership
        # This is important for shipment scenario where keys may already exist from preload
        host = str(dev.hostname)
        port = dev.port
        
        dev_as_script_user = Device(
            host=host,
            user=script_user,
            passwd=script_password,
            port=port,
            gather_facts=False,
        )
        
        try:
            dev_as_script_user.open()
            
            # Delete old keys as script_user (they can delete their own keys)
            delete_cmd = (
                "rm -f {key} {pub} {pkey} {ppub}; "
                "echo 'Deleted old SSH keys'"
            ).format(key=key_path, pub=pub_path, pkey=peer_key_path, ppub=peer_pub_path)
            
            result = dev_as_script_user.rpc.request_shell_execute(command=delete_cmd)
            text = _rpc_text(result).strip()
            if text:
                print("[%s] SSH key cleanup:\n%s" % (name, text))
            
            if _generate_as_connection(dev_as_script_user, "script_user"):
                print("[%s] SSH keys generated for %s" % (name, script_user))
                return True
            print("[%s] WARN generated keys as %s but verification failed" % (name, script_user))
            return False
            
        except Exception as exc:
            # If we can't connect as script_user, fall back to deploy_user/root.
            print("[%s] INFO unable to connect as %s, attempting key generation as deploy_user" % (name, script_user))
            
            delete_cmd = (
                "rm -f {key} {pub} {pkey} {ppub}"
            ).format(key=key_path, pub=pub_path, pkey=peer_key_path, ppub=peer_pub_path)
            
            try:
                dev.rpc.request_shell_execute(command=delete_cmd)
            except Exception:
                pass  # Ignore deletion failures in fallback
            
            if _generate_as_connection(dev, "deploy_user"):
                return True

            fallback_user_norm = str(fallback_user or "").strip()
            if (
                fallback_user_norm
                and fallback_password
                and fallback_user_norm != str(deploy_user).strip()
            ):
                print("[%s] INFO retrying key generation as fallback user %s" % (name, fallback_user_norm))
                dev_as_fallback = Device(
                    host=host,
                    user=fallback_user_norm,
                    passwd=fallback_password,
                    port=port,
                    gather_facts=False,
                )
                try:
                    dev_as_fallback.open()
                    try:
                        dev_as_fallback.rpc.request_shell_execute(command=mkdir_cmd)
                    except Exception:
                        pass
                    try:
                        dev_as_fallback.rpc.request_shell_execute(command=delete_cmd)
                    except Exception:
                        pass
                    if _generate_as_connection(dev_as_fallback, "fallback_user"):
                        return True
                except Exception as f_exc:
                    print("[%s] WARN fallback key generation failed as %s: %s" % (name, fallback_user_norm, f_exc))
                finally:
                    try:
                        dev_as_fallback.close()
                    except Exception:
                        pass

            print("[%s] WARN SSH keys not generated for %s (all attempts failed)" % (name, script_user))
            return False
        finally:
            try:
                dev_as_script_user.close()
            except Exception:
                pass

    except Exception as exc:
        # Non-fatal: deployment can continue if keys already exist and are readable
        print("[%s] WARN SSH key generation failed for %s: %s" % (name, script_user, exc))
        return True




# ---------------------------------------------------------------------------
# Junos bootstrap
# ---------------------------------------------------------------------------


def bootstrap_script_user_on_device(
    name: str,
    device: Dict[str, Any],
    deploy_user: str,
    deploy_password: Optional[str],
    script_user: str,
    script_password: str,
    peer_cmd_user: str,
    peer_cmd_password: str,
    peer_cmd_class: str,
    fallback_user: Optional[str] = None,
    fallback_password: Optional[str] = None,
    port: int = 22,
    dry_run: bool = False,
) -> bool:
    host = str(device.get("ip") or device.get("mgmt_ip") or "")
    if not host:
        raise ValueError("Device %s has no ip/mgmt_ip" % name)

    print("[%s] bootstrap SCRIPT_USER %s via deploy user %s@%s" % (name, script_user, deploy_user, host))

    if dry_run:
        print("[%s] DRY-RUN check if SCRIPT_USER exists" % name)
        print("[%s] DRY-RUN create user only if missing" % name)
        print("[%s] DRY-RUN ensure class super-user" % name)
        print("[%s] DRY-RUN check if PEER_CMD_USER exists" % name)
        print("[%s] DRY-RUN create PEER_CMD_USER only if missing" % name)
        print("[%s] DRY-RUN ensure class %s for PEER_CMD_USER" % (name, peer_cmd_class))
        print("[%s] DRY-RUN fix /var/home/%s/.ssh ownership and permissions" % (name, script_user))
        print("[%s] DRY-RUN fix /var/home/%s/.ssh ownership and permissions" % (name, peer_cmd_user))
        return True

    dev = Device(
        host=host,
        user=deploy_user,
        passwd=deploy_password,
        port=port,
        gather_facts=False,
    )

    try:
        dev.open()

        # Always rollback first to clear any pending config from previous operations
        try:
            dev.rpc.request_shell_execute(command="cli -c 'rollback 0'")
        except Exception:
            pass  # Rollback may not always be available; continue anyway

        script_exists = user_exists(dev, script_user)
        peer_exists = user_exists(dev, peer_cmd_user)
        peer_class_exists = class_exists(dev, peer_cmd_class)
        script_name = str(QKD.get("SCRIPT_NAME", "qkd_onbox.py"))

        script_encrypted = None if script_exists else encrypted_junos_password(script_password)
        peer_encrypted = None if peer_exists else (
            encrypted_junos_password(peer_cmd_password) if peer_cmd_password else None
        )

        commands = []
        if not peer_class_exists:
            commands.extend(build_peer_cmd_class_commands(peer_cmd_class, script_name))
        commands.extend(
            build_set_commands(
                script_user,
                "super-user",
                script_encrypted,
                script_exists,
                lock_password_when_missing=False,
            )
        )
        commands.extend(
            build_set_commands(
                peer_cmd_user,
                peer_cmd_class,
                peer_encrypted,
                peer_exists,
                lock_password_when_missing=True,
            )
        )

        cu = Config(dev)
        # Clear any pending config in candidate before loading bootstrap config
        try:
            cu.rollback()
        except Exception:
            pass
        
        cu.load("\n".join(commands), format="set", merge=True)
        diff = cu.diff()

        if diff:
            print("[%s] candidate diff:\n%s" % (name, diff))
            cu.commit(comment="QKD bootstrap users script=%s peer=%s" % (script_user, peer_cmd_user))
            print("[%s] OK user bootstrap committed (script=%s peer=%s)" % (name, script_user, peer_cmd_user))
        else:
            print("[%s] no user config change required (script=%s peer=%s)" % (name, script_user, peer_cmd_user))
            try:
                cu.rollback()
            except Exception:
                pass

        if not run_shell_fix(dev, name, script_user, deploy_user):
            return False
        if not run_shell_fix(dev, name, peer_cmd_user, deploy_user):
            return False

        # Generate SSH keys for script_user (for peer commands and rotation)
        if not generate_ssh_keys_for_script_user(
            dev,
            name,
            script_user,
            script_password,
            deploy_user,
            fallback_user=fallback_user,
            fallback_password=fallback_password,
        ):
            return False

        return True

    except Exception as exc:
        print("[%s] FAIL user bootstrap: %s" % (name, exc))
        return False
    finally:
        try:
            dev.close()
        except Exception:
            pass


def bootstrap_script_users(
    devices: Optional[Dict[str, Dict[str, Any]]] = None,
    repo_root: Optional[Path] = None,
    only: Optional[List[str]] = None,
    deploy_user: Optional[str] = None,
    deploy_password: Optional[str] = None,
    script_user: Optional[str] = None,
    script_password: Optional[str] = None,
    peer_cmd_password: Optional[str] = None,
    dry_run: bool = False,
    prompt_for_deploy_password: bool = False,
    skip_if_no_deploy_password: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Bootstrap SCRIPT_USER on managed devices.

    Default behavior is NON-INTERACTIVE and DEPLOY-SAFE:
      - do not prompt for root password
      - if no root/deploy password is configured, skip bootstrap and return success

    This allows qkd_orchestrator.py deploy to run without asking for root password.
    To actually bootstrap fresh routers, run this module explicitly with
    --ask-deploy-password or pass prompt_for_deploy_password=True.
    """
    repo_root = repo_root or BASE_DIR
    inventory_base = load_inventory_base(repo_root)

    if devices is None:
        devices = load_runtime_devices(repo_root)

    selected = filter_devices(devices, only=only)

    resolved_script_user = get_script_user(inventory_base, script_user)
    resolved_script_password = get_script_password(inventory_base, script_password)
    resolved_peer_cmd_user = get_peer_cmd_user(inventory_base)
    resolved_peer_cmd_password = get_peer_cmd_password(inventory_base, peer_cmd_password)
    resolved_peer_cmd_class = get_peer_cmd_class(inventory_base)
    resolved_deploy_user = get_deploy_user(inventory_base, deploy_user)
    resolved_deploy_password = get_deploy_password_from_config(inventory_base, deploy_password)
    secrets = _secrets_block(inventory_base)
    fallback_user = "root"
    fallback_password = str(secrets.get("root_password")) if secrets.get("root_password") else None

    if not dry_run and resolved_deploy_password is None and prompt_for_deploy_password:
        resolved_deploy_password = prompt_deploy_password_once(resolved_deploy_user)

    if not dry_run and resolved_deploy_password is None and skip_if_no_deploy_password:
        print("=== QKD SCRIPT_USER bootstrap ===")
        print("devices      = %d" % len(selected))
        print("deploy_user  = %s" % resolved_deploy_user)
        print("script_user  = %s" % resolved_script_user)
        print("dry_run      = %s" % dry_run)
        print("deploy_pwd   = none")
        print("action       = skipped")
        print("reason       = no deploy/root password configured and prompting disabled")
        print("hint         = run lib/common/script_user_bootstrap.py --ask-deploy-password to bootstrap fresh routers")
        print("")
        return list(selected.keys()), []

    ok: List[str] = []
    failed: List[str] = []

    print("=== QKD SCRIPT_USER bootstrap ===")
    print("devices      = %d" % len(selected))
    print("deploy_user  = %s" % resolved_deploy_user)
    print("bootstrap_id = configured")
    print("script_user  = %s" % resolved_script_user)
    print("peer_cmd_user= %s" % resolved_peer_cmd_user)
    print("peer_cmd_cls = %s" % resolved_peer_cmd_class)
    print("peer_cmd_pwd = %s" % ("configured" if resolved_peer_cmd_password else "locked (key-only)"))
    print("dry_run      = %s" % dry_run)
    print("deploy_pwd   = %s" % ("configured/prompted" if resolved_deploy_password else "none"))
    print("idempotent   = true")
    print("")

    for name, device in selected.items():
        success = bootstrap_script_user_on_device(
            name=name,
            device=device,
            deploy_user=resolved_deploy_user,
            deploy_password=resolved_deploy_password,
            script_user=resolved_script_user,
            script_password=resolved_script_password,
            peer_cmd_user=resolved_peer_cmd_user,
            peer_cmd_password=resolved_peer_cmd_password,
            peer_cmd_class=resolved_peer_cmd_class,
            fallback_user=fallback_user,
            fallback_password=fallback_password,
            dry_run=dry_run,
        )
        if success:
            ok.append(name)
        else:
            failed.append(name)

    print("")
    print("=== QKD SCRIPT_USER bootstrap summary ===")
    print("OK     : %s" % (", ".join(ok) if ok else "none"))
    print("FAILED : %s" % (", ".join(failed) if failed else "none"))

    return ok, failed


# ---------------------------------------------------------------------------
# Backward-compatible aliases
# ---------------------------------------------------------------------------


def bootstrap_admin_user_on_device(*args: Any, **kwargs: Any) -> bool:
    return bootstrap_script_user_on_device(*args, **kwargs)


def bootstrap_admin_users(*args: Any, **kwargs: Any) -> Tuple[List[str], List[str]]:
    return bootstrap_script_users(*args, **kwargs)


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="script_user_bootstrap.py",
        description="Create/update the QKD SCRIPT_USER on managed Junos devices.",
    )
    parser.add_argument("--repo-root", default=str(BASE_DIR))
    parser.add_argument("--only", nargs="*", help="Optional device names to bootstrap")
    parser.add_argument("--deploy-user")
    parser.add_argument("--deploy-password")
    parser.add_argument("--ask-deploy-password", action="store_true")
    parser.add_argument("--script-user")
    parser.add_argument("--script-password")
    parser.add_argument("--ask-script-password", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script_password = args.script_password

    if args.ask_script_password:
        script_password = getpass.getpass("SCRIPT_USER password: ")

    ok, failed = bootstrap_script_users(
        repo_root=Path(args.repo_root).resolve(),
        only=args.only,
        deploy_user=args.deploy_user,
        deploy_password=args.deploy_password,
        script_user=args.script_user,
        script_password=script_password,
        dry_run=args.dry_run,
        prompt_for_deploy_password=args.ask_deploy_password,
        skip_if_no_deploy_password=not args.ask_deploy_password,
    )

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
