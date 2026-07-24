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
import os
import subprocess
import shlex
import re
import shutil
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
        "SCRIPT_USER": "etsi_user",
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


def _inventory_base_path(repo_root: Path) -> Path:
    return repo_root / CONFIG.get("inventory_dir", "config/inventory") / "inventory_base.yaml"


def _runtime_devices_path(repo_root: Path) -> Path:
    return repo_root / CONFIG.get("runtime_dir", "config/runtime") / "devices.yaml"


def load_inventory_base(repo_root: Path) -> Dict[str, Any]:
    path = _inventory_base_path(repo_root)
    if not path.exists():
        return {}
    return _load_yaml(path)


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
        os.getenv("QKD_SCRIPT_USER")
        or secrets.get("script_user")
        or QKD.get("SCRIPT_USER")
        or "etsi_user"
    )


def get_script_password(inventory_base: Dict[str, Any], override: Optional[str] = None) -> str:
    if override is not None:
        return override

    secrets = _secrets_block(inventory_base)
    value = (
        os.getenv("QKD_SCRIPT_PASSWORD")
        or secrets.get("script_password")
        or secrets.get("admin_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
    )

    if not value:
        raise ValueError(
            "Cannot determine SCRIPT_USER password. Expected one of "
            "QKD_SCRIPT_PASSWORD, secrets.script_password, secrets.admin_password, "
            "QKD_DEFAULT_PASSWORD, or secrets.default_password "
            "in inventory_base.yaml, or pass --script-password."
        )

    return str(value)


def get_script_user_auth_mode(
    inventory_base: Dict[str, Any],
    override: Optional[str] = None,
) -> str:
    value = (
        override
        or os.getenv("QKD_SCRIPT_USER_AUTH_MODE")
        or _secrets_block(inventory_base).get("script_user_auth_mode")
        or "password"
    )
    mode = str(value).strip().lower()
    aliases = {
        "key": "key-only",
        "pubkey": "key-only",
        "public-key": "key-only",
        "key-only": "key-only",
        "password": "password",
    }
    mode = aliases.get(mode, mode)
    if mode not in ("password", "key-only"):
        raise ValueError("Unsupported script_user auth mode: %s" % mode)
    return mode


def ensure_local_script_user_keypair(script_user: str) -> Tuple[str, str]:
    key_dir = Path.home() / ".qkd" / "script_user_keys" / script_user
    key_dir.mkdir(parents=True, exist_ok=True)
    private_key = key_dir / "qkd_id_ed25519"
    public_key = key_dir / "qkd_id_ed25519.pub"

    if not private_key.exists() or not public_key.exists():
        cmd = [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "%s@qkd-bootstrap" % script_user,
            "-f",
            str(private_key),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to generate local keypair for SCRIPT_USER %s\nstdout=%s\nstderr=%s"
                % (script_user, result.stdout, result.stderr)
            )

    try:
        private_key.chmod(0o600)
    except Exception:
        pass
    try:
        public_key.chmod(0o644)
    except Exception:
        pass

    line = public_key.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if not line.startswith("ssh-") and not line.startswith("ecdsa-"):
        raise RuntimeError("Invalid public key generated at %s" % public_key)

    return str(private_key), line


def mirror_local_script_user_keypair_to_ssh(
    script_user: str,
    source_private_key: str,
) -> str:
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)

    src_priv = Path(source_private_key)
    src_pub = Path(source_private_key + ".pub")
    dst_priv = ssh_dir / ("qkd_%s_id_ed25519" % script_user)
    dst_pub = ssh_dir / ("qkd_%s_id_ed25519.pub" % script_user)

    shutil.copy2(str(src_priv), str(dst_priv))
    if src_pub.exists():
        shutil.copy2(str(src_pub), str(dst_pub))

    try:
        dst_priv.chmod(0o600)
    except Exception:
        pass
    try:
        if dst_pub.exists():
            dst_pub.chmod(0o644)
    except Exception:
        pass

    return str(dst_priv)


