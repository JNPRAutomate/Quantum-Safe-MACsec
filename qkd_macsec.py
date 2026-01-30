#!/usr/bin/env python3

"""
Macsec with keys from QKD

This script allows the JUNOS device to fetch
keys from KME and update the MACSEC CAK accordingly.

This is an event script and should be kept in "/var/db/scripts/events/qkd.py"
This script can be scheduled using event-options config in JUNOS.
Currently the user is harcoded as "lab".

Copyright 2025 Juniper Networks, Inc. All rights reserved.
Licensed under the Juniper Networks Script Software License (the "License").
You may not use this script file except in compliance with the License, which
is located at
http://www.juniper.net/support/legal/scriptlicense/
Unless required by applicable law or otherwise agreed to in writing by the
parties, software distributed under the License is distributed on an "AS IS"
BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

Date: 2025-11-27
Version: 3.2

"""
__version__ = "v3.2.0"

import requests
from jnpr.junos import Device
from jnpr.junos.utils.config import Config
import paramiko
from paramiko import SSHException, AuthenticationException
from lxml import etree
from lxml.builder import E
import argparse
from threading import Thread, Lock
import logging
import logging.handlers
import json
import copy
import base64
import uuid
import hashlib
import re
import subprocess
import os
import datetime
from scp import SCPClient, SCPException
import os.path
import time
import Profile
import ipaddress

try:
    import jcs
    onbox = True
except ImportError:
    from OpenSSL import crypto
    onbox = False
    print("onbox {}".format(onbox))
    print(datetime.datetime.now())
    


CUR_DIR = '/var/home/admin'


##### if not onbox:
#####     # out = subprocess.check_output(["pwd"])
#####     # subprocess.run(["mkdir", "certs"], stderr=subprocess.PIPE)
#####     # OFFBOX_CERTS_DIR = out.strip().decode("utf-8") + '/certs/'
#####     OFFBOX_CERTS_DIR = f'{CUR_DIR}/certs'

OFFBOX_CERTS_DIR = f'{CUR_DIR}/certs'

# etsi-gs-qkd-014-referenceimplementation variables
# https://github.com/cybermerqury/etsi-gs-qkd-014-referenceimplementation
DATABASE_PORT = '10000'
DATABASE_HOST = '9.173.9.102'
DATABASE_USER = 'db_user'
DATABASE_PASSWORD = 'db_password'

DATABASE_URL = f'postgres://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/key_store'

# CURDIR=$(dir $(realpath $(lastword $(MAKEFILE_LIST))))
CERTS_DIR=f'{CUR_DIR}/certs/'

# ETSI 014 reference implementation configuration.
ETSI_014_REF_IMPL_DB_URL = f'{DATABASE_URL}'
ETSI_014_REF_IMPL_IP_ADDR = f'{DATABASE_HOST}'
ETSI_014_REF_IMPL_NUM_WORKER_THREADS = 2
ETSI_014_REF_IMPL_PORT_NUM = 443
ETSI_014_REF_IMPL_TLS_CERT = f'{OFFBOX_CERTS_DIR}/kme_001.crt'
ETSI_014_REF_IMPL_TLS_PRIVATE_KEY = f'{OFFBOX_CERTS_DIR}/kme_001.key'
ETSI_014_REF_IMPL_TLS_ROOT_CRT = f'{OFFBOX_CERTS_DIR}/root.crt'


# match a pipe character | followed by {...} JSON. Used for parsing device output containing inline JSON.
desc_re = re.compile(r'\|({.*})')
CA_CERT = f'{CERTS_DIR}/root.crt'

# API URL
ADDR = f'{ETSI_014_REF_IMPL_IP_ADDR}:{ETSI_014_REF_IMPL_PORT_NUM}'
KME_URL_T = f'https://{ADDR}/api/v1/keys'
# this creates https://9.173.9.102:443/api/v1/keys
CKN_PREFIX = 'abcd1234abcd5678abcd1234abcd5678'

# LOG_FILENAME = '/var/log/qkd_trace.log'
LOG_FILENAME = f'{CUR_DIR}/qkd_test.log'
# KEYID_JSON_FILENAME = '/var/log/{}last_key.json'
##### KEYID_JSON_FILENAME = '/home/testuser/etsi-gs-qkd-014-referenceimplementation-main/{}_last_key_test.json'
KEYID_JSON_FILENAME = '/var/home/admin/{}last_key.json'
# KEYID_JSON_FILENAME = '/home/administrator/{}_last_key_test.json'
# CERTS_DIR = '/var/tmp/acx1/'
# CERTS_DIR = '/home/testuser/etsi-gs-qkd-014-referenceimplementation-main/certs'

# Useful for debugging performance issues.
prof = Profile.Profile(file="/var/home/admin/scaler.prof", verbose=True, enabled=True, mode="w+")

threads = []

# Decorator func for threading
def background(func):
    def bg_func(*args, **kwargs):
        t = Thread(target=func, args=args, kwargs=kwargs)
        t.setDaemon(True)
        t.start()
        threads.append(t)
    return bg_func


