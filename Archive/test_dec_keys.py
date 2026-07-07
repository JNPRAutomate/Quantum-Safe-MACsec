#!/usr/bin/env python3

import sys
import datetime
import re

LOG_FILE = "/var/tmp/test_sae2.log"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.datetime.now()} - {msg}\n")
    print(msg)

def main():
    # Junos passes everything as a single string sometimes
    raw_args = " ".join(sys.argv)

    match = re.search(r'arguments\s+(\S+)', raw_args)

    if match:
        key_id = match.group(1)
    else:
        log("ERROR: missing key_id")
        return

    log(f"[SAE2] Received key_id: {key_id}")

if __name__ == "__main__":
    main()
root@vqfx-2:RE:0% rm apply*
root@vqfx-2:RE:0% rm qkd*
root@vqfx-2:RE:0% cat test_dec_keys.py
#!/usr/bin/env python3

import requests
import json
import sys
import urllib3

urllib3.disable_warnings()

CERT = "/var/db/scripts/certs/vqfx2.crt"
KEY  = "/var/db/scripts/certs/vqfx2.key"
CA   = "/var/db/scripts/certs/rootCA.crt"

KME_IP = "100.100.100.11"
PEER_ID = "vqfx1"
headers = {"X-KME-Client-ID": "sae_002"}  
KEY_ID = "f592da28-f299-4a55-af3c-175ad81e1e3b"
URL = f"https://{KME_IP}:8443/api/v1/keys/{PEER_ID}/dec_keys"


def main():

    print("[INFO] Calling dec_keys...")
    print(f"[INFO] URL: {URL}")
    print(f"[INFO] KEY_ID: {KEY_ID}")

    try:
        r = requests.get(
            URL,
            params={"key_ID": KEY_ID},
            cert=(CERT, KEY),
            verify=CA,
            timeout=5,
            headers={
                "X-KME-Client-ID": "sae_002"
            }
        )

        print("STATUS:", r.status_code)

        if r.status_code != 200:
            print("ERROR:", r.text)
            return

        data = r.json()

        print("\n=== RESPONSE ===")
        print(json.dumps(data, indent=2))

        key = data["keys"][0]["key"]

        print("\n=== RESULT ===")
        print("KEY (base64):", key)

    except Exception as e:
        print("EXCEPTION:", str(e))


if __name__ == "__main__":
    main()