"""Persistent lab PKI and guarded imports into existing licensed QBT containers."""

import datetime
import hashlib
import ipaddress
import json
import shlex
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from lib.qbt.network import ADDRESSES
from lib.qbt.runtime import admin_command


def write_private(path, data):
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(0o600)


def prepare_pki(directory, rotate=False):
    directory = Path(directory).resolve()
    if rotate and directory.exists() and any(directory.iterdir()):
        from lib.qbt.hierarchical import supersede_directory

        print(f"[pki] previous PKI kept in {supersede_directory(directory)}", flush=True)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    expected = ["ca.pem", "ca.key"] + [
        name + suffix for name in ("evo1", "evo2", "sae-001", "sae-002")
        for suffix in (".pem", ".key")
    ]
    present = [name for name in expected if (directory / name).exists()]
    if present:
        if len(present) != len(expected):
            raise ValueError("Incomplete PKI directory; refusing to overwrite existing identities")
        validate_pki(directory)
        return directory
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "QBT EVO Lab CA")])
    ca = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    def save(name, cert, key):
        write_private(directory / (name + ".pem"), cert.public_bytes(serialization.Encoding.PEM))
        write_private(directory / (name + ".key"), key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    save("ca", ca, ca_key)
    for name in ("evo1", "evo2", "sae-001", "sae-002"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        sans = [x509.DNSName(name)]
        server = name.startswith("evo")
        if server:
            sans.extend(x509.IPAddress(ipaddress.ip_address(ip)) for ip in ADDRESSES[name.upper()])
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=180))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH
            ]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        save(name, cert, key)
    validate_pki(directory)
    return directory


def validate_pki(directory):
    now = datetime.datetime.now(datetime.timezone.utc)
    ca = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
    if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError("PKI issuer is not a CA")
    for name in ("ca", "evo1", "evo2", "sae-001", "sae-002"):
        cert = x509.load_pem_x509_certificate((directory / (name + ".pem")).read_bytes())
        key = serialization.load_pem_private_key((directory / (name + ".key")).read_bytes(), password=None)
        if cert.public_key().public_numbers() != key.public_key().public_numbers():
            raise ValueError(f"{name}: certificate and key mismatch")
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError(f"{name}: certificate is outside validity interval")
        if cert.issuer != ca.subject:
            raise ValueError(f"{name}: unexpected certificate issuer")
        ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
        if name != "ca":
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            if name not in san.get_values_for_type(x509.DNSName):
                raise ValueError(f"{name}: required identity SAN missing")
            if name.startswith("evo"):
                for address in ADDRESSES[name.upper()]:
                    if ipaddress.ip_address(address) not in san.get_values_for_type(x509.IPAddress):
                        raise ValueError(f"{name}: required server IP SAN missing")


def import_pki(client, name, directory, run, transfer, rotate=False):
    container = "qbt-" + name.lower()
    root = "/var/db/qbt/" + name.lower()
    status = run(client, admin_command(container, "pki", "cert", "show"))
    marker = root + "/secrets/pki/imported.json"
    cert = x509.load_pem_x509_certificate((directory / (name.lower() + ".pem")).read_bytes())
    identity = json.dumps({
        "server": cert.fingerprint(hashes.SHA256()).hex(),
        "ca": hashlib.sha256((directory / "ca.pem").read_bytes()).hexdigest(),
    }, sort_keys=True)
    recorded = run(client, f"if test -f {marker}; then cat {marker}; fi")
    changed = recorded and json.loads(recorded) != json.loads(identity)
    if changed and not rotate:
        raise ValueError(f"{name}: existing PKI fingerprint differs; explicit rotation required")
    loaded = "Private key: Not Loaded" not in status or "Public key:  Not Loaded" not in status
    if loaded and not rotate:
        if not recorded:
            raise ValueError(f"{name}: existing untracked PKI; refusing to overwrite")
        run(client, admin_command(container, "pki", "cert", "validate"))
        return
    run(client, f"mkdir -p {root}/secrets/pki && chmod 700 {root}/secrets/pki")
    # The QBT TLS backend rejects the generator's PKCS#1 RSA encoding even
    # though its database certificate validator accepts it. Preserve the key,
    # converting only its transfer encoding to PKCS#8.
    with tempfile.TemporaryDirectory(prefix="qbt-pki-import-") as staging:
        key = serialization.load_pem_private_key(
            (directory / (name.lower() + ".key")).read_bytes(), password=None
        )
        converted = Path(staging) / "server.key"
        write_private(converted, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        for filename, local in (
            ("ca.pem", directory / "ca.pem"),
            ("server.key", converted),
            ("server.pem", directory / (name.lower() + ".pem")),
        ):
            remote = root + "/secrets/pki/" + filename
            transfer(client, local, remote)
            run(client, "chmod 444 " + remote)
    if loaded and rotate:
        run(client, admin_command(container, "pki", "cert", "remove"))
        run(client, admin_command(container, "pki", "cert", "remove-key"))
    for arguments in (
        ("pki", "ca", "import", "/run/secrets/pki/ca.pem"),
        ("pki", "cert", "import-key", "/run/secrets/pki/server.key"),
        ("pki", "cert", "import-certificate", "/run/secrets/pki/server.pem"),
        ("pki", "cert", "validate"),
    ):
        print(name, run(client, admin_command(container, *arguments)))
    run(client, f"printf %s {shlex.quote(identity)} > {marker} && chmod 600 {marker}")
    # The KME loads its TLS certificate at start; without a restart it keeps
    # serving the previous one and peers reject it against the new CA.
    print(f"{name}: restarting {container} so it serves the imported certificate", flush=True)
    run(client, f"docker restart {container} >/dev/null")
    run(
        client,
        f"i=0; while [ $i -lt 30 ]; do "
        f"test \"$(docker inspect -f '{{{{.State.Running}}}}' {container})\" = true && exit 0; "
        "sleep 1; i=$((i+1)); done; exit 1",
    )
