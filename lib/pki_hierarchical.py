#!/usr/bin/env python3

from pathlib import Path
import argparse
import datetime
import ipaddress
import shutil
import sys

import yaml

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from lib.config import load_yaml

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_PKI_CONFIG = BASE_DIR / "config" / "pki" / "hierarchical_ca.yml"

def short_name(path):
    return Path(path).name


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def write_private_key(path, key):
    path = Path(path)
    ensure_dir(path.parent)

    data = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    with open(path, "wb") as f:
        f.write(data)

    path.chmod(0o600)


def write_certificate(path, cert):
    path = Path(path)
    ensure_dir(path.parent)

    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    path.chmod(0o644)


def write_bytes(path, data, mode=0o644):
    path = Path(path)
    ensure_dir(path.parent)

    with open(path, "wb") as f:
        f.write(data)

    path.chmod(mode)


def load_private_key(path):
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def load_certificate(path):
    with open(path, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def hash_algorithm(name):
    name = str(name).lower()

    if name == "sha256":
        return hashes.SHA256()

    if name == "sha384":
        return hashes.SHA384()

    if name == "sha512":
        return hashes.SHA512()

    raise ValueError(f"Unsupported hash algorithm: {name}")


def generate_key(key_size):
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=int(key_size),
    )


def build_subject(defaults, common_name):
    attributes = []

    country = defaults.get("country")
    organization = defaults.get("organization")
    organizational_unit = defaults.get("organizational_unit")

    if country:
        attributes.append(x509.NameAttribute(NameOID.COUNTRY_NAME, str(country)))

    if organization:
        attributes.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, str(organization)))

    if organizational_unit:
        attributes.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, str(organizational_unit)))

    attributes.append(x509.NameAttribute(NameOID.COMMON_NAME, str(common_name)))

    return x509.Name(attributes)


def key_usage_from_profile(profile):
    ku = profile.get("key_usage", {})

    return x509.KeyUsage(
        digital_signature=bool(ku.get("digital_signature", False)),
        content_commitment=bool(ku.get("content_commitment", False)),
        key_encipherment=bool(ku.get("key_encipherment", False)),
        data_encipherment=bool(ku.get("data_encipherment", False)),
        key_agreement=bool(ku.get("key_agreement", False)),
        key_cert_sign=bool(ku.get("key_cert_sign", False)),
        crl_sign=bool(ku.get("crl_sign", False)),
        encipher_only=ku.get("encipher_only", False) if ku.get("key_agreement", False) else None,
        decipher_only=ku.get("decipher_only", False) if ku.get("key_agreement", False) else None,
    )


def extended_key_usage_from_profile(profile):
    eku_cfg = profile.get("extended_key_usage", {})

    if not eku_cfg.get("enabled", False):
        return None

    eku = []

    if eku_cfg.get("server_auth", False):
        eku.append(ExtendedKeyUsageOID.SERVER_AUTH)

    if eku_cfg.get("client_auth", False):
        eku.append(ExtendedKeyUsageOID.CLIENT_AUTH)

    if not eku:
        return None

    return x509.ExtendedKeyUsage(eku)


def basic_constraints_from_profile(profile, fallback_path_length=None):
    bc = profile.get("basic_constraints", {})

    is_ca = bool(bc.get("ca", False))

    if is_ca:
        path_length = bc.get("path_length", fallback_path_length)
    else:
        path_length = None

    return x509.BasicConstraints(
        ca=is_ca,
        path_length=path_length,
    )


def san_from_device(device, include_dns=True, include_ip=True, include_localhost_dns=False):
    san = []

    names = []

    for key in ("name", "hostname", "sae_id", "common_name"):
        value = device.get(key)
        if value and value not in names:
            names.append(str(value))

    for value in device.get("dns", []) or []:
        if value and value not in names:
            names.append(str(value))

    if include_localhost_dns and "localhost" not in names:
        names.append("localhost")

    if include_dns:
        for name in names:
            san.append(x509.DNSName(name))

    if include_ip:
        ip = device.get("ip") or device.get("mgmt_ip") or device.get("address")

        if ip:
            san.append(x509.IPAddress(ipaddress.ip_address(str(ip))))

    if not san:
        return None

    return x509.SubjectAlternativeName(san)


def device_name(device):
    for key in ("name", "hostname", "sae_id", "common_name"):
        value = device.get(key)

        if value:
            return str(value)

    raise ValueError(f"Cannot determine device name from inventory entry: {device}")


def device_common_name(device):
    return str(device.get("common_name") or device_name(device))


