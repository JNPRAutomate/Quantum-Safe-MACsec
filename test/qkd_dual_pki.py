#!/usr/bin/env python3

import argparse
import shutil
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

PKI_DIR = BASE_DIR / "certs" / "dual_pki"

KME_PKI_DIR = PKI_DIR / "kme_pki"
KME_ROOT_DIR = KME_PKI_DIR / "root_ca"
KME_ISSUING_DIR = KME_PKI_DIR / "issuing_ca"
KME_CERTS_DIR = KME_PKI_DIR / "certs"

JUNIPER_PKI_DIR = PKI_DIR / "juniper_pki"
JUNIPER_ROOT_DIR = JUNIPER_PKI_DIR / "root_ca"
JUNIPER_ISSUING_DIR = JUNIPER_PKI_DIR / "issuing_ca"
JUNIPER_CERTS_DIR = JUNIPER_PKI_DIR / "certs"

TRUST_EXCHANGE_DIR = PKI_DIR / "trust_exchange"

KME_TRUSTED_JUNIPER_CA_DIR = TRUST_EXCHANGE_DIR / "install_on_kme"
JUNIPER_TRUSTED_KME_CA_DIR = TRUST_EXCHANGE_DIR / "install_on_juniper"


KME_DEVICES = [
    {
        "name": "kme_001",
        "ip": "100.100.100.10",
        "dns": ["kme_001", "kme1", "localhost"],
    },
    {
        "name": "kme_002",
        "ip": "100.100.100.11",
        "dns": ["kme_002", "kme2", "localhost"],
    },
]


JUNIPER_DEVICES = [
    {
        "name": "vqfx-1",
        "ip": "100.123.252.101",
        "dns": ["vqfx-1"],
    },
    {
        "name": "vqfx-2",
        "ip": "100.123.252.102",
        "dns": ["vqfx-2"],
    },
]


def run(cmd):
    print(f"→ {' '.join(str(x) for x in cmd)}")
    try:
        subprocess.run(
            [str(x) for x in cmd],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print("❌ Command failed")
        if e.stdout:
            print(e.stdout)
        if e.stderr:
            print(e.stderr)
        return False


def ensure_dirs():
    for directory in [
        KME_ROOT_DIR,
        KME_ISSUING_DIR,
        KME_CERTS_DIR,
        JUNIPER_ROOT_DIR,
        JUNIPER_ISSUING_DIR,
        JUNIPER_CERTS_DIR,
        KME_TRUSTED_JUNIPER_CA_DIR,
        JUNIPER_TRUSTED_KME_CA_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def write_file(path, data):
    path.write_text(data)
    print(f"✅ Written: {path}")


def make_san_line(ip, dns_names):
    san_entries = [f"IP:{ip}"]
    for dns in dns_names:
        san_entries.append(f"DNS:{dns}")
    return ",".join(san_entries)


def create_root_ca(name, out_dir, days):
    root_key = out_dir / f"{name}-root-ca.key"
    root_crt = out_dir / f"{name}-root-ca.crt"
    root_ext = out_dir / f"{name}-root-ca.ext"

    if root_key.exists() and root_crt.exists():
        print(f"✅ Existing Root CA found: {root_crt}")
        return root_key, root_crt

    write_file(
        root_ext,
        """basicConstraints=critical,CA:TRUE,pathlen:1
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always,issuer
""",
    )

    if not run(["openssl", "genrsa", "-out", root_key, "4096"]):
        raise RuntimeError("Root CA key generation failed")

    if not run(
        [
            "openssl",
            "req",
            "-x509",
            "-new",
            "-nodes",
            "-key",
            root_key,
            "-sha256",
            "-days",
            str(days),
            "-out",
            root_crt,
            "-subj",
            f"/C=IT/O=Juniper Networks Lab/CN={name} Root CA",
            "-extfile",
            root_ext,
        ]
    ):
        raise RuntimeError("Root CA certificate generation failed")

    root_key.chmod(0o600)
    root_crt.chmod(0o644)

    print(f"✅ Created Root CA: {root_crt}")
    return root_key, root_crt


def create_issuing_ca(name, out_dir, root_key, root_crt, days):
    issuing_key = out_dir / f"{name}-issuing-ca.key"
    issuing_csr = out_dir / f"{name}-issuing-ca.csr"
    issuing_crt = out_dir / f"{name}-issuing-ca.crt"
    issuing_ext = out_dir / f"{name}-issuing-ca.ext"
    chain_crt = out_dir / f"{name}-ca-chain.crt"

    if issuing_key.exists() and issuing_crt.exists() and chain_crt.exists():
        print(f"✅ Existing Issuing CA found: {issuing_crt}")
        return issuing_key, issuing_crt, chain_crt

    write_file(
        issuing_ext,
        """basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
""",
    )

    if not run(["openssl", "genrsa", "-out", issuing_key, "4096"]):
        raise RuntimeError("Issuing CA key generation failed")

    if not run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            issuing_key,
            "-out",
            issuing_csr,
            "-subj",
            f"/C=IT/O=Juniper Networks Lab/CN={name} Issuing CA",
        ]
    ):
        raise RuntimeError("Issuing CA CSR generation failed")

    if not run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            issuing_csr,
            "-CA",
            root_crt,
            "-CAkey",
            root_key,
            "-CAcreateserial",
            "-out",
            issuing_crt,
            "-days",
            str(days),
            "-sha256",
            "-extfile",
            issuing_ext,
        ]
    ):
        raise RuntimeError("Issuing CA signing failed")

    with open(chain_crt, "wb") as out:
        out.write(issuing_crt.read_bytes())
        out.write(root_crt.read_bytes())

    issuing_key.chmod(0o600)
    issuing_crt.chmod(0o644)
    chain_crt.chmod(0o644)

    print(f"✅ Created Issuing CA: {issuing_crt}")
    print(f"✅ Created CA chain  : {chain_crt}")

    return issuing_key, issuing_crt, chain_crt