def write_local_ssh_alias_config(
    devices: Dict[str, Dict[str, Any]],
    script_user: str,
    identity_file: str,
) -> Path:
    """
    Create/update a local SSH include file for quick access to managed devices.

    Generated aliases allow commands like:
      ssh mx1
      ssh acx3
    """
    ssh_dir = Path.home() / ".ssh"
    config_d = ssh_dir / "config.d"
    main_config = ssh_dir / "config"
    include_file = config_d / "qkd_managed_devices.conf"
    inline_begin = "# BEGIN QKD MANAGED DEVICE ALIASES"
    inline_end = "# END QKD MANAGED DEVICE ALIASES"

    ssh_dir.mkdir(parents=True, exist_ok=True)
    config_d.mkdir(parents=True, exist_ok=True)

    include_line = "Include %s/.ssh/config.d/*.conf" % str(Path.home())
    if main_config.exists():
        content = main_config.read_text(encoding="utf-8")
    else:
        content = ""

    # Keep Include at the top-level to avoid it being swallowed by a trailing
    # Match block in ~/.ssh/config on some OpenSSH configurations.
    existing_lines = content.splitlines()
    include_re = re.compile(r"^\s*Include\s+.*\.ssh/config\.d/\*\.conf\s*$")
    cleaned_lines = [ln for ln in existing_lines if not include_re.match(ln)]

    # Remove previously generated inline alias block, if present.
    try:
        start_idx = cleaned_lines.index(inline_begin)
        end_idx = cleaned_lines.index(inline_end, start_idx + 1)
        cleaned_lines = cleaned_lines[:start_idx] + cleaned_lines[end_idx + 1 :]
    except ValueError:
        pass
    rebuilt = include_line
    if cleaned_lines:
        rebuilt += "\n" + "\n".join(cleaned_lines)
    if not rebuilt.endswith("\n"):
        rebuilt += "\n"
    main_config.write_text(rebuilt, encoding="utf-8")
    try:
        main_config.chmod(0o600)
    except Exception:
        pass

    lines: List[str] = []
    lines.append("# Auto-generated by script_user_bootstrap.py")
    lines.append("# QKD managed device aliases")

    for name in sorted(devices.keys()):
        device = devices.get(name) or {}
        host_ip = str(device.get("ip") or device.get("mgmt_ip") or "").strip()
        if not host_ip:
            continue

        alias = str(name).strip().lower()
        canonical = str(name).strip()

        def _append_host_block(host_value: str) -> None:
            lines.append("")
            lines.append(f"Host {host_value}")
            lines.append(f"    HostName {host_ip}")
            lines.append(f"    User {script_user}")
            lines.append(f"    IdentityFile {identity_file}")
            lines.append("    IdentitiesOnly yes")
            lines.append("    StrictHostKeyChecking no")
            lines.append("    UserKnownHostsFile /dev/null")
            lines.append("    Port 22")

        # Write dedicated blocks for lowercase and canonical names to avoid
        # OpenSSH host-pattern ambiguity across versions.
        _append_host_block(alias)
        if canonical and canonical != alias:
            _append_host_block(canonical)

    include_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        include_file.chmod(0o600)
    except Exception:
        pass

    # Fallback for environments where Include is ignored or unsupported:
    # keep a managed alias block directly in ~/.ssh/config.
    inline_lines: List[str] = []
    inline_lines.append(inline_begin)
    inline_lines.append("# Auto-generated by script_user_bootstrap.py")
    inline_lines.extend(lines[2:])
    inline_lines.append(inline_end)

    rebuilt_lines = rebuilt.rstrip("\n").splitlines()
    # Place managed aliases near the top (right after Include) so they are not
    # shadowed by earlier generic Host * entries in user SSH config.
    if rebuilt_lines:
        first = rebuilt_lines[0]
        tail = rebuilt_lines[1:]
        final_lines = [first, ""] + inline_lines
        if tail:
            final_lines.extend([""] + tail)
    else:
        final_lines = inline_lines
    main_config.write_text("\n".join(final_lines) + "\n", encoding="utf-8")
    try:
        main_config.chmod(0o600)
    except Exception:
        pass

    return include_file


