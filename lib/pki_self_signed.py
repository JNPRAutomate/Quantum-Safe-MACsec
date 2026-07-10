from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime
from lib.settings import CONFIG,PKI
import ipaddress

# ----------------------------------------
# Helper
# ----------------------------------------
def write_file(path, data, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        f.write(data)
    
    if mode is not None:
        path.chmod(mode)

    return path
# ----------------------------------------
# CA CERT
# ----------------------------------------
def build_ca_certificate(cert_path, key_path, cn="QKD-Root"):

    key = rsa.generate_private_key(public_exponent=65537, key_size=PKI["KEY_SIZE"])
    
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, PKI["COUNTRY"]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, PKI["ORG"]),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])


    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now() + datetime.timedelta(days=PKI["VALIDITY_DAYS"]))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        
        .add_extension(x509.KeyUsage(
                            digital_signature=True,
                            key_encipherment=False,
                            key_cert_sign=True,
                            crl_sign=True,
                            content_commitment=False,
                            data_encipherment=False,
                            key_agreement=False,
                            encipher_only=False,
                            decipher_only=False,
                    ),critical=True)
        .sign(key, hashes.SHA256())
    )

    write_file(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    write_file(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


# ----------------------------------------
# CLIENT CERT (SAE)
# ----------------------------------------
def build_client_certificate(cert_path, key_path, ca_cert_path, ca_key_path, cn, ip):
    """
    Build a leaf certificate signed by the self-signed Root CA.

    Used for:
      - SAE/client certificates
      - KME/server certificates

    Important:
      KME certificates must include both:
        DNS:<kme_name>
        IP:<kme_ip>

      Otherwise HTTPS verification fails when qkd_onbox.py calls:
        https://<kme_ip>:8443
    """

    ca_cert = x509.load_pem_x509_certificate(
        open(ca_cert_path, "rb").read()
    )

    ca_key = serialization.load_pem_private_key(
        open(ca_key_path, "rb").read(),
        password=None
    )

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=PKI["KEY_SIZE"]
    )

    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, PKI["COUNTRY"]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, PKI["ORG"]),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])

    san_list = [
        x509.DNSName(str(cn))
    ]

    if ip:
        try:
            san_list.append(
                x509.IPAddress(
                    ipaddress.ip_address(str(ip))
                )
            )
        except ValueError:
            #
            # If ip is actually a hostname/FQDN, add it as DNS SAN.
            #
            san_list.append(
                x509.DNSName(str(ip))
            )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(
            datetime.datetime.now(datetime.timezone.utc)
        )
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=PKI["VALIDITY_DAYS"])
        )
        .add_extension(
            x509.BasicConstraints(
                ca=False,
                path_length=None
            ),
            critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.CLIENT_AUTH,
                ExtendedKeyUsageOID.SERVER_AUTH
            ]),
            critical=False
        )
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False
        )
        .sign(
            ca_key,
            hashes.SHA256()
        )
    )

    write_file(
        cert_path,
        cert.public_bytes(serialization.Encoding.PEM)
    )

    write_file(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    

# ----------------------------------------
# BUILD PKI
# ----------------------------------------
def build_pem_file(key_path, cert_path, pem_path):
    key_path = Path(key_path)
    cert_path = Path(cert_path)
    pem_path = Path(pem_path)

    pem_path.parent.mkdir(parents=True, exist_ok=True)

    with open(pem_path, "wb") as out:
        with open(key_path, "rb") as key_file:
            out.write(key_file.read())

        with open(cert_path, "rb") as cert_file:
            out.write(cert_file.read())

    pem_path.chmod(0o600)

    return pem_path

def build_unique_kme_inventory(devices):
    """
    Build ordered unique KME inventory from runtime device records.

    Input device records are generated by qkd_orchestrator and may contain:

        {
            "name": "acx1",
            "kme_ip": "100.123.252.10",
            ...
        }

    or:

        {
            "name": "acx1",
            "kme": {
                "ip": "100.123.252.10"
            },
            ...
        }

    Returns:

        [
            {"name": "kme_001", "ip": "100.123.252.10"},
            {"name": "kme_002", "ip": "100.123.252.11"},
            ...
        ]
    """

    seen = set()
    kmes = []

    for device in devices:
        ip = (
            device.get("kme_ip")
            or device.get("kme", {}).get("ip")
            or device.get("qkd", {}).get("kme_ip")
        )

        if not ip:
            continue

        ip = str(ip)

        if ip in seen:
            continue

        seen.add(ip)

        index = len(kmes) + 1

        kmes.append(
            {
                "name": f"kme_{index:03d}",
                "ip": ip,
            }
        )

    return kmes
 
def build_self_signed_pki(devices, profile):
    """
    Build self-signed PKI material generated by qkd_orchestrator.

    qkd_orchestrator owns self-signed PKI generation.

    Generated layout:

        certs/self_signed/
            offbox_rootCA.crt
            offbox_rootCA.key

            sae_001/
                sae_001.crt
                sae_001.key
                sae_001.pem

            kme/
                kme_001.crt
                kme_001.key
                kme_001.pem
    """

    certs_dir = Path(CONFIG["self_signed_dir"])
    certs_dir.mkdir(parents=True, exist_ok=True)

    ca_cert = certs_dir / PKI["SELF_SIGNED_CA_CERT_NAME"]
    ca_key = certs_dir / PKI["SELF_SIGNED_CA_KEY_NAME"]

    print("Generating self-signed Root CA...")
    build_ca_certificate(
        cert_path=ca_cert,
        key_path=ca_key,
    )

    print("Generating SAE certificates...")

    for device in devices:
        sae_id = (
            device.get("sae_id")
            or device.get("qkd", {}).get("sae_id")
        )

        ip = device.get("ip")

        if not sae_id:
            raise ValueError(f"Missing sae_id in device record: {device}")

        if not ip:
            raise ValueError(f"Missing ip in device record: {device}")

        sae_dir = certs_dir / sae_id
        sae_dir.mkdir(parents=True, exist_ok=True)

        sae_cert = sae_dir / f"{sae_id}.crt"
        sae_key = sae_dir / f"{sae_id}.key"
        sae_pem = sae_dir / f"{sae_id}.pem"

        print(f"Generating cert for {sae_id}...")

        build_client_certificate(
            cert_path=sae_cert,
            key_path=sae_key,
            ca_cert_path=ca_cert,
            ca_key_path=ca_key,
            cn=sae_id,
            ip=ip,
        )

        build_pem_file(
            key_path=sae_key,
            cert_path=sae_cert,
            pem_path=sae_pem,
        )

        print(f"{sae_id} ready")

    print("Generating KME certificates...")

    kme_dir = certs_dir / "kme"
    kme_dir.mkdir(parents=True, exist_ok=True)

    kmes = build_unique_kme_inventory(devices)

    if not kmes:
        raise RuntimeError(
            "No KME IPs found in device inventory. "
            "Cannot generate KME certificates."
        )

    for kme in kmes:
        name = kme["name"]
        ip = kme["ip"]

        kme_cert = kme_dir / f"{name}.crt"
        kme_key = kme_dir / f"{name}.key"
        kme_pem = kme_dir / f"{name}.pem"

        print(f"Generating cert for {name} ({ip})...")

        build_client_certificate(
            cert_path=kme_cert,
            key_path=kme_key,
            ca_cert_path=ca_cert,
            ca_key_path=ca_key,
            cn=name,
            ip=ip,
        )

        build_pem_file(
            key_path=kme_key,
            cert_path=kme_cert,
            pem_path=kme_pem,
        )

        print(f"{name} ready")

    print("✅ PKI generation complete")



# checks: 
# openssl x509 -in vqfx1.crt -noout -subject
# openssl x509 -in vqfx1.crt -noout -issuer
# openssl x509 -in vqfx1.crt -noout -text | grep -A 2 "Subject Alternative"
# openssl x509 -in vqfx1.crt -noout -text | grep -A 2 "Extended Key Usage"
# openssl verify -CAfile /certs/offbox_rootCA.crt /certs/vqfx1.pem


# from offbox, let's copy the certs into the QKD, (my linux box): 
# scp offbox_rootCA.crt \
#    offbox_rootCA.key \      
#    ./vqfx1/vqfx1.pem \     
#    ./vqfx2/vqfx2.pem \     
#    root@10.54.13.16:/root/etsi-gs-qkd-014-referenceimplementation/certs/ 

# in the kme folder linux box 
# cp offbox_rootCA.crt root.crt
# cp offbox_rootCA.key root.key
# rm offbox_rootCA.* 
# rm -f kme_001.crt kme_001.csr kme_001.ext

# now recreate the cert kme since make clean/ make will flush all certs from folder

# STEP 1: create the extension file for the cert
# cat > kme_001.ext <<EOF
# basicConstraints=CA:FALSE
# keyUsage=digitalSignature,keyEncipherment
# extendedKeyUsage=serverAuth
# subjectAltName=IP:100.100.100.10,DNS:kme_001
# EOF

# STEP 2: create the cert and key for kme_001
# openssl req \
#   -newkey rsa:4096 -nodes \
#   -keyout kme_001.key \
#   -out kme_001.csr \
#   -subj "/C=IT/O=Juniper Networks/CN=100.100.100.10"

# STEP 3: sign the cert with the rootCA
# openssl x509 -req \
#   -in kme_001.csr \
#   -CA root.crt \
#   -CAkey root.key \
#   -CAcreateserial \
#   -days 365 \
#   -extfile kme_001.ext \
#   -out kme_001.crt

# STEP 4: create the pem file for kme_001
# cat kme_001.key kme_001.crt > kme_001.pem

# STEP 5: restart the kme containers to load the new certs
# docker restart kme1 kme2 


# cat > kme_002.ext <<EOF
# > basicConstraints=CA:FALSE
# > keyUsage=digitalSignature,keyEncipherment
# > extendedKeyUsage=serverAuth
# > subjectAltName=IP:100.100.100.11,DNS:kme_002
# > EOF

# openssl req \
# >   -newkey rsa:4096 -nodes \
# >   -keyout kme_002.key \
# >   -out kme_002.csr \
# >   -subj "/C=IT/O=Juniper Networks/CN=100.100.100.11"

# openssl x509 -req \
# >   -in kme_002.csr \
# >   -CA root.crt \
# >   -CAkey root.key \
# >   -CAcreateserial \
# >   -days 365 \
# >   -extfile kme_002.ext \
# >   -out kme_002.crt

# cat kme_002.key kme_002.crt > kme_002.pem
