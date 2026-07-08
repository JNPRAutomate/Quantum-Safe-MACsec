# ETSI API logic

import json,os, requests
from qkd_runtime import *

def fetch_kme_key(session, local_name, log, remote_mnmgt_add,
                  kme_host, kme_port, key_id, additional_slave_SAE_IDs=None):
    """
    Fetch keys from the KME.

    If key_id is provided:
        GET /dec_keys?key_ID=<id>

    Otherwise:
        GET /enc_keys
    """

    if ONBOX:
        client_crt = CERTS_DIR + local_name + '.crt'
        client_key = CERTS_DIR + local_name + '.key'
        CLIENT_CERT = (client_crt, client_key)
        CA_CERT = CERTS_DIR + 'client-root-ca.crt'
    
    else:
        client_crt = OFFBOX_CERTS_DIR + '/' + local_name + '.crt'
        client_key = OFFBOX_CERTS_DIR + '/' + local_name + '.key'
        CLIENT_CERT = (client_crt, client_key)
        CA_CERT = OFFBOX_CERTS_DIR + '/root.crt'
        
    if not os.path.isfile(client_crt):
        log.error(f"Client certificate not found: {client_crt}")
        return None
    if not os.path.isfile(client_key):
        log.error(f"Client key not found: {client_key}")
        return None
    if not os.path.isfile(CA_CERT):
        log.error(f"CA certificate not found: {CA_CERT}")
        return None

    try:
        log.debug(f"KME host      : {kme_host}")
        log.debug(f"Remote SAE    : {remote_mnmgt_add}")
        log.debug(f"Client cert   : {client_crt}")
        log.debug(f"CA cert       : {CA_CERT}")

        headers = {
            "Content-Type": "application/json"
        }
        
        base_url = f"{kme_host}:{kme_port}"
        
        if key_id:
            url = (
                f"{base_url}/api/v1/keys/"
                f"{remote_mnmgt_add}/dec_keys?key_ID={key_id}"
            )

            log.debug(f"Retrieving key_id: {key_id}")
            log.debug(f"GET {url}")

            response = session.get(
                url,
                verify=CA_CERT,
                cert=CLIENT_CERT,
                headers=headers,
                timeout=30
            )

        else:
            url = (
                f"{base_url}/api/v1/keys/"
                f"{remote_mnmgt_add}/enc_keys"
            )

            log.debug(f"Requesting new key")
            log.debug(f"GET {url}")

            response = session.get(
                url,
                verify=CA_CERT,
                cert=CLIENT_CERT,
                headers=headers,
                timeout=30
            )

        log.debug(f"HTTP status: {response.status_code}")

        response.raise_for_status()

        response_json = response.json()

        log.debug("KME response OK")
        log.debug("KME response:\n%s",json.dumps(response_json, indent=2))

        return response_json

    except requests.RequestException as e:
        log.error(f"KME request failed: {e}")
        return None

    except Exception as e:
        log.error(f"Unexpected KME error: {e}")
        return None