@background
def req_thread(tnum, reqs, targets_dict, log):
    prof.start("Thread-{0}".format(tnum))
    for device in reqs:
        print(device)
        with Device(host=targets_dict[device]['ip'], user=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22) as dev:
            if should_check_certs():
                # print(targets_dict[dev.facts['hostname'].split('-re')[0]]['ip'])
                # prof.start("renew_certificates()-Thread {0} Device {1}".format(tnum,device))
                renew_certificates(dev, log, targets_dict=targets_dict)
                # prof.stop("renew_certificates()-Thread {0} Device {1}".format(tnum,device))
            prof.start("check_and_apply_initial_config()-Thread {0} Device {1}".format(tnum,device))
            print("start check_and_apply_initial_config")
            check_and_apply_initial_config(dev, targets_dict, log)
            print("stop check_and_apply_initial_config")
            prof.stop("check_and_apply_initial_config()-Thread {0} Device {1}".format(tnum,device))
            prof.start("process()-Thread {0} Device {1}".format(tnum,device))
            print("start process")
            process(dev, targets_dict, log)
            print("stop process")
            prof.stop("process()-Thread {0} Device {1}".format(tnum,device))
    prof.stop("Thread-{0}".format(tnum))

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
    # ca_subj = ca_cert.get_subject()
    # ca_subj.commonName = ca_subject
    ca_cert.get_subject().CN = ca_subject
    # ca_cert.set_issuer(ca_subj)
    ca_cert.set_issuer(ca_cert.get_subject())
    ca_cert.set_pubkey(ca_key)
    ca_cert.gmtime_adj_notBefore(0)
    ca_cert.gmtime_adj_notAfter(5 * 365 * 24 * 60 * 60)  # 10 years
    # Add extensions
    ca_cert.add_extensions([
        crypto.X509Extension(b"subjectKeyIdentifier", False, b"hash", subject=ca_cert),
        # crypto.X509Extension(b"authorityKeyIdentifier", False, b"keyid:always", issuer=ca_cert),
        # crypto.X509Extension(b"authorityKeyIdentifier", False, b"keyid", issuer=ca_cert),
        crypto.X509Extension(b"basicConstraints", False, b"CA:TRUE"),
        # crypto.X509Extension(b"keyUsage", False, b"keyCertSign, cRLSign"),
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

    # client_subj = client_cert.get_subject()
    # client_subj.commonName = client_subject
    client_cert.get_subject().CN = client_subject
    client_cert.set_issuer(ca_cert.get_subject())
    client_cert.set_pubkey(client_key)
    client_cert.gmtime_adj_notBefore(0)
    client_cert.gmtime_adj_notAfter(10*365*24*60*60)
    # Add extensions
    client_cert.add_extensions([
        crypto.X509Extension(b"basicConstraints", False, b"CA:FALSE"),
        # crypto.X509Extension(b"subjectKeyIdentifier", False, b"hash", subject=client_cert),
        crypto.X509Extension(b"authorityKeyIdentifier", False, b"keyid:always", issuer=ca_cert),
        # crypto.X509Extension(b"authorityKeyIdentifier", False, b"keyid", issuer=ca_cert),
        # crypto.X509Extension(b"extendedKeyUsage", False, b"clientAuth"),
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

def createSSHClient(device, username, password, port=22, retries=3, delay=5, timeout=10):
    """
    Create an SSH connection to a device with retries and timeout.
    
    Parameters:
        device (str): Hostname or IP of the device
        username (str): SSH username
        password (str): SSH password
        port (int): SSH port (default 22)
        retries (int): Number of retry attempts (default 3)
        delay (int): Delay in seconds between retries (default 5)
        timeout (int): SSH connection timeout in seconds (default 10)
        
    Returns:
        paramiko.SSHClient object if successful, None if connection fails
    """
    for attempt in range(1, retries + 1):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=device,
                port=port,
                username=username,
                password=password,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout
            )
            print(f"Connected to {device} on attempt {attempt}")
            return client
        except (SSHException, AuthenticationException, TimeoutError) as e:
            print(f"Attempt {attempt} failed to connect to {device}: {e}")
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"Failed to connect to {device} after {retries} attempts.")
                return None


# def createSSHClient(device, username, password, port=22):
#     """
#     Create the ssh connection to the device
#     """
#     try:
#         client = paramiko.SSHClient()
#         client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#         client.connect(device, port=port, username=username, password=password)
#     except SSHException as e:
#         print(f'SSH connection error: {e}')
#     return client

def get_certificates(dev, log, targets_dict):
    """
    Get the certificates from a device.
    """

    if onbox:
        local_name = dev.facts['hostname']
#####        ca_cert_path = os.path.join(CERTS_DIR, f'account-1286-server-ca-qukaydee-com.crt')
        ca_cert_path = os.path.join(CERTS_DIR, f'client-root-ca.crt')
#####        ca_key_path = os.path.join(CERTS_DIR, f'ca_JUNIPER.key')
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
                # local_name = dev.facts['hostname'].split('-re')[0]
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

def initialize_logging(args):
    """
    Initializes logging based on provided arguments.
    """
    LOG_NOTICE = 25
    logging.addLevelName(LOG_NOTICE, "NOTICE")

    def log_notice(self, message, *args, **kwargs):
        if self.isEnabledFor(LOG_NOTICE):
            self._log(LOG_NOTICE, message, args, **kwargs)
    logging.Logger.notice = log_notice

    LOG_LEVELS = [logging.ERROR, logging.WARNING, LOG_NOTICE, logging.INFO, logging.DEBUG]
    logging.captureWarnings(True)

    verbosity = min(args.verbose, len(LOG_LEVELS) - 1)
    log_level = LOG_LEVELS[verbosity]

    log = logging.getLogger()
    formatter = logging.Formatter('%(asctime)s %(threadName)-10s %(name)s %(levelname)-8s %(message)s')
    stderr = logging.StreamHandler()
    stderr.setFormatter(formatter)
    log.addHandler(stderr)

    if args.trace:
        fh = logging.FileHandler('trace.log')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        log.setLevel(logging.DEBUG)
        log.addHandler(fh)
        stderr.setLevel(log_level)
    elif onbox:
        fh = logging.handlers.RotatingFileHandler(LOG_FILENAME, maxBytes=10000000, backupCount=5)
        fh.setLevel(logging.INFO)
        fh.setFormatter(formatter)
        log.setLevel(logging.INFO)
        log.addHandler(fh)
        stderr.setLevel(log_level)
    else:
        log.setLevel(log_level)

    log.info('Logging modules initialized successfully')
    print('Logging modules initialized successfully')
    return log

def get_previous_key_ids(log, name):
    """
    Retrieves the previous key IDs from the JSON file.
    """
    try:
        with open(KEYID_JSON_FILENAME.format(name), 'r') as openfile:
            return json.load(openfile)
    except FileNotFoundError:
        # File is not created yet. Maybe this script is being run for 1st time.
        log.info(f"File to read previous keyId(s) not found")
        return {}
    except ValueError:
        # File is present but is blank
        log.info(f"No previous keyId(s) found in file")
        return {}

def save_key_ids(key_dict, local_name):
    """
    Saves the key IDs to the JSON file.
    """
    with open(KEYID_JSON_FILENAME.format(local_name), 'w+') as outfile:
        outfile.write(json.dumps(key_dict))
        print(f'Saved the key IDs to the JSON file: {KEYID_JSON_FILENAME.format(local_name)}')

##### def fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_url, key_id=None, additional_slave_SAE_IDs=None):
def fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id, additional_slave_SAE_IDs=None):
    """
    Fetches keys from the KME.
    """
    if onbox:
        client_crt  = CERTS_DIR + local_name + '.crt'
        print(f"client_crt: {client_crt}")
        client_key  = CERTS_DIR + local_name + '.key'
        print(f"client_key: {client_key}")
        CLIENT_CERT = (client_crt, client_key)
#####        CA_CERT = CERTS_DIR + 'account-1286-server-ca-qukaydee-com.crt'
        print(f"CLIENT_CERT: {CLIENT_CERT}")
        CA_CERT = CERTS_DIR + 'client-root-ca.crt'
        print(f"CA_CERT: {CA_CERT}")
    else:
        # the get_certificates() function is getting the certificates together with the last folder from OFFBOX_CERTS_DIR
        # client_crt  = OFFBOX_CERTS_DIR + str(CERTS_DIR).split('/')[-2] + '/' + local_name + '.crt'
        # client_key  = OFFBOX_CERTS_DIR + str(CERTS_DIR).split('/')[-2] + '/' + local_name + '.key'
        client_crt  = OFFBOX_CERTS_DIR + '/' + local_name + '.crt'
        client_key  = OFFBOX_CERTS_DIR + '/' + local_name + '.key'
        CLIENT_CERT = (client_crt, client_key)
        CA_CERT = OFFBOX_CERTS_DIR + '/' + 'root.crt'
        print(client_crt)
    try:
        if key_id:
            print(f'retrieving key: {key_id}')
            # response = session.get(f"{kme_url}dec_keys?key_ID={key_id}", verify=False, cert=CLIENT_CERT)
            # response = session.get(f"{kme_url}dec_keys?key_ID={key_id}", verify=CA_CERT, cert=CLIENT_CERT)
            # response = session.get(f"{kme_url}172.30.198.54/dec_keys?key_ID={key_id}", verify=CA_CERT, cert=CLIENT_CERT)
            # https://kme-1.acct-1286.etsi-qkd-api.qukaydee.com/api/v1/keys/mx304-10/enc_keys
            # response = session.get(f"{kme_url}{remote_mnmgt_add}/dec_keys?key_ID={key_id}", verify=CA_CERT, cert=CLIENT_CERT)
            # response = session.get(f"https://{ADDR}/{remote_mnmgt_add}/dec_keys?key_ID={key_id}", verify=CA_CERT, cert=CLIENT_CERT)
            response = session.get(f"{kme_host}/api/v1/keys/{remote_mnmgt_add}/dec_keys?key_ID={key_id}", verify=CA_CERT, cert=CLIENT_CERT, headers={"Content-Type": "application/json"})
#####            response = session.get(f"https://{ADDR}/api/v1/keys/{remote_mnmgt_add}/dec_keys?key_ID={key_id}", cert=CLIENT_CERT, verify=CA_CERT)
            #response = session.get(f"https://{ADDR}/api/v1/keys/sae_001/dec_keys?key_ID={key_id}", cert=CLIENT_CERT, verify=CA_CERT)
#####            print(remote_mnmgt_add)
#####            print(f"https://{ADDR}/api/v1/keys/{remote_mnmgt_add}/dec_keys?key_ID={key_id}")
            #print(f"https://{ADDR}/api/v1/keys/sae_001/dec_keys?key_ID={key_id}")
            print(response.status_code)
        else:
            # url = f"https://{KME_URL_T}/{remote_mnmgt_add}/enc_keys?number=1&size=128"
            # print(f"https://{ADDR}/api/v1/keys/{remote_mnmgt_add}/enc_keys?number=1&size=128")
#####            response = session.get(f"{ADDR}/api/v1/keys/{remote_mnmgt_add}/enc_keys", verify=CA_CERT, cert=CLIENT_CERT)
#####            response = session.post(f"{ADDR}/api/v1/keys/{remote_mnmgt_add}/enc_keys", cert=CLIENT_CERT, verify=CA_CERT, headers={"Content-Type": "application/json"}, json={"additional_slave_SAE_IDs":["mx10008-24","mx10008-23"]})
#####            response = session.po('{ADDR}/api/v1/keys/{remote_mnmgt_add}/enc_keys', verify=CA_CERT, cert=CLIENT_CERT)
            response = session.get(f"{kme_host}/api/v1/keys/{remote_mnmgt_add}/enc_keys", verify=CA_CERT, cert=CLIENT_CERT, headers={"Content-Type": "application/json"})


            # response = session.get(f"{kme_url}172.30.198.55/enc_keys", verify=CA_CERT, cert=CLIENT_CERT)
            # print(f'{kme_url}{remote_mnmgt_add}/enc_keys')
            # response = session.get(f"{kme_url}{remote_mnmgt_add}/enc_keys", verify=CA_CERT, cert=CLIENT_CERT)
            # curl --cacert account-1286-server-ca-qukaydee-com.crt --cert mx304-9.crt --key mx304-9.key -X POST -H "Content-Type: application/json" "https://kme-1.acct-1286.etsi-qkd-api.qukaydee.com/api/v1/keys/mx304-10/enc_keys" -d '{"additional_slave_SAE_IDs":["mx10008-24","mx10008-23"]}'
            # remote_mnmgt_add is the master device
            # response = session.post(f"{kme_url}{remote_mnmgt_add}/enc_keys", cert=CLIENT_CERT, verify=CA_CERT, headers={"Content-Type": "application/json"}, json={"additional_slave_SAE_IDs":["mx10008-24","mx10008-23"]})
            # curl --cacert root.crt --cert client_crt --key client_key -X POST -H "Content-Type: application/json" "https://kme-1.acct-1286.etsi-qkd-api.qukaydee.com/api/v1/keys/mx304-10/enc_keys" -d '{"additional_slave_SAE_IDs":["mx10008-24","mx10008-23"]}'
#####            response = session.get(f"https://{ADDR}/api/v1/keys/{remote_mnmgt_add}/enc_keys?number=1&size=128", cert=CLIENT_CERT, verify=CA_CERT)
            #response = session.get(f"https://{ADDR}/sae_002/enc_keys?number=1&size=128", cert=CLIENT_CERT, verify=CA_CERT)
            #response = session.get(f"https://{ADDR}/sae_002/enc_keys?number=1&size=128", cert=CLIENT_CERT, verify=CA_CERT)
            #response = session.get(f"https://{KME_URL_T}/{remote_mnmgt_add}/enc_keys?number=1&size=128", cert=CLIENT_CERT, verify=CA_CERT)
            # TODO:
            print(response.status_code)
            print(response.text)
            print(response)
            # print("Parsed JSON:")
            #print(json.dumps(response_json, indent=4))
            # print(f"https://{ADDR}/api/v1/keys/{remote_mnmgt_add}/enc_keys?number=1&size=128")
            #print(f"https://{ADDR}/api/v1/keys/sae_002/enc_keys?number=1&size=128")
            #print(json.loads(response))
            # curl -i --tlsv1.3 --cacert "${CERTS_DIR}"/root.crt --key "${CERTS_DIR}"/sae_001.key --cert "${CERTS_DIR}"/sae_001.crt "https://${ADDR}/sae_002/enc_keys?number=1&size=24"
        try:
            if response.status_code == 200:
                response_json = response.json()
            #print(response_json.get("keys", [])[0])
            # response = re.search(r'\{"keys":\[\{(.*?)\}\]\}', response)
            # json_str = '{"' + match.group(1) + '}'
            # json_data = json.loads(json_str)
            #print(f"local_name: {local_name}: {response.json()}")
            # return json_data
            #return response.json()
                return response_json
            else:
                print(f'Request failed with status code {response.status_code}')
                print(response.text)
        except requests.RequestException as e:
            log.error(f"KME request failed: {e.response.text}")
            print(f"KME request failed: {e.response.text}")
            return None
    except requests.RequestException as e:
        log.error(f"KME request failed: {e.response.text}")
        print(f"KME request failed: {e.response.text}")
        return None

def check_and_apply_initial_config(dev, targets_dict, log):
    """
    Check if the initial macsec configuration is already applied and 
    apply it on the device if not.
    This function will be trigerred only one time on the 1st script run.
    """
    device_name= dev.facts['hostname'].split('-re')[0]
    device_ip = targets_dict[device_name]["ip"]
    c_a= targets_dict["CA_server"]["c_a"] # connectivity-association
    interfaces = targets_dict[device_name]["interfaces"]
    kme_name = targets_dict[device_name]["kme"]["kme_name"]
    kme_ip = targets_dict[device_name]["kme"]["kme_ip"]
    start_time = targets_dict["system"]["event_options"]["start_time"]

    # Check if configuration is already present on device
    config_check = dev.rpc.get_config(filter_xml=E.configuration(E.security(E.macsec(E('connectivity-association', E.name(c_a))))))
    if config_check.find('.//name') is not None:
        log.info("Initial macsec configuration already applied on the device: {}.".format(device_name))
        return

    # defining the list of commands to be applied for the initial macsec configuration
    initial_macsec_commands = [
        f"set security macsec connectivity-association {c_a} cipher-suite gcm-aes-xpn-256",
        f"set security macsec connectivity-association {c_a} security-mode static-cak",
        f"set security macsec connectivity-association {c_a} pre-shared-key ckn abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678",
        f"set security macsec connectivity-association {c_a} pre-shared-key cak abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678"
    ]
    for interface in interfaces:
        initial_macsec_commands.extend([
            f"set security macsec interfaces {interface} apply-macro qkd kme-ca false",
            f"set security macsec interfaces {interface} apply-macro qkd kme-host {kme_name}",
            f"set security macsec interfaces {interface} apply-macro qkd kme-port 443",
            f"set security macsec interfaces {interface} connectivity-association {c_a}",
            f"set security macsec interfaces {interface} apply-macro qkd kme-keyid-check true",
            # f"set system static-host-mapping {kme_name} inet {kme_ip}",
            f"set system static-host-mapping {device_name} inet {device_ip}"
        ])

    if onbox:
        initial_macsec_commands.extend([
            # even-options configuration
            f"set event-options generate-event every10mins time-interval 600 start-time {start_time}",
            f"set event-options policy qkd events every10mins",
            f"set event-options policy qkd then event-script ETSIA_v3.1.0_Phase2_v1.py",
#####            f"set event-options event-script file onbox.py python-script-user remote",
            f"set event-options event-script file ETSIA_v3.1.0_Phase2_v1.py python-script-user admin",
            f"set event-options traceoptions file script.log",
            f"set event-options traceoptions file size 10m",
        ])

    log.info("Applying initial macsec configuration on the device: {}.".format(device_name))
    try:
        dev.timeout = 300
        with Config(dev) as cu:
            cu.lock()
            for command in initial_macsec_commands:
                cu.load(command, format='set')
            cu.commit()
            log.info("Initial macsec configuration applied successfully on the device: {}.".format(device_name))
    except Exception as e:
        log.error(f'Initial macsec configuration commit failed: {e}')
    finally:
        cu.unlock()
        # sleep for 60 seconds so that config is applied on all the devices
        time.sleep(60)

def get_key_id_from_master(dev, log, targets_dict):
    """
    Get the Key_ID from the master device.
    """

    master_name = targets_dict["qkd_roles"]['master']
    print(master_name)
    master_key_id_file = KEYID_JSON_FILENAME.format(master_name)
    print(master_key_id_file)
    if not onbox:
        if os.path.exists(master_key_id_file):
            return master_key_id_file
        else:
            print('the master key id file does not exists')
            return None
    else:   
        client = createSSHClient(master_name, username=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22)
        print(client)
        try:
            with SCPClient(client.get_transport()) as scp:
                scp.get(remote_path=master_key_id_file, local_path=master_key_id_file, preserve_times=True)
        except SCPException as e:
            print(f'SCP get exception error: {e}')
            return None
    return master_key_id_file

def process(dev, targets_dict, log):
    """
    Processes the JUNOS device to fetch keys and update the MACSEC CAK accordingly.
    """
    conf_filter = (
        E.configuration(
            E.security(
                E.macsec()
            )
        )
    )

    config_xml = dev.rpc.get_config(filter_xml=conf_filter)
    log.debug(etree.tostring(config_xml, pretty_print=True).decode())

    macsec_xml = config_xml.find('security/macsec')

    session = requests.Session()
    new_key_dict = {}

    # placeholders for new macsec and interfaces config
    qkd_macsec_xml = E.macsec()

    commit = False

    ca_name = macsec_xml.findtext('connectivity-association/name')
    print(f"ca_name: {ca_name}")
    qkd_ca_xml = copy.deepcopy(macsec_xml.find(f'connectivity-association[name="{ca_name}"]'))

    local_name = dev.facts['hostname'].split('-re')[0]
    print(f"local_name: {local_name}")
    kme_host = targets_dict[local_name]["kme"]["kme_name"]
    #kme_url = KME_URL_T.format(kme_host)
    kme_url = KME_URL_T
    print(f"kme_host: {kme_host}")
    log.info('base url: ' + kme_url)

    if targets_dict["qkd_roles"]['master'] == local_name:
        log.info(local_name + ' is Master')
        print(local_name + ' is Master')
        remote_mnmgt_add = targets_dict["qkd_roles"]['slave']
        print(f"remote_mnmgt_add: {remote_mnmgt_add}")
        additional_slave_SAE_IDs = targets_dict["qkd_roles"]['additional_slave_SAE_IDs']
#####        r = fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_url, additional_slave_SAE_IDs=additional_slave_SAE_IDs)
        r = fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id=None, additional_slave_SAE_IDs=None)
        print(f"response: {r}")
        if r is not None:
