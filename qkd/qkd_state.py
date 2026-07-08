# KEY-ID persistence logic

from qkd_ssh import createSSHClient
from qkd_runtime import *

from scp import SCPClient, SCPException
import os
import json


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

def get_key_id_from_master(dev, log, targets_dict):
    """
    Get the Key_ID from the master device.
    """

    master_name = targets_dict["qkd_roles"]['master']
    log.debug(f"master_name: {master_name}")
    master_key_id_file = KEYID_JSON_FILENAME.format(master_name)
    log.debug(f"master_key_id_file: {master_key_id_file}")
    if not ONBOX:
        if os.path.exists(master_key_id_file):
            return master_key_id_file
        else:
            log.debug(f"Master key file not found: {master_key_id_file}")
            return None
    else:   
        client = createSSHClient(master_name, username=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22)
        log.debug(f"SSH client connected to {master_name}")
        try:
            with SCPClient(client.get_transport()) as scp:
                scp.get(remote_path=master_key_id_file, local_path=master_key_id_file, preserve_times=True)
        except SCPException as e:
            print(f'SCP get exception error: {e}')
            return None
    return master_key_id_file