def create_leaf_cert(
    pki_name,
    device,
    out_dir,
    issuing_key,
    issuing_crt,
    ca_chain_crt,
    days,
):
    name = device["name"]
    ip = device["ip"]
    dns_names = device.get("dns", [])

    key = out_dir / f"{name}.key"
    csr = out_dir / f"{name}.csr"
    crt = out_dir / f"{name}.crt"
    ext = out_dir / f"{name}.ext"
    chain = out_dir / f"{name}.chain.crt"
    pem = out_dir / f"{name}.pem"

    if key.exists() and crt.exists() and chain.exists() and pem.exists():
        print(f"✅ Existing leaf cert found: {crt}")
        return key, crt, chain, pem

    san_line = make_san_line(ip, dns_names)

    write_file(
        ext,
        f"""basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
subjectAltName={san_line}
""",
    )

    if not run(["openssl", "genrsa", "-out", key, "4096"]):
        raise RuntimeError(f"{name} key generation failed")

    if not run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            key,
            "-out",
            csr,
            "-subj",
            f"/C=IT/O=Juniper Networks Lab/CN={name}",
        ]
    ):
        raise RuntimeError(f"{name} CSR generation failed")

    if not run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            csr,
            "-CA",
            issuing_crt,
            "-CAkey",
            issuing_key,
            "-CAcreateserial",
            "-out",
            crt,
            "-days",
            str(days),
            "-sha256",
            "-extfile",
            ext,
        ]
    ):
        raise RuntimeError(f"{name} certificate signing failed")

    with open(chain, "wb") as out:
        out.write(crt.read_bytes())
        out.write(ca_chain_crt.read_bytes())

    with open(pem, "wb") as out:
        out.write(key.read_bytes())
        out.write(chain.read_bytes())

    key.chmod(0o600)
    crt.chmod(0o644)
    chain.chmod(0o644)
    pem.chmod(0o600)

    print(f"✅ Created {pki_name} leaf cert : {crt}")
    print(f"✅ Created {pki_name} chain     : {chain}")
    print(f"✅ Created {pki_name} PEM       : {pem}")

    return key, crt, chain, pem


