Below is the **“How to Run”** section. It includes clear steps, examples for both **onbox** and **offbox**, and environment expectations.

---

# How to Run the Script

This section explains how to run the QKD–MACsec automation script in both **offbox** (management host) and **onbox** (directly on a router) modes, with real and practical examples.

---

## 1. Requirements

### **Offbox Mode (recommended for managing many devices)**

You need a Linux/BSD/Unix host with:

* Python 3.7+
* Juniper PyEZ (`junos-eznc`)
* `paramiko`, `scp`, `pyOpenSSL`
* SSH reachability to each device
* The `targets.yaml` or `targets.json` configuration file

### **Onbox Mode (script runs directly on JunOS)**

You need:

* JunOS with `event-options` and Python enabled
* Script copied to `/var/db/scripts/event/`
* Proper permissions for `python-script-user`

---

## 2. Script Usage

The script supports the following command-line options:

| Option          | Description                                     |
| --------------- | ----------------------------------------------- |
| `-t, --threads` | Number of parallel threads (offbox only)        |
| `-v`            | Increase verbosity (can be used multiple times) |
| `-tr, --trace`  | Log debug-level output to `trace.log`           |

---

## 3. Running in Offbox Mode

### **Basic Example**

Run the script with default settings:

```bash
python3 qkd_macsec.py
```

If no thread count is provided, the script uses **1 thread**.

---

### **Using Multiple Threads**

To run on 10 devices in parallel:

```bash
python3 qkd_macsec.py --threads 10
```

This will:

* Spawn 10 worker threads
* Each thread executes `req_thread()`
* Each device goes through the full workflow:

  * Initial MACsec config (if not applied)
  * Certificate validation/renewal
  * Key ID retrieval (master → slave)
  * KME key fetch
  * MACsec key/CA update
  * Key ID persistence

---

### **Verbose Output**

More `-v` flags increase log detail.

```bash
python3 qkd_macsec.py -vv
```

* `-v`: INFO
* `-vv`: DEBUG
* `-vvv`: VERY verbose

---

### **Write Debug Information to trace.log**

```bash
python3 qkd_macsec.py --trace
```

This enables debug-level trace logging in:

```
./trace.log
```

---

### **Full Example with All Options**

```bash
python3 qkd_macsec.py --threads 5 -vv --trace
```

Meaning:

* Use 5 threads
* Verbose debug messages to console
* Full debug tracing logged to `trace.log`

---

## 4. Running in Onbox Mode (JunOS Event Script)

The script can also run as an **event-triggered script** using `event-options`.

### **Copy the Script to the Device**

Place the file in:

```
/var/db/scripts/event/qkd_macsec.py
```

Make sure permissions allow execution:

```bash
chmod 755 /var/db/scripts/event/qkd_macsec.py
```

---

### **Configure Event-Options (example)**

Example JunOS config:

```text
set event-options generate-event every10mins time-interval 600 start-time 00:00:00
set event-options policy qkd events every10mins
set event-options policy qkd then event-script qkd_macsec.py
set event-options event-script file qkd_macsec.py python-script-user admin
set event-options traceoptions file script.log
set event-options traceoptions file size 10m
```

This will:

* Trigger the script every 10 minutes
* Run it as user `admin`
* Log to `/var/log/script.log`

---

### **Manual Onbox Execution**

You can manually run the script on the router:

```bash
python3 /var/db/scripts/event/qkd_macsec.py
```

This follows the **same device workflow**:

* Certificate validation/renewal
* Key ID fetch (from master or local file)
* KME communication
* MACsec updates

---

## 5. File Outputs

During execution, the script generates:

#### **Key ID file**

```
<hostname>_keyID.json
```

#### **Certificates**

```
ca_cert.pem
ca_key.pem
client_cert.pem
client_key.pem
```

#### **Logs**

* `script.log` (onbox trace)
* `trace.log` (offbox debug trace if enabled)
* Console output based on `-v` flags

---

## 6. Typical Workflow Example

### **Scenario**

You have 3 devices:

* `PE1` (master)
* `PE2` (slave)
* `PE3` (slave)

And you want to configure all of them at once with QKD–MACsec.

### **Run:**

```bash
python3 qkd_macsec.py --threads 3 -vv --trace
```

### The script will:

1. Connect to PE1, PE2, PE3
2. Apply initial MACsec config (if needed)
3. Validate certificates; renew if needed
4. Fetch CA and client certificates
5. PE1 fetches key from KME → saves key ID
6. PE2/PE3 retrieve master key ID
7. Each device fetches its KME key
8. Final MACsec config is applied
9. All key IDs saved for next iteration

---

## 7. Confirming Device Configuration

On a JunOS device, check MACsec settings:

```bash
show security macsec
show security certificates local
show log script.log
```

Check event triggers:

```bash
show event-options policy
show event-options event-script
```

---

# Appendix 
1. **A complete `targets.yaml` example**
2. **A full Troubleshooting section**

---

# targets.yaml (Example Configuration File)

This file defines all devices, roles (master/slave), KME endpoints, credentials, CA behavior, and system parameters.


