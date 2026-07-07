#!/usr/bin/env python3

import requests
import json
import urllib3

urllib3.disable_warnings()

CERT = "/var/db/scripts/certs/vqfx1.crt"
KEY  = "/var/db/scripts/certs/vqfx1.key"
CA   = "/var/db/scripts/certs/rootCA.crt"

KME_IP = "100.100.100.10"
#PEER_ID = "vqfx2"   # IMPORTANT: must match CN of peer cert
PEER_ID = "sae_002"
URL = f"https://{KME_IP}:8443/api/v1/keys/{PEER_ID}/enc_keys"


def main():

    print("[INFO] Calling enc_keys...")
    print(f"[INFO] URL: {URL}")

    try:
        r = requests.get(
            URL,
            cert=(CERT, KEY),
            verify=CA,
            timeout=5
        )

        print("STATUS:", r.status_code)

        if r.status_code != 200:
            print("ERROR:", r.text)
            return

        data = r.json()

        print("\n=== RESPONSE ===")
        print(json.dumps(data, indent=2))

        key_id = data["keys"][0]["key_ID"]
        key = data["keys"][0]["key"]

        print("\n=== PARSED ===")
        print("KEY_ID:", key_id)
        print("KEY (base64):", key)

    except Exception as e:
        print("EXCEPTION:", str(e))


if __name__ == "__main__":
    main()