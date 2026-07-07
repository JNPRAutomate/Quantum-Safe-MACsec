import os
import datetime

from scp import SCPClient, SCPException

try:
    import jcs
    onbox = True
except ImportError:
    from OpenSSL import crypto
    onbox = False

from qkd_ssh import createSSHClient


def generate_ca_certificate(ca_cert_path, ca_key_path, ca_subject):
    """
    Generate a CA certificate.
    """

    # Generate CA key
    ca_key = crypto.PKey()
    ca_key.generate_key(crypto.TYPE_RSA, 2048)

    # Create CA certificate
    ca_cert = crypto.X509()
    ca_cert.set_version(2)
    ca_cert.set_serial_number(int(uuid.uuid4()))
    ca_cert.get_subject().CN = ca_subject
    ca_cert.set_issuer(ca_cert.get_subject())
    ca_cert.set_pubkey(ca_key)
    ca_cert.gmtime_adj_notBefore(0)
    ca_cert.gmtime_adj_notAfter(5 * 365 * 24 * 60 * 60)  # 10 years
    # Add extensions
    ca_cert.add_extensions([
        crypto.X509Extension(b"subjectKeyIdentifier", False, b"hash", subject=ca_cert),
        crypto.X509Extension(b"basicConstraints", False, b"CA:TRUE"),
    ])
    # Sign the certificate with the key
    ca_cert.sign(ca_key, 'sha256')

    # Save CA certificate
    with open(ca_cert_path, "wb") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, ca_cert))

    # Save CA private key
    with open(ca_key_path, "wb") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, ca_key))
    
    return ca_cert_path, ca_key_path

def generate_client_certificate(client_cert_path, client_key_path, ca_cert_path, ca_key_path, client_subject):
    """
    Generate a client certificate signed by the CA.
    """

    # Load CA key
    ca_key = crypto.load_privatekey(crypto.FILETYPE_PEM, open(ca_key_path, 'rb').read())

    # Load CA certificate
    ca_cert = crypto.load_certificate(crypto.FILETYPE_PEM, open(ca_cert_path, 'rb').read())

    # Generate client key
    client_key = crypto.PKey()
    client_key.generate_key(crypto.TYPE_RSA, 2048)

    # Create CA certificate
    client_cert = crypto.X509()
    client_cert.set_version(2)
    client_cert.set_serial_number(int(uuid.uuid4()))

    client_cert.get_subject().CN = client_subject
    client_cert.set_issuer(ca_cert.get_subject())
    client_cert.set_pubkey(client_key)
    client_cert.gmtime_adj_notBefore(0)
    client_cert.gmtime_adj_notAfter(10*365*24*60*60)
    # Add extensions
    client_cert.add_extensions([
        crypto.X509Extension(b"basicConstraints", False, b"CA:FALSE"),
        crypto.X509Extension(b"authorityKeyIdentifier", False, b"keyid:always", issuer=ca_cert),
        crypto.X509Extension(b"keyUsage", False, b"Digital Signature, Non Repudiation, Key Encipherment, Data Encipherment"),
    ])
    # Sign the certificate with the key
    client_cert.sign(ca_key, 'sha256')

    # Save client certificate
    with open(client_cert_path, "wb+") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, client_cert))

    # Save client private key
    with open(client_key_path, "wb+") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, client_key))

    return client_cert_path, client_key_path

def is_certificate_valid(cert_path, min_valid_days=10):
    """
    Check if the certificate is valid for at least `min_valid_days` days.
    """
    with open(cert_path, 'rb') as cert_file:
        cert_data = cert_file.read()
    cert = crypto.load_certificate(crypto.FILETYPE_PEM, cert_data)
    not_after = datetime.datetime.strptime(cert.get_notAfter().decode('ascii'), '%Y%m%d%H%M%SZ')
    remaining_days = (not_after - datetime.datetime.now()).days
    return remaining_days >= min_valid_days

