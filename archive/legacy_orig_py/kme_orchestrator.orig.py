#!/usr/bin/env python3
"""
kme_orchestrator.py
Manages lifecycle KME (Docker, certs, restart, validate, database, network)

KME-side installer/orchestrator for Quantum-Safe MACsec lab/runtime.

Design decision:
    - qkd_orchestrator.py is the only component that generates PKI material.
    - kme_orchestrator.py does NOT generate certificates.
    - kme_orchestrator.py only installs/copies already generated KME certificates
      into the ETSI GS QKD 014 reference implementation certs directory, and can
      optionally restart KME containers and initialize the DB.

Supported PKI profiles:
    - self_signed
    - hierarchical_ca

Runtime source of truth:
    config/runtime/pki_profile.yaml

Expected generated sources:

self_signed:
    certs/self_signed/
        offbox_rootCA.crt
        offbox_rootCA.key
        kme/
            kme_001.crt
            kme_001.key
            kme_001.pem
            ...

hierarchical_ca:
    certs/hierarchical_ca/
        kme_pki/certs/kme_001/
            kme_001.crt
            kme_001.key
            kme_001.pem
            kme_001.chain.crt
        trust_exchange/install_on_kme/
            trusted-juniper-ca-bundle.crt
            juniper-root-ca.crt
            juniper-issuing-ca.crt
"""

import argparse
import shutil
import subprocess
from pathlib import Path

from lib.common.settings import CONFIG, PKI
from lib.common.config import load_runtime_pki_profile


# ----------------------------------------
# PATHS
# ----------------------------------------

BASE_DIR = Path(__file__).resolve().parent

KME_PROJECT_DIR = Path.home() / "kme-lab" / "etsi-gs-qkd-014-referenceimplementation"
KME_CERT_DEST_DIR = KME_PROJECT_DIR / "certs"
KME_CERT_PATH = str(KME_CERT_DEST_DIR)


# ----------------------------------------
# DISPLAY HELPERS
# ----------------------------------------

def display_text(value):
    """
    Return a display-only shortened representation of paths.

    IMPORTANT:
    This changes only what is printed on screen.
    It must never modify the real command/path used by subprocess, scp, ssh, or shutil.
    """

    text = str(value)

    repo_root = str(BASE_DIR)
    kme_root = str(KME_PROJECT_DIR.parent)

    text = text.replace(repo_root + "/", "")
    text = text.replace(kme_root + "/", "")

    return text


def repo_path(path):
    return display_text(path)


def kme_path(path):
    return display_text(path)


def display_cmd(cmd):
    return " ".join(display_text(arg) for arg in cmd)


def print_dry_run_command(cmd):
    """
    Pretty-print commands for dry-run mode only.

    The real command is not changed.
    This function only controls terminal output.
    """

    cmd = [str(x) for x in cmd]

    if not cmd:
        return

    command = cmd[0]

    if command == "scp":
        destination = cmd[-1]

        sources = []

        i = 1
        while i < len(cmd) - 1:
            token = cmd[i]

            if token in ["-i", "-o"]:
                i += 2
                continue

            if token.startswith("-"):
                i += 1
                continue

            sources.append(token)
            i += 1

        print("[DRY-RUN] Would copy files:")

        for src in sources:
            print(f"  - {display_text(src)}")

        print("[DRY-RUN] Destination:")
        print(f"  {display_text(destination)}")
        return

    if command == "ssh":
        remote = None
        remote_cmd = None

        if len(cmd) >= 3:
            remote = cmd[-2]
            remote_cmd = cmd[-1]

        if (
            remote_cmd 
            and remote_cmd.startswith("mkdir -p ")
            and "&&" not in remote_cmd
            ):
            remote_dir = remote_cmd.replace("mkdir -p ", "", 1)

            print("[DRY-RUN] Would create remote directory:")
            print(f"  {remote}:{display_text(remote_dir)}")
            return

        if remote_cmd and " && " in remote_cmd:
            print("[DRY-RUN] Would run remote command:")
            print(f"  host: {remote}")
            print("  command:")

            for part in remote_cmd.split(" && "):
                print(f"    {display_text(part)}")

            return

    print(f"[DRY-RUN] Would run: {display_cmd(cmd)}")


