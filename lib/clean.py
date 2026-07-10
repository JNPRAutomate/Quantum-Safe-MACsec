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

    cleanup_cmds = [
        #
        # Junos configuration cleanup.
        #
        f"cli -c 'configure; {config_body}; commit; exit'",

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
        "rm -f /var/tmp/qkd_db_* >/dev/null 2>&1",
        "rm -f /var/tmp/qkd_debug* >/dev/null 2>&1",
        "rm -rf /var/tmp/qkd_onbox_* >/dev/null 2>&1",

        #
        # Remove certs.
        #
        f"rm -rf {remote_cert_dir}",
        f"rm -rf {op_script_dir}/certs",
        f"rm -rf {event_script_dir}/certs",

        #
        # Verify.
        #
        "echo '=== QKD CLEAN VERIFY START ==='",
        "echo '[var/tmp]'; ls -1 /var/tmp/qkd* 2>/dev/null || true",
        "echo '[scripts/op]'; ls -1 /var/db/scripts/op/qkd_onbox.py 2>/dev/null || true",
        "echo '[scripts/event]'; ls -1 /var/db/scripts/event/qkd_onbox.py 2>/dev/null || true",
        "echo '[certs]'; ls -ld /var/db/scripts/certs /var/db/scripts/op/certs /var/db/scripts/event/certs 2>/dev/null || true",
        "echo '=== QKD CLEAN VERIFY END ==='"
    ]

    shell_cmd = "; ".join(cleanup_cmds)

    dev = Device(
        host=ip,
        user=user,
        passwd=passwd,
        port=22
    )

    try:

        dev.open()

        rsp = dev.rpc.request_shell_execute(
            command=shell_cmd
        )
        
        try:
            output = etree.tostring(rsp, encoding="unicode", method="text")
            if output.strip():
                print(output.strip())
        except Exception:
            pass

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
