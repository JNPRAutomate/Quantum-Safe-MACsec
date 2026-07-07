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

Date: 2026-07-07
Version: 3.2.2

"""
__version__ = "v3.2.2"

from jnpr.junos import Device
from jnpr.junos.utils.config import Config
from lxml import etree
from lxml.builder import E
import argparse
from threading import Thread
import logging
import logging.handlers
import json
import copy
import base64
import uuid
import re
import os
import datetime
from scp import SCPClient, SCPException
import os.path
import time
from config_loader import load_targets
import qkd_certs
import requests
from qkd_ssh import createSSHClient
from qkd_identity import get_certs_dir,get_log_file,get_keyid_file
from qkd_kme import fetch_kme_key
from qkd_certs import (
    generate_ca_certificate,
    generate_client_certificate,
    is_certificate_valid,
    get_certificates,
    upload_certificates,
    fetch_ca_certificate,
    renew_certificates,
    should_check_certs,
)

try:
    import jcs
    onbox = True
except ImportError:
    from OpenSSL import crypto
    onbox = False
    print("onbox {}".format(onbox))
    print(datetime.datetime.now())
    

CERTS_DIR = get_certs_dir()
LOG_FILENAME = get_log_file()

OFFBOX_CERTS_DIR = "./certs/"

CA_CERT = f"{CERTS_DIR}client-root-ca.crt"


# this creates https://9.173.9.102:443/api/v1/keys
CKN_PREFIX = 'abcd1234abcd5678abcd1234abcd5678'


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
    for device in reqs:
        print(device)
        with Device(host=targets_dict[device]['ip'], user=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22) as dev:
            if should_check_certs():
                renew_certificates(dev, log, targets_dict=targets_dict)
            print("start check_and_apply_initial_config")
            check_and_apply_initial_config(dev, targets_dict, log)
            print("stop check_and_apply_initial_config")
            print("start process")
            process(dev, targets_dict, log)
            print("stop process")



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
        with open(get_keyid_file(name), 'r') as openfile:
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
    with open(get_keyid_file(local_name), 'w+') as outfile:
        outfile.write(json.dumps(key_dict))
        print(f'Saved the key IDs to the JSON file: {get_keyid_file(local_name)}')


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
    master_name = targets_dict["qkd_roles"]["master"]
    slave_name = targets_dict["qkd_roles"]["slave"]

    initial_macsec_commands.append(
        f"set system static-host-mapping {master_name} inet {targets_dict[master_name]['ip']}"
    )
    initial_macsec_commands.append(
        f"set system static-host-mapping {slave_name} inet {targets_dict[slave_name]['ip']}"
    )
    
    for interface in interfaces:
        initial_macsec_commands.extend([
            f"set security macsec interfaces {interface} apply-macro qkd kme-ca false",
            f"set security macsec interfaces {interface} apply-macro qkd kme-host {kme_name}",
            f"set security macsec interfaces {interface} apply-macro qkd kme-port 8443",
            f"set security macsec interfaces {interface} connectivity-association {c_a}",
            f"set security macsec interfaces {interface} apply-macro qkd kme-keyid-check true",
            f"set system static-host-mapping {device_name} inet {device_ip}"
        ])

    if onbox:
        initial_macsec_commands.extend([
            # even-options configuration
            f"set event-options generate-event every10mins time-interval 600 start-time {start_time}",
            f"set event-options policy qkd events every10mins",
            f"set event-options policy qkd then event-script qkd.py",
            f"set event-options event-script file qkd.py python-script-user admin",
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
    print(f"kme_host: {kme_host}")
    log.info('base url: ' + kme_host)

    if targets_dict["qkd_roles"]['master'] == local_name:
        log.info(local_name + ' is Master')
        print(local_name + ' is Master')
        remote_mnmgt_add = targets_dict["qkd_roles"]['slave']
        print(f"remote_mnmgt_add: {remote_mnmgt_add}")
        additional_slave_SAE_IDs = targets_dict["qkd_roles"]['additional_slave_SAE_IDs']
        r = fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id=None, additional_slave_SAE_IDs=None)
        print(f"response: {r}")
        if r is not None:
            print('KME: [GET] Get Keys API: for {} {}'.format(remote_mnmgt_add,r))
            key = r['keys'][0]
            log.info(f'Received KeyId: {key["key_ID"]}')
            print('Received KeyId:' + key['key_ID'])
            new_key_dict[local_name] = key['key_ID'].strip()
        else: 
            log.error("KME request returned no key")
            return
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
                print('before load and commit')
                cu.load(qkd_config_xml, format = 'xml', merge = True)
                print(cu.diff())
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
    mka_session_info_xml = dev.rpc.get_mka_session_information({'format':'text'}, summary=True)
    print(f'----------------------{local_name}-----------------------------')

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

    args = get_args()

    log = initialize_logging(args)

    targets_dict = load_targets()
    
    if not onbox:
        log.info('Offbox approach taken')
        print('Offbox approach taken')
        # execute the process function for all the devices on multiple threads
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
            rpt += 1
        n = 0
        print('before threads')
        while dlist:
            sreqs = dlist[:rpt]
            dlist = dlist[rpt:]
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
                print("111")
                check_and_apply_initial_config(dev, targets_dict, log)
                print("112")
                print("113")
                print("114")
                process(dev, targets_dict, log)
                print("115")
        except Exception as e:
            log.error(f"Failed to process host: {str(e)}")

if __name__ == '__main__':
    main()
