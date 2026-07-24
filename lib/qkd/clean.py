# qkd_clean.py

import os
import yaml
import shutil
from lxml import etree
from pathlib import Path

from jnpr.junos import Device

from lib.common.settings import CONFIG, PKI, QKD
from lib.common.config import load_inventory_base

BASE_DIR = Path(__file__).resolve().parents[2]
ONBOX_SCRIPT_NAME = "qkd_onbox.py"


# ----------------------------------------
# CLEAN LOCAL RUNTIME
# ----------------------------------------
def clean_runtime():
    """
    Remove local generated runtime artifacts under config/runtime.

    Safety:
      - refuses to delete if path does not look like runtime
      - skips hidden files
    """

    runtime_dir = BASE_DIR / CONFIG["runtime_dir"]

    if not runtime_dir.exists():
        return

    if "runtime" not in str(runtime_dir):
        raise RuntimeError("Refusing to clean unsafe directory!")

    print(f"Cleaning local runtime only: {runtime_dir}")

    for f in runtime_dir.iterdir():

        if f.name.startswith("."):
            continue

        if f.is_file():
            f.unlink()

        elif f.is_dir():
            shutil.rmtree(f)

# ----------------------------------------
# CLEAN LOCAL CERTS
# ----------------------------------------
def clean_certs():
    """
    Remove local certs directory.

    This is controlled by --pki.
    """

    certs_dir = BASE_DIR / CONFIG["certs_dir"]

    if not certs_dir.exists():
        return

    print(f"Cleaning certs dir: {certs_dir}")

    for f in certs_dir.iterdir():

        if f.name.startswith("."):
            continue

        if f.is_file():
            f.unlink()

        elif f.is_dir():
            shutil.rmtree(f)

    print("CERTS CLEANED")

# ----------------------------------------
# COLLECT QKD CLEAN CANDIDATES
# ----------------------------------------
def collect_qkd_clean_candidates(device):
    """
    Collect interfaces, connectivity-associations, and keychains that belong
    to QKD links for one device.

    Supports both old and new inventory styles:
      - link["ca_names"]
      - link["ca_name"]
      - link["keychain_name"]

    Also derives fallback keychain name:
      QKD_<ca_name>
    """

    iface_candidates = []
    ca_candidates = []
    keychain_candidates = []

    for link in device.get("links", []):

        local_iface = link.get("interface")

        #
        # Clean only local interfaces belonging to this device.
        # Never try to delete peer interfaces from another device,
        # otherwise ACX4/ACX5 receive invalid et-2/x/x deletes.
        #

        if local_iface and local_iface not in iface_candidates:
            iface_candidates.append(local_iface)

        ca_name = link.get("ca_name")

        if ca_name and ca_name not in ca_candidates:
            ca_candidates.append(ca_name)

        for ca in link.get("ca_names", []):
            if ca and ca not in ca_candidates:
                ca_candidates.append(ca)

        keychain_name = link.get("keychain_name")

        if keychain_name and keychain_name not in keychain_candidates:
            keychain_candidates.append(keychain_name)

    for ca in ca_candidates:
        fallback_keychain = f"QKD_{ca}"

        if fallback_keychain not in keychain_candidates:
            keychain_candidates.append(fallback_keychain)

    return iface_candidates, ca_candidates, keychain_candidates