#####            print('KME: [GET] Get Keys API: ' + kme_url + remote_mnmgt_add + '/enc_keys')
            print('KME: [GET] Get Keys API: for {} {}'.format(remote_mnmgt_add,r))
            key = r['keys'][0]
            log.info(f'Received KeyId: {key["key_ID"]}')
            print('Received KeyId:' + key['key_ID'])
            new_key_dict[local_name] = key['key_ID'].strip()
    else:
        log.info(local_name + ' is Slave')
        print(local_name + ' is Slave')
        remote_mnmgt_add = targets_dict["qkd_roles"]['master']
        # extract keyID from master_key_id_file dictionary, retrying to get the keyID every 5 secs for 5 mins
        retries = 0
        while True:
            if retries > 60:
                log.error(f"local_name: {local_name} too many retries while getting the master_key_id")
                print(f"local_name: {local_name} too many retries while getting the master_key_id")
                # break
                return
            else:
                master_key_id_file = get_key_id_from_master(dev, log, targets_dict)
                print(master_key_id_file)
                # master_key_id_file_path = KEYID_JSON_FILENAME.format(targets_dict["qkd_roles"]['master'])
                # check if the new master_key_id was updated for the master device (.json file created less than 10 mins ago - in the case the script is running every 10 mins)
                if os.path.isfile(master_key_id_file) and ((datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(master_key_id_file))) > datetime.timedelta(minutes=10)):
                    retries += 1
                    log.error(f"local_name: {local_name} master new KeyId not yet available, Retrying {retries}")
                    print(f'local_name: {local_name} master new KeyId not yet available, Retrying {retries}')
                    time.sleep(5)
                    continue
                last_key_dict = get_previous_key_ids(log, local_name)
                print(last_key_dict)
                # # assign none value so that it can be compared with the master value in the while loop
                last_key_dict = {local_name: "None"} if not last_key_dict else last_key_dict
                print(f"local_name: {local_name} last_key_dict: {last_key_dict}")
                master_key_dict = get_previous_key_ids(log, remote_mnmgt_add)
                new_key_dict[local_name] = master_key_dict[remote_mnmgt_add]
                print(new_key_dict[local_name])
                print(f"local_name: {local_name} master_key_dict: {master_key_dict}")
                print(f"local_name: {local_name} new_key_dict: {new_key_dict}")
                if last_key_dict[local_name] == new_key_dict[local_name]:
                    retries += 1
                    log.info(f'local_name: {local_name} Same KeyId: {last_key_dict[local_name]}, Retrying {retries}')
                    print(f'local_name: {local_name} Same KeyId: {last_key_dict[local_name]}, Retrying {retries}')
                    time.sleep(5)
                    continue
                else:
                    break

