#!/usr/bin/env python3
"""
kme_orchestrator.py

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

from lib.settings import CONFIG, PKI
from lib.config import load_runtime_pki_profile


# ----------------------------------------
# PATHS
# ----------------------------------------

BASE_DIR = Path(__file__).resolve().parent

KME_PROJECT_DIR = Path.home() / "kme-lab" / "etsi-gs-qkd-014-referenceimplementation"
KME_CERT_DEST_DIR = KME_PROJECT_DIR / "certs"
KME_CERT_PATH = str(KME_CERT_DEST_DIR)


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

    return parser.parse_args()


# ----------------------------------------
# COMMAND HELPERS
# ----------------------------------------

def run(cmd):
    print(f"→ {' '.join(str(x) for x in cmd)}")

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
            print(f"❌ ERROR:\n{exc.stderr.rstrip()}")
        else:
            print(f"❌ ERROR: command failed with rc={exc.returncode}")
        return False


def is_kme_reachable(kme_ip):
    return run(["ping", "-c", "1", kme_ip])


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


def hierarchical_kme_certs_dir():
    return hierarchical_dir() / "kme_pki" / "certs"


def hierarchical_install_on_kme_dir():
    return hierarchical_dir() / "trust_exchange" / "install_on_kme"


# ----------------------------------------
# FILE COLLECTION
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


def collect_self_signed_kme_files():
    """
    Collect self-signed KME runtime material generated by qkd_orchestrator.

    The KME orchestrator does not generate certificates. It expects:
        certs/self_signed/offbox_rootCA.crt
        certs/self_signed/kme/kme_*.crt/key/pem

    Returns:
        list[Path]
    """

    source_dir = self_signed_kme_dir()
    root_ca = self_signed_root_ca_cert()

    if not assert_file(root_ca, "self-signed Root CA certificate"):
        return []

    if not assert_dir(source_dir, "self-signed KME cert directory"):
        return []

    files = [root_ca]

    for src in sorted(source_dir.iterdir()):
        if src.is_file() and src.suffix in [".crt", ".key", ".pem"]:
            files.append(src)

    if len(files) == 1:
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
        print(f"❌ No hierarchical KME cert/trust files found in: {certs_dir} and {trust_dir}")
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
# INSTALL LOCAL KME CERTS
# ----------------------------------------

def install_file_to_kme_dir(src, dst_name=None):
    KME_CERT_DEST_DIR.mkdir(parents=True, exist_ok=True)

    src = Path(src)
    dst = KME_CERT_DEST_DIR / (dst_name or src.name)

    shutil.copy2(src, dst)

    if dst.suffix in [".key", ".pem"]:
        dst.chmod(0o600)
    else:
        dst.chmod(0o644)

    print(f"✅ Installed: {dst}")
    return dst


def install_self_signed_local_kme_certs():
    print("\n=== Installing self-signed KME certificates ===")

    files = collect_self_signed_kme_files()

    if not files:
        return False

    root_ca = self_signed_root_ca_cert()

    # Install Root CA cert as root.crt for the reference implementation.
    install_file_to_kme_dir(root_ca, dst_name="root.crt")

    for src in files:
        if src == root_ca:
            continue
        install_file_to_kme_dir(src)

    print("✅ Self-signed KME certificate installation completed")
    return True


def install_hierarchical_local_kme_certs():
    print("\n=== Installing hierarchical CA KME certificates ===")

    files = collect_hierarchical_kme_files()

    if not files:
        return False

    for src in files:
        install_file_to_kme_dir(src)

    print("✅ Hierarchical KME certificate installation completed")
    return True


def install_local_kme_certs():
    profile = current_pki_profile()

    if profile == "self_signed":
        return install_self_signed_local_kme_certs()

    if profile == "hierarchical_ca":
        return install_hierarchical_local_kme_certs()

    raise ValueError(f"Unsupported PKI profile: {profile}")


# ----------------------------------------
# COPY CERTS TO REMOTE KME
# ----------------------------------------

def ensure_remote_kme_cert_dir(kme_ip):
    return run([
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        f"root@{kme_ip}",
        f"mkdir -p {KME_CERT_PATH}",
    ])


def copy_files_to_remote_kme(kme_ip, files):
    if not files:
        print("❌ No files to copy")
        return False

    if not ensure_remote_kme_cert_dir(kme_ip):
        print("❌ Failed to create remote KME cert directory")
        return False

    return run(["scp"] + [str(src) for src in files] + [f"root@{kme_ip}:{KME_CERT_PATH}/"])


def copy_self_signed_to_remote_kme(kme_ip):
    print(f"\n=== Copying self-signed KME certificates to remote KME {kme_ip} ===")

    files = collect_self_signed_kme_files()

    if not files:
        return False

    if not copy_files_to_remote_kme(kme_ip, files):
        print("⚠️ Self-signed KME cert copy failed")
        return False

    root_ca_name = PKI["SELF_SIGNED_CA_CERT_NAME"]

    # The reference implementation expects root.crt.
    if not run([
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        f"root@{kme_ip}",
        f"cd {KME_CERT_PATH} && cp {root_ca_name} root.crt",
    ]):
        print("⚠️ Root CA staging as root.crt failed")
        return False

    print("✅ Self-signed KME certificates copied")
    return True


def copy_hierarchical_to_remote_kme(kme_ip):
    print(f"\n=== Copying hierarchical CA KME certificates to remote KME {kme_ip} ===")

    files = collect_hierarchical_kme_files()

    if not files:
        return False

    if not copy_files_to_remote_kme(kme_ip, files):
        print("⚠️ Hierarchical KME cert copy failed")
        return False

    print("✅ Hierarchical KME certificates copied")
    return True


def copy_to_kme(kme_ip):
    profile = current_pki_profile()

    if profile == "self_signed":
        return copy_self_signed_to_remote_kme(kme_ip)

    if profile == "hierarchical_ca":
        return copy_hierarchical_to_remote_kme(kme_ip)

    raise ValueError(f"Unsupported PKI profile: {profile}")


# ----------------------------------------
# RESTART LOCAL KME CONTAINERS
# ----------------------------------------

def restart_local_kme_containers():
    print("\n=== Restarting local KME containers ===")

    cmd = (
        "docker ps --format '{{.Names}}' "
        "| grep -i kme "
        "| xargs -r docker restart"
    )

    success = run(["sh", "-c", cmd])

    if success:
        print("✅ Local KME containers restarted")
    else:
        print("⚠️ Local KME container restart failed")

    return success


# ----------------------------------------
# RESTART REMOTE KME
# ----------------------------------------

def restart_kme(kme_ip, reachable):
    if not reachable:
        print("⚠️ Skipping restart → KME unreachable")
        return False

    print("\n=== Restart remote KME ===")

    success = run([
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        f"root@{kme_ip}",
        "cd /root/etsi-gs-qkd-014-referenceimplementation && "
        "docker compose -f docker-compose-kme.yml down -v && "
        "docker compose -f docker-compose-kme.yml up -d",
    ])

    if success:
        print("✅ Restart done")
    else:
        print("⚠️ Restart failed")

    return success


# ----------------------------------------
# INIT DB
# ----------------------------------------

def init_db(kme_ip, reachable):
    if not reachable:
        print("⚠️ Skipping DB init → KME unreachable")
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
        ok = run([
            "ssh",
            "-o", "BatchMode=yes",
            f"root@{kme_ip}",
            f"echo \"{schema}\" | docker exec -i {db} psql -U db_user key_store",
        ])
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

    print(f"=== KME orchestrator ===")
    print(f"PKI profile              : {profile}")
    print(f"KME project directory    : {KME_PROJECT_DIR}")
    print(f"KME cert destination     : {KME_CERT_DEST_DIR}")

    # ----------------------------------------
    # LOCAL MODE
    # ----------------------------------------
    if not args.kme_ip:
        print("\n=== LOCAL MODE ===")
        print("No --kme-ip provided. No remote SSH or SCP will be attempted.")

        if not install_local_kme_certs():
            print("❌ Local KME certificate installation failed")
            return

        if args.restart:
            restart_local_kme_containers()

        return

    # ----------------------------------------
    # REMOTE MODE
    # ----------------------------------------
    print("\n=== REMOTE MODE ===")

    kme_ip = args.kme_ip
    reachable = is_kme_reachable(kme_ip)

    if not reachable:
        print("❌ KME unreachable by ping")
        return

    if not copy_to_kme(kme_ip):
        print("❌ Remote KME certificate copy failed")
        return

    if args.restart:
        restart_kme(kme_ip, reachable=True)

    if args.init_db:
        init_db(kme_ip, reachable=True)


if __name__ == "__main__":
    main()