# ----------------------------------------
# CLEAN ONE REMOTE DEVICE
# ----------------------------------------
def clean_device(name, device, full_macsec=False):
    try:
        ip = device["ip"]
        user = device["auth"]["username"]
        passwd = device["auth"]["password"]

        script_name = ONBOX_SCRIPT_NAME
        script_user = str(device.get("script_user") or QKD.get("SCRIPT_USER") or "admin")
        script_dir = QKD.get("SCRIPT_DIR", "/var/db/scripts")
        op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
        event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")
        remote_cert_dir = PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")
        script_home_dir = f"{QKD.get('SSH_HOME_BASE', '/var/home')}/{script_user}"
        script_log_dir = f"{script_home_dir}/logs"

        print(f"Cleaning device {name} {ip}", flush=True)

        iface_candidates, ca_candidates, keychain_candidates = (
            collect_qkd_clean_candidates(device)
        )

        def safe_iface_name(iface):
            return iface.replace("/", "_")

        def device_sae_id():
            qkd = device.get("qkd", {}) or {}

            return (
                qkd.get("sae_id")
                or device.get("local_sae")
                or device.get("sae")
                or device.get("sae_id")
                or name
            )

        def runtime_state_paths():
            sae = device_sae_id()

            paths = [
                f"{script_home_dir}/qkd_onbox_{sae}.lock",
            ]

            for link in device.get("links", []):
                iface = link.get("interface")
                peer = link.get("peer")

                if not iface or not peer:
                    continue

                safe_iface = safe_iface_name(iface)

                paths.extend(
                    [
                        f"{script_home_dir}/qkd_db_{peer}_{safe_iface}.json",
                        f"{script_home_dir}/qkd_onbox_{sae}_{safe_iface}_install-key.lock",
                        f"{script_home_dir}/qkd_onbox_{sae}_{safe_iface}_status.lock",
                    ]
                )

            deduped = []

            for path in paths:
                if path not in deduped:
                    deduped.append(path)

            return deduped

        runtime_paths = runtime_state_paths()
        soft_runtime_paths = [
            f"{script_log_dir}/qkd_debug.log",
            f"{script_log_dir}/qkd_debug_*.log",
            # Legacy cleanup for older deployments that wrote under /var/tmp.
            "/var/tmp/qkd_debug.log",
            "/var/tmp/qkd_debug_*.log",
        ]
        config_cmds = [
            "delete event-options generate-event QKD_TIMER",
            "delete event-options policy QKD",
            "delete event-options policy QKD_POLICY",
            f"delete event-options event-script file {script_name}",
            f"delete system scripts op file {script_name}",
            f"delete system login user {script_user}",
        ]

        if full_macsec:
            config_cmds.append("delete security macsec")
            config_cmds.append("delete security authentication-key-chains")
        else:
            for iface in iface_candidates:
                config_cmds.append(
                    f"delete security macsec interfaces {iface}"
                )

            for ca in ca_candidates:
                config_cmds.append(
                    f"delete security macsec connectivity-association {ca}"
                )

            for keychain in keychain_candidates:
                config_cmds.append(
                    f"delete security authentication-key-chains key-chain {keychain}"
                )

        for iface in iface_candidates:
            config_cmds.append(
                f"delete interfaces {iface} description"
            )

        config_body = "; ".join(config_cmds)

        config_cleanup_cmd = (
            f"cli -c 'configure; {config_body}; commit; exit'"
        )

        file_cleanup_parts = [
            f"rm -f {event_script_dir}/{script_name}",
            f"rm -f {op_script_dir}/{script_name}",
            f"rm -f {op_script_dir}/qkd_onbox_inventory.json",
            f"rm -f {op_script_dir}/qkd_onbox_config.json",
            f"rm -f /var/tmp/{script_name}",
            "rm -f /var/db/scripts/event/qkd.conf",
            f"rm -rf {remote_cert_dir}",
            f"rm -rf {script_dir}/certs",
            f"rm -rf {op_script_dir}/certs",
            f"rm -rf {event_script_dir}/certs",
            f"rm -rf {script_home_dir}",
        ]

        for path in runtime_paths:
            if path.endswith(".lock"):
                file_cleanup_parts.append(f"rm -rf {path}")
            else:
                file_cleanup_parts.append(f"rm -f {path}")
        for path in soft_runtime_paths:
            file_cleanup_parts.append(f"rm -f {path}")
        
        file_cleanup_cmd = "; ".join(file_cleanup_parts)

        dev = Device(
            host=ip,
            user=user,
            passwd=passwd,
            port=22,
        )

        def rpc_text(rsp):
            try:
                return etree.tostring(
                    rsp,
                    encoding="unicode",
                    method="text",
                ).strip()
            except Exception:
                return str(rsp).strip()

        ##
        def run_shell(label, command, strict=True, show_output=True, show_label=True):
            if show_label:
                print(f"[{name}] {label}", flush=True)

            rsp = dev.rpc.request_shell_execute(
                command=command
            )

            output = rpc_text(rsp)

            if show_output and output:
                for line in output.splitlines():
                    line = line.strip()

                    if not line:
                        continue
                    
                    if "warning: statement not found" in line:
                        continue
                    
                    if "Entering configuration mode" in line:
                        continue
                    
                    if "Exiting configuration mode" in line:
                        continue
                    
                    if "No match" in line:
                        continue
                    
                    if line == "True":
                        continue
                    
                    print(f"[{name}] {line}", flush=True)

            bad_markers = [
                "Ambiguous output redirect",
                "syntax error",
                "commit failed",
                "unknown command",
                "error:",
            ]

            low = output.lower()

            if strict and any(marker.lower() in low for marker in bad_markers):
                raise RuntimeError(
                    f"{label} failed on {name}\n"
                    f"command={command}\n"
                    f"output={output}"
                )

            return output
        ##
        def run_cli_show(command):
            rsp = dev.rpc.cli(
                command,
                format="text",
            )

            return rpc_text(rsp)

        def has_dual_re():
            out = run_cli_show("show chassis routing-engine")
            low = (out or "").lower()
            return (
                ("re0" in low and "re1" in low)
                or ("routing engine 0" in low and "routing engine 1" in low)
                or ("slot 0:" in low and "slot 1:" in low)
            )

        def clean_peer_re_files(paths):
            if not has_dual_re():
                return

            print(f"[{name}] dual-RE detected: best-effort peer RE file cleanup")
            unique_paths = []
            for path in paths:
                if path and path not in unique_paths:
                    unique_paths.append(path)

            for re_name in ("re0", "re1"):
                print(f"[{name}] peer {re_name} cleanup ({len(unique_paths)} paths)")
                real_errors = 0
                for path in unique_paths:
                    out = run_shell(
                        f"peer {re_name} delete {path}",
                        f"cli -c 'file delete {re_name}:{path}'",
                        strict=False,
                        show_output=False,
                        show_label=False,
                    )
                    low = (out or "").lower()
                    if (
                        "error" in low
                        and "no such file" not in low
                        and "cannot stat" not in low
                        and "not found" not in low
                    ):
                        real_errors += 1

                if real_errors:
                    print(f"[{name}] WARN peer {re_name} cleanup had {real_errors} non-benign errors")

        ##
        def remote_path_exists(path):
            output = run_shell(
                f"verify path {path}",
                (
                    f"test -e {path} "
                    f"&& echo EXISTS:{path} "
                    f"|| true"
                ),
                strict=False,
                show_output=False,
                show_label=False,
            )
        
            return f"EXISTS:{path}" in output
        ##
        
        dev.open()

        try:
            dual_re = has_dual_re()
            if dual_re:
                config_cleanup_sync_cmd = (
                    f"cli -c 'configure; {config_body}; commit synchronize; exit'"
                )
                sync_out = run_shell(
                    "config cleanup (commit synchronize)",
                    config_cleanup_sync_cmd,
                    strict=False,
                )
                sync_low = (sync_out or "").lower()
                if "commit complete" not in sync_low:
                    print(f"[{name}] WARN commit synchronize cleanup failed, retrying local commit")
                    run_shell(
                        "config cleanup (local fallback)",
                        config_cleanup_cmd,
                        strict=True,
                    )
            else:
                run_shell(
                    "config cleanup",
                    config_cleanup_cmd,
                    strict=True,
                )

            run_shell(
                "file/cert/runtime cleanup",
                file_cleanup_cmd,
                strict=False,
            )

            peer_cleanup_paths = [
                f"{event_script_dir}/{script_name}",
                f"{op_script_dir}/{script_name}",
                f"{op_script_dir}/qkd_onbox_inventory.json",
                f"{op_script_dir}/qkd_onbox_config.json",
                f"/var/tmp/{script_name}",
                "/var/db/scripts/event/qkd.conf",
                remote_cert_dir,
                f"{script_dir}/certs",
                f"{op_script_dir}/certs",
                f"{event_script_dir}/certs",
                script_log_dir,
                script_home_dir,
            ]
            for path in runtime_paths:
                if path not in peer_cleanup_paths:
                    peer_cleanup_paths.append(path)
            clean_peer_re_files(peer_cleanup_paths)

            failures = []

            set_output = run_cli_show(
                "show configuration | display set"
            )

            forbidden_patterns = [
                "set event-options generate-event QKD_TIMER",
                "set event-options policy QKD",
                "set event-options policy QKD_POLICY",
                f"set event-options event-script file {script_name}",
                f"set system scripts op file {script_name}",
            ]

            if full_macsec:
                forbidden_patterns.extend(
                    [
                        "set security macsec ",
                        "set security authentication-key-chains ",
                    ]
                )
            else:
                for iface in iface_candidates:
                    forbidden_patterns.append(
                        f"set security macsec interfaces {iface}"
                    )

                for ca in ca_candidates:
                    forbidden_patterns.append(
                        f"set security macsec connectivity-association {ca}"
                    )

                for keychain in keychain_candidates:
                    forbidden_patterns.append(
                        f"set security authentication-key-chains key-chain {keychain}"
                    )

            for iface in iface_candidates:
                forbidden_patterns.append(
                    f"set interfaces {iface} description"
                )

            config_leftovers = []

            for line in set_output.splitlines():
                line = line.strip()

                for pattern in forbidden_patterns:
                    if pattern in line:
                        config_leftovers.append(line)
                        break

            if config_leftovers:
                failures.append(
                    "configuration leftovers:\n"
                    + "\n".join(config_leftovers)
                )

            paths_should_be_absent = [
                f"{op_script_dir}/{script_name}",
                f"{event_script_dir}/{script_name}",
                f"{op_script_dir}/qkd_onbox_inventory.json",
                f"{op_script_dir}/qkd_onbox_config.json",
                f"/var/tmp/{script_name}",
                "/var/db/scripts/event/qkd.conf",
                remote_cert_dir,
                f"{script_dir}/certs",
                f"{op_script_dir}/certs",
                f"{event_script_dir}/certs",
                script_log_dir,
                script_home_dir,
            ]

            for path in runtime_paths:
                if path not in paths_should_be_absent:
                    paths_should_be_absent.append(path)

            file_leftovers = []

            for path in paths_should_be_absent:
                if remote_path_exists(path):
                    file_leftovers.append(path)
            
            soft_leftovers = []

            for path in soft_runtime_paths:
                if remote_path_exists(path):
                    soft_leftovers.append(path)

            if soft_leftovers:
                print(f"[{name}] cleanup warning: soft runtime leftovers:")
                for path in soft_leftovers:
                    print(f"[{name}]   {path}")
            
            if file_leftovers:
                failures.append(
                    "file/runtime/cert leftovers:\n"
                    + "\n".join(file_leftovers)
                )

            if failures:
                print(f"[FAIL] Device clean verification failed: {name}")

                for item in failures:
                    print(item)

                return False

            print(f"[OK] Device clean complete: {name}")
            return True

        finally:
            try:
                dev.close()
            except Exception:
                pass

    except Exception as e:
        fallback_auth = device.get("_fallback_auth") if isinstance(device, dict) else None
        if (
            not device.get("_clean_retried")
            and isinstance(fallback_auth, dict)
            and fallback_auth.get("username")
            and fallback_auth.get("password")
        ):
            current_auth = device.get("auth") if isinstance(device, dict) else {}
            current_user = (current_auth or {}).get("username")
            fallback_user = fallback_auth.get("username")
            if fallback_user != current_user:
                print(
                    f"[WARN] Device clean auth failed on {name} as {current_user}; retrying with fallback user {fallback_user}"
                )
                device["_clean_retried"] = True
                device["auth"] = {
                    "username": fallback_auth.get("username"),
                    "password": fallback_auth.get("password"),
                }
                return clean_device(name, device, full_macsec=full_macsec)

        print(f"[FAIL] Device clean failed: {name}: {e}")
        return False        