# ----------------------------------------
# CLI
# ----------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Install/copy already generated QKD/KME certificates into the "
            "ETSI GS QKD 014 reference implementation. Certificate generation "
            "is owned by qkd_orchestrator.py."
        )
    )

    parser.add_argument(
        "--kme-ip",
        required=False,
        default=None,
        help="Remote KME host IP. If omitted, local install mode is used.",
    )

    parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart KME docker containers after certificate installation.",
    )

    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize KME DB tables after remote restart.",
    )

    parser.add_argument(
        "--project-dir",
        required=False,
        default=None,
        help=(
            "Override local KME reference implementation directory. "
            "Default: ~/kme-lab/etsi-gs-qkd-014-referenceimplementation"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be installed/copied/restarted without changing anything.",
    )
    parser.add_argument(
    "--ssh-user",
    default="root",
    help="Remote KME SSH user (default: root)",
    )

    parser.add_argument(
        "--ssh-key",
        default=None,
        help="SSH private key used for remote KME access",
    )

    return parser.parse_args()


# ----------------------------------------
# COMMAND HELPERS
# ----------------------------------------
def ssh_base_cmd(args, kme_ip):
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
    ]

    if args.ssh_key:
        cmd.extend([
            "-i",
            str(Path(args.ssh_key).expanduser()),
            "-o",
            "IdentitiesOnly=yes",
        ])

    cmd.append(f"{args.ssh_user}@{kme_ip}")

    return cmd


def scp_base_cmd(args):
    cmd = ["scp"]

    if args.ssh_key:
        cmd.extend([
            "-i",
            str(Path(args.ssh_key).expanduser()),
            "-o",
            "IdentitiesOnly=yes",
        ])

    return cmd

def run(cmd, dry_run=False):
    if dry_run:
        print_dry_run_command(cmd)
        return True

    printable_cmd = " ".join(str(x) for x in cmd)

    print(f"→ {printable_cmd}")

    try:
        result = subprocess.run(
            [str(x) for x in cmd],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.stdout:
            print(result.stdout.rstrip())

        return True

    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout.rstrip())

        if exc.stderr:
            print(f"ERROR:\n{exc.stderr.rstrip()}")
        else:
            print(f"ERROR: command failed with rc={exc.returncode}")

        return False


def is_kme_reachable(kme_ip, dry_run=False):
    return run(
        [
            "ping",
            "-c",
            "1",
            kme_ip,
        ],
        dry_run=dry_run,
    )


# ----------------------------------------
# PKI PROFILE HELPERS
# ----------------------------------------

def current_pki_profile():
    runtime_pki = load_runtime_pki_profile()
    return runtime_pki["pki"]["profile"]


def self_signed_dir():
    return BASE_DIR / CONFIG["self_signed_dir"]


def hierarchical_dir():
    return BASE_DIR / CONFIG["hierarchical_dir"]


def self_signed_kme_dir():
    return self_signed_dir() / "kme"


def self_signed_root_ca_cert():
    return self_signed_dir() / PKI["SELF_SIGNED_CA_CERT_NAME"]


def self_signed_root_ca_key():
    return self_signed_dir() / PKI["SELF_SIGNED_CA_KEY_NAME"]


def hierarchical_kme_certs_dir():
    return hierarchical_dir() / "kme_pki" / "certs"


def hierarchical_install_on_kme_dir():
    return hierarchical_dir() / "trust_exchange" / "install_on_kme"


# ----------------------------------------
# VALIDATION HELPERS
# ----------------------------------------

def assert_file(path, description):
    path = Path(path)

    if not path.exists():
        print(f"❌ Missing {description}: {path}")
        return False

    if not path.is_file():
        print(f"❌ Expected file for {description}, got: {path}")
        return False

    return True


def assert_dir(path, description):
    path = Path(path)

    if not path.exists():
        print(f"❌ Missing {description}: {path}")
        return False

    if not path.is_dir():
        print(f"❌ Expected directory for {description}, got: {path}")
        return False

    return True


