# QKD MACsec Lab Test — Complete Replication Guide

**Date:** June 2026
**Repository:** https://github.com/JNPRAutomate/Quantum-Safe-MACsec


## Table of Contents

1. [Project Goal](#project-goal)
2. [Lab Equipment](#lab-equipment)
3. [Lab Topology](#lab-topology)
4. [Background](#background)
   - What is Quantum-Safe MACsec and Why is it Needed?
   - Difference Between MACsec and Quantum-Safe MACsec
   - What is a QKD Device?
   - Why the Ubuntu Server?
   - How QKD Keys Are Used in MACsec
   - What is Hitless Key Rollover?
   - What are TLS Certificates and Why are They Needed?
5. [How the QKD Key Exchange Works](#how-the-qkd-key-exchange-works)
6. [Step-by-Step Procedure](#step-by-step-procedure)
   - Step 1: Synchronize Device Clocks
   - Step 2: Baseline MACsec Configuration
   - Step 3: Generate TLS Certificates
   - Step 4: Deploy Certificates to Switches
   - Step 5: Deploy the Mock KME Server
   - Step 6: Modify qkd_macsec.py
   - Step 7: Create a Compatible Profile.py
   - Step 8: Switch Configuration — Remove Management Instance
   - Step 9: Switch Configuration — User and Event-Options
   - Step 10: Switch Configuration — Hostname Resolution
   - Step 11: File Permissions
   - Step 12: Deploy and Verify
7. [Issues Encountered and Solutions](#issues-encountered-and-solutions)
8. [Files and Their Locations](#files-and-their-locations)
9. [Debugging Tips](#debugging-tips)


## Project Goal

Validate that the JNPRAutomate Quantum-Safe-MACsec Python script (`qkd_macsec.py`) runs successfully onbox on Juniper switches, fetching cryptographic keys from a mock KME (Key Management Entity) via the ETSI QKD 014 REST-API and programming them into MACsec as the new CAK (Connectivity Association Key), achieving hitless key rollover — all without physical QKD hardware.


## Lab Equipment

- 2× Juniper MACsec-capable switches (tested on QFX5120-48YM, QFX5130-48CM, and QFX5700)
- 1× Ubuntu 22.04.5 LTS server
- Appropriate cabling for MACsec link (AOC, DAC, or fiber with optics)
- Management network connectivity between all three devices


## Lab Topology

```
        Management Network
   [Ubuntu Server]          [Master Switch]              [Slave Switch]
   Mock KME                 QKD Master                   QKD Slave
   <server_mgmt_ip>         <master_mgmt_ip>             <slave_mgmt_ip>
      │                        │                            │
      └────────────────────────┴────────────────────────────┘
                               │
                        [Lab mgmt switch]

   Revenue ports (MACsec-encrypted link):
                          TX ──── fiber ──── RX
   [Master interface]                            [Slave interface]
   10.0.0.1/30            RX ──── fiber ──── TX  10.0.0.2/30
```


## Background


### What is Quantum-Safe MACsec and Why is it Needed?

Standard MACsec uses asymmetric public-key cryptography to distribute the symmetric keys that encrypt traffic, and that asymmetric step is vulnerable to future quantum computers running Shor's algorithm. Quantum Safe MACsec replaces that vulnerable key exchange by using Quantum Key Distribution (QKD) to deliver the encryption keys — encoding them in quantum states of photons so that any eavesdropping is physically detectable. This ensures the MACsec-encrypted link remains secure even against adversaries with quantum computing capabilities.


### Difference Between MACsec and Quantum-Safe MACsec

The encryption itself is identical — both use the same AES-256 algorithm to encrypt traffic on the wire. The difference is how the encryption key gets there.

Standard MACsec uses traditional key exchange methods to distribute the CAK — either manually configured pre-shared keys or asymmetric public-key cryptography via 802.1X. The asymmetric crypto is vulnerable to future quantum computers that could use Shor's algorithm to crack the key exchange and recover the CAK.

Quantum-Safe MACsec replaces that key exchange step with QKD. Instead of relying on math problems that quantum computers could solve, the keys are derived from quantum physics — photons encoded in quantum states where any eavesdropping is physically detectable. The QKD device delivers the key to the switch via the ETSI QKD 014 API, the script programs it as the new CAK, and MACsec encrypts traffic with it just like before.

So the "quantum-safe" part isn't about the encryption — AES-256 is already quantum-resistant. It's about making the key distribution quantum-resistant so an attacker can't intercept or compute the key in the first place.


### What is a QKD Device?

A QKD device is a specialized piece of optical hardware that generates and distributes cryptographic keys using quantum physics rather than math. The two devices sit at either end of a dedicated fiber-optic link and exchange single photons (or weak laser pulses) encoded in quantum states. By measuring those photons and comparing notes over a classical channel, the two devices independently derive the same random symmetric key — and can detect if anyone tapped the fiber in between.

A QKD device typically contains a few key components: a photon source (usually a laser that emits very weak pulses at ~1550 nm), single-photon detectors, a classical processing engine for the "sifting" and error-correction math, and a built-in Key Management Entity (KME) that stores the resulting keys and serves them to consuming applications (like your switch) over the ETSI QKD 014 REST-API.

In a production deployment, the physical setup looks like this:

- Site A: Switch ↔ (Ethernet/REST-API) ↔ QKD Device A
- Site B: Switch ↔ (Ethernet/REST-API) ↔ QKD Device B
- Between QKD A and QKD B: dedicated single-mode fiber for the quantum channel (photons), plus a classical service channel for reconciliation
- Between the two switches: your normal 10G/25G/100G/400G MACsec-encrypted Ethernet link

QKD devices are expensive, specialized equipment — you can't just download software onto a generic server and call it a QKD device, because the quantum key exchange depends on physical photon-level hardware.


### Why the Ubuntu Server?

We used the Ubuntu server as a stand-in for real QKD hardware, which we didn't have. It ran a small Flask application that mimicked the ETSI QKD 014 REST-API endpoints that a real QKD Key Management Entity would expose, allowing the switches to fetch keys over HTTPS as if they were talking to an actual quantum key distribution device.

This setup validates the entire software pipeline end to end — the onbox Python script lifecycle, the ETSI REST-API integration, the key-ID exchange, the CAK programming via Junos CLI/NETCONF calls, and the hitless MACsec key rollover. The only thing not being tested is the actual quantum-physics key generation, which is purely the QKD vendor's domain and transparent to the switches.

In production, the whole security guarantee comes from the physics of the QKD devices themselves — without real QKD hardware, you're just doing regular pre-shared key distribution with extra steps.


### How QKD Keys Are Used in MACsec

The QKD-delivered AES-256-bit key is used directly as the CAK (Connectivity Association Key). The SAK (Session Association Key), which actually encrypts traffic frames, is cryptographically derived from the CAK per the MACsec standard and is frequently updated. This means QKD secures the root of trust, while the standard MACsec SAK derivation and rollover mechanism stays intact.


### What is Hitless Key Rollover?

Hitless key rollover means the MACsec encryption key is replaced with a new one without dropping any traffic. The encrypted link stays up the entire time — no packets are lost, no session interruptions, no flap.

During a hitless rollover, MACsec keeps the old secure association active while it programs the new key into a new secure association. Both sides negotiate the switchover using MKA (MACsec Key Agreement) protocol. Once both sides confirm they have the new key ready, they cut over simultaneously. The old association is retired and the new one takes over. From the perspective of anything sending traffic across that link, nothing happened — the packets kept flowing.

You can see evidence of hitless rollover in the `show security macsec connections` output — the AN (Association Number) increments with each key rollover, and in `show security mka sessions summary` you can see the old key as `preceding` (still `live`) and the new key as `primary` (`in-progress`) during the transition.


### What are TLS Certificates and Why are They Needed?

TLS certificates are what make the HTTPS connection between the switches and the KME secure. When the QKD script calls the KME to fetch a key, that key is the most sensitive piece of data in the entire system — it's the MACsec encryption key. If someone intercepted it in transit, they could decrypt all the traffic on the MACsec link. TLS certificates encrypt that API connection and verify that both sides are who they claim to be.

A CA (Certificate Authority) certificate is the root of trust. You create a CA cert first, then use it to "sign" all the other certificates (the server cert and client certs). When a switch connects to the mock KME, it checks the server's certificate and asks "was this signed by a CA I trust?" If the server cert was signed by the CA cert that's stored on the switch (`client-root-ca.crt`), the connection is trusted.

Without HTTPS, the encryption keys would travel in plaintext — anyone with access to the management network could capture them and use them to decrypt all the MACsec-encrypted traffic. That would defeat the entire purpose of MACsec.


## How the QKD Key Exchange Works

1. The **master** switch calls `GET https://<kme_ip>/api/v1/keys/<slave_hostname>/enc_keys` to request a new key
2. The KME returns a base64-encoded AES-256 key and a UUID key-ID
3. The master programs the new CKN (derived from the key-ID) and CAK (decoded from the key) into the MACsec connectivity association, then saves the key-ID to a JSON file
4. The **slave** switch SCPs the master's key-ID JSON file to its local disk
5. The slave calls `GET https://<kme_ip>/api/v1/keys/<master_hostname>/dec_keys?key_ID=<uuid>` to fetch the matching key
6. The slave programs the same CKN and CAK, and MACsec performs a hitless key rollover


## Step-by-Step Procedure


### Step 1: Synchronize Device Clocks

Before doing anything else, ensure all three devices (both switches and the server) have synchronized clocks. Clock skew causes TLS certificate errors ("certificate is not yet valid") and key-ID timestamp mismatches between master and slave.

Check the time on all devices:

On the server:
```
date
```

On Junos Evolved switches (QFX5130, QFX5700):
```
date
```
To set: `sudo date -s "2026-06-08 15:30:00"`

On classic Junos switches (QFX5120):
```
date
```
To set (as root): `date 202606081530.00` (format: `YYYYMMDDhhmm.ss`)

For a permanent fix, configure NTP on all devices so clocks stay synchronized:
```
set system ntp server <server_ip>
```

**Important:** Classic Junos is based on FreeBSD and uses FreeBSD date syntax. Junos Evolved is based on Linux and uses Linux date syntax. The `sudo` command is not available on classic Junos.


### Step 2: Baseline MACsec Configuration

Configure static-CAK MACsec between the two switches before introducing QKD. This validates that MACsec works independently and isolates MACsec issues from script issues.

On both switches (identical except for the IP address):

```
set security macsec connectivity-association <ca_name> cipher-suite gcm-aes-256
set security macsec connectivity-association <ca_name> security-mode static-cak
set security macsec connectivity-association <ca_name> pre-shared-key ckn <64_hex_characters>
set security macsec connectivity-association <ca_name> pre-shared-key cak <64_hex_characters>
set security macsec interfaces <interface> connectivity-association <ca_name>
set interfaces <interface> mtu 9216
set interfaces <interface> unit 0 family inet address <ip>/30
commit
```

Note: On QFX5120/QFX5130/QFX5700, the MACsec interface binding syntax is `set security macsec interfaces <interface> connectivity-association <ca_name>`, NOT `set interfaces <interface> ether-options 802.1ae connectivity-association <ca_name>` (which is MX-series syntax).

Verify with `show security macsec connections` — you should see encryption on, secure channels active.

**Important:** The QFX5120-48YM requires a valid MACsec license. Check with `show system license`. An expired license will cause MACsec to silently not work.

Once MACsec is verified working, the `qkd_macsec.py` script will take over and manage the MACsec configuration from this point forward. The script's `check_and_apply_initial_config()` function creates the MACsec connectivity association, pre-shared keys, interface bindings, and event-options configuration automatically. You do not need to maintain the manual MACsec configuration after this step.


### Step 3: Generate TLS Certificates

On the Ubuntu server, create a working directory and generate all certificates:

```bash
mkdir ~/qkd-certs && cd ~/qkd-certs

# CA certificate
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt -subj "/CN=Lab QKD CA/O=Lab/C=US"

# Mock KME server certificate
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=mock-kme.lab/O=Lab/C=US"

# SAN extension file — replace IP with your server's actual management IP
cat > server_ext.cnf << EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names

[alt_names]
IP.1 = <server_management_ip>
DNS.1 = mock-kme.lab
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 3650 -extfile server_ext.cnf

# Client certificates — use exact Junos hostnames as CN
openssl genrsa -out <master_hostname>.key 2048
openssl req -new -key <master_hostname>.key -out <master_hostname>.csr -subj "/CN=<master_hostname>/O=Lab/C=US"
openssl x509 -req -in <master_hostname>.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out <master_hostname>.crt -days 3650

openssl genrsa -out <slave_hostname>.key 2048
openssl req -new -key <slave_hostname>.key -out <slave_hostname>.csr -subj "/CN=<slave_hostname>/O=Lab/C=US"
openssl x509 -req -in <slave_hostname>.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out <slave_hostname>.crt -days 3650

# Verify
openssl verify -CAfile ca.crt server.crt
openssl x509 -in server.crt -text -noout | grep -A2 "Subject Alternative Name"
```

**Important:** The SAN IP in `server_ext.cnf` must match the server's management IP. If the switch connects to `https://10.92.71.247` but the cert only has `10.92.72.64` in the SAN, the TLS handshake will fail. Also ensure device clocks are synchronized (Step 1) — if a switch's clock is behind the cert creation time, you'll get "certificate is not yet valid" errors.


### Step 4: Deploy Certificates to Switches

Create the certs directory on each switch first: `mkdir -p /var/home/admin/certs/`

From the Ubuntu server:

```bash
# Master switch
scp ca.crt <user>@<master_ip>:/var/home/admin/certs/client-root-ca.crt
scp <master_hostname>.crt <user>@<master_ip>:/var/home/admin/certs/<master_hostname>.crt
scp <master_hostname>.key <user>@<master_ip>:/var/home/admin/certs/<master_hostname>.key

# Slave switch
scp ca.crt <user>@<slave_ip>:/var/home/admin/certs/client-root-ca.crt
scp <slave_hostname>.crt <user>@<slave_ip>:/var/home/admin/certs/<slave_hostname>.crt
scp <slave_hostname>.key <user>@<slave_ip>:/var/home/admin/certs/<slave_hostname>.key
```

The cert filenames must match the exact Junos hostname — the script builds the path as `CERTS_DIR + hostname + '.crt'`. Verify the hostname with `show system hostname` on each switch.


### Step 5: Deploy the Mock KME Server

Install Flask on the Ubuntu server:

```bash
pip3 install flask
```

Create `/root/mock-kme/mock_kme.py`:

```python
from flask import Flask, request, jsonify
import uuid, secrets, base64

app = Flask(__name__)
key_store = {}

@app.route('/api/v1/keys/<slave_sae_id>/enc_keys', methods=['GET'])
def get_key(slave_sae_id):
    key_id = str(uuid.uuid4())
    key_bytes = secrets.token_bytes(32)
    key_value = base64.b64encode(key_bytes).decode()
    key_store[key_id] = key_value
    return jsonify({
        "keys": [{"key_ID": key_id, "key": key_value}]
    })

@app.route('/api/v1/keys/<master_sae_id>/dec_keys', methods=['GET'])
def get_key_by_id(master_sae_id):
    key_id = request.args.get('key_ID')
    key_value = key_store.get(key_id)
    if key_value:
        return jsonify({"keys": [{"key_ID": key_id, "key": key_value}]})
    return jsonify({"error": "key not found"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=443, ssl_context=('/root/qkd-certs/server.crt', '/root/qkd-certs/server.key'))
```

Key design notes for the mock KME:
- Uses **GET** method (not POST) — matches what the script sends
- Returns **base64-encoded** keys — the script decodes them with `base64.b64decode()`
- The `dec_keys` endpoint takes `key_ID` as a **query parameter** (not JSON body)
- Keys are 32 bytes (256-bit) for GCM-AES-256

Run it in the background so it survives session timeouts:

```bash
nohup python3 /root/mock-kme/mock_kme.py > /root/mock-kme/kme.log 2>&1 &
```

Verify it's running: `ss -tlnp | grep 443`

Test the endpoints:

```bash
curl --cacert /root/qkd-certs/ca.crt https://<server_ip>:443/api/v1/keys/test/enc_keys
```

You should receive a JSON response with a `key_ID` and base64-encoded `key`.

**Important:** If the Flask server is restarted or the certs are regenerated, you must kill and restart the Flask process. Flask loads certs into memory at startup and won't pick up new cert files without a restart.


### Step 6: Modify qkd_macsec.py

The following changes are required to the repo's `qkd_macsec.py` for this lab:

**6a. Update `targets_dict` in `main()`:**

```python
targets_dict = {
    "system": {
        "maxthreads": 1,
        "event_options": {
            "start_time": "2026-06-01.13:00:00"
        }
    },
    "secrets": {
        "username": "<junos_username>",
        "password": "<junos_password>"
    },
    "CA_server": {
        "CA_cert": {
            "fetch": False,
            "generate": False
        },
        "c_a": "<connectivity_association_name>",
        "ca_server_ip": "<server_ip>",
        "ca_path": "/root/qkd-certs/",
        "ca_cert_name": "ca.crt",
        "ca_key_name": "ca.key",
        "ca_user": "root",
        "ca_pass": "password"
    },
    "qkd_roles": {
        "master": "<master_hostname>",
        "slave": "<slave_hostname>",
        "additional_slave_SAE_IDs": []
    },
    "<master_hostname>": {
        "root_enc_pass": "<encrypted_root_password>",
        "ip": "<master_mgmt_ip>",
        "interfaces": ["<master_macsec_interface>"],
        "kme": {
            "kme_name": "https://<server_ip>",
            "kme_ip": "<server_ip>"
        }
    },
    "<slave_hostname>": {
        "root_enc_pass": "<encrypted_root_password>",
        "ip": "<slave_mgmt_ip>",
        "interfaces": ["<slave_macsec_interface>"],
        "kme": {
            "kme_name": "https://<server_ip>",
            "kme_ip": "<server_ip>"
        }
    }
}
```

The `master` and `slave` values must exactly match the output of `show system hostname` on each switch. The `username` and `password` must be a valid Junos login user on all switches — the slave SSHs into the master to fetch the key-ID file, so the credentials must work between switches. Get the encrypted root passwords from `show configuration system root-authentication encrypted-password` on each switch.

**Important:** If you change interfaces or connectivity association names later, you must update the script. The `check_and_apply_initial_config()` function automatically creates MACsec config and event-options based on these values. If the script has stale values, it will recreate old config every time it runs — even after you delete it manually.

**6b. Fix the error handler in `fetch_kme_key()` (CRITICAL):**

The original script has a bug where failed KME connections crash the error handler. Change all four occurrences of:

```python
log.error(f"KME request failed: {e.response.text}")
print(f"KME request failed: {e.response.text}")
```

To:

```python
log.error(f"KME request failed: {e}")
print(f"KME request failed: {e}")
```

Without this fix, any connection failure produces `'NoneType' object has no attribute 'text'` instead of the actual error, making debugging impossible.

**6c. Fix `createSSHClient()` paramiko compatibility:**

Remove `auth_timeout=timeout` from the `client.connect()` call. Older paramiko versions on Junos don't support this parameter. Change:

```python
client.connect(
    hostname=device, port=port, username=username,
    password=password, timeout=timeout,
    banner_timeout=timeout, auth_timeout=timeout
)
```

To:

```python
client.connect(
    hostname=device, port=port, username=username,
    password=password, timeout=timeout,
    banner_timeout=timeout
)
```

Also increase the default timeout from 10 to 30 seconds in the function signature:

```python
def createSSHClient(device, username, password, port=22, retries=3, delay=5, timeout=30):
```

**6d. (Optional) Add remote host mapping in `check_and_apply_initial_config()`:**

The script creates a static-host-mapping for the local device but not for the remote peer. To avoid manually adding the master's hostname mapping on the slave, add these lines before the `for interface in interfaces` loop:

```python
    master_name = targets_dict["qkd_roles"]["master"]
    slave_name = targets_dict["qkd_roles"]["slave"]
    initial_macsec_commands.append(f"set system static-host-mapping {master_name} inet {targets_dict[master_name]['ip']}")
    initial_macsec_commands.append(f"set system static-host-mapping {slave_name} inet {targets_dict[slave_name]['ip']}")
```


### Step 7: Create a Compatible Profile.py

The repo's `profile_v3.2.0.py` is incompatible with `qkd_macsec.py` v3.2.0 — the constructor parameters don't match. `qkd_macsec.py` calls `Profile.Profile(file=..., verbose=True, enabled=True, mode="w+")` but the repo's version expects `file_path=` and doesn't accept `mode=`.

A compatible `Profile.py` must accept `file=`, `verbose=`, `enabled=`, and `mode=` parameters, and implement `start()`, `stop()`, `report()`, and `close()` methods.

Place `Profile.py` in `/var/db/scripts/event/` on both switches alongside `qkd_macsec.py`. Set permissions: `chmod 755 /var/db/scripts/event/Profile.py`


### Step 8: Switch Configuration — Remove Management Instance (Classic Junos Only)

**This applies to classic Junos (QFX5120) and is the most critical switch configuration change.** The QFX5120 uses a separate `mgmt_junos` routing instance for the management interface by default. Junos event scripts run in the default routing instance (`inet.0`), which cannot reach the management network. This causes persistent `No route to host` errors.

The fix is to remove the management instance entirely so everything uses the default routing table:

```
configure
delete system management-instance
delete routing-instances mgmt_junos
set routing-options static route 0.0.0.0/0 next-hop <gateway_ip>
commit
```

**Reboot both switches after this change.** The management interface will move from `mgmt_junos` to the default routing instance.

After reboot, verify from the shell: `ping -c 3 <server_ip>` and `curl --cacert /var/home/admin/certs/client-root-ca.crt https://<server_ip>:443/api/v1/keys/test/enc_keys`

**Note:** Junos Evolved switches (QFX5130, QFX5700) did not exhibit this routing instance issue in our testing and may not require this step.


### Step 9: Switch Configuration — User and Event-Options

On both switches, create a login user if one doesn't already exist:

```
set system login user <username> class super-user authentication plain-text-password
```

The username must match what's in the script's `targets_dict`. The password must be the same on both switches (the slave SSHs to the master to fetch the key ID file).

Configure the event-options and Python scripting:

```
set system scripts language python3
set event-options generate-event qkd-trigger time-interval 600
set event-options policy qkd events qkd-trigger
set event-options policy qkd then event-script qkd_macsec.py
set event-options event-script file qkd_macsec.py python-script-user <username>
commit
```

The `python-script-user` must reference a user defined under `system login`. The `root` user does not work.

**Important:** The time-interval must be 600 seconds (10 minutes) or longer. With shorter intervals (e.g., 60 seconds), the master generates a new key every cycle, and by the time the slave fetches and programs the matching key, the master has already moved to a different key. They never sync. The 600-second interval gives both sides enough time to complete the full key exchange and MKA negotiation before the next cycle starts.

**Note:** The `check_and_apply_initial_config()` function in the script also creates event-options config automatically. If you want the script to manage event-options, you only need to configure the minimum to trigger the first run. If you want full control, comment out the event-options block in the script (the `if onbox:` section inside `check_and_apply_initial_config()`).


### Step 10: Switch Configuration — Hostname Resolution

The slave switch needs to resolve the master's hostname for the SCP key-ID file transfer. Add a static host mapping on the slave:

```
set system static-host-mapping <master_hostname> inet <master_management_ip>
commit
```

**Note:** If you added the remote host mapping code in Step 6d, the script creates this automatically on the first run and this manual step is not needed.


### Step 11: File Permissions

The event script runs as the configured `python-script-user`, not as root. All files the script reads or writes must be accessible to this user.

On **both switches**:

```bash
chown -R <username> /var/home/admin/
chmod -R 777 /var/home/admin/
chmod 644 /var/home/admin/certs/*
touch /var/home/admin/qkd_test.log && chmod 666 /var/home/admin/qkd_test.log
touch /var/home/admin/scaler.prof && chmod 666 /var/home/admin/scaler.prof
```

On the **master**:
```bash
touch /var/home/admin/<master_hostname>last_key.json && chmod 666 /var/home/admin/<master_hostname>last_key.json
```

On the **slave**:
```bash
touch /var/home/admin/<slave_hostname>last_key.json && chmod 666 /var/home/admin/<slave_hostname>last_key.json
touch /var/home/admin/<master_hostname>last_key.json && chmod 666 /var/home/admin/<master_hostname>last_key.json
```

The slave needs both files: its own key ID file and a local copy of the master's key ID file (downloaded via SCP).

**Important:** The SCP transfer uses `preserve_times=True`, which can reset file permissions. If you see repeated "Permission denied" errors after the first successful run, re-run `chown -R <username> /var/home/admin/` and `chmod -R 777 /var/home/admin/`.


### Step 12: Deploy and Verify

1. Copy `qkd_macsec.py` and `Profile.py` to `/var/db/scripts/event/` on both switches
2. Set execute permissions: `chmod 755 /var/db/scripts/event/qkd_macsec.py` and `chmod 755 /var/db/scripts/event/Profile.py`
3. Start the mock KME on the Ubuntu server
4. Reload event scripts: `request system scripts event-scripts reload`
5. Wait 600 seconds (10 minutes) for the first trigger cycle to complete on both switches
6. Check logs and MACsec status

**Verification commands:**

```bash
# Check event script execution log (Junos Evolved)
cat /var/home/admin/qkd_test.log | tail -30

# Check event script error log (Classic Junos)
# Look for "script run SUCCESS" or error messages
cat /var/log/escript.log | tail -20

# Check MACsec status
show security macsec connections

# Check MKA session — CAK Names should match on both switches, status should be "live"
show security mka sessions summary

# Check key ID files
cat /var/home/admin/<hostname>last_key.json

# Check mock KME received requests
cat /root/mock-kme/kme.log
```

**Success criteria:**
- Both switches show `script run SUCCESS` in the QKD test log
- `show security macsec connections` shows encryption on with active secure channels
- `show security mka sessions summary` shows `live` status with matching CAK Names on both switches and Rx > 0
- The mock KME log shows GET requests from both switches
- The AN (Association Number) in `show security macsec connections` increments with each successful key rollover


## Issues Encountered and Solutions

**Issue 1: MACsec interface binding syntax**
The `ether-options 802.1ae` syntax is for MX-series. QFX uses `set security macsec interfaces <interface> connectivity-association <name>`.

**Issue 2: MACsec license expiration**
An expired MACsec license causes the config to be accepted but MACsec doesn't activate. `show security macsec connections` returns empty output. Renew the license.

**Issue 3: CAK length validation**
The CAK for GCM-AES-256 must be exactly 64 hex characters. One extra character produces `Length 65 is not within range (1..64)`.

**Issue 4: Profile.py incompatibility**
The repo's `profile_v3.2.0.py` has different constructor parameters than what `qkd_macsec.py` expects. A custom `Profile.py` is required that accepts `file=` and `mode=` parameters. Without `Profile.py`, the script crashes silently on import and Junos event scripts don't report import errors to syslog.

**Issue 5: Script indentation corruption**
Editing the script or copying from web pages can introduce mixed tabs/spaces. Junos reports this as `IndentationError: unindent does not match any outer indentation level`. Always verify with `python3 -c "import py_compile; py_compile.compile('qkd_macsec.py', doraise=True)"` before deploying.

**Issue 6: Management routing instance isolation (Classic Junos — CRITICAL)**
Junos event scripts run in the default routing instance (`inet.0`), but the management interface lives in `mgmt_junos`. The Python `requests` library cannot reach the KME because there's no route in `inet.0`. Solutions attempted: static routes (failed — no interface in inet.0 reaches the next-hop), socket source address binding (failed), event-options routing-instance (syntax not supported), setfib (FIB number not discoverable with 65535 FIBs). **Solution that worked:** Remove `system management-instance` and `routing-instances mgmt_junos` entirely, place the default route in `inet.0`, and reboot.

**Issue 7: Error handler crash masking real errors**
The `fetch_kme_key()` function's `except` block references `e.response.text`, which crashes with `'NoneType' object has no attribute 'text'` when the connection fails before getting a response. This hides the actual error. Fix by changing to `str(e)`.

**Issue 8: paramiko auth_timeout incompatibility**
The Junos-bundled paramiko doesn't support `auth_timeout`. Remove it from the `client.connect()` call.

**Issue 9: Hostname resolution for SCP**
The slave SCPs the key-ID file from the master using the hostname, not the IP. Without DNS, this fails with `Name does not resolve`. Fix with `set system static-host-mapping` or by adding remote host mapping code to the script.

**Issue 10: File permission errors**
The event script runs as a non-root user but needs to read certs and write JSON/log files in `/var/home/admin/`. SCP with `preserve_times=True` also resets file ownership. Fix by setting `chmod -R 777` and `chown -R <user>` on `/var/home/admin/`.

**Issue 11: python-script-user must be a Junos login user**
The `root` user and any user not defined under `system login` will cause a commit error. Create a dedicated super-user login account.

**Issue 12: Clock skew between devices**
TLS certificates have a "not before" date. If a switch's clock is behind the cert creation time, the cert appears "not yet valid." Also, the slave compares the master's key-ID file timestamp to determine if a new key is available — if clocks are out of sync, the slave may endlessly retry or never detect a new key. Synchronize all device clocks before starting.

**Issue 13: Event timer too short (60 seconds)**
With 60-second intervals, the master generates a new key every cycle. By the time the slave fetches and programs the matching key, the master has already moved to a different key. The MKA primary session shows `in-progress` with Rx=0 indefinitely because the CKN values never match at the same time. Fix by using 600-second (10-minute) intervals.

**Issue 14: Script automatically recreates deleted config**
The `check_and_apply_initial_config()` function recreates MACsec connectivity associations, interface bindings, apply-macros, and event-options every time it runs and doesn't find them. If you delete stale config without updating the script's `targets_dict` first, the script puts it right back within one trigger cycle. Always update the script before deleting config.

**Issue 15: Flask server using stale certificates**
Flask loads TLS certificates into memory at startup. If you regenerate certificates and don't restart Flask, the running process still serves the old cert. The new CA cert on the switches won't match the old server cert, causing TLS verification failures. Always kill and restart Flask after regenerating certs.


## Files and Their Locations

| File | Location | Device |
|------|----------|--------|
| `qkd_macsec.py` | `/var/db/scripts/event/` | Both switches |
| `Profile.py` | `/var/db/scripts/event/` | Both switches |
| `client-root-ca.crt` | `/var/home/admin/certs/` | Both switches |
| `<hostname>.crt` | `/var/home/admin/certs/` | Respective switch |
| `<hostname>.key` | `/var/home/admin/certs/` | Respective switch |
| `<hostname>last_key.json` | `/var/home/admin/` | See Step 11 |
| `qkd_test.log` | `/var/home/admin/` | Both switches |
| `scaler.prof` | `/var/home/admin/` | Both switches |
| `mock_kme.py` | `/root/mock-kme/` | Ubuntu server |
| `server.crt`, `server.key`, `ca.crt` | `/root/qkd-certs/` | Ubuntu server |
| `server_ext.cnf` | `/root/qkd-certs/` | Ubuntu server |
| `escript.log` | `/var/log/` | Both switches (Junos event script errors) |


## Debugging Tips

- **`/var/home/admin/qkd_test.log`** is the primary log — check here first for `script run SUCCESS` or error messages
- **`/var/log/escript.log`** shows event script errors on classic Junos (import errors, syntax errors, runtime exceptions). On Evolved, this file often stays empty when things work
- **`/root/mock-kme/kme.log`** shows incoming requests to the mock KME — if no GET requests appear, the switches can't reach the server
- If `escript.log` is empty and `qkd_test.log` doesn't exist, the event-options config is wrong, `set system scripts language python3` is missing, or the script file is not in `/var/db/scripts/event/`
- If the script fails with no error in `qkd_test.log`, the crash happens before the logging framework initializes — most likely `Profile.py` is missing or has incompatible parameters
- Always run `python3 -c "import py_compile; py_compile.compile('qkd_macsec.py', doraise=True)"` on a machine with Python before deploying to catch syntax errors
- Use `curl --cacert /var/home/admin/certs/client-root-ca.crt https://<server_ip>:443/api/v1/keys/test/enc_keys` from the switch shell to verify KME connectivity before relying on the script
- Use `show security mka sessions summary` to see real-time MKA negotiation status — matching CAK Names with `live` status and Rx > 0 confirms a successful key rollover
- If `show security mka sessions summary` shows `in-progress` with Rx=0 and different CAK Names between switches, the event timer is too short or the clocks are out of sync
- Verify the mock KME is running with `ss -tlnp | grep 443` on the server — Flask crashes silently when the terminal session times out if not started with `nohup`
