#!/usr/bin/env python3
"""Standalone certificate inspector for local and third-party artifacts.

Features:
- Analyze X.509 certificates from PEM/DER files and PEM bundles.
- Analyze private keys and report algorithm/size and encryption state.
- Detect issuer, CA flags, self-signed certificates, and validity windows.
- Optionally export machine-readable JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, padding, rsa
    from cryptography.x509.oid import NameOID

    try:
        from cryptography.hazmat.primitives.serialization import pkcs7
    except Exception:
        pkcs7 = None
except Exception as exc:  # pragma: no cover
    print(
        "ERROR: missing dependency 'cryptography'.\n"
        "Install with: pip install cryptography\n"
        f"Details: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)


CERT_EXTS = {
    ".crt",
    ".cer",
    ".pem",
    ".der",
    ".key",
    ".p7b",
    ".p7c",
    ".bundle",
    ".ca-bundle",
}


def iter_input_files(inputs: List[str], recursive: bool) -> List[Path]:
    files: List[Path] = []
    for raw in inputs:
        path = Path(raw)
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
            continue

        walker = path.rglob("*") if recursive else path.glob("*")
        for item in walker:
            if not item.is_file():
                continue
            if item.suffix.lower() in CERT_EXTS:
                files.append(item)

    # Preserve order, remove duplicates
    seen: set = set()
    deduped: List[Path] = []
    for item in files:
        key = str(item.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def split_pem_blocks(blob: bytes) -> List[Tuple[str, bytes]]:
    blocks: List[Tuple[str, bytes]] = []
    marker = b"-----BEGIN "
    pos = 0
    while True:
        begin = blob.find(marker, pos)
        if begin == -1:
            break
        end_label = blob.find(b"-----", begin + len(marker))
        if end_label == -1:
            break
        label = blob[begin + len(marker):end_label].decode("ascii", errors="ignore").strip()
        end_marker = f"-----END {label}-----".encode("ascii")
        end = blob.find(end_marker, end_label)
        if end == -1:
            pos = end_label + 5
            continue
        block_end = end + len(end_marker)
        # Include trailing newline if present.
        if block_end < len(blob) and blob[block_end:block_end + 1] in (b"\n", b"\r"):
            block_end += 1
            if block_end < len(blob) and blob[block_end:block_end + 1] == b"\n":
                block_end += 1
        blocks.append((label, blob[begin:block_end]))
        pos = block_end
    return blocks


def first_cn(name: x509.Name) -> Optional[str]:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else None


def key_details_from_public_key(public_key: Any) -> Dict[str, Any]:
    if isinstance(public_key, rsa.RSAPublicKey):
        return {"type": "RSA", "size_bits": public_key.key_size}
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        return {
            "type": "EC",
            "size_bits": public_key.key_size,
            "curve": public_key.curve.name,
        }
    if isinstance(public_key, dsa.DSAPublicKey):
        return {"type": "DSA", "size_bits": public_key.key_size}
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        return {"type": "Ed25519", "size_bits": 256}
    if isinstance(public_key, ed448.Ed448PublicKey):
        return {"type": "Ed448", "size_bits": 456}
    return {"type": type(public_key).__name__}


def verify_self_signature(cert: x509.Certificate) -> bool:
    pub = cert.public_key()
    try:
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                cert.signature_hash_algorithm,
            )
            return True
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                ec.ECDSA(cert.signature_hash_algorithm),
            )
            return True
        if isinstance(pub, dsa.DSAPublicKey):
            pub.verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                cert.signature_hash_algorithm,
            )
            return True
        if isinstance(pub, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            pub.verify(cert.signature, cert.tbs_certificate_bytes)
            return True
    except Exception:
        return False
    return False


def cert_extensions(cert: x509.Certificate) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        out["basic_constraints"] = {"ca": bc.ca, "path_length": bc.path_length}
    except x509.ExtensionNotFound:
        pass

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        out["subject_alt_name"] = {
            "dns": san.get_values_for_type(x509.DNSName),
            "ip": [str(v) for v in san.get_values_for_type(x509.IPAddress)],
            "uri": san.get_values_for_type(x509.UniformResourceIdentifier),
            "email": san.get_values_for_type(x509.RFC822Name),
        }
    except x509.ExtensionNotFound:
        pass

    try:
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        # cryptography only allows encipher_only/decipher_only when key_agreement is true.
        encipher_only = ku.encipher_only if ku.key_agreement else None
        decipher_only = ku.decipher_only if ku.key_agreement else None
        out["key_usage"] = {
            "digital_signature": ku.digital_signature,
            "content_commitment": ku.content_commitment,
            "key_encipherment": ku.key_encipherment,
            "data_encipherment": ku.data_encipherment,
            "key_agreement": ku.key_agreement,
            "key_cert_sign": ku.key_cert_sign,
            "crl_sign": ku.crl_sign,
            "encipher_only": encipher_only,
            "decipher_only": decipher_only,
        }
    except x509.ExtensionNotFound:
        pass

    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        out["extended_key_usage"] = [oid.dotted_string for oid in eku]
    except x509.ExtensionNotFound:
        pass

    return out


def cert_summary(cert: x509.Certificate) -> Dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    subject = cert.subject.rfc4514_string()
    issuer = cert.issuer.rfc4514_string()

    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc

    subject_cn = first_cn(cert.subject)
    issuer_cn = first_cn(cert.issuer)

    same_dn = cert.subject == cert.issuer
    self_signed_verified = same_dn and verify_self_signature(cert)

    ext = cert_extensions(cert)
    basic_constraints = ext.get("basic_constraints", {})
    is_ca = bool(basic_constraints.get("ca", False))

    try:
        sig_algo = cert.signature_hash_algorithm.name
    except Exception:
        sig_algo = cert.signature_algorithm_oid._name or cert.signature_algorithm_oid.dotted_string

    return {
        "subject": subject,
        "subject_cn": subject_cn,
        "issuer": issuer,
        "issuer_cn": issuer_cn,
        "serial_number": hex(cert.serial_number),
        "version": cert.version.name,
        "signature_algorithm": sig_algo,
        "public_key": key_details_from_public_key(cert.public_key()),
        "not_valid_before_utc": not_before.isoformat(),
        "not_valid_after_utc": not_after.isoformat(),
        "is_expired": now > not_after,
        "days_until_expiry": int((not_after - now).total_seconds() // 86400),
        "is_ca": is_ca,
        "is_self_signed_dn": same_dn,
        "is_self_signed_verified": self_signed_verified,
        "is_root_ca_candidate": bool(is_ca and self_signed_verified),
        "extensions": ext,
    }


def private_key_summary(private_key: Any, encrypted: bool, load_error: Optional[str]) -> Dict[str, Any]:
    info = key_details_from_public_key(private_key.public_key())
    return {
        "private_key_type": info.get("type"),
        "private_key_size_bits": info.get("size_bits"),
        "curve": info.get("curve"),
        "encrypted": encrypted,
        "load_error": load_error,
    }


def likely_encrypted_pem_block(block: bytes) -> bool:
    upper = block.decode("utf-8", errors="ignore").upper()
    return ("BEGIN ENCRYPTED PRIVATE KEY" in upper) or ("PROC-TYPE: 4,ENCRYPTED" in upper)


def load_certificates(data: bytes) -> List[x509.Certificate]:
    certs: List[x509.Certificate] = []
    blocks = split_pem_blocks(data)
    for label, block in blocks:
        if label == "CERTIFICATE":
            try:
                certs.append(x509.load_pem_x509_certificate(block))
            except Exception:
                continue

    if certs:
        return certs

    # DER single certificate fallback
    try:
        certs.append(x509.load_der_x509_certificate(data))
    except Exception:
        pass

    # PKCS7 chain support if available
    if not certs and pkcs7 is not None:
        try:
            certs.extend(pkcs7.load_pem_pkcs7_certificates(data))
        except Exception:
            pass
        if not certs:
            try:
                certs.extend(pkcs7.load_der_pkcs7_certificates(data))
            except Exception:
                pass

    return certs


def load_private_keys(data: bytes, password: Optional[bytes]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    blocks = split_pem_blocks(data)

    key_blocks = [
        block for label, block in blocks
        if "PRIVATE KEY" in label
    ]

    # DER private key attempt only when no PEM blocks exist.
    if not key_blocks and blocks == []:
        try:
            key = serialization.load_der_private_key(data, password=password)
            results.append(private_key_summary(key, encrypted=bool(password), load_error=None))
        except TypeError as exc:
            results.append(
                {
                    "private_key_type": None,
                    "private_key_size_bits": None,
                    "curve": None,
                    "encrypted": True,
                    "load_error": str(exc),
                }
            )
        except Exception:
            pass
        return results

    for block in key_blocks:
        encrypted = likely_encrypted_pem_block(block)
        try:
            key = serialization.load_pem_private_key(block, password=password)
            results.append(private_key_summary(key, encrypted=encrypted, load_error=None))
        except TypeError as exc:
            # Usually means key is encrypted and password is missing.
            results.append(
                {
                    "private_key_type": None,
                    "private_key_size_bits": None,
                    "curve": None,
                    "encrypted": True,
                    "load_error": str(exc),
                }
            )
        except ValueError as exc:
            # Wrong password, unsupported key format, or invalid key.
            results.append(
                {
                    "private_key_type": None,
                    "private_key_size_bits": None,
                    "curve": None,
                    "encrypted": encrypted,
                    "load_error": str(exc),
                }
            )

    return results


def analyze_file(path: Path, password: Optional[bytes]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "file": str(path),
        "exists": path.exists(),
        "certificates": [],
        "private_keys": [],
        "errors": [],
    }
    if not path.exists():
        out["errors"].append("file_not_found")
        return out

    try:
        data = path.read_bytes()
    except Exception as exc:
        out["errors"].append(f"read_error: {exc}")
        return out

    try:
        certs = load_certificates(data)
        out["certificates"] = [cert_summary(cert) for cert in certs]
    except Exception as exc:
        out["errors"].append(f"certificate_parse_error: {exc}")

    try:
        keys = load_private_keys(data, password=password)
        out["private_keys"] = keys
    except Exception as exc:
        out["errors"].append(f"private_key_parse_error: {exc}")

    return out


def print_human_report(reports: List[Dict[str, Any]]) -> None:
    for report in reports:
        print("=" * 80)
        print(f"FILE: {report['file']}")

        if report["errors"]:
            for err in report["errors"]:
                print(f"  ERROR: {err}")

        certs = report.get("certificates", [])
        keys = report.get("private_keys", [])

        print(f"  Certificates found: {len(certs)}")
        for i, cert in enumerate(certs, start=1):
            pub = cert.get("public_key", {})
            print(f"    [{i}] Subject CN: {cert.get('subject_cn')}")
            print(f"        Subject: {cert.get('subject')}")
            print(f"        Issuer : {cert.get('issuer')}")
            print(f"        Root CA candidate: {cert.get('is_root_ca_candidate')}")
            print(f"        Self-signed (verified): {cert.get('is_self_signed_verified')}")
            print(f"        Is CA: {cert.get('is_ca')}")
            print(
                "        Public Key: "
                f"{pub.get('type')} {pub.get('size_bits')}"
                + (f" curve={pub.get('curve')}" if pub.get("curve") else "")
            )
            print(f"        Signature Algo: {cert.get('signature_algorithm')}")
            print(f"        Valid From: {cert.get('not_valid_before_utc')}")
            print(f"        Valid To  : {cert.get('not_valid_after_utc')}")
            print(f"        Expired   : {cert.get('is_expired')}")

        print(f"  Private keys found: {len(keys)}")
        for i, key in enumerate(keys, start=1):
            print(f"    [{i}] Type: {key.get('private_key_type')}")
            print(f"        Size: {key.get('private_key_size_bits')}")
            if key.get("curve"):
                print(f"        Curve: {key.get('curve')}")
            print(f"        Encrypted (password-protected): {key.get('encrypted')}")
            if key.get("load_error"):
                print(f"        Load note: {key.get('load_error')}")
    print("=" * 80)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze certificates, bundles, and private keys from local or third-party sources."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Files and/or directories to inspect.",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Recursively walk input directories.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password used to try decrypting encrypted private keys.",
    )
    parser.add_argument(
        "--password-prompt",
        action="store_true",
        help="Prompt for private key password securely.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any file has parse errors.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.password and args.password_prompt:
        parser.error("Use either --password or --password-prompt, not both.")

    password_bytes: Optional[bytes] = None
    if args.password is not None:
        password_bytes = args.password.encode("utf-8")
    elif args.password_prompt:
        import getpass

        password_bytes = getpass.getpass("Private key password: ").encode("utf-8")

    files = iter_input_files(args.inputs, recursive=args.recursive)
    if not files:
        print("No matching files found.", file=sys.stderr)
        return 1

    reports = [analyze_file(path, password=password_bytes) for path in files]

    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=False))
    else:
        print_human_report(reports)

    if args.strict:
        has_errors = any(bool(rep.get("errors")) for rep in reports)
        if has_errors:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