def load_runtime_devices(path):
    data = load_yaml(path)

    devices = data.get("devices")

    if not isinstance(devices, dict):
        raise ValueError(f"Invalid runtime devices file: missing 'devices' dictionary in {path}")

    return devices


def build_juniper_leaf_devices(runtime_devices):
    leaf_devices = []

    for runtime_name, dev in runtime_devices.items():
        qkd = dev.get("qkd", {})
        sae_id = qkd.get("sae_id")

        if not sae_id:
            raise ValueError(f"Missing qkd.sae_id for device {runtime_name}")

        ip = dev.get("ip")

        if not ip:
            raise ValueError(f"Missing ip for device {runtime_name}")

        leaf_devices.append(
            {
                "name": str(sae_id),
                "hostname": str(runtime_name),
                "common_name": str(sae_id),
                "sae_id": str(sae_id),
                "ip": str(ip),
                "dns": [
                    str(sae_id),
                    str(runtime_name),
                ],
            }
        )

    return leaf_devices


def build_kme_leaf_devices(runtime_devices):
    seen = {}
    ordered_ips = []

    for runtime_name, dev in runtime_devices.items():
        kme = dev.get("kme", {})
        ip = kme.get("ip")

        if not ip:
            raise ValueError(f"Missing kme.ip for device {runtime_name}")

        ip = str(ip)

        if ip not in seen:
            seen[ip] = []
            ordered_ips.append(ip)

        seen[ip].append(str(runtime_name))

    leaf_devices = []

    for index, ip in enumerate(ordered_ips, start=1):
        name = f"kme_{index:03d}"

        leaf_devices.append(
            {
                "name": name,
                "hostname": name,
                "common_name": name,
                "ip": ip,
                "dns": [
                    name,
                    f"kme{index}",
                    "localhost",
                ],
                "used_by_devices": seen[ip],
            }
        )

    return leaf_devices


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def cert_validity(days):
    start = now_utc() - datetime.timedelta(minutes=1)
    end = now_utc() + datetime.timedelta(days=int(days))

    return start, end


def create_root_ca(
    cert_path,
    key_path,
    common_name,
    subject_defaults,
    profile,
    key_size,
    validity_days,
    digest_name,
    path_length,
):
    key = generate_key(key_size)

    subject = build_subject(subject_defaults, common_name)

    not_before, not_after = cert_validity(validity_days)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            basic_constraints_from_profile(profile, fallback_path_length=path_length),
            critical=bool(profile.get("basic_constraints", {}).get("critical", True)),
        )
        .add_extension(
            key_usage_from_profile(profile),
            critical=bool(profile.get("key_usage", {}).get("critical", True)),
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
    )

    cert = builder.sign(
        private_key=key,
        algorithm=hash_algorithm(digest_name),
    )

    write_private_key(key_path, key)
    write_certificate(cert_path, cert)

    print(f"OK root CA cert: {short_name(cert_path)}")
    print(f"OK root CA key : {short_name(key_path)}")

    return key, cert


def create_issuing_ca(
    cert_path,
    key_path,
    root_cert,
    root_key,
    common_name,
    subject_defaults,
    profile,
    key_size,
    validity_days,
    digest_name,
    path_length,
):
    key = generate_key(key_size)

    subject = build_subject(subject_defaults, common_name)

    not_before, not_after = cert_validity(validity_days)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(root_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            basic_constraints_from_profile(profile, fallback_path_length=path_length),
            critical=bool(profile.get("basic_constraints", {}).get("critical", True)),
        )
        .add_extension(
            key_usage_from_profile(profile),
            critical=bool(profile.get("key_usage", {}).get("critical", True)),
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
    )

    cert = builder.sign(
        private_key=root_key,
        algorithm=hash_algorithm(digest_name),
    )

    write_private_key(key_path, key)
    write_certificate(cert_path, cert)

    print(f"OK issuing CA cert: {short_name(cert_path)}")
    print(f"OK issuing CA key : {short_name(key_path)}")

    return key, cert


