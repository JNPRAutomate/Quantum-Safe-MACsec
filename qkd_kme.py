import json
import requests

def fetch_kme_key(session, local_name, log, remote_mnmgt_add,
                  kme_host, key_id, additional_slave_SAE_IDs=None):
    """
    Fetch keys from the KME.

    If key_id is provided:
        GET /dec_keys?key_ID=<id>

    Otherwise:
        GET /enc_keys
    """

    if onbox:
        client_crt = CERTS_DIR + local_name + '.crt'
        client_key = CERTS_DIR + local_name + '.key'
        CLIENT_CERT = (client_crt, client_key)

        CA_CERT = CERTS_DIR + 'client-root-ca.crt'
    else:
        client_crt = OFFBOX_CERTS_DIR + '/' + local_name + '.crt'
        client_key = OFFBOX_CERTS_DIR + '/' + local_name + '.key'
        CLIENT_CERT = (client_crt, client_key)

        CA_CERT = OFFBOX_CERTS_DIR + '/root.crt'

    try:
        print(f"KME host      : {kme_host}")
        print(f"Remote SAE    : {remote_mnmgt_add}")
        print(f"Client cert   : {client_crt}")
        print(f"Client key    : {client_key}")
        print(f"CA cert       : {CA_CERT}")

        headers = {
            "Content-Type": "application/json"
        }

        if key_id:
            url = (
                f"{kme_host}/api/v1/keys/"
                f"{remote_mnmgt_add}/dec_keys?key_ID={key_id}"
            )

            print(f"Retrieving key_id: {key_id}")
            print(f"GET {url}")

            response = session.get(
                url,
                verify=CA_CERT,
                cert=CLIENT_CERT,
                headers=headers,
                timeout=30
            )

        else:
            url = (
                f"{kme_host}/api/v1/keys/"
                f"{remote_mnmgt_add}/enc_keys"
            )

            print(f"Requesting new key")
            print(f"GET {url}")

            response = session.get(
                url,
                verify=CA_CERT,
                cert=CLIENT_CERT,
                headers=headers,
                timeout=30
            )

        print(f"HTTP status: {response.status_code}")

        response.raise_for_status()

        response_json = response.json()

        print("KME response OK")
        print(json.dumps(response_json, indent=2))

        return response_json

    except requests.RequestException as e:
        log.error(f"KME request failed: {e}")
        print(f"KME request failed: {e}")
        return None

    except Exception as e:
        log.error(f"Unexpected KME error: {e}")
        print(f"Unexpected KME error: {e}")
        return None

