# MASTER / SLAVE LOGIC
import os, time, copy, uuid, base64, datetime, requests
from lxml import etree
from lxml.builder import E

from jnpr.junos.utils.config import Config

from qkd_kme import fetch_kme_key

from qkd_state import (
    get_previous_key_ids,
    save_key_ids,
    get_key_id_from_master,
)

from qkd_runtime import *

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
    if macsec_xml is None:
        log.error("No MACSEC configuration found")
        return
    
    session = requests.Session()
    new_key_dict = {}

    # placeholders for new macsec and interfaces config
    qkd_macsec_xml = E.macsec()

    commit = False

    ca_name = macsec_xml.findtext('connectivity-association/name')
    if not ca_name:
        log.error("No connectivity-association found")
        return
    
    print(f"ca_name: {ca_name}")
    qkd_ca_xml = copy.deepcopy(macsec_xml.find(f'connectivity-association[name="{ca_name}"]'))
    if qkd_ca_xml is None:
        log.error(f"Connectivity association {ca_name} not found")
        return

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
        r = fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id=None, additional_slave_SAE_IDs=additional_slave_SAE_IDs)
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
                
                if master_key_id_file is None:
                    retries += 1
                    time.sleep(5)
                    continue
                
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
    ckn_node = qkd_ca_xml.find('pre-shared-key/ckn')
    cak_node = qkd_ca_xml.find('pre-shared-key/cak')

    if ckn_node is None or cak_node is None:
        log.error(
            f"CA {ca_name} missing pre-shared-key nodes"
        )
        return
    
    ckn_node.text = CKN_PREFIX + uuid.UUID(key['key_ID']).hex
    print("ckn {}".format(ckn_node.text))
    cak_node.text = str(base64.b64decode(key['key']).hex())[:64]
    print("cak {}".format(cak_node.text))
    qkd_macsec_xml.append(qkd_ca_xml)
    commit = True

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
            try: 
                cu.unlock()
            except Exception:
                pass
        # Save the key-ID in a persistant file
        save_key_ids(new_key_dict, local_name)
    try: 
        mka_session_info_xml = dev.rpc.get_mka_session_information({'format':'text'}, summary=True)
        log.info(
            etree.tostring(
                mka_session_info_xml,
                pretty_print=True
            ).decode()
        )
    except Exception as e: 
            log.info(f"MKA session information unavailable: {e}")
    print(f'----------------------{local_name}-----------------------------')
