import subprocess
from pathlib import Path
from settings import PKI
import argparse
import shutil

BASE_DIR = Path(__file__).resolve().parent
CERTS_DIR = BASE_DIR / "certs"

KME_PROJECT_DIR = Path("/root/etsi-gs-qkd-014-referenceimplementation")
KME_CERT_DEST_DIR = KME_PROJECT_DIR / "certs"
KME_CERT_PATH = str(KME_CERT_DEST_DIR)

LOCAL_KME_DIR = CERTS_DIR / "kme"
LOCAL_KME_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------
# CLI
# ----------------------------------------
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kme-ip", required=False, default=None)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--num-kme", type=int, default=2)
    return parser.parse_args()


# ----------------------------------------
# RUN CMD
# ----------------------------------------
def run(cmd):
    print(f"→ {' '.join(cmd)}")
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ ERROR:\n{e.stderr}")
        return False


# ----------------------------------------
# REACHABILITY CHECK
# ----------------------------------------
def is_kme_reachable(kme_ip):
    return run(["ping", "-c", "1", kme_ip])


# ----------------------------------------
# COPY CERTS
# ----------------------------------------
def copy_to_kme(kme_ip):

    print(f"\n=== Copying Root CA to KME {kme_ip} ===")

    files = [
        str(CERTS_DIR / PKI["CA_CERT_NAME"]),
        str(CERTS_DIR / PKI["CA_KEY_NAME"]),
    ]

    if not run(["scp"] + files + [f"root@{kme_ip}:{KME_CERT_PATH}/"]):
        print("⚠️ Root CA copy failed")
        return False

    if not run([
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        f"root@{kme_ip}",
        f"cd {KME_CERT_PATH} && "
        f"mv {PKI['CA_CERT_NAME']} root.crt && "
        f"mv {PKI['CA_KEY_NAME']} root.key"
    ]):
        print("⚠️ Root CA rename failed")
        return False

    print("✅ Root CA copied")
    return True

# ----------------------------------------
# BUILD REMOTE
# ----------------------------------------
def build_kme_certs(kme_ip, num_kme, reachable):

    if not reachable:
        print("⚠️ Skipping remote build → KME unreachable")
        build_kme_certs_local(num_kme)
        return

    print("\n=== Building KME certs on remote ===")

    script = f"""
set -e
cd {KME_CERT_PATH}

rm -f kme_*.crt kme_*.csr kme_*.key kme_*.ext kme_*.pem

build_kme() {{
    NAME=$1
    IP=$2

    cat > ${{NAME}}.ext <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:${{IP}},DNS:${{NAME}}
EOF

    openssl req -newkey rsa:4096 -nodes \\
        -keyout ${{NAME}}.key \\
        -out ${{NAME}}.csr \\
        -subj "/C=IT/O=Juniper Networks/CN=${{IP}}"

    openssl x509 -req \\
        -in ${{NAME}}.csr \\
        -CA root.crt \\
        -CAkey root.key \\
        -CAcreateserial \\
        -days 365 \\
        -extfile ${{NAME}}.ext \\
        -out ${{NAME}}.crt

    cat ${{NAME}}.key ${{NAME}}.crt > ${{NAME}}.pem
}}

BASE_IP=100.123.252

for i in $(seq 1 {num_kme}); do
    IDX=$(printf "%03d" $i)
    IP_SUFFIX=$((9 + i))
    IP=${{BASE_IP}}.${{IP_SUFFIX}}

    echo ">>> REMOTE kme_${{IDX}} ($IP)"
    build_kme kme_${{IDX}} $IP
done
"""

    success = run([
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        f"root@{kme_ip}",
        script
    ])

    if not success:
        print("⚠️ Remote build FAILED → switching to LOCAL fallback")
        build_kme_certs_local(num_kme)


# ----------------------------------------
# BUILD LOCAL
# ----------------------------------------
def build_kme_certs_local(num_kme):

    print(f"\n=== FALLBACK: building {num_kme} KME certs locally ===")

    root_crt = CERTS_DIR / PKI["CA_CERT_NAME"]
    root_key = CERTS_DIR / PKI["CA_KEY_NAME"]

    BASE_IP = "100.123.252"

    for i in range(1, num_kme + 1):
        idx = f"{i:03d}"
        ip = f"{BASE_IP}.{9 + i}"

        name = f"kme_{idx}"

        key = LOCAL_KME_DIR / f"{name}.key"
        csr = LOCAL_KME_DIR / f"{name}.csr"
        crt = LOCAL_KME_DIR / f"{name}.crt"
        pem = LOCAL_KME_DIR / f"{name}.pem"
        ext = LOCAL_KME_DIR / f"{name}.ext"

        print(f">>> LOCAL {name} ({ip})")

        with open(ext, "w") as f:
            f.write(f"""basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:{ip},DNS:{name}
""")

        run([
            "openssl", "req",
            "-newkey", "rsa:4096",
            "-nodes",
            "-keyout", str(key),
            "-out", str(csr),
            "-subj", f"/C=IT/O=Juniper Networks/CN={ip}"
        ])

        run([
            "openssl", "x509",
            "-req",
            "-in", str(csr),
            "-CA", str(root_crt),
            "-CAkey", str(root_key),
            "-CAcreateserial",
            "-days", "365",
            "-extfile", str(ext),
            "-out", str(crt)
        ])

        with open(pem, "wb") as out:
            out.write(open(key, "rb").read())
            out.write(open(crt, "rb").read())

    print(f"✅ Local certs in: {LOCAL_KME_DIR}")

# ----------------------------------------
# INSTALL LOCAL KME CERTS
# ----------------------------------------
def install_local_kme_certs():

    print("\n=== Installing local KME certificates ===")

    root_ca_crt = CERTS_DIR / PKI["CA_CERT_NAME"]
    root_ca_key = CERTS_DIR / PKI["CA_KEY_NAME"]

    if not root_ca_crt.exists():
        print(f"❌ Missing Root CA certificate: {root_ca_crt}")
        return False

    if not root_ca_key.exists():
        print(f"❌ Missing Root CA private key: {root_ca_key}")
        return False

    if not LOCAL_KME_DIR.exists():
        print(f"❌ Missing local KME certificate directory: {LOCAL_KME_DIR}")
        return False

    # Copy Root CA into the local KME staging directory using the names
    # expected by the ETSI GS QKD 014 reference implementation.
    staged_root_crt = LOCAL_KME_DIR / "root.crt"
    staged_root_key = LOCAL_KME_DIR / "root.key"

    shutil.copy2(root_ca_crt, staged_root_crt)
    shutil.copy2(root_ca_key, staged_root_key)

    staged_root_crt.chmod(0o644)
    staged_root_key.chmod(0o600)

    print(f"✅ Staged Root CA certificate: {staged_root_crt}")
    print(f"✅ Staged Root CA private key : {staged_root_key}")

    # Ensure the reference implementation cert directory exists.
    KME_CERT_DEST_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Installing certificates into: {KME_CERT_DEST_DIR}")

    # Copy all generated KME cert material into the reference implementation.
    for src in sorted(LOCAL_KME_DIR.iterdir()):

        if not src.is_file():
            continue

        dst = KME_CERT_DEST_DIR / src.name

        shutil.copy2(src, dst)

        if dst.suffix == ".key":
            dst.chmod(0o600)
        elif dst.suffix == ".pem":
            dst.chmod(0o600)
        elif dst.suffix == ".crt":
            dst.chmod(0o644)
        else:
            dst.chmod(0o644)

        print(f"✅ Installed: {dst}")

    print("✅ Local KME certificate installation completed")
    return True

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

    success = run([
        "sh",
        "-c",
        cmd
    ])

    if success:
        print("✅ Local KME containers restarted")
    else:
        print("⚠️ Local KME container restart failed")

    return success

# ----------------------------------------
# RESTART
# ----------------------------------------
def restart_kme(kme_ip, reachable):

    if not reachable:
        print("⚠️ Skipping restart → KME unreachable")
        return

    print("\n=== Restart KME ===")

    success = run([
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        f"root@{kme_ip}",
        "cd /root/etsi-gs-qkd-014-referenceimplementation && "
        "docker compose -f docker-compose-kme.yml down -v && "
        "docker compose -f docker-compose-kme.yml up -d"
    ])

    if success:
        print("✅ Restart done")
    else:
        print("⚠️ Restart failed")


# ----------------------------------------
# INIT DB
# ----------------------------------------
def init_db(kme_ip, reachable):

    if not reachable:
        print("⚠️ Skipping DB init → KME unreachable")
        return

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

    for db in ["postgres-kme1", "postgres-kme2"]:
        run([
            "ssh",
            "-o", "BatchMode=yes",
            f"root@{kme_ip}",
            f"echo \"{schema}\" | docker exec -i {db} psql -U db_user key_store"
        ])

    print("✅ DB ready")


# ----------------------------------------
# MAIN
# ----------------------------------------
def main():

    args = parse_args()

    # ----------------------------------------
    # LOCAL ONLY MODE
    # ----------------------------------------
    if not args.kme_ip:
        print("=== LOCAL MODE ===")
        print("No --kme-ip provided.")
        print(f"Building {args.num_kme} KME certificates locally.")
        print(f"Local KME project directory : {KME_PROJECT_DIR}")
        print(f"Local KME cert destination : {KME_CERT_DEST_DIR}")
        print("No remote SSH, SCP, or remote DB initialization will be attempted.")

        build_kme_certs_local(args.num_kme)

        if not install_local_kme_certs():
            print("❌ Local KME certificate installation failed")
            return

        if args.restart:
            restart_local_kme_containers()

        return

    # ----------------------------------------
    # REMOTE MODE
    # ----------------------------------------
    kme_ip = args.kme_ip

    reachable = is_kme_reachable(kme_ip)

    if not reachable:
        print("⚠️ KME unreachable by ping → LOCAL FALLBACK")
        build_kme_certs_local(args.num_kme)
        return

    copied = copy_to_kme(kme_ip)

    if copied:
        build_kme_certs(kme_ip, args.num_kme, reachable=True)
    else:
        print("⚠️ Remote copy failed → LOCAL FALLBACK")
        build_kme_certs_local(args.num_kme)
        return

    if args.restart:
        restart_kme(kme_ip, reachable=True)
        init_db(kme_ip, reachable=True)


if __name__ == "__main__":
    main()