def get_deploy_user(inventory_base: Dict[str, Any], override: Optional[str] = None) -> str:
    if override:
        return override

    secrets = _secrets_block(inventory_base)
    return str(
        os.getenv("QKD_BOOTSTRAP_USER")
        or secrets.get("bootstrap_user")
        or secrets.get("deploy_user")
        or secrets.get("default_user")
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
        os.getenv("QKD_BOOTSTRAP_PASSWORD")
        or secrets.get("bootstrap_password")
        or secrets.get("deploy_password")
        or secrets.get("root_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
    )

    return str(value) if value else None


def prompt_deploy_password_once(deploy_user: str) -> str:
    return getpass.getpass("Password for deploy user %s: " % deploy_user)


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


def script_user_exists(dev: Device, script_user: str) -> bool:
    try:
        result = dev.rpc.cli(
            "show configuration system login user %s" % script_user,
            format="text",
        )
        text = _rpc_text(result).strip()
        if not text:
            return False
        if "user %s" % script_user in text:
            return True
        if "class" in text or "authentication" in text or "encrypted-password" in text:
            return True
        return False
    except Exception:
        return False


def build_set_commands(
    script_user: str,
    encrypted_password: Optional[str],
    user_exists: bool,
    auth_mode: str = "password",
    public_key_line: Optional[str] = None,
    remove_encrypted_password: bool = False,
) -> List[str]:
    commands = [
        "set system login user %s class super-user" % script_user,
    ]

    if auth_mode == "password":
        if not user_exists:
            if not encrypted_password:
                raise ValueError("encrypted_password is required when creating SCRIPT_USER")
            commands.append(
                "set system login user %s authentication encrypted-password \"%s\""
                % (script_user, encrypted_password)
            )
        return commands

    if auth_mode == "key-only":
        if not public_key_line:
            raise ValueError("public_key_line is required for key-only auth mode")
        parts = public_key_line.strip().split()
        if len(parts) < 2:
            raise ValueError("Invalid public_key_line for key-only auth mode")
        key_type = parts[0]
        key_payload = public_key_line.replace('"', '\\"')
        if remove_encrypted_password:
            commands.append(
                "delete system login user %s authentication encrypted-password" % script_user
            )
        commands.append(
            "set system login user %s authentication %s \"%s\""
            % (script_user, key_type, key_payload)
        )
        return commands

    raise ValueError("Unsupported auth_mode=%s" % auth_mode)

    return commands


def build_ssh_fix_command(script_user: str, public_key_line: Optional[str] = None) -> str:
    home = "/var/home/%s" % script_user
    ssh_dir = "%s/.ssh" % home
    authorized_keys = "%s/authorized_keys" % ssh_dir

    append_public_key = ""
    if public_key_line:
        quoted = shlex.quote(public_key_line)
        append_public_key = (
            "grep -q -F %s %s || echo %s >> %s; "
            % (quoted, authorized_keys, quoted, authorized_keys)
        )

    return (
        "mkdir -p {ssh_dir}; "
        "touch {authorized_keys}; "
        "{append_public_key}"
        "chown {user} {ssh_dir} {authorized_keys}; "
        "chmod 700 {ssh_dir}; "
        "chmod 600 {authorized_keys}; "
        "ls -ld {ssh_dir}; "
        "ls -l {authorized_keys}"
    ).format(
        user=script_user,
        ssh_dir=ssh_dir,
        authorized_keys=authorized_keys,
        append_public_key=append_public_key,
    )


def run_shell_fix(
    dev: Device,
    name: str,
    script_user: str,
    deploy_user: str,
    public_key_line: Optional[str] = None,
) -> bool:
    # Non-privileged bootstrap users cannot reliably repair another user's
    # home/.ssh ownership on all Junos variants.
    if deploy_user not in ("root", script_user):
        print(
            "[%s] INFO ssh home fix skipped: deploy user %s is not privileged for %s home ownership repair" %
            (name, deploy_user, script_user)
        )
        return True

    command = build_ssh_fix_command(script_user, public_key_line=public_key_line)
    try:
        result = dev.rpc.request_shell_execute(command=command)
        text = _rpc_text(result).strip()
        if text:
            print("[%s] ssh home fix output:\n%s" % (name, text))

        low = text.lower()
        error_markers = [
            "permission denied",
            "operation not permitted",
            "invalid user",
            "no such file or directory",
            "cannot access",
            "cannot create directory",
            "cannot touch",
        ]
        if any(marker in low for marker in error_markers):
            print(
                "[%s] FAIL ssh home fix: insufficient privileges or invalid runtime user state for %s" %
                (name, script_user)
            )
            print(
                "[%s] hint: bootstrap user must be able to repair %s/.ssh ownership and permissions" %
                (name, script_user)
            )
            return False

        return True
    except Exception as exc:
        print("[%s] FAIL ssh home fix: %s" % (name, exc))
        return False


def run_script_user_key_fix(
    dev: Device,
    name: str,
    script_user: str,
    deploy_user: str,
) -> bool:
    ssh_home_base = QKD.get("SSH_HOME_BASE", "/var/home")
    key_name = QKD.get("SSH_KEY_NAME", "qkd_id_ed25519")
    key_path = f"{ssh_home_base}/{script_user}/.ssh/{key_name}"
    pub_path = f"{key_path}.pub"

    if deploy_user not in ("root", script_user):
        print(
            "[%s] INFO ssh key fix skipped: deploy user %s is not privileged for %s ownership repair" %
            (name, deploy_user, script_user)
        )
        return True

    key_comment = f"{script_user}@qkd-bootstrap"

    def _run(command: str) -> str:
        result = dev.rpc.request_shell_execute(command=command)
        text = _rpc_text(result).strip()
        if text:
            print("[%s] ssh key fix output:\n%s" % (name, text))
        return text

    try:
        ssh_dir = f"{ssh_home_base}/{script_user}/.ssh"

        _run(
            f"mkdir -p {shlex.quote(ssh_dir)}; "
            f"chown {shlex.quote(script_user)} {shlex.quote(ssh_dir)}; "
            f"chmod 700 {shlex.quote(ssh_dir)}"
        )

        key_probe = _run(f"ls -l {shlex.quote(key_path)}")
        if "no such file or directory" in key_probe.lower() or not key_probe:
            _run(
                f"rm -f {shlex.quote(key_path)} {shlex.quote(pub_path)}; "
                f"ssh-keygen -q -t ed25519 -N '' -C {shlex.quote(key_comment)} -f {shlex.quote(key_path)}"
            )

        _run(
            f"rm -f {shlex.quote(pub_path)}; "
            f"ssh-keygen -y -f {shlex.quote(key_path)} > {shlex.quote(pub_path)}"
        )

        verify = _run(
            f"chown {shlex.quote(script_user)} {shlex.quote(key_path)} {shlex.quote(pub_path)}; "
            f"chmod 600 {shlex.quote(key_path)}; "
            f"chmod 644 {shlex.quote(pub_path)}; "
            f"ls -l {shlex.quote(key_path)}; "
            f"ls -l {shlex.quote(pub_path)}; "
            f"wc -c {shlex.quote(key_path)}; "
            f"wc -c {shlex.quote(pub_path)}"
        )

        low = verify.lower()
        error_markers = [
            "permission denied",
            "operation not permitted",
            "invalid user",
            "cannot access",
            "cannot create directory",
            "cannot touch",
            "chown:",
            "ssh-keygen:",
            "overwrite (y/n)?",
            "no such file or directory",
        ]
        if any(marker in low for marker in error_markers):
            print(
                "[%s] FAIL ssh key fix: insufficient privileges or invalid runtime user state for %s" %
                (name, script_user)
            )
            print(
                "[%s] hint: bootstrap user must be able to repair %s key ownership and permissions" %
                (name, script_user)
            )
            return False

        wc_sizes = [int(m.group(1)) for m in re.finditer(r"(?m)^\s*(\d+)\s+", verify)]
        if len(wc_sizes) >= 2 and (wc_sizes[-2] <= 0 or wc_sizes[-1] <= 0):
            print("[%s] FAIL ssh key fix: key files are empty after repair" % name)
            return False

        return True
    except Exception as exc:
        print("[%s] FAIL ssh key fix: %s" % (name, exc))
        return False


# ---------------------------------------------------------------------------
# Junos bootstrap
# ---------------------------------------------------------------------------


def bootstrap_script_user_on_device(
    name: str,
    device: Dict[str, Any],
    deploy_user: str,
    deploy_password: Optional[str],
    script_user: str,
    script_password: Optional[str],
    script_auth_mode: str = "password",
    public_key_line: Optional[str] = None,
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
        if script_auth_mode == "key-only":
            print("[%s] DRY-RUN configure SCRIPT_USER key-only authentication" % name)
        print("[%s] DRY-RUN fix /var/home/%s/.ssh ownership and permissions" % (name, script_user))
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

        exists = script_user_exists(dev, script_user)
        if script_auth_mode == "password":
            if not script_password:
                raise ValueError("SCRIPT_USER password is required for password auth mode")
            encrypted_password = None if exists else encrypted_junos_password(script_password)
            remove_encrypted_password = False
        else:
            encrypted_password = None
            remove_encrypted_password = False
            if exists:
                try:
                    existing_cfg = _rpc_text(
                        dev.rpc.cli(
                            "show configuration system login user %s | display set" % script_user,
                            format="text",
                        )
                    )
                    remove_encrypted_password = "encrypted-password" in existing_cfg
                except Exception:
                    remove_encrypted_password = False

        commands = build_set_commands(
            script_user,
            encrypted_password,
            exists,
            auth_mode=script_auth_mode,
            public_key_line=public_key_line,
            remove_encrypted_password=remove_encrypted_password,
        )

        cu = Config(dev)
        cu.load("\n".join(commands), format="set", merge=True)
        diff = cu.diff()

        if diff:
            print("[%s] candidate diff:\n%s" % (name, diff))
            cu.commit(comment="QKD bootstrap SCRIPT_USER %s" % script_user)
            print("[%s] OK SCRIPT_USER bootstrap committed" % name)
        else:
            print("[%s] no SCRIPT_USER config change required" % name)
            try:
                cu.rollback()
            except Exception:
                pass

        if not run_shell_fix(
            dev,
            name,
            script_user,
            deploy_user,
            public_key_line=public_key_line,
        ):
            print(
                "[%s] WARN ssh home fix did not complete; continuing because this can be platform-specific on Junos" %
                name
            )
            print(
                "[%s] hint: predeploy/provisioning will continue with runtime checks and config-based peer SSH auth" %
                name
            )

        if not run_script_user_key_fix(dev, name, script_user, deploy_user):
            print(
                "[%s] WARN ssh key fix did not complete; continuing because this can be platform-specific on Junos" %
                name
            )
            print(
                "[%s] hint: the script user private key must remain owned by %s for runtime SSH checks" %
                (name, script_user)
            )

        return True

    except Exception as exc:
        print("[%s] FAIL SCRIPT_USER bootstrap: %s" % (name, exc))
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
    script_auth_mode: Optional[str] = None,
    write_local_ssh_config: bool = True,
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
    resolved_script_auth_mode = get_script_user_auth_mode(inventory_base, script_auth_mode)
    if resolved_script_auth_mode == "password":
        resolved_script_password = get_script_password(inventory_base, script_password)
        local_private_key_path = None
        local_public_key_line = None
        local_ssh_config_path = None
    else:
        resolved_script_password = None
        source_private_key_path, local_public_key_line = ensure_local_script_user_keypair(resolved_script_user)
        local_private_key_path = mirror_local_script_user_keypair_to_ssh(
            resolved_script_user,
            source_private_key_path,
        )
        local_ssh_config_path = None
        if write_local_ssh_config and not dry_run:
            local_ssh_config_path = write_local_ssh_alias_config(
                selected,
                resolved_script_user,
                local_private_key_path,
            )
    resolved_deploy_user = get_deploy_user(inventory_base, deploy_user)
    resolved_deploy_password = get_deploy_password_from_config(inventory_base, deploy_password)

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
    print("script_user  = %s" % resolved_script_user)
    print("auth_mode    = %s" % resolved_script_auth_mode)
    print("dry_run      = %s" % dry_run)
    print("deploy_pwd   = %s" % ("configured/prompted" if resolved_deploy_password else "none"))
    if local_private_key_path:
        print("local_key    = %s" % local_private_key_path)
    if local_ssh_config_path:
        print("local_ssh_cfg= %s" % local_ssh_config_path)
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
            script_auth_mode=resolved_script_auth_mode,
            public_key_line=local_public_key_line,
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
    parser.add_argument("--script-auth-mode", choices=["password", "key-only"], default=None)
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
        script_auth_mode=args.script_auth_mode,
        dry_run=args.dry_run,
        prompt_for_deploy_password=args.ask_deploy_password,
        skip_if_no_deploy_password=not args.ask_deploy_password,
    )

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