```
system:
  maxthreads: 4
  event_options:
    start_time: "2025-03-23.13:00:00"

secrets:
  username: "admin"
  password: "admin123!"

CA_server:
  CA_cert:
    fetch: false
    generate: false
  c_a: "CA_basic"
  ca_server_ip: "192.168.10.10"
  ca_path: "/opt/ca/"
  ca_cert_name: "root_ca_cert.pem"
  ca_key_name: "root_ca_key.pem"
  ca_user: "caadmin"
  ca_pass: "capassword12!"

qkd_roles:
  master: "acx-1"
  slave: "acx-2"
  additional_slave_SAE_IDs: ["acx-3", "acx-4"]

acx-1:
  root_enc_pass: "$6$3eHulK1c$Yq.kaamV8hcuviwRebQI4gUMRSOGVIiBN8o/QTw7sfZ4GCfExd3TjyuUrsyrgfoBW3xNQVT5/gtGg6.S09okg0"
  ip: "9.173.8.201"
  interfaces:
    - "et-0/0/20:2"
  kme:
    kme_name: "https://idq-1"
    kme_ip: "9.173.9.102"

acx-2:
  root_enc_pass: "$6$bOeXyUQ7$oefu0aDycBhyLGDE.TCExBrdVkYOhg2IOesMVwRQvid9iDpMzwm5yZPvYhKlBu3sZ0YbHBAH0ro5SQWTnscWf."
  ip: "9.173.8.202"
  interfaces:
    - "et-0/0/20:2"
  kme:
    kme_name: "https://idq-2"
    kme_ip: "9.173.9.103"

acx-3:
  root_enc_pass: "$6$someotherpass$morehashdata"
  ip: "9.173.8.203"
  interfaces:
    - "et-0/0/20:2"
  kme:
    kme_name: "https://idq-3"
    kme_ip: "9.173.9.104"

acx-4:
  root_enc_pass: "$6$randompass$hashxxxyyy"
  ip: "9.173.8.204"
  interfaces:
    - "et-0/0/20:2"
  kme:
    kme_name: "https://idq-4"
    kme_ip: "9.173.9.105"
```

---

# Troubleshooting Guide

This section helps diagnose common issues when running the QKD–MACsec automation script.

---

## 1. Script Fails to Connect to Device (SSH Error)

### Symptoms:

* `TimeoutError`
* `Authentication failed`
* `SSHException: Error reading SSH protocol banner`

### Causes:

* Wrong username/password
* Device not reachable
* SSH disabled or filtered by firewall

### Fix:

```bash
ssh admin@<device-ip>
```

If SSH fails manually → fix network or credentials.

Ensure device allows Python script execution:

```text
set system scripts language python3
```

---

## 2. `KeyID JSON file not found`

### Message:

```
File to read previous keyId(s) not found
```

This is **not an error** during the first run.

### Fix:

None required — the script will create `<device>_keyID.json` automatically.

---

## 3. MACsec Not Coming Up After Script Execution

### Checks:

1. Verify MACsec status:

```bash
show security macsec
```

2. Verify key exchange status:

```bash
show security macsec connectivity-association
```

3. Check KME communication:

```bash
show log messages | match kme
```

### Common Causes:

* Key mismatch between master and slaves
* Wrong interface in `targets.yaml`
* KME IP unreachable
* CA certificate mismatch

---

## 4. Certificate Creation or Upload Fails

### Symptoms:

* Missing certificate fields
* “invalid certificate format”
* Upload fails on device

### Fix Checklist:

* Ensure OpenSSL is installed (offbox)
* Ensure CA paths are correct
* Ensure CA and client keys are readable by script
* Check certificate validity:

```bash
openssl x509 -in client_cert.pem -text
```

---

## 5. `SCPException` When Fetching Master KeyID File

### Causes:

* Wrong master hostname
* Wrong file path
* SCP blocked

### Fix:

Check remote file existence:

```bash
file list /var/home/admin/acx-1_keyID.json
```

Verify scp manually:

```bash
scp admin@acx-1:/var/home/admin/acx-1_keyID.json .
```

---

## 6. Threads Start but Nothing Happens (Offbox Mode)

### Causes:

* `targets.yaml` has no devices
* Threads created but `req_thread()` exits early due to local error
* Logging verbosity too low to see details

### Fix:

Run with high verbosity:

```bash
python3 qkd_macsec.py --threads 5 -vvv
```

Check `trace.log` if enabled.

---

## 7. Onbox Mode: Script Not Triggering

### Causes:

* Event-options misconfigured
* Script not in correct directory
* Wrong owner/permissions

### Fix Steps:

1. Verify script placement:

```
/var/db/scripts/event/<your-script>.py
```

2. Verify JunOS event options:

```bash
show event-options policy
show event-options event-script
```

3. Check logs:

```bash
show log script.log
```

---

## 8. KME Not Returning Key

### Symptoms:

* “key_id not found”
* “failed to fetch key”
* “400/403 from KME”

### Fix:

* Verify KME is reachable:

```bash
curl https://<kme-ip>/status
```

* Validate TLS certificates
* Ensure device hostname matches KME policy

---

## 9. Device Fails at `check_and_apply_initial_config()`

### Symptoms:

* Interface not found
* MACsec configuration rejected

### Fix:

Verify interfaces in `targets.yaml` match device:

```bash
show interfaces terse | match et-0/0/20
```

---

## 10. Script Crashes with Python Exceptions

### Common Causes:

* Missing dependencies
* Invalid path
* Typo in hostname
* Corrupted JSON keyID file

### Fix:

Delete corrupted keyID file:

```bash
rm <device>_keyID.json
```

Re-run script.

---