# ----------------------------------------
# CLEAN HANDLER
# ----------------------------------------
def handle_clean(args):
    """
    Clean handler used by qkd_orchestrator.py.

    Behavior:
      - --local-only:
          clean local runtime only
      - --local-only --pki:
          clean local runtime and local certs
      - no --local-only:
          clean remote devices first, then local runtime
      - no --local-only --pki:
          clean remote devices, local runtime, and local certs

    Important:
      Local certs are removed ONLY when --pki is explicitly provided.
    """

    print("=== QKD clean ===")
    print(f"local_only = {args.local_only}")
    print(f"pki        = {args.pki}")
    print(f"full_macsec = {args.full_macsec}")
    print(f"continue_on_failure = {getattr(args, 'continue_on_failure', False)}")
    print("")

    devices_file = BASE_DIR / CONFIG["runtime_dir"] / "devices.yaml"

    # ----------------------------------------
    # LOCAL ONLY MODE
    # ----------------------------------------
    if args.local_only:

        clean_runtime()

        if args.pki:
            clean_certs()
        else:
            print("Skipping local cert cleanup. Use --pki to remove certs.")

        print("Local clean complete")
        return

    # ----------------------------------------
    # REMOTE + LOCAL MODE
    # ----------------------------------------
    devices = {}

    if devices_file.exists():

        with open(devices_file) as f:
            data = yaml.safe_load(f) or {}

        devices = data.get("devices", {})

        print("Using runtime devices.yaml")

    else:

        print("No runtime devices.yaml found -> fallback to inventory_base")

        base = load_inventory_base()
        devices = base.get("devices", {})

        if devices:
            print("Using inventory_base devices")
        else:
            print("No devices found anywhere -> skipping remote device cleanup")

    inventory_base = load_inventory_base()
    secrets = inventory_base.get("secrets", {}) if isinstance(inventory_base, dict) else {}
    if not isinstance(secrets, dict):
        secrets = {}

    clean_user = (
        os.getenv("QKD_BOOTSTRAP_USER")
        or secrets.get("bootstrap_user")
        or secrets.get("deploy_user")
        or "root"
    )

    clean_password = (
        os.getenv("QKD_BOOTSTRAP_PASSWORD")
        or secrets.get("bootstrap_password")
        or secrets.get("deploy_password")
        or secrets.get("root_password")
        or os.getenv("QKD_DEFAULT_PASSWORD")
        or secrets.get("default_password")
    )

    if not clean_password:
        raise RuntimeError(
            "Missing clean credentials. Set QKD_BOOTSTRAP_PASSWORD (recommended) or "
            "configure one of secrets.bootstrap_password/deploy_password/root_password/default_password."
        )

    for _, device in devices.items():
        if not isinstance(device, dict):
            continue
        auth = device.get("auth")
        if isinstance(auth, dict):
            device["_fallback_auth"] = {
                "username": auth.get("username"),
                "password": auth.get("password"),
            }
        if not isinstance(auth, dict):
            auth = {}
            device["auth"] = auth
        auth["username"] = clean_user
        auth["password"] = clean_password

    print(f"Remote clean auth user = {clean_user}")

    failed = []

    for name, device in devices.items():
        ok = clean_device(
            name,
            device,
            full_macsec=args.full_macsec
        )

        if not ok:
            failed.append(name)

    if failed:
        if not getattr(args, "continue_on_failure", False):
            raise RuntimeError(
                f"Remote clean failed for devices: {', '.join(failed)}. "
                f"Local runtime was not removed."
            )

        print(
            "[WARN] Remote clean failed for devices: %s" % ", ".join(failed)
        )
        print(
            "[WARN] Continuing local cleanup because --continue-on-failure is enabled."
        )

    clean_runtime()

    if args.pki:
        clean_certs()
    else:
        print("Skipping local cert cleanup. Use --pki to remove certs.")

    print("Full clean complete")



