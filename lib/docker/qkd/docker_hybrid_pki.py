"""Two-domain hierarchical PKI between Linux ETSI KMEs and EVO PhioTX clients.

Mirrors the legacy hierarchical_ca model (config/pki/hierarchical_ca.yml)
without importing or writing the legacy tree:

    KME Root CA     -> KME Issuing CA     -> kme-<phiotx container>  (serverAuth)
    Juniper Root CA -> Juniper Issuing CA -> <phiotx container>      (clientAuth)

Trust exchange: each KME trusts the Juniper chain to authenticate PhioTX
clients; each PhioTX qkd store holds its Juniper chain plus the KME chain to
authenticate the KME servers. The client CN is the ETSI SAE identity the
reference KME extracts, so it must equal the PhioTX container name.
"""

import datetime
import ipaddress
import os
from pathlib import Path
import shutil

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import yaml

BASE_DIR = Path(__file__).resolve().parents[3]
PKI_DIR = BASE_DIR / "certs" / "docker_hybrid_ca"
SOURCE_CONFIG = BASE_DIR / "config" / "pki" / "hierarchical_ca.yml"
HASHES = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}


def _settings():
    pki = (yaml.safe_load(SOURCE_CONFIG.read_text(encoding="utf-8")) or {})["pki"]
    return {
        "key_size": int(pki["crypto"]["key_size"]),
        "hash": HASHES[pki["crypto"]["hash_algorithm"]],
        "subject": pki["subject_defaults"],
        "validity": pki["validity"],
        "kme": pki["kme_domain"],
        "juniper": pki["juniper_domain"],
    }


def _name(subject, common_name):
    return x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, subject["country"]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, subject["organization"]),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, subject["organizational_unit"]),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])


def _write_key(path, key):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    data = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    path.chmod(0o600)


def _write_pem(path, *certificates):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(b"".join(
        cert.public_bytes(serialization.Encoding.PEM) for cert in certificates
    ))
    path.chmod(0o644)


def _load(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def _load_key(path):
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def _sign(subject, public_key, issuer, issuer_key, days, extensions, digest):
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    )
    for extension, critical in extensions:
        builder = builder.add_extension(extension, critical=critical)
    return builder.sign(issuer_key, digest())


def _ca_usage():
    return x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=True,
        crl_sign=True, encipher_only=False, decipher_only=False,
    )


def _leaf_usage():
    return x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=True,
        data_encipherment=False, key_agreement=False, key_cert_sign=False,
        crl_sign=False, encipher_only=False, decipher_only=False,
    )


def _domain(directory, domain, settings):
    """Create or reuse Root CA -> Issuing CA for one trust domain."""
    root_prefix = domain["root_ca"]["filename_prefix"]
    issuing_prefix = domain["issuing_ca"]["filename_prefix"]
    root_cert_path = directory / "root_ca" / f"{root_prefix}.crt"
    root_key_path = directory / "root_ca" / f"{root_prefix}.key"
    issuing_cert_path = directory / "issuing_ca" / f"{issuing_prefix}.crt"
    issuing_key_path = directory / "issuing_ca" / f"{issuing_prefix}.key"
    if all(path.is_file() for path in (
        root_cert_path, root_key_path, issuing_cert_path, issuing_key_path
    )):
        return {
            "root": _load(root_cert_path), "issuing": _load(issuing_cert_path),
            "issuing_key": _load_key(issuing_key_path),
            "root_path": root_cert_path, "issuing_path": issuing_cert_path,
        }

    digest = settings["hash"]
    validity = settings["validity"]
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=settings["key_size"])
    root_name = _name(settings["subject"], domain["root_ca"]["common_name"])
    root = _sign(
        root_name, root_key.public_key(), root_name, root_key,
        int(validity["root_ca_days"]),
        [(x509.BasicConstraints(ca=True, path_length=domain["root_ca"].get("path_length", 1)), True),
         (_ca_usage(), True)],
        digest,
    )
    issuing_key = rsa.generate_private_key(public_exponent=65537, key_size=settings["key_size"])
    issuing = _sign(
        _name(settings["subject"], domain["issuing_ca"]["common_name"]),
        issuing_key.public_key(), root.subject, root_key,
        int(validity["issuing_ca_days"]),
        [(x509.BasicConstraints(ca=True, path_length=domain["issuing_ca"].get("path_length", 0)), True),
         (_ca_usage(), True)],
        digest,
    )
    _write_key(root_key_path, root_key)
    _write_pem(root_cert_path, root)
    _write_key(issuing_key_path, issuing_key)
    _write_pem(issuing_cert_path, issuing)
    return {
        "root": root, "issuing": issuing, "issuing_key": issuing_key,
        "root_path": root_cert_path, "issuing_path": issuing_cert_path,
    }


def _leaf(ca, settings, common_name, usage, address, key_path, cert_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=settings["key_size"])
    names = [x509.DNSName(common_name)]
    if address:
        names.append(x509.IPAddress(ipaddress.ip_address(address)))
    cert = _sign(
        _name(settings["subject"], common_name), key.public_key(),
        ca["issuing"].subject, ca["issuing_key"],
        int(settings["validity"]["leaf_cert_days"]),
        [(x509.BasicConstraints(ca=False, path_length=None), True),
         (_leaf_usage(), True),
         (x509.ExtendedKeyUsage([usage]), False),
         (x509.SubjectAlternativeName(names), False)],
        settings["hash"],
    )
    _write_key(key_path, key)
    _write_pem(cert_path, cert)
    return cert


def build_hybrid_pki(endpoints, *, fresh=False, base_dir=PKI_DIR):
    """
    Issue the KME/Juniper hierarchical PKI.

    endpoints maps device -> {"container": phiotx name, "addr": KME IPv4}.
    Returns per-device paths for the KME server and the PhioTX qkd client.
    Leaves are reissued on every call so certificate SANs always match the
    configured KME addresses; CA material is reused unless fresh=True.
    """
    base_dir = Path(base_dir)
    if fresh and base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    base_dir.chmod(0o700)
    settings = _settings()
    kme = _domain(base_dir / "kme_pki", settings["kme"], settings)
    juniper = _domain(base_dir / "juniper_pki", settings["juniper"], settings)

    exchange = base_dir / "trust_exchange"
    juniper_bundle = exchange / "install_on_kme" / "trusted-juniper-ca-bundle.crt"
    kme_bundle = exchange / "install_on_juniper" / "trusted-kme-ca-bundle.crt"
    _write_pem(juniper_bundle, juniper["root"], juniper["issuing"])
    _write_pem(kme_bundle, kme["root"], kme["issuing"])

    result = {}
    for device, endpoint in sorted(endpoints.items()):
        container = endpoint["container"]
        server = f"kme-{container}"
        server_dir = base_dir / "kme_pki" / "certs"
        client_dir = base_dir / "juniper_pki" / "certs"
        server_cert = _leaf(
            kme, settings, server, ExtendedKeyUsageOID.SERVER_AUTH, endpoint["addr"],
            server_dir / f"{server}.key", server_dir / f"{server}.crt",
        )
        _write_pem(server_dir / f"{server}-chain.crt", server_cert, kme["issuing"])
        _leaf(
            juniper, settings, container, ExtendedKeyUsageOID.CLIENT_AUTH, None,
            client_dir / f"{container}.key", client_dir / f"{container}.crt",
        )
        result[device] = {
            "server_name": server,
            "server_key": server_dir / f"{server}.key",
            "server_chain": server_dir / f"{server}-chain.crt",
            "server_trust": juniper_bundle,
            "client_key": client_dir / f"{container}.key",
            "client_crt": client_dir / f"{container}.crt",
            "client_trust": kme_bundle,
            # tx_install_crt order: own chain first, then the KME chain.
            "client_cas": [
                juniper["root_path"], juniper["issuing_path"],
                kme["root_path"], kme["issuing_path"],
            ],
        }
    print(f"[OK] hierarchical_ca hybrid PKI ready under {base_dir}")
    return result
