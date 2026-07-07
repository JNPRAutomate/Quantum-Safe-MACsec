#!/usr/bin/env python3!/usr/bin/env

LOG_FILE = "/var/tmp/test_sae1.log"

import subprocess
import datetime

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.datetime.now()} - {msg}\n")
    print(msg)

def send_key_id(peer_ip, key_id):
    ssh_cmd = [
        "ssh",
        "-i", "/var/home/admin/.ssh/id_rsa",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=3",
        f"admin@{peer_ip}",
        f"op apply_key arguments {key_id}"
    ]

    result = subprocess.run(ssh_cmd, capture_output=True, text=True)

    log(f"SSH STDOUT: {result.stdout}")
    log(f"SSH STDERR: {result.stderr}")

    return result.returncode == 0

def main():

    key_id = "TESTKEY123"
    log(f"[SAE1] Using key_id: {key_id}")

    if not send_key_id("10.54.12.193", key_id):
       log("ERROR: Failed to notify peer")
       return
    log(f"[SAE1] Local apply of key_id {key_id}")

if __name__ == "__main__":
    main()