def cert_has_san_ip(cert_path):
    cert_path = Path(cert_path)

    result = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(cert_path),
            "-noout",
            "-text",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        print(f"❌ openssl failed for {repo_path(cert_path)}")
        if result.stderr:
            print(result.stderr.rstrip())
        return False

    text = result.stdout

    if "Subject Alternative Name" not in text:
        print(f"❌ Missing Subject Alternative Name in {repo_path(cert_path)}")
        return False

    if "IP Address:" not in text:
        print(f"❌ Missing SAN IP Address in {repo_path(cert_path)}")
        return False

    return True


def verify_kme_cert_san_ip(files):
    """
    Verify kme_*.crt files include SAN IP Address.
    """

    certs = [
        Path(path)
        for path in files
        if Path(path).name.startswith("kme_") and Path(path).suffix == ".crt"
    ]

    if not certs:
        print("❌ No kme_*.crt files found for SAN validation")
        return False

    ok = True

    for cert in sorted(certs):
        if cert_has_san_ip(cert):
            print(f"✅ SAN OK: {repo_path(cert)}")
        else:
            ok = False

    return ok


# ----------------------------------------
# FILE COLLECTION
# ----------------------------------------

def collect_self_signed_kme_files():
    """
    Collect self-signed KME runtime material generated by qkd_orchestrator.

    Expected:
        certs/self_signed/offbox_rootCA.crt
        certs/self_signed/offbox_rootCA.key
        certs/self_signed/kme/kme_*.crt/key/pem

    Returns:
        list[Path]
    """

    source_dir = self_signed_kme_dir()
    root_ca = self_signed_root_ca_cert()
    root_key = self_signed_root_ca_key()

    if not assert_file(root_ca, "self-signed Root CA certificate"):
        return []

    if not assert_file(root_key, "self-signed Root CA private key"):
        return []

    if not assert_dir(source_dir, "self-signed KME cert directory"):
        return []

    files = [
        root_ca,
        root_key,
    ]

    for src in sorted(source_dir.iterdir()):
        if src.is_file() and src.suffix in [".crt", ".key", ".pem"]:
            files.append(src)

    if len(files) == 2:
        print(f"❌ No self-signed KME cert files found in: {source_dir}")
        return []

    return files


def collect_hierarchical_kme_files():
    """
    Collect hierarchical KME runtime material generated by qkd_orchestrator.

    Expected:
        certs/hierarchical_ca/kme_pki/certs/kme_00x/*
        certs/hierarchical_ca/trust_exchange/install_on_kme/*

    Returns:
        list[Path]
    """

    certs_dir = hierarchical_kme_certs_dir()
    trust_dir = hierarchical_install_on_kme_dir()

    if not assert_dir(certs_dir, "hierarchical KME certs directory"):
        return []

    if not assert_dir(trust_dir, "hierarchical KME trust directory"):
        return []

    files = []

    for dev_dir in sorted(certs_dir.iterdir()):
        if not dev_dir.is_dir():
            continue

        for src in sorted(dev_dir.iterdir()):
            if not src.is_file():
                continue

            if src.name.endswith((".crt", ".key", ".pem", ".chain.crt")):
                files.append(src)

    for src in sorted(trust_dir.iterdir()):
        if src.is_file():
            files.append(src)

    if not files:
        print(
            f"❌ No hierarchical KME cert/trust files found in: "
            f"{certs_dir} and {trust_dir}"
        )
        return []

    return files


def collect_kme_files_for_active_profile():
    profile = current_pki_profile()

    if profile == "self_signed":
        return collect_self_signed_kme_files()

    if profile == "hierarchical_ca":
        return collect_hierarchical_kme_files()

    raise ValueError(f"Unsupported PKI profile: {profile}")


# ----------------------------------------
# CLEAN KME CERT DIRECTORY
# ----------------------------------------