def build_kme_pki(root_days, issuing_days, leaf_days):
    print("\n=== Building KME PKI ===")

    root_key, root_crt = create_root_ca(
        name="kme",
        out_dir=KME_ROOT_DIR,
        days=root_days,
    )

    issuing_key, issuing_crt, ca_chain_crt = create_issuing_ca(
        name="kme",
        out_dir=KME_ISSUING_DIR,
        root_key=root_key,
        root_crt=root_crt,
        days=issuing_days,
    )

    for device in KME_DEVICES:
        create_leaf_cert(
            pki_name="KME",
            device=device,
            out_dir=KME_CERTS_DIR,
            issuing_key=issuing_key,
            issuing_crt=issuing_crt,
            ca_chain_crt=ca_chain_crt,
            days=leaf_days,
        )

    return {
        "root_key": root_key,
        "root_crt": root_crt,
        "issuing_key": issuing_key,
        "issuing_crt": issuing_crt,
        "ca_chain_crt": ca_chain_crt,
    }


def build_juniper_pki(root_days, issuing_days, leaf_days):
    print("\n=== Building Juniper PKI ===")

    root_key, root_crt = create_root_ca(
        name="juniper",
        out_dir=JUNIPER_ROOT_DIR,
        days=root_days,
    )

    issuing_key, issuing_crt, ca_chain_crt = create_issuing_ca(
        name="juniper",
        out_dir=JUNIPER_ISSUING_DIR,
        root_key=root_key,
        root_crt=root_crt,
        days=issuing_days,
    )

    for device in JUNIPER_DEVICES:
        create_leaf_cert(
            pki_name="Juniper",
            device=device,
            out_dir=JUNIPER_CERTS_DIR,
            issuing_key=issuing_key,
            issuing_crt=issuing_crt,
            ca_chain_crt=ca_chain_crt,
            days=leaf_days,
        )

    return {
        "root_key": root_key,
        "root_crt": root_crt,
        "issuing_key": issuing_key,
        "issuing_crt": issuing_crt,
        "ca_chain_crt": ca_chain_crt,
    }


def exchange_trust_anchors(kme_pki, juniper_pki):
    print("\n=== Exchanging CA trust anchors ===")

    kme_peer_bundle = KME_TRUSTED_JUNIPER_CA_DIR / "trusted-juniper-ca-bundle.crt"
    juniper_peer_bundle = JUNIPER_TRUSTED_KME_CA_DIR / "trusted-kme-ca-bundle.crt"

    shutil.copy2(juniper_pki["root_crt"], KME_TRUSTED_JUNIPER_CA_DIR / "juniper-root-ca.crt")
    shutil.copy2(juniper_pki["issuing_crt"], KME_TRUSTED_JUNIPER_CA_DIR / "juniper-issuing-ca.crt")

    shutil.copy2(kme_pki["root_crt"], JUNIPER_TRUSTED_KME_CA_DIR / "kme-root-ca.crt")
    shutil.copy2(kme_pki["issuing_crt"], JUNIPER_TRUSTED_KME_CA_DIR / "kme-issuing-ca.crt")

    with open(kme_peer_bundle, "wb") as out:
        out.write(juniper_pki["issuing_crt"].read_bytes())
        out.write(juniper_pki["root_crt"].read_bytes())

    with open(juniper_peer_bundle, "wb") as out:
        out.write(kme_pki["issuing_crt"].read_bytes())
        out.write(kme_pki["root_crt"].read_bytes())

    for path in [
        KME_TRUSTED_JUNIPER_CA_DIR / "juniper-root-ca.crt",
        KME_TRUSTED_JUNIPER_CA_DIR / "juniper-issuing-ca.crt",
        JUNIPER_TRUSTED_KME_CA_DIR / "kme-root-ca.crt",
        JUNIPER_TRUSTED_KME_CA_DIR / "kme-issuing-ca.crt",
        kme_peer_bundle,
        juniper_peer_bundle,
    ]:
        path.chmod(0o644)
        print(f"✅ Trust artifact: {path}")

    return {
        "install_on_kme": KME_TRUSTED_JUNIPER_CA_DIR,
        "install_on_juniper": JUNIPER_TRUSTED_KME_CA_DIR,
        "kme_peer_bundle": kme_peer_bundle,
        "juniper_peer_bundle": juniper_peer_bundle,
    }