#####        r = fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_url, key_id=new_key_dict[local_name])
        r = fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id=new_key_dict[local_name], additional_slave_SAE_IDs=None)
        print(f"response: {r}")
        if r is not None:
            print('KME: [GET] Get Keys API: for {} {}'.format(remote_mnmgt_add,r))
            key = r['keys'][0]
            log.info(f'Received KeyId: {key["key_ID"]}')
            print('Received KeyId:' + key['key_ID'])
            new_key_dict[local_name] = key['key_ID'].strip()

        else:
            return

    # Junos wants len(ckn) == 64 and UUID is 32 hex digid (128 bit)
    # change size directly on API
    qkd_ca_xml.find('pre-shared-key/ckn').text = CKN_PREFIX + uuid.UUID(key['key_ID']).hex
    print("ckn {}".format(qkd_ca_xml.find('pre-shared-key/ckn').text))
    qkd_ca_xml.find('pre-shared-key/cak').text = str(base64.b64decode(key['key']).hex())[:64]
    print("cak {}".format(qkd_ca_xml.find('pre-shared-key/cak').text))
    qkd_macsec_xml.append(qkd_ca_xml)
    commit = True
    
    # Adding Root Authentication**
    root_auth_xml = E.system(
        E("root-authentication",
            E("encrypted-password", targets_dict[local_name]["root_enc_pass"])
        )
    )

    qkd_config_xml = E.configuration(E.security(qkd_macsec_xml))
    # qkd_config_xml = E.configuration(root_auth_xml, E.security(qkd_macsec_xml))
    
    print(f"local_name: {local_name} commit {commit}")
    print("Configuration {}".format(etree.tostring(qkd_config_xml,pretty_print=True).decode()))
    log.info(etree.tostring(qkd_config_xml, pretty_print=True).decode())
    if commit:
        try:
            dev.timeout = 800
            print(commit)
            # with Config(dev, mode = 'exclusive') as cu:
            with Config(dev) as cu:
                cu.lock()
                # cu.load(qkd_config_xml, merge=True)
                print('before load and commit')
                # print('before load and commit')
                cu.load(qkd_config_xml, format = 'xml', merge = True)
                # cu.load(qkd_config_xml, format = 'xml')
                # cu.load(qkd_config_xml, format = 'xml', update = True)
                print(cu.diff())
                # cu.commit(confirm = 15, timeout = 900)
                cu.commit()
                log.info('QKD commit passed')
                print(f'========================== Device: {local_name} =============================')
                print('========================== script run SUCCESS =============================')
                log.info(f'========================== Device: {local_name} =============================')
                log.info('========================== script run SUCCESS =============================')
        except Exception as e:
            log.error(f'QKD commit failed: {e}')
            print(f'========================== Device: {local_name} =============================')
            print('========================== script run FAILED =============================')
            log.info(f'========================== Device: {local_name} =============================')
            log.info('========================== script run FAILED =============================')
        finally:
            cu.unlock()
        # Save the key-ID in a persistant file
        save_key_ids(new_key_dict, local_name)
    # time.sleep(10)
    mka_session_info_xml = dev.rpc.get_mka_session_information({'format':'text'}, summary=True)
    print(f'----------------------{local_name}-----------------------------')