def clean_local_kme_cert_dir(dry_run=False):
    """
    Clean the local ETSI reference implementation cert directory before installing
    current PKI material.

    This prevents mixed generations:
      - old root.crt/root.key
      - old kme_*.crt with new kme_*.key
      - stale csr/srl material
    """

    patterns = [
        "root.crt",
        "root.key",
        "offbox_rootCA.crt",
        "offbox_rootCA.key",
        "kme_*.crt",
        "kme_*.key",
        "kme_*.pem",
        "kme_*.csr",
        "kme_*.srl",
        "*.srl",
    ]

    if dry_run:
        print("")
        print("[DRY-RUN] Would clean local KME cert directory:")
        print(f"  {kme_path(KME_CERT_DEST_DIR)}")

        for pattern in patterns:
            print(f"  pattern: {pattern}")

        print("")
        return True

    KME_CERT_DEST_DIR.mkdir(parents=True, exist_ok=True)

    removed = []

    for pattern in patterns:
        for path in KME_CERT_DEST_DIR.glob(pattern):
            if path.is_file():
                path.unlink()
                removed.append(path.name)

    print("")
    print(f"Cleaned local KME cert directory: {kme_path(KME_CERT_DEST_DIR)}")

    if removed:
        print("Removed old KME cert material:")

        for item in sorted(removed):
            print(f"  - {item}")

    return True


def clean_remote_kme_cert_dir(args, kme_ip, dry_run=False):
    """
    Clean remote KME cert directory before copying current profile certs.
    """

    remote_cmd = (
        f"mkdir -p {KME_CERT_PATH} && "
        f"cd {KME_CERT_PATH} && "
        "rm -f root.crt root.key "
        "offbox_rootCA.crt offbox_rootCA.key "
        "kme_*.crt kme_*.key kme_*.pem "
        "kme_*.csr kme_*.srl *.srl"
    )
    return run(
        ssh_base_cmd(args, kme_ip) + [remote_cmd],
        dry_run=dry_run,
    )

# ----------------------------------------
# LOCAL INSTALL
# ----------------------------------------

def install_file_to_kme_dir(src, dst_name=None, dry_run=False):
    src = Path(src)
    dst = KME_CERT_DEST_DIR / (dst_name or src.name)

    if dry_run:
        print(f"[DRY-RUN] Would install: {repo_path(src)} -> {kme_path(dst)}")

        if dst.suffix in [".key", ".pem"]:
            print(f"[DRY-RUN] Would chmod 600: {kme_path(dst)}")
        else:
            print(f"[DRY-RUN] Would chmod 644: {kme_path(dst)}")

        return dst

    KME_CERT_DEST_DIR.mkdir(parents=True, exist_ok=True)

    shutil.copy2(src, dst)

    if dst.suffix in [".key", ".pem"]:
        dst.chmod(0o600)
    else:
        dst.chmod(0o644)

    print(f"Installed: {kme_path(dst)}")
    return dst


def cert_role_from_name(name):
    if name.endswith(".chain.crt"):
        return "chain"

    if name.endswith(".crt"):
        return "cert"

    if name.endswith(".key"):
        return "key"

    if name.endswith(".pem"):
        return "pem"

    return "file"


def print_self_signed_local_install_plan(files):
    root_ca = self_signed_root_ca_cert()
    root_key = self_signed_root_ca_key()

    print("")
    print("[DRY-RUN] Local KME install plan")
    print("")

    print("Trust/root material:")
    print(f"  root.crt <- {repo_path(root_ca)}")
    print(f"  root.key <- {repo_path(root_key)}")
    print("")

    print("KME leaf certificates:")

    for src in sorted(files):
        src = Path(src)

        if src in [root_ca, root_key]:
            continue

        print(f"  {src.name:<16} <- {repo_path(src)}")

    print("")
    print("Destination:")
    print(f"  {kme_path(KME_CERT_DEST_DIR)}")
    print("")

    print("Permissions:")
    print("  certificates : 644")
    print("  private keys : 600")
    print("  pem files    : 600")
    print("")


