# qkd_clean.py

import os
import shlex
import time
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
# PARSE ORPHAN CONNECTIVITY-ASSOCIATIONS
# ----------------------------------------
def parse_orphan_connectivity_associations(display_set_output, target_keychains, known_ca_names):
    """Parse `show ... | display set` output for connectivity-associations
    that reference one of target_keychains but are not already accounted
    for in known_ca_names.

    These are "orphans" relative to our inventory: legacy/renamed CAs left
    over on the device (e.g. from earlier lab iterations) that still
    reference a keychain we are about to delete. If left alone, Junos
    rejects the delete-keychain commit with
    "authentication-key-chains not defined !!" (statements constraint
    check failed), causing clean() to fail on an otherwise healthy device.

    Pure/parsing-only so it can be unit tested without a device connection.
    """
    target_keychains = set(target_keychains or [])
    known_ca_names = set(known_ca_names or [])

    if not target_keychains:
        return []

    orphans = []
    for line in (display_set_output or "").splitlines():
        parts = line.strip().split()
        # set security macsec connectivity-association <NAME> pre-shared-key-chain <KEYCHAIN>
        if (
            len(parts) >= 7
            and parts[0] == "set"
            and parts[1] == "security"
            and parts[2] == "macsec"
            and parts[3] == "connectivity-association"
            and parts[5] == "pre-shared-key-chain"
        ):
            ca_name = parts[4]
            keychain_name = parts[6].strip('"')
            if (
                keychain_name in target_keychains
                and ca_name not in known_ca_names
                and ca_name not in orphans
            ):
                orphans.append(ca_name)
    return orphans

# ----------------------------------------
# CLEAN ONE REMOTE DEVICE
# ----------------------------------------
def clean_device(name, device, full_macsec=False):
    try:
        ip = device["ip"]
        user = device["auth"]["username"]
        passwd = device["auth"]["password"]

        script_name = ONBOX_SCRIPT_NAME
        secrets = device.get("secrets") or {}
        script_user = str(device.get("script_user") or secrets.get("script_user") or QKD.get("SCRIPT_USER") or "etsi_user")
        script_user_class = str(secrets.get("script_user_class") or QKD.get("SCRIPT_USER_CLASS") or "")
        peer_cmd_user = str(device.get("peer_cmd_user") or secrets.get("peer_cmd_user") or QKD.get("PEER_CMD_USER") or "etsi_peer_view")
        peer_cmd_user_class = str(secrets.get("peer_cmd_user_class") or QKD.get("PEER_CMD_USER_CLASS") or "qkd-peer-cmd-class")
        script_dir = QKD.get("SCRIPT_DIR", "/var/db/scripts")
        op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
        event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")
        remote_cert_dir = PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")
        ssh_home_base = QKD.get('SSH_HOME_BASE', '/var/home')
        script_home_dir = f"{ssh_home_base}/{script_user}"
        script_log_dir = f"{script_home_dir}/logs"
        peer_cmd_home_dir = f"{ssh_home_base}/{peer_cmd_user}"

        print(f"Cleaning device {name} {ip}", flush=True)

        iface_candidates, ca_candidates, keychain_candidates = (
            collect_qkd_clean_candidates(device)
        )

        def discover_orphan_connectivity_associations(target_keychains):
            """Detect connectivity-associations already configured on the
            device that reference a keychain we are about to delete, but
            that are not present in our own inventory-derived ca_candidates
            (e.g. renamed/legacy CAs left over from earlier lab iterations
            or manual testing).

            Without this, deleting the keychain while an orphan CA still
            references it makes Junos reject the commit with
            "authentication-key-chains not defined !!" (statements
            constraint check failed), and clean() fails fail-closed on an
            otherwise unrelated device.
            """
            if not target_keychains:
                return []

            probe = Device(host=ip, user=user, passwd=passwd, port=22)
            try:
                probe.open()
                rsp = probe.rpc.cli(
                    "show configuration security macsec connectivity-association | display set",
                    format="text",
                )
                output = etree.tostring(rsp, encoding="unicode", method="text").strip()
            except Exception as exc:
                print(f"[{name}] WARN could not probe existing connectivity-associations: {exc}", flush=True)
                return []
            finally:
                try:
                    probe.close()
                except Exception:
                    pass

            return parse_orphan_connectivity_associations(
                output, target_keychains, ca_candidates
            )

        if not full_macsec:
            orphan_cas = discover_orphan_connectivity_associations(keychain_candidates)
            if orphan_cas:
                print(
                    f"[{name}] Found orphan connectivity-association(s) referencing "
                    f"target keychain(s), adding to cleanup: {orphan_cas}",
                    flush=True,
                )
                ca_candidates.extend(orphan_cas)

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
            f"delete system login user {peer_cmd_user}",
            f"delete system login class {peer_cmd_user_class}",
            "delete system login class qkd-script-class",
        ]
        # Only delete script_user_class if it's a custom class (not a Junos built-in)
        builtin_classes = {"super-user", "operator", "read-only", "unauthorized"}
        if script_user_class and script_user_class.lower() not in builtin_classes:
            config_cmds.append(f"delete system login class {script_user_class}")

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
            "rm -rf /var/tmp/qkd_peer_inbox",
            "rm -rf /var/tmp/qkd_peer_status",
            "rm -rf /var/tmp/qkd_peer_ack",
            "rm -f /var/db/scripts/event/qkd.conf",
            f"rm -rf {remote_cert_dir}",
            f"rm -rf {script_dir}/certs",
            f"rm -rf {op_script_dir}/certs",
            f"rm -rf {event_script_dir}/certs",
            f"rm -rf {script_home_dir}",
            f"rm -rf {peer_cmd_home_dir}",
        ]

        for path in runtime_paths:
            if path.endswith(".lock"):
                file_cleanup_parts.append(f"rm -rf {path}")
            else:
                file_cleanup_parts.append(f"rm -f {path}")
        for path in soft_runtime_paths:
            file_cleanup_parts.append(f"rm -f {path}")
        
        file_cleanup_cmd = "; ".join(file_cleanup_parts)
        shared_transport_paths = [
            "/var/tmp/qkd_peer_inbox",
            "/var/tmp/qkd_peer_status",
            "/var/tmp/qkd_peer_ack",
        ]

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
                return True

            unique_paths = []
            for path in paths:
                if path and path not in unique_paths:
                    unique_paths.append(path)

            if not unique_paths:
                return True

            print(f"[{name}] dual-RE detected: recursive peer RE cleanup ({len(unique_paths)} paths)")
            for path in unique_paths:
                command = (
                    "request routing-engine execute command "
                    f"\"rm -rf {shlex.quote(path)}\" routing-engine other"
                )
                output = run_shell(
                    "peer RE recursive cleanup",
                    "cli -c " + shlex.quote(command),
                    strict=False,
                    show_output=False,
                    show_label=False,
                )
                low = (output or "").lower()
                if (
                    "syntax error" in low
                    or "unknown command" in low
                    or "command not found" in low
                    or "could not connect" in low
                    or "cannot connect" in low
                    or "error:" in low
                ):
                    print(f"[{name}] WARN peer RE cleanup failed for {path}: {output}")
                    return False

            for path in unique_paths:
                command = (
                    "request routing-engine execute command "
                    f"\"ls -ld {shlex.quote(path)}\" routing-engine other"
                )
                output = run_shell(
                    "peer RE cleanup verification",
                    "cli -c " + shlex.quote(command),
                    strict=False,
                    show_output=False,
                    show_label=False,
                )
                if "No such file or directory" not in output:
                    print(f"[{name}] WARN peer RE path remains after cleanup: {path}")
                    return False

            return True

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
                "/var/tmp/qkd_peer_inbox",
                "/var/tmp/qkd_peer_status",
                "/var/tmp/qkd_peer_ack",
                "/var/db/scripts/event/qkd.conf",
                remote_cert_dir,
                f"{script_dir}/certs",
                f"{op_script_dir}/certs",
                f"{event_script_dir}/certs",
                script_log_dir,
                script_home_dir,
                peer_cmd_home_dir,
            ]
            for path in runtime_paths:
                if path not in peer_cleanup_paths:
                    peer_cleanup_paths.append(path)
            peer_cleanup_ok = clean_peer_re_files(peer_cleanup_paths)

            for attempt in range(1, 4):
                lingering_shared_paths = [
                    path for path in shared_transport_paths if remote_path_exists(path)
                ]
                if not lingering_shared_paths:
                    break

                print(
                    f"[{name}] removing recreated shared transport directories "
                    f"(attempt {attempt}/3): {', '.join(lingering_shared_paths)}"
                )
                run_shell(
                    "final shared transport cleanup",
                    "; ".join(f"rm -rf {path}" for path in lingering_shared_paths),
                    strict=True,
                )
                peer_cleanup_ok = clean_peer_re_files(lingering_shared_paths) and peer_cleanup_ok
                if attempt < 3:
                    time.sleep(2)

            failures = []
            if not peer_cleanup_ok:
                failures.append("peer RE cleanup verification failed")

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
                "/var/tmp/qkd_peer_inbox",
                "/var/tmp/qkd_peer_status",
                "/var/tmp/qkd_peer_ack",
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
        # Inject inventory secrets so clean_device() can read script_user_class, peer_cmd_user_class etc.
        if "secrets" not in device:
            device["secrets"] = secrets
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