#####    print(etree.tostring(mka_session_info_xml, pretty_print=True).decode())

# def get_args():
#     """
#     Defines and parses command-line arguments.
#     """
#     parser = argparse.ArgumentParser()
#     # parser.add_argument('targets', metavar='host', nargs='*', help="list of target hosts")
#     parser.add_argument("-t", "--threads", type=int, help="Number of threads to use")
#     parser.add_argument('-v','--verbose', default=0, action='count', help="increase verbosity level")
#     parser.add_argument('-tr','--trace', action='store_true', help="dump debug level logs to trace.log file")
#     return parser.parse_args()


def get_args():
    """
    Defines and parses command-line arguments for the script.
    
    Returns:
        argparse.Namespace: Parsed command-line arguments
    """
    parser = argparse.ArgumentParser(
        description="Script to configure devices with QKD MACSEC and fetch keys from KME."
    )

    # Number of threads to run in parallel
    parser.add_argument(
        "-t", "--threads",
        type=int,
        default=1,
        help="Number of threads to use for processing devices (default: 1)."
    )

    # Verbosity level: can be increased with multiple -v flags
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity. Use -v, -vv, -vvv for more detailed logs."
    )

    # Trace flag: output debug-level logs to a file
    parser.add_argument(
        "-tr", "--trace",
        action="store_true",
        help="Enable trace logging and dump debug-level logs to trace.log file."
    )

    return parser.parse_args()

