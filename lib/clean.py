# qkd_clean.py

import yaml
import shutil
from lxml import etree
from pathlib import Path

from jnpr.junos import Device

from lib.settings import CONFIG, PKI, QKD
from lib.config import load_inventory_base

BASE_DIR = Path(__file__).resolve().parent.parent


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

    ip = device["ip"]
    user = device["auth"]["username"]
    passwd = device["auth"]["password"]

    script_name = QKD["SCRIPT_NAME"]
    script_dir = QKD.get("SCRIPT_DIR", "/var/db/scripts")
    op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
    event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")
    remote_cert_dir = PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")

    print(f"Cleaning device {name} {ip}")

    iface_candidates, ca_candidates, keychain_candidates = (
        collect_qkd_clean_candidates(device)
    )

    config_cmds = [
        "delete event-options generate-event QKD_TIMER",
        "delete event-options policy QKD",
        "delete event-options policy QKD_POLICY",
        f"delete event-options event-script file {script_name}",
        f"delete system scripts op file {script_name}",
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

    file_cleanup_cmd = "; ".join(
        [
            #
            # Remove deployed scripts.
            #
            f"rm -f {event_script_dir}/{script_name}",
            f"rm -f {op_script_dir}/{script_name}",
            f"rm -f /var/tmp/{script_name}",
            "rm -f /var/db/scripts/event/qkd.conf",

            #
            # Remove runtime files.
            #
            # IMPORTANT:
            # Do not use >/dev/null 2>&1 here.
            # Junos request_shell_execute may run through csh/tcsh
            # and can throw 'Ambiguous output redirect'.
            #
            "rm -f /var/tmp/qkd_db_*",
            "rm -f /var/tmp/qkd_debug*",
            "rm -rf /var/tmp/qkd_onbox_*",

            #
            # Remove certs.
            #
            f"rm -rf {remote_cert_dir}",
            f"rm -rf {script_dir}/certs",
            f"rm -rf {op_script_dir}/certs",
            f"rm -rf {event_script_dir}/certs",
        ]
    )

    verify_cmd = "; ".join(
        [
            "echo '=== QKD CLEAN VERIFY START ==='",

            #"echo '[config qkd/macsec/auth-key-chain]'",
            #"cli -c \"show configuration | display set | match 'qkd|QKD|macsec|authentication-key-chains'\"",
            "echo '[config display set]'",
            "cli -c \"show configuration | display set\"",
            
            "echo '[scripts]'",
            f"ls -l {op_script_dir}/{script_name}",
            f"ls -l {event_script_dir}/{script_name}",
            f"ls -l /var/tmp/{script_name}",

            "echo '[certs]'",
            f"ls -ld {remote_cert_dir}",
            f"ls -ld {script_dir}/certs",
            f"ls -ld {op_script_dir}/certs",
            f"ls -ld {event_script_dir}/certs",

            "echo '[runtime tmp]'",
            "ls -l /var/tmp/qkd*",

            "echo '=== QKD CLEAN VERIFY END ==='",
        ]
    )

    dev = Device(
        host=ip,
        user=user,
        passwd=passwd,
        port=22
    )

    def rpc_text(rsp):
        try:
            return etree.tostring(
                rsp,
                encoding="unicode",
                method="text"
            ).strip()
        except Exception:
            return str(rsp).strip()

    def run_step(label, command):
        print(f"[{name}] running {label}")

        rsp = dev.rpc.request_shell_execute(
            command=command
        )

        output = rpc_text(rsp)

        if output:
            print(output)

        bad_markers = [
            "Ambiguous output redirect",
            "syntax error",
            "commit failed",
            "unknown command",
            "error:",
        ]

        low = output.lower()

        if any(marker.lower() in low for marker in bad_markers):
            raise RuntimeError(
                f"{label} failed on {name}\n"
                f"command={command}\n"
                f"output={output}"
            )

        return output

    try:

        dev.open()

        run_step(
            "config cleanup",
            config_cleanup_cmd
        )

        run_step(
            "file/cert/runtime cleanup",
            file_cleanup_cmd
        )

        #
        # Verify step is informational.
        # It may show 'No such file' for removed files, which is fine.
        # But run_step still catches real shell/parser errors.
        #
        run_step(
            "verify cleanup",
            verify_cmd
        )

        print(f"Device clean complete: {name}")

        return True

    except Exception as e:
        print(f"Device clean failed: {name}: {e}")
        return False

    finally:

        try:
            dev.close()
        except Exception:
            pass
    

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
        raise RuntimeError(
            f"Remote clean failed for devices: {', '.join(failed)}. "
            f"Local runtime was not removed."
        )

    clean_runtime()

    if args.pki:
        clean_certs()
    else:
        print("Skipping local cert cleanup. Use --pki to remove certs.")

    print("Full clean complete")