def get_certificates(dev, log, targets_dict):
    """
    Get the certificates from a device.
    """

    if onbox:
        local_name = dev.facts['hostname']
        ca_cert_path = os.path.join(CERTS_DIR, f'client-root-ca.crt')
        ca_key_path = os.path.join(CERTS_DIR, f'client-root-ca.key')
        client_cert_path = os.path.join(CERTS_DIR, f'{local_name}.crt')
        client_key_path = os.path.join(CERTS_DIR, f'{local_name}.key')
    else:
        # if the certificate is generated on one device -> it will be generated on all the devices
        local_name = dev.facts['hostname'].split('-re')[0]
        client = createSSHClient(local_name, username=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22)
        try:
            with SCPClient(client.get_transport()) as scp:
                scp.get(remote_path=CERTS_DIR, local_path=OFFBOX_CERTS_DIR, recursive=True)
                # ca_cert_path and ca_key_path has the same name only for tests.
                ca_cert_path = os.path.join(OFFBOX_CERTS_DIR, f'account-1286-server-ca-qukaydee-com.crt')
                ca_key_path = os.path.join(OFFBOX_CERTS_DIR, f'account-1286-server-ca-qukaydee-com.crt')
                client_cert_path = os.path.join(OFFBOX_CERTS_DIR, f'{local_name}.crt')
                client_key_path = os.path.join(OFFBOX_CERTS_DIR, f'{local_name}.key')
        except SCPException as e:
            print(f'SCP get exception error: {e}')

    return ca_cert_path, ca_key_path, client_cert_path, client_key_path

def upload_certificates(dev, cert_path, key_path, log, targets_dict):
    """
    Upload the certificates to the device.
    """

    local_name = dev.facts['hostname'].split('-re')[0]
    # TODO: line below used for testing
    local_name = targets_dict[local_name]['ip']
    client = createSSHClient(local_name, username=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22)
    try:
        with SCPClient(client.get_transport()) as scp:
            cert_files = [cert_path, key_path]
            scp.put(files=cert_files, remote_path=CERTS_DIR)
    except SCPException as e:
        print(f'SCP put exception error: {e}')

def fetch_ca_certificate(targets_dict):
    """
    Get the CA certificate from remote server.
    """
    ca_server_ip = targets_dict["CA_server"]["ca_server_ip"]
    ca_user = targets_dict["CA_server"]["ca_user"]
    ca_pass = targets_dict["CA_server"]["ca_pass"]
    ca_path = targets_dict["CA_server"]["ca_path"]
    ca_cert_name = targets_dict["CA_server"]["ca_cert_name"]
    ca_key_name = targets_dict["CA_server"]["ca_key_name"]
    client = createSSHClient(ca_server_ip, username=ca_user, password=ca_pass, port=22)
    LOCAL_PATH = CERTS_DIR if onbox else OFFBOX_CERTS_DIR
    try:
        with SCPClient(client.get_transport()) as scp:
            scp.get(remote_path=ca_path, local_path=LOCAL_PATH, recursive=True)
            ca_cert_path = os.path.join(LOCAL_PATH, ca_cert_name)
            ca_key_path = os.path.join(LOCAL_PATH, ca_key_name)
    except SCPException as e:
        print(f'SCP exception error: {e}')
    return ca_cert_path, ca_key_path

def renew_certificates(dev, log, targets_dict):
    """
    Renew certificates either if they are valid for less than `min_valid_days` days 
    or if they are not present on the device yet and then upload them to device.
    """
    if not onbox:
        # assuming all the certificates are generated for all the devices in the 1st script run
        ca_cert_path, ca_key_path, client_cert_path, client_key_path = \
            get_certificates(dev, log, targets_dict=targets_dict)
    if not os.path.isfile(client_cert_path) and not os.path.isfile(ca_cert_path):
        renew = True
    elif not is_certificate_valid(client_cert_path):
        renew = True
    else:
        renew = False
    if renew:
        if targets_dict["CA_server"]["CA_cert"]["fetch"]:
            ca_cert_path, ca_key_path = fetch_ca_certificate(targets_dict)
        elif targets_dict["CA_server"]["CA_cert"]["generate"]:
            ca_cert_path, ca_key_path = generate_ca_certificate(ca_cert_path, ca_key_path, 'Juniper CA')
        else:
            log.info('The CA certificate was manually generated and uploaded')
        generate_client_certificate(client_cert_path, client_key_path, ca_cert_path, ca_key_path, 'client')
        if not onbox:
            upload_certificates(dev, client_cert_path, client_key_path, log, targets_dict=targets_dict)
            # assumed that the CA certificate was manually generated and uploaded to devices
            if targets_dict["CA_server"]["CA_cert"]["fetch"] or targets_dict["CA_server"]["CA_cert"]["generate"]:
                upload_certificates(dev, ca_cert_path, ca_key_path, log, targets_dict=targets_dict)

def should_check_certs():
    """
    Check the validity of the certificates every 5 days.
    Checks if the current time is beween 00:00 and 02:00 and
    if the current day is a multiple of 5 starting from the 1st of the month
    """
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")
    # Check if the current time is between 00:00 and 02:00
    if "00:00" <= current_time <= "01:00":
        # Calculate the number of days passed since the start of the month
        days_passed = (now - now.replace(day=1)).days + 1
        # Check if the current day is a multiple of 5 starting from the 1st of the month
        if days_passed % 5 == 1:
                return True
    return False