def main():
    global prof

    args = get_args()

    log = initialize_logging(args)

    # The dictionary containing target devices and their respective information
    # (ex: interfaces, kme, connectivity-association etc.) forming a 121 tunnel 
    # for a number of channelled devices
    targets_dict = {
        "system": {
            "maxthreads": 1,
            "event_options": {
#####                "start_time": "yyyy-mm-dd.hh:mm(+|-)hhmm"
                "start_time": "2025-3-23.13:00:00"
            }
        },
        "secrets": {
            "username": "admin",
            "password": "admin123!"
        },
        "CA_server": {
            "CA_cert": {
                "fetch": False,
                "generate": False
            },
            "c_a":  "CA_basic", # connectivity-association
            "ca_server_ip": "ca_server_ip",
            "ca_path": "ca_path",
            "ca_cert_name": "ca_cert_name",
            "ca_key_name": "ca_key_name",
            "ca_user": "username",
            "ca_pass": "password"
        },
        "qkd_roles": {
            # "master": "sae-001",
            # "slave": "sae-002",
#####            "master": "nw-rt-igw-acx7-01.34krtl",
#####            "slave": "nw-rt-igw-acx7-02.34krtl",
            "master": "acx-1",
            "slave": "acx-2",
            # additional devices in the case of the MacSec chain formed from at least 3 devices ->
            # otherwise additional_slave_SAE_IDs list has to be empty
            "additional_slave_SAE_IDs": []
        },
        # "sae-001": {
#####        "nw-rt-igw-acx7-01.34krtl": {
#####            "root_enc_pass": "$6$twmogE0q$D5jBfJp2hSTuBgYqHdfwa7tVhXhYd.XS5FMrKwwtwzW8LBpJ5PXchjO8Os8Aaddjtb6hZNOdayZmG2Lq1yFbS.",
#####            "ip": "172.16.51.242",
#####            "interfaces": ["et-0/0/22:0"],
#####            "kme": {
#####                "kme_name": "172.16.51.241",
#####                "kme_ip": "172.16.51.241"
#####            }
        "acx-1": {
            "root_enc_pass": "$6$3eHulK1c$Yq.kaamV8hcuviwRebQI4gUMRSOGVIiBN8o/QTw7sfZ4GCfExd3TjyuUrsyrgfoBW3xNQVT5/gtGg6.S09okg0",
            "ip": "9.173.8.201",
            "interfaces": ["et-0/0/20:2"],
            "kme": {
                "kme_name": "https://kme-1",
                "kme_ip": "9.173.9.102"
            }
        },
        # "sae-002": {
#####        "nw-rt-igw-acx7-02.34krtl": {
#####            "root_enc_pass": "$6$XKNVDbaq$BP9ZiF/g246snGqZGjRoA2BqCouKiH1WjmL3yXThBJPeJ.Pj211zM.OOOy/wXCRN/WWAeekMR.8mlhfTTCMhl1",
#####            "ip": "172.16.51.243",
#####            "interfaces": ["et-0/0/20:0"],
#####            "kme": {
#####                "kme_name": "172.16.51.241",
#####                "kme_ip": "172.16.51.241"
#####            }
        "acx-2": {
            "root_enc_pass": "$6$bOeXyUQ7$oefu0aDycBhyLGDE.TCExBrdVkYOhg2IOesMVwRQvid9iDpMzwm5yZPvYhKlBu3sZ0YbHBAH0ro5SQWTnscWf.",
            "ip": "9.173.8.202",
            "interfaces": ["et-0/0/20:2"],
            "kme": {
                "kme_name": "https://kme-2",
                "kme_ip": "9.173.9.103"
            }
        # }
        }
    }


    if not onbox:
        log.info('Offbox approach taken')
        print('Offbox approach taken')
        # execute the process function for all the devices on multiple threads
        # dlist = []
        dlist = [d for d in targets_dict["qkd_roles"]["additional_slave_SAE_IDs"]]
        dlist.insert(0, targets_dict["qkd_roles"]["slave"])
        dlist.insert(0, targets_dict["qkd_roles"]["master"])
        # Calculate reqs per thread and launch threads
        if args.threads:
            maxthreads = args.threads
        else:
            maxthreads = targets_dict["system"]["maxthreads"]
        rpt = int(len(dlist) / maxthreads)
        if len(dlist) % maxthreads > 0:
            # log.info('Adjusting threadload +1')
            rpt += 1
        # log.info("reqs: {0} maxt: {1} rpt: {2} cap: {3}".format(
        #    len(dlist), maxthreads, rpt, maxthreads * rpt))
        n = 0
        print('before threads')
        while dlist:
            sreqs = dlist[:rpt]
            dlist = dlist[rpt:]
            # log.info("sreqs: {0} reqs: {1}".format(len(sreqs), len(dlist)))
            req_thread(n, sreqs, targets_dict, log)
            n += 1
        log.info("Waiting on {0} threads".format(len(threads)))
        for t in threads:
            t.join()
        
        print('after threads')
    elif onbox:
        log.info('Onbox approach taken')
        print('Onbox approach taken')
        try:
            with Device() as dev:
                device = dev.facts['hostname'].split('-re')[0]
                # commented out because pyOpenSSL is not part of the Junos python3 modules
                # if should_check_certs():
                #     prof.start("renew_certificates() Device {0}".format(device))
                #     renew_certificates(dev, targets_dict, log)
                #     prof.stop("renew_certificates() Device {0}".format(device))
                prof.start("check_and_apply_initial_config() Device {0}".format(device))
                print("111")
                check_and_apply_initial_config(dev, targets_dict, log)
                print("112")
                prof.stop("check_and_apply_initial_config() Device {0}".format(device))
                print("113")
                prof.start("process() Device {0}".format(device))
                print("114")
                process(dev, targets_dict, log)
                print("115")
                prof.stop("process() Device {0}".format(device))
        except Exception as e:
            log.error(f"Failed to process host: {str(e)}")

if __name__ == '__main__':
    main()
    prof.close()