from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime, os
from newMACSEC39_ready_for_git.lib.qkd_settings import PKI
import ipaddress

# ----------------------------------------
# Helper
# ----------------------------------------
def write_file(path, data):
    with open(path, "wb") as f:
        f.write(data)

# ----------------------------------------
# CA CERT
# ----------------------------------------
def build_ca_certificate(cert_path, key_path, cn="QKD-Root"):

    key = rsa.generate_private_key(public_exponent=65537, key_size=PKI["KEY_SIZE"])
    
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, PKI["C"]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, PKI["O"]),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])


    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now() + datetime.timedelta(days=PKI["DAYS"]))
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

    ca_cert = x509.load_pem_x509_certificate(open(ca_cert_path, "rb").read())
    ca_key = serialization.load_pem_private_key(open(ca_key_path, "rb").read(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=PKI["KEY_SIZE"])

    subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, PKI["C"]),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, PKI["O"]),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ])

    san_list = [
         x509.DNSName(cn)
         #x509.IPAddress(ipaddress.ip_address(ip))
     ]

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=PKI["DAYS"]))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
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
        .sign(ca_key, hashes.SHA256())
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
# BUILD PKI
# ----------------------------------------
 
def build_self_signed_pki(devices, profile):

    os.makedirs("certs", exist_ok=True)

    ca_cert = f"certs/{PKI['CA_CERT_NAME']}"
    ca_key = f"certs/{PKI['CA_KEY_NAME']}"
    
    print("Generating CA...")
    build_ca_certificate(ca_cert, ca_key)

    # this loop cycle generates certs/vqfx1_001/vqfx1_001.crt
    # for dev in devices:
    #     name = dev["name"]
    #     ip = dev["ip"]
# 
    #     dev_dir = f"certs/{name}"
    #     os.makedirs(dev_dir, exist_ok=True)
# 
    #     cert_path = f"{dev_dir}/{name}.crt"
    #     key_path = f"{dev_dir}/{name}.key"
    #     pem_path = f"{dev_dir}/{name}.pem"
# 
    #     print(f"Generating cert for {name}...")
# 
    #     build_client_certificate(
    #         cert_path,
    #         key_path,
    #         ca_cert,
    #         ca_key,
    #         cn=name,
    #         ip=ip
    #     )
# 
    #     with open(pem_path, "wb") as f:
    #         f.write(open(key_path, "rb").read())
    #         f.write(open(cert_path, "rb").read())
# 
    #     print(f"{name} ready")

    #  this loop cycle generates now certs/sae_001/sae_001.crt instead of certs/vqfx1_001/vqfx1_001.crt
    for i, dev in enumerate(devices, start=1):

        sae_name = dev["sae_id"]
        ip = dev["ip"]

        dev_dir = f"certs/{sae_name}"
        os.makedirs(dev_dir, exist_ok=True)

        cert_path = f"{dev_dir}/{sae_name}.crt"
        key_path = f"{dev_dir}/{sae_name}.key"
        pem_path = f"{dev_dir}/{sae_name}.pem"

        print(f"Generating cert for {sae_name}...")

        build_client_certificate(
            cert_path,
            key_path,
            ca_cert,
            ca_key,
            cn=sae_name,
            ip=ip
        )

        with open(pem_path, "wb") as f:
            f.write(open(key_path, "rb").read())
            f.write(open(cert_path, "rb").read())

        print(f"{sae_name} ready")
    
    print("✅ PKI generation complete")

    
    print("\n=== ACTION REQUIRED: INSTALL CERTS ON KME ===\n")

    kme_certs_dir = "/root/etsi-gs-qkd-014-referenceimplementation/certs"

    print("Run the following from offbox:\n")

    print("# Copy the offbox Root CA certificate and private key to the KME host")
    print(f"scp certs/{PKI['CA_CERT_NAME']} root@<ubuntu-ip>:{kme_certs_dir}/")
    print(f"scp certs/{PKI.get('CA_KEY_NAME', 'offbox_rootCA.key')} root@<ubuntu-ip>:{kme_certs_dir}/")

    print("")
    print("WARNING: the Root CA private key is sensitive.")
    print("Copy it only to the trusted KME certificate-generation host.")
    print("Do not copy SAE client certificates to generate KME server certificates.")
    print("")

    print("Then, on the KME host, regenerate the KME/server certificates using the copied offbox Root CA.")
    print("After regenerating the KME certificates, copy/update them inside the KME containers and restart the KME services.\n")


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