def create_leaf_certificate(
    cert_path,
    key_path,
    chain_path,
    pem_path,
    issuing_cert,
    issuing_key,
    ca_chain_bytes,
    device,
    subject_defaults,
    profile,
    key_size,
    validity_days,
    digest_name,
    include_dns_san=True,
    include_ip_san=True,
    include_localhost_dns=False,
):
    key = generate_key(key_size)

    common_name = device_common_name(device)
    subject = build_subject(subject_defaults, common_name)

    not_before, not_after = cert_validity(validity_days)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuing_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            basic_constraints_from_profile(profile),
            critical=bool(profile.get("basic_constraints", {}).get("critical", True)),
        )
        .add_extension(
            key_usage_from_profile(profile),
            critical=bool(profile.get("key_usage", {}).get("critical", True)),
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuing_key.public_key()),
            critical=False,
        )
    )

    eku = extended_key_usage_from_profile(profile)

    if eku is not None:
        builder = builder.add_extension(eku, critical=False)

    san = san_from_device(
        device,
        include_dns=include_dns_san,
        include_ip=include_ip_san,
        include_localhost_dns=include_localhost_dns,
    )

    if san is not None:
        builder = builder.add_extension(san, critical=False)

    cert = builder.sign(
        private_key=issuing_key,
        algorithm=hash_algorithm(digest_name),
    )

    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    chain_bytes = cert_bytes + ca_chain_bytes
    pem_bytes = key_bytes + chain_bytes

    write_private_key(key_path, key)
    write_certificate(cert_path, cert)
    write_bytes(chain_path, chain_bytes, mode=0o644)
    write_bytes(pem_path, pem_bytes, mode=0o600)

    print(f"OK leaf cert : {short_name(cert_path)}")
    print(f"OK leaf key  : {short_name(key_path)}")
    print(f"OK leaf chain: {short_name(chain_path)}")
    print(f"OK leaf pem  : {short_name(pem_path)}")

    return key, cert


def concat_certs(*certs):
    data = b""

    for cert in certs:
        data += cert.public_bytes(serialization.Encoding.PEM)

    return data


def verify_signed_by(child_cert, issuer_cert):
    issuer_public_key = issuer_cert.public_key()

    issuer_public_key.verify(
        child_cert.signature,
        child_cert.tbs_certificate_bytes,
        padding.PKCS1v15(),
        child_cert.signature_hash_algorithm,
    )

    return True


def verify_tree(root_cert, issuing_cert, leaf_certs):
    verify_signed_by(issuing_cert, root_cert)

    for cert in leaf_certs:
        verify_signed_by(cert, issuing_cert)

    return True


def extract_inventory_devices(inventory, section_name):
    section = inventory.get(section_name)

    if section is None:
        raise KeyError(f"Inventory section not found: {section_name}")

    if isinstance(section, list):
        return section

    if isinstance(section, dict):
        for key in ("devices", "items", "nodes", "hosts", "kmes"):
            value = section.get(key)

            if isinstance(value, list):
                return value

    raise ValueError(f"Unsupported inventory format for section: {section_name}")


def domain_paths(output_dir, layout, domain):
    output_dir = Path(output_dir)

    if domain == "kme":
        return {
            "root_ca_dir": output_dir / layout["kme_root_ca_dir"],
            "issuing_ca_dir": output_dir / layout["kme_issuing_ca_dir"],
            "certs_dir": output_dir / layout["kme_certs_dir"],
        }

    if domain == "juniper":
        return {
            "root_ca_dir": output_dir / layout["juniper_root_ca_dir"],
            "issuing_ca_dir": output_dir / layout["juniper_issuing_ca_dir"],
            "certs_dir": output_dir / layout["juniper_certs_dir"],
        }

    raise ValueError(f"Unsupported domain: {domain}")