def print_hierarchical_local_install_plan(files):
    kme_files = {}
    trust_files = []

    for src in sorted(files):
        src = Path(src)

        if "trust_exchange" in str(src):
            trust_files.append(src)
            continue

        kme_name = src.parent.name
        role = cert_role_from_name(src.name)

        if kme_name not in kme_files:
            kme_files[kme_name] = {}

        kme_files[kme_name][role] = src

    print("")
    print("[DRY-RUN] Local KME install plan")
    print("")

    print("KME leaf certificates:")
    print("")

    for kme_name in sorted(kme_files.keys()):
        item = kme_files[kme_name]

        print(f"  {kme_name}:")

        if "cert" in item:
            print(f"    cert  <- {repo_path(item['cert'])}")

        if "key" in item:
            print(f"    key   <- {repo_path(item['key'])}")

        if "pem" in item:
            print(f"    pem   <- {repo_path(item['pem'])}")

        if "chain" in item:
            print(f"    chain <- {repo_path(item['chain'])}")

        print("")

    print("Trust material for KME:")
    print("")

    bundle = None

    for src in sorted(trust_files):
        src = Path(src)

        if src.name == "trusted-juniper-ca-bundle.crt":
            label = "trusted bundle"
            bundle = src

        elif src.name == "juniper-root-ca.crt":
            label = "juniper root"

        elif src.name == "juniper-issuing-ca.crt":
            label = "juniper issuing"

        else:
            label = "trust file"

        print(f"  {label:<15} <- {repo_path(src)}")

    print("")

    if bundle:
        print("Runtime compatibility alias:")
        print(f"  root.crt <- {repo_path(bundle)}")
        print("")

    print("Destination:")
    print(f"  {kme_path(KME_CERT_DEST_DIR)}")
    print("")

    print("Permissions:")
    print("  certificates : 644")
    print("  private keys : 600")
    print("  pem files    : 600")
    print("")


def install_hierarchical_root_alias(dry_run=False):
    """
    For hierarchical_ca runtime compatibility.

    The ETSI reference implementation expects a root.crt trust file.
    In hierarchical_ca mode, root.crt should be the Juniper trust bundle
    used by the KME to validate Juniper/SAE client certificates.

    Source:
        trusted-juniper-ca-bundle.crt

    Destination:
        root.crt
    """

    bundle = KME_CERT_DEST_DIR / "trusted-juniper-ca-bundle.crt"
    root_crt = KME_CERT_DEST_DIR / "root.crt"

    if dry_run:
        print(
            f"[DRY-RUN] Would create root.crt alias: "
            f"{kme_path(bundle)} -> {kme_path(root_crt)}"
        )
        print(f"[DRY-RUN] Would chmod 644: {kme_path(root_crt)}")
        return True

    if not bundle.exists():
        print(f"Missing trust bundle for root.crt alias: {kme_path(bundle)}")
        return False

    shutil.copy2(bundle, root_crt)
    root_crt.chmod(0o644)

    print(f"Installed root.crt alias: {kme_path(root_crt)}")
    return True


def install_self_signed_local_kme_certs(dry_run=False):
    print("\n=== Installing self-signed KME certificates ===")

    files = collect_self_signed_kme_files()

    if not files:
        return False

    if not verify_kme_cert_san_ip(files):
        return False

    if dry_run:
        print_self_signed_local_install_plan(files)
        print("[DRY-RUN] Self-signed local KME install plan completed")
        return True

    root_ca = self_signed_root_ca_cert()
    root_key = self_signed_root_ca_key()

    install_file_to_kme_dir(
        root_ca,
        dst_name="root.crt",
        dry_run=False,
    )

    install_file_to_kme_dir(
        root_key,
        dst_name="root.key",
        dry_run=False,
    )

    for src in files:
        if src in [root_ca, root_key]:
            continue

        install_file_to_kme_dir(
            src,
            dry_run=False,
        )

    print("✅ Self-signed KME certificate installation completed")
    return True


def install_hierarchical_local_kme_certs(dry_run=False):
    print("\n=== Installing hierarchical CA KME certificates ===")

    files = collect_hierarchical_kme_files()

    if not files:
        return False

    if not verify_kme_cert_san_ip(files):
        return False

    if dry_run:
        print_hierarchical_local_install_plan(files)
        print("[DRY-RUN] Hierarchical local KME install plan completed")
        return True

    for src in files:
        install_file_to_kme_dir(
            src,
            dry_run=False,
        )

    if not install_hierarchical_root_alias(dry_run=False):
        return False

    print("✅ Hierarchical KME certificate installation completed")
    return True


def install_local_kme_certs(dry_run=False):
    profile = current_pki_profile()

    if not clean_local_kme_cert_dir(dry_run=dry_run):
        return False

    if profile == "self_signed":
        return install_self_signed_local_kme_certs(dry_run=dry_run)

    if profile == "hierarchical_ca":
        return install_hierarchical_local_kme_certs(dry_run=dry_run)

    raise ValueError(f"Unsupported PKI profile: {profile}")

##################

# ----------------------------------------
# REMOTE COPY
# ----------------------------------------

def create_remote_hierarchical_root_alias(args, kme_ip, dry_run=False):
    """
    Create root.crt on the remote KME host as a copy of
    trusted-juniper-ca-bundle.crt.
    """

    remote_cmd = (
        f"cd {KME_CERT_PATH} && "
        "cp trusted-juniper-ca-bundle.crt root.crt && "
        "chmod 644 root.crt"
    )

    return run(
        ssh_base_cmd(args, kme_ip) + [remote_cmd],
        dry_run=dry_run,
    )


def ensure_remote_kme_cert_dir(args, kme_ip, dry_run=False):
    return run(ssh_base_cmd(args, kme_ip) + [f"mkdir -p {KME_CERT_PATH}"],dry_run=dry_run)


def copy_files_to_remote_kme(args, kme_ip, files, dry_run=False):
    if not files:
        print("❌ No files to copy")
        return False

    if not ensure_remote_kme_cert_dir(
        args,
        kme_ip,
        dry_run=dry_run,
    ):
        print("❌ Failed to create remote KME cert directory")
        return False

    if not clean_remote_kme_cert_dir(
        args,
        kme_ip,
        dry_run=dry_run,
    ):
        print("❌ Failed to clean remote KME cert directory")
        return False

    return run(
        scp_base_cmd(args)
        + [str(src) for src in files]
        + [f"{args.ssh_user}@{kme_ip}:{KME_CERT_PATH}/"],
        dry_run=dry_run,
    )


def copy_self_signed_to_remote_kme(args, kme_ip, dry_run=False):
    print(f"\n=== Copying self-signed KME certificates to remote KME {kme_ip} ===")

    files = collect_self_signed_kme_files()

    if not files:
        return False

    if not verify_kme_cert_san_ip(files):
        return False

    if not copy_files_to_remote_kme(args, kme_ip, files, dry_run=dry_run):
        print("⚠️ Self-signed KME cert copy failed")
        return False

    if dry_run:
        print("[DRY-RUN] Self-signed KME certificate copy plan completed")
        return True

    root_ca_name = PKI["SELF_SIGNED_CA_CERT_NAME"]
    root_key_name = PKI["SELF_SIGNED_CA_KEY_NAME"]

    remote_cmd = (
        f"cd {KME_CERT_PATH} && "
        f"cp {root_ca_name} root.crt && "
        f"cp {root_key_name} root.key && "
        "chmod 644 root.crt && "
        "chmod 600 root.key"
    )

    if not run(
        ssh_base_cmd(args, kme_ip) + [remote_cmd],
        dry_run=dry_run,
    ):
        print("⚠️ Root CA staging as root.crt/root.key failed")
        return False

    print("✅ Self-signed KME certificates copied")
    return True


def copy_hierarchical_to_remote_kme(args, kme_ip, dry_run=False):
    print(f"\n=== Copying hierarchical CA KME certificates to remote KME {kme_ip} ===")

    files = collect_hierarchical_kme_files()

    if not files:
        return False

    if not verify_kme_cert_san_ip(files):
        return False

    if not copy_files_to_remote_kme(args, kme_ip, files, dry_run=dry_run):
        print("⚠️ Hierarchical KME cert copy failed")
        return False

    if not create_remote_hierarchical_root_alias(args, kme_ip, dry_run=dry_run):
        print("⚠️ Hierarchical root.crt alias creation failed")
        return False

    if dry_run:
        print("[DRY-RUN] Hierarchical KME certificate copy plan completed")
    else:
        print("✅ Hierarchical KME certificates copied")

    return True


def copy_to_kme(args, kme_ip, dry_run=False):
    profile = current_pki_profile()

    if profile == "self_signed":
        return copy_self_signed_to_remote_kme(
            args,
            kme_ip,
            dry_run=dry_run,
        )

    if profile == "hierarchical_ca":
        return copy_hierarchical_to_remote_kme(
            args,
            kme_ip,
            dry_run=dry_run,
        )

    raise ValueError(f"Unsupported PKI profile: {profile}")


# ----------------------------------------
# RESTART LOCAL KME CONTAINERS
# ----------------------------------------

def restart_local_kme_containers(dry_run=False):
    print("\n=== Restarting local KME containers ===")

    cmd = (
        "docker ps --format '{{.Names}}' "
        "| grep -i kme "
        "| xargs -r docker restart"
    )

    success = run(
        [
            "sh",
            "-c",
            cmd,
        ],
        dry_run=dry_run,
    )

    if success:
        if dry_run:
            print("[DRY-RUN] Local KME restart plan completed")
        else:
            print("✅ Local KME containers restarted")
    else:
        print("⚠️ Local KME container restart failed")

    return success


# ----------------------------------------
# RESTART REMOTE KME
# ----------------------------------------

def restart_kme(args, kme_ip, reachable, dry_run=False):
    if not reachable:
        print("⚠️ Skipping restart -> KME unreachable")
        return False

    print("\n=== Restart remote KME ===")

    remote_cmd = (
        "cd /root/etsi-gs-qkd-014-referenceimplementation && "
        "docker compose -f docker-compose-kme.yml down -v && "
        "docker compose -f docker-compose-kme.yml up -d"
    )

    success = run(
        ssh_base_cmd(args, kme_ip) + [remote_cmd],
        dry_run=dry_run,
    )
    
    if success:
        if dry_run:
            print("[DRY-RUN] Remote KME restart plan completed")
        else:
            print("✅ Restart done")
    else:
        print("⚠️ Restart failed")

    return success


# ----------------------------------------
# INIT DB
# ----------------------------------------

def init_db(args, kme_ip, reachable, dry_run=False):
    if not reachable:
        print("⚠️ Skipping DB init -> KME unreachable")
        return False

    print("\n=== Init DB ===")

    schema = """
CREATE TABLE IF NOT EXISTS keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content BYTEA NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
"""

    success = True

    for db in ["postgres-kme1", "postgres-kme2"]:
        remote_cmd = (
            f"echo \"{schema}\" | "
            f"docker exec -i {db} psql -U db_user key_store"
        )

        ok = run(
            ssh_base_cmd(args, kme_ip) + [remote_cmd],
            dry_run=dry_run,
        )
        success = success and ok

    if success:
        print("✅ DB ready")
    else:
        print("⚠️ DB init failed")

    return success


# ----------------------------------------
# MAIN
# ----------------------------------------

def main():
    args = parse_args()

    if args.project_dir:
        global KME_PROJECT_DIR, KME_CERT_DEST_DIR, KME_CERT_PATH

        KME_PROJECT_DIR = Path(args.project_dir).expanduser().resolve()
        KME_CERT_DEST_DIR = KME_PROJECT_DIR / "certs"
        KME_CERT_PATH = str(KME_CERT_DEST_DIR)

    profile = current_pki_profile()

    if args.dry_run:
        print("DRY-RUN mode enabled: no files will be copied and no commands will be executed.")

    print("=== KME orchestrator ===")
    print(f"PKI profile              : {profile}")
    print(f"KME project directory    : {kme_path(KME_PROJECT_DIR)}")
    print(f"KME cert destination     : {kme_path(KME_CERT_DEST_DIR)}")

    if not args.kme_ip:
        print("\n=== LOCAL MODE ===")
        print("No --kme-ip provided. No remote SSH or SCP will be attempted.")

        if not install_local_kme_certs(dry_run=args.dry_run):
            print("Local KME certificate installation failed")
            return

        if args.restart:
            restart_local_kme_containers(dry_run=args.dry_run)

        return

    print("\n=== REMOTE MODE ===")

    kme_ip = args.kme_ip
    reachable = is_kme_reachable(kme_ip, dry_run=args.dry_run)

    if not reachable:
        print("❌ KME unreachable by ping")
        return

    if not copy_to_kme(args, kme_ip, dry_run=args.dry_run):
        print("❌ Remote KME certificate copy failed")
        return

    if args.restart:
        restart_kme(args, kme_ip, reachable=True, dry_run=args.dry_run)

    if args.init_db:
        init_db(args, kme_ip, reachable=True, dry_run=args.dry_run)


if __name__ == "__main__":
    main()