def verify_cert_chain(leaf_crt, ca_bundle):
    print(f"\n=== Verifying {leaf_crt.name} against {ca_bundle.name} ===")
    return run(
        [
            "openssl",
            "verify",
            "-CAfile",
            ca_bundle,
            leaf_crt,
        ]
    )


def verify_all():
    print("\n=== Verifying generated chains ===")

    kme_ca_bundle = KME_ISSUING_DIR / "kme-ca-chain.crt"
    juniper_ca_bundle = JUNIPER_ISSUING_DIR / "juniper-ca-chain.crt"

    ok = True

    for crt in sorted(KME_CERTS_DIR.glob("kme_*.crt")):
        if crt.name.endswith(".chain.crt"):
            continue
        ok = verify_cert_chain(crt, kme_ca_bundle) and ok

    for crt in sorted(JUNIPER_CERTS_DIR.glob("*.crt")):
        if crt.name.endswith(".chain.crt"):
            continue
        ok = verify_cert_chain(crt, juniper_ca_bundle) and ok

    if ok:
        print("✅ All certificate chains verified successfully")
    else:
        print("❌ One or more certificate chain verifications failed")

    return ok


def print_summary():
    print("\n=== PKI output summary ===")

    print(f"\nKME Root CA:")
    print(f"  {KME_ROOT_DIR / 'kme-root-ca.crt'}")
    print(f"  {KME_ROOT_DIR / 'kme-root-ca.key'}")

    print(f"\nKME Issuing CA:")
    print(f"  {KME_ISSUING_DIR / 'kme-issuing-ca.crt'}")
    print(f"  {KME_ISSUING_DIR / 'kme-issuing-ca.key'}")
    print(f"  {KME_ISSUING_DIR / 'kme-ca-chain.crt'}")

    print(f"\nKME certs:")
    print(f"  {KME_CERTS_DIR}")

    print(f"\nJuniper Root CA:")
    print(f"  {JUNIPER_ROOT_DIR / 'juniper-root-ca.crt'}")
    print(f"  {JUNIPER_ROOT_DIR / 'juniper-root-ca.key'}")

    print(f"\nJuniper Issuing CA:")
    print(f"  {JUNIPER_ISSUING_DIR / 'juniper-issuing-ca.crt'}")
    print(f"  {JUNIPER_ISSUING_DIR / 'juniper-issuing-ca.key'}")
    print(f"  {JUNIPER_ISSUING_DIR / 'juniper-ca-chain.crt'}")

    print(f"\nJuniper device certs:")
    print(f"  {JUNIPER_CERTS_DIR}")

    print(f"\nInstall on KME:")
    print(f"  {KME_TRUSTED_JUNIPER_CA_DIR}")

    print(f"\nInstall on Juniper:")
    print(f"  {JUNIPER_TRUSTED_KME_CA_DIR}")


def clean():
    if PKI_DIR.exists():
        shutil.rmtree(PKI_DIR)
        print(f"✅ Removed: {PKI_DIR}")


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove generated dual PKI directory before building",
    )

    parser.add_argument(
        "--root-days",
        type=int,
        default=3650,
        help="Root CA validity in days",
    )

    parser.add_argument(
        "--issuing-days",
        type=int,
        default=1825,
        help="Issuing CA validity in days",
    )

    parser.add_argument(
        "--leaf-days",
        type=int,
        default=365,
        help="Leaf certificate validity in days",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify generated leaf certificates against their CA chains",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.clean:
        clean()

    ensure_dirs()

    kme_pki = build_kme_pki(
        root_days=args.root_days,
        issuing_days=args.issuing_days,
        leaf_days=args.leaf_days,
    )

    juniper_pki = build_juniper_pki(
        root_days=args.root_days,
        issuing_days=args.issuing_days,
        leaf_days=args.leaf_days,
    )

    exchange_trust_anchors(
        kme_pki=kme_pki,
        juniper_pki=juniper_pki,
    )

    if args.verify:
        verify_all()

    print_summary()


if __name__ == "__main__":
    main()