def build_domain(
    domain_name,
    domain_cfg,
    pki_cfg,
    profiles,
    leaf_devices,
):
    pki = pki_cfg["pki"]

    output_dir = BASE_DIR / pki["output_dir"]
    layout = pki["layout"]

    paths = domain_paths(output_dir, layout, domain_name)

    for path in paths.values():
        ensure_dir(path)

    crypto = pki["crypto"]
    subject_defaults = pki["subject_defaults"]
    validity = pki["validity"]

    root_profile = profiles["root_ca"]
    issuing_profile = profiles["issuing_ca"]

    leaf_profile_name = domain_cfg["leaf_certificates"]["profile"]
    leaf_profile = profiles[leaf_profile_name]

    root_ca_cfg = domain_cfg["root_ca"]
    issuing_ca_cfg = domain_cfg["issuing_ca"]
    leaf_cfg = domain_cfg["leaf_certificates"]

    root_prefix = root_ca_cfg["filename_prefix"]
    issuing_prefix = issuing_ca_cfg["filename_prefix"]

    root_key_path = paths["root_ca_dir"] / f"{root_prefix}.key"
    root_cert_path = paths["root_ca_dir"] / f"{root_prefix}.crt"

    issuing_key_path = paths["issuing_ca_dir"] / f"{issuing_prefix}.key"
    issuing_cert_path = paths["issuing_ca_dir"] / f"{issuing_prefix}.crt"
    ca_chain_path = paths["issuing_ca_dir"] / f"{domain_cfg['name']}-ca-chain.crt"

    key_size = crypto.get("key_size", root_profile.get("default_key_size", 4096))
    digest_name = crypto.get("hash_algorithm", "sha256")

    root_key, root_cert = create_root_ca(
        cert_path=root_cert_path,
        key_path=root_key_path,
        common_name=root_ca_cfg["common_name"],
        subject_defaults=subject_defaults,
        profile=root_profile,
        key_size=key_size,
        validity_days=root_ca_cfg.get("validity_days", validity["root_ca_days"]),
        digest_name=digest_name,
        path_length=root_ca_cfg.get("path_length", 1),
    )

    issuing_key, issuing_cert = create_issuing_ca(
        cert_path=issuing_cert_path,
        key_path=issuing_key_path,
        root_cert=root_cert,
        root_key=root_key,
        common_name=issuing_ca_cfg["common_name"],
        subject_defaults=subject_defaults,
        profile=issuing_profile,
        key_size=key_size,
        validity_days=issuing_ca_cfg.get("validity_days", validity["issuing_ca_days"]),
        digest_name=digest_name,
        path_length=issuing_ca_cfg.get("path_length", 0),
    )

    ca_chain_bytes = concat_certs(issuing_cert, root_cert)
    write_bytes(ca_chain_path, ca_chain_bytes, mode=0o644)

    print(f"OK CA chain: {short_name(ca_chain_path)}")

    leaf_certs = []
    
    for device in leaf_devices:
        name = device_name(device)
        dev_dir = paths["certs_dir"] / name
        ensure_dir(dev_dir)

        cert_path = dev_dir / f"{name}.crt"
        key_path = dev_dir / f"{name}.key"
        chain_path = dev_dir / f"{name}.chain.crt"
        pem_path = dev_dir / f"{name}.pem"

        _, leaf_cert = create_leaf_certificate(
            cert_path=cert_path,
            key_path=key_path,
            chain_path=chain_path,
            pem_path=pem_path,
            issuing_cert=issuing_cert,
            issuing_key=issuing_key,
            ca_chain_bytes=ca_chain_bytes,
            device=device,
            subject_defaults=subject_defaults,
            profile=leaf_profile,
            key_size=key_size,
            validity_days=validity["leaf_cert_days"],
            digest_name=digest_name,
            include_dns_san=bool(leaf_cfg.get("include_dns_san", True)),
            include_ip_san=bool(leaf_cfg.get("include_ip_san", True)),
            include_localhost_dns=bool(leaf_cfg.get("include_localhost_dns", False)),
        )

        leaf_certs.append(leaf_cert)

    return {
        "name": domain_cfg["name"],
        "root_cert": root_cert,
        "root_key": root_key,
        "root_cert_path": root_cert_path,
        "issuing_cert": issuing_cert,
        "issuing_key": issuing_key,
        "issuing_cert_path": issuing_cert_path,
        "ca_chain_path": ca_chain_path,
        "leaf_certs": leaf_certs,
        "paths": paths,
    }


def build_trust_exchange(pki_cfg, kme_result, juniper_result):
    pki = pki_cfg["pki"]
    trust_cfg = pki["trust_exchange"]

    if not trust_cfg.get("enabled", True):
        print("Trust exchange disabled")
        return

    output_dir = BASE_DIR / pki["output_dir"]
    layout = pki["layout"]

    install_on_kme_dir = output_dir / layout["install_on_kme_dir"]
    install_on_juniper_dir = output_dir / layout["install_on_juniper_dir"]

    ensure_dir(install_on_kme_dir)
    ensure_dir(install_on_juniper_dir)

    install_on_kme_cfg = trust_cfg["install_on_kme"]
    install_on_juniper_cfg = trust_cfg["install_on_juniper"]

    juniper_bundle_path = install_on_kme_dir / install_on_kme_cfg["bundle_name"]
    kme_bundle_path = install_on_juniper_dir / install_on_juniper_cfg["bundle_name"]

    juniper_bundle = b""
    kme_bundle = b""

    if install_on_kme_cfg.get("include_juniper_issuing_ca", True):
        juniper_bundle += juniper_result["issuing_cert"].public_bytes(serialization.Encoding.PEM)

    if install_on_kme_cfg.get("include_juniper_root_ca", True):
        juniper_bundle += juniper_result["root_cert"].public_bytes(serialization.Encoding.PEM)

    if install_on_juniper_cfg.get("include_kme_issuing_ca", True):
        kme_bundle += kme_result["issuing_cert"].public_bytes(serialization.Encoding.PEM)

    if install_on_juniper_cfg.get("include_kme_root_ca", True):
        kme_bundle += kme_result["root_cert"].public_bytes(serialization.Encoding.PEM)

    write_bytes(juniper_bundle_path, juniper_bundle, mode=0o644)
    write_bytes(kme_bundle_path, kme_bundle, mode=0o644)

    shutil.copy2(juniper_result["root_cert_path"], install_on_kme_dir / "juniper-root-ca.crt")
    shutil.copy2(juniper_result["issuing_cert_path"], install_on_kme_dir / "juniper-issuing-ca.crt")

    shutil.copy2(kme_result["root_cert_path"], install_on_juniper_dir / "kme-root-ca.crt")
    shutil.copy2(kme_result["issuing_cert_path"], install_on_juniper_dir / "kme-issuing-ca.crt")

    print(f"OK install on KME bundle    : {short_name(juniper_bundle_path)}")
    print(f"OK install on Juniper bundle: {short_name(kme_bundle_path)}")


def load_all_profiles(pki_cfg):
    profile_paths = pki_cfg["pki"]["profiles"]

    profiles = {}

    for name, profile_entry in profile_paths.items():

        if isinstance(profile_entry, dict):
            path = profile_entry.get("file")
        else:
            path = profile_entry

        if not path:
            raise ValueError(
                f"Missing profile file path for profile '{name}'"
            )

        profiles[name] = load_yaml(BASE_DIR / path)

    return profiles


def build_hierarchical_pki(config_path=DEFAULT_PKI_CONFIG, clean=False, verify=False):
    pki_cfg = load_yaml(config_path)
    cfg = pki_cfg["pki"]
    
    output_dir = BASE_DIR / cfg["output_dir"]

    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
        print(f"OK removed: {cfg['output_dir']} folder")
    
    profiles = load_all_profiles(pki_cfg)

    devices_source = cfg.get("devices_source")

    if not devices_source:
        raise ValueError (f"Missing pki.devices_source in {config_path}")
    
    devices_path = BASE_DIR / devices_source
    runtime_devices = load_runtime_devices(devices_path)

    kme_leaf_devices = build_kme_leaf_devices(runtime_devices)
    juniper_leaf_devices = build_juniper_leaf_devices(runtime_devices)
    
    print("")
    print("Runtime source")
    print(f"  Devices: config/runtime/devices.yaml")
    print("  Make sure this file has already been generated in runtime folder")
    print(f"  KME certs: {len(kme_leaf_devices)}")
    print(f"  Juniper certs: {len(juniper_leaf_devices)}")

    print("")
    print("Building KME PKI")
    print("")

    kme_result = build_domain(
        domain_name="kme",
        domain_cfg=cfg["kme_domain"],
        pki_cfg=pki_cfg,
        profiles=profiles,
        leaf_devices=kme_leaf_devices,
    )

    print("")
    print("Building Juniper PKI")
    print("")

    juniper_result = build_domain(
        domain_name="juniper",
        domain_cfg=cfg["juniper_domain"],
        pki_cfg=pki_cfg,
        profiles=profiles,
        leaf_devices=juniper_leaf_devices,
    )

    print("")
    print("Building trust exchange")
    print("")

    build_trust_exchange(
        pki_cfg=pki_cfg,
        kme_result=kme_result,
        juniper_result=juniper_result,
    )

    if verify:
        print("")
        print("Verifying KME chain")
        verify_tree(
            root_cert=kme_result["root_cert"],
            issuing_cert=kme_result["issuing_cert"],
            leaf_certs=kme_result["leaf_certs"],
        )
        print("OK KME chain verified")

        print("")
        print("Verifying Juniper chain")
        verify_tree(
            root_cert=juniper_result["root_cert"],
            issuing_cert=juniper_result["issuing_cert"],
            leaf_certs=juniper_result["leaf_certs"],
        )
        print("OK Juniper chain verified")

    print("")
    print("Dual PKI generation complete")
    print(f"Output directory: {cfg['output_dir']}")    

def parse_args():
    parser = argparse.ArgumentParser(
        description="QKD dual PKI builder for KME and Juniper trust domains"
    )

    parser.add_argument(
        "--config",
        default=str(DEFAULT_PKI_CONFIG),
        help="PKI YAML configuration file",
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove output directory before generating certificates",
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify generated certificate chains",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    try:
        build_hierarchical_pki(
            config_path=args.config,
            clean=args.clean,
            verify=args.verify,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()