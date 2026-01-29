# QKD MACsec Automation Script — Functional Overview

## 1. Purpose of the Script

This script automates the full lifecycle required to configure **QKD-backed MACsec** on network devices.
It handles:

* Secure SSH connectivity
* Certificate generation, renewal, and upload
* Retrieval of QKD keys from a KME
* Distribution of key IDs between devices
* Applying initial and periodic MACsec configuration
* Multithreaded off-box management of many devices
* Profiling and logging for debugging and traceability

It supports two execution modes:

* **Offbox mode** — Run from a management host and configure multiple devices in parallel.
* **Onbox mode** — Run directly on a device, performing the full workflow locally.

The script is separated into logical components (threads, certificates, SSH, KME interactions, MACsec config, etc.), with each function contributing toward completing the workflow.

---

# 2. Functional Breakdown

Below is a description of every function, grouped by subsystem, showing how they work together.

---

## 3. Threading / Parallel Execution

### `def background(func):`

A decorator that runs the wrapped function asynchronously in a background thread.

* Creates a `Thread`
* Starts it as a daemon
* Tracks it in the global `threads` list

Used primarily to parallelize device operations in **offbox mode**.

---

### `def req_thread(tnum, reqs, targets_dict, log):`

Worker function executed in background threads.

* Iterates over a subset (`reqs`) of devices assigned to this thread.
* For each device:

  * Creates a connection (`Device`)
  * Applies initial config
  * Fetches and pushes keys
  * Applies final MACsec configuration

Contributes to scaling the script for multi-device deployments.

---

## 4. Certificate Management

### `def generate_ca_certificate(ca_cert_path, ca_key_path, ca_subject):`

Creates a self-signed **CA certificate** and private key, used for signing client certificates.

Used when the CA certificate is missing or expired.

---

### `def generate_client_certificate(client_cert_path, client_key_path, ca_cert_path, ca_key_path, client_subject):`

Generates a client certificate/key pair signed by the CA.

Uploaded later to the device for secure KME communication.

---

### `def is_certificate_valid(cert_path, min_valid_days=10):`

Checks if a certificate exists and whether it is still valid for at least `min_valid_days` days.

Used before renewing certificates.

---

### `def get_certificates(dev, log, targets_dict):`

Downloads the device's **existing** client certificate and key over SSH.

Used to validate and determine whether renewal is required.

---

### `def upload_certificates(dev, cert_path, key_path, log, targets_dict):`

Uploads new or renewed certificates back onto the device via SCP.

---

### `def fetch_ca_certificate(targets_dict):`

Retrieves (or generates) the CA certificate on the management host.

May be used to distribute a trusted CA to devices.

---

### `def renew_certificates(dev, log, targets_dict):`

Central certificate workflow on a device:

1. Check existing cert validity
2. If invalid or missing:

   * Generate new client cert
   * Upload it to the device
3. Log success/failure

Used within the main processing flow.

---

### `def should_check_certs():`

Tells the script if certificate validation should be performed.
Triggered via command-line flags or environment conditions.

---

## 5. Logging / CLI

### `def initialize_logging(args):`

Configures logging:

* Verbosity (`-v`)
* Trace mode (`--trace`)
* Logging to file or console

Essential for debugging distributed device operations.

---

### `def get_args():`

Defines command-line parameters for:

* Thread count
* Verbose output
* Trace logs

Used by `main()` to interpret user input.

---

## 6. Key ID Tracking

### `def get_previous_key_ids(log, name):`

Reads previously stored key IDs from the device's JSON file.

Used to determine if a new key has been fetched and prevent unnecessary work.

---

### `def save_key_ids(key_dict, local_name):`

Writes the key IDs into a JSON file for persistent tracking.

---

## 7. KME Key Retrieval

### `def fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id, additional_slave_SAE_IDs=None):`

Contacts the **KME** and retrieves a fresh QKD key for the specified device.

* Negotiates session keys
* Stores the returned key material locally
* Updates key IDs for master/slave/additional devices

Core function for QKD key retrieval.

---

## 8. Device MACsec Configuration

### `def check_and_apply_initial_config(dev, targets_dict, log):`

Runs initial device configuration:

* Basic MACsec setup
* CA/client certificate preparation
* Upload of trust anchors
* Validation checks

Executed once per device unless the device already has MACsec config.

---

### `def get_key_id_from_master(dev, log, targets_dict):`

Retrieves the **master’s Key ID**:

* Offbox: Reads from master's JSON file
* Onbox: SCP fetches key ID file

Used so slaves can request the correct KME key.

---

### `def process(dev, targets_dict, log):`

The **central device workflow**:

1. Renew certificates (if needed)
2. Fetch key ID from master (if slave)
3. Retrieve new KME key
4. Apply MACsec key + policy configuration
5. Save the new key ID locally

This is what each thread or onbox execution ultimately performs.

---

## 9. Main Program Flow

### `def main():`

Entry point of the script.

Responsible for:

1. Parsing CLI arguments
2. Initializing logging
3. Loading configuration (`targets_dict`)
4. Deciding Onbox vs Offbox execution
5. Offbox:

   * Split device list into thread groups
   * Call `req_thread()` for each
   * Wait for all threads to finish
6. Onbox:

   * Run full `process()` directly and sequentially

This function connects everything into a complete automated workflow.

---

# 10. End-to-End Workflow Summary

The entire script implements this high-level pipeline:

```
Parse Args → Init Logging → Load Targets → (Offbox: Threading)  
     ↓  
Connect to Device  
     ↓  
Certificate Handling (validate, renew, upload)  
     ↓  
Retrieve Key ID  
     ↓  
Fetch QKD Key from KME  
     ↓  
Apply MACsec Configuration  
     ↓  
Save Key IDs → Done
```

This structure ensures **automated, scalable, secure MACsec provisioning** with QKD key material across many devices.

---




# Environment configuration block.
* Defines a base directory (CUR_DIR) path and certificate path
* Builds all file paths relative to that directory.
* Sets up database connection configuration settings.
* Specifies TLS certificates.
* Defines API URLs.
* Creates logging locations and profiling locations.


CUR_DIR means “current working directory for this application”
It is simply the base folder from which the program reads/writes files like:
* certs
* logs
* JSON output
* profiling data

OFFBOX_CERTS_DIR stores certificates:
* TLS certificate
* TLS private key
* Root CA


DATABASE_PORT = '10000'
DATABASE_HOST = '9.173.9.102'
DATABASE_USER = 'db_user'
DATABASE_PASSWORD = 'db_password'

DATABASE_URL = f'postgres://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/key_store'
These define how the ETSI-014 reference implementation connects to PostgreSQL.
Final URL produced:

postgres://db_user:db_password@9.173.9.102:10000/key_store


CERTS_DIR = f'{CUR_DIR}/certs/'
Same as OFFBOX_CERTS_DIR, used to store:
CA_CERT = f'{CERTS_DIR}/root.crt'
This is the path to the CA certificate.


ETSI_014_REF_IMPL_TLS_CERT = f'{OFFBOX_CERTS_DIR}/kme_001.crt'
ETSI_014_REF_IMPL_TLS_PRIVATE_KEY = f'{OFFBOX_CERTS_DIR}/kme_001.key'
ETSI_014_REF_IMPL_TLS_ROOT_CRT = f'{OFFBOX_CERTS_DIR}/root.crt'
These are TLS files for a Key Management Entity (KME) implementation.

# Threads
The threads[] list will hold every thread that the decorator creates.This list will hold every thread the program starts. It allows the program to keep track of all the work happening in parallel.
Useful if you later want to:
* wait for them
* check their status
* debug how many threads are running

The decorator starts that function in a separate background thread.
The thread is set as a daemon thread, which means it does not block the program from exiting.
The thread starts immediately.
The thread object is added to the threads list for tracking.
The original function runs asynchronously (in parallel), without waiting for it to finish.
In simple terms:
Putting @background above a function makes it run in the background.


# Understanding the Threading Code

## 1. A list to track background threads

```python
threads = []
```

This list will hold every thread the program starts.
It allows the program to keep track of all the work happening in parallel.

---

## 2. A decorator that runs functions in the background

```python
def background(func):
    def bg_func(*args, **kwargs):
        t = Thread(target=func, args=args, kwargs=kwargs)
        t.setDaemon(True)
        t.start()
        threads.append(t)
    return bg_func
```

This is a **decorator**.
Decorators wrap a function and change how it behaves.

### What this decorator does:

* When you call a decorated function, it **starts that function in a separate background thread**.
* The thread is set as a **daemon thread**, which means it does not block the program from exiting.
* The thread starts immediately.
* The thread object is added to the `threads` list for tracking.
* The original function runs *asynchronously* (in parallel), without waiting for it to finish.

### In simple terms:

**Putting `@background` above a function makes it run in the background.**

---

## 3. A threaded function that processes devices

```python
@background
def req_thread(tnum, reqs, targets_dict, log):
```

By adding `@background`, this function now runs in its **own thread** whenever it's called.

---

# `req_thread()` Explained

This function is designed to run on one thread and process a list of devices.

Here’s what it does step-by-step:

---

## 1. Start profiling for the whole thread

```python
prof.start("Thread-{0}".format(tnum))
```

This logs performance information for the thread.

---

## 2. Loop through each device in the list

```python
for device in reqs:
    print(device)
```

It prints each device name as it works on it.

---

## 3. Connect to the network device

```python
with Device(host=targets_dict[device]['ip'],
            user=targets_dict["secrets"]["username"],
            password=targets_dict["secrets"]["password"],
            port=22) as dev:
```

For each device:

* It opens an SSH session to the Juniper device.
* The connection automatically closes when done.

---

## 4. Optionally renew certificates

```python
if should_check_certs():
    renew_certificates(dev, log, targets_dict=targets_dict)
```

If the program decides certificates need checking, it:

* runs certificate renewal
* logs the activity
* profiles how long it took

---

## 5. Apply initial configuration

```python
check_and_apply_initial_config(dev, targets_dict, log)
```

This step ensures that the device has the correct initial configuration
before performing further tasks.

Profiling is used around this step to measure performance.

---

## 6. Run the main processing logic

```python
process(dev, targets_dict, log)
```

This is the main work done for each device.
It could be anything: pushing config, collecting data, etc.

Again, profiling measures how long it takes.

---

## 7. Stop profiling at the end of the thread

```python
prof.stop("Thread-{0}".format(tnum))
```

This finalizes all timing measurements for the thread.

---

## Summary

✔ The `@background` decorator makes any function run in a **background thread**.

✔ `req_thread()` is a function that:

* receives a list of network devices
* connects to each device
* optionally renews certificates
* applies initial configuration
* performs the main processing logic
* logs and profiles everything
* does each device in order
* but runs the *entire function* in its own thread

✔ Multiple `req_thread()` calls allow the program to work on many devices **in parallel**, each handled by a separate thread.

---


# `generate_ca_certificate` Explained

This function creates a **self-signed Certificate Authority (CA) certificate** and its private key, and saves them as files.

---

## **Function Signature**

```python
def generate_ca_certificate(ca_cert_path, ca_key_path, ca_subject):
```

**Parameters:**

* `ca_cert_path` → file path where the CA certificate will be saved (PEM format)
* `ca_key_path` → file path where the CA private key will be saved (PEM format)
* `ca_subject` → the “common name” (CN) for the CA (e.g., `"My Root CA"`)

**Returns:**

* Tuple `(ca_cert_path, ca_key_path)` → paths of the saved certificate and key files

---

## **Step 1: Generate the CA private key**

```python
ca_key = crypto.PKey()
ca_key.generate_key(crypto.TYPE_RSA, 2048)
```

* Creates a **2048-bit RSA key**, which will be used to sign the CA certificate.
* This key is the secret that proves the CA’s identity.

---

## **Step 2: Create the CA certificate object**

```python
ca_cert = crypto.X509()
ca_cert.set_version(2)
ca_cert.set_serial_number(int(uuid.uuid4()))
ca_cert.get_subject().CN = ca_subject
ca_cert.set_issuer(ca_cert.get_subject())
ca_cert.set_pubkey(ca_key)
```

* `X509()` → creates a new certificate object.
* `set_version(2)` → X.509 version 3 certificate (0-based counting in OpenSSL: version 2 = v3).
* `set_serial_number(int(uuid.uuid4()))` → unique serial number using a UUID.
* `get_subject().CN = ca_subject` → sets the **Common Name (CN)** field of the certificate.
* `set_issuer(ca_cert.get_subject())` → issuer is the same as subject → **self-signed**.
* `set_pubkey(ca_key)` → associate the public key with the certificate.

---

## **Step 3: Set validity period**

```python
ca_cert.gmtime_adj_notBefore(0)
ca_cert.gmtime_adj_notAfter(5 * 365 * 24 * 60 * 60)  # 5 years
```

* Certificate is valid **from now** (`notBefore`) until 5 years (`notAfter`).
* Time is expressed in **seconds**.

---

## **Step 4: Add X.509 extensions**

```python
ca_cert.add_extensions([
    crypto.X509Extension(b"subjectKeyIdentifier", False, b"hash", subject=ca_cert),
    crypto.X509Extension(b"basicConstraints", False, b"CA:TRUE"),
])
```

* `subjectKeyIdentifier` → allows others to identify this certificate’s public key.
* `basicConstraints` → marks this certificate as a **Certificate Authority** (`CA:TRUE`).
* Some other extensions are commented out (authorityKeyIdentifier, keyUsage), but could be added later.

---

## **Step 5: Sign the certificate**

```python
ca_cert.sign(ca_key, 'sha256')
```

* Uses the CA private key to **sign the certificate** with SHA-256.
* This makes it a valid self-signed CA certificate.

---

## **Step 6: Save the certificate and key to files**

```python
with open(ca_cert_path, "wb") as f:
    f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, ca_cert))

with open(ca_key_path, "wb") as f:
    f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, ca_key))
```

* Writes the certificate and private key in **PEM format**.
* These files can be used later to sign server or client certificates.

---

## **Step 7: Return the paths**

```python
return ca_cert_path, ca_key_path
```

* Returns the file paths for reference, so the caller knows where they were saved.

---

## Summary

1. Create a new RSA key (private key for the CA).
2. Create a new X.509 certificate object.
3. Fill in the certificate info (subject, issuer, serial number, public key).
4. Set how long the certificate is valid (5 years).
5. Add CA-specific extensions (marks it as a certificate authority).
6. Sign the certificate using the CA private key (self-signed).
7. Save both certificate and private key to files.
8. Return the file paths.

---
Here’s a **plain-language explanation in Markdown** of your `generate_client_certificate` function, step by step.

---

# `generate_client_certificate` Explained

This function creates a **client certificate** that is signed by an existing **CA (Certificate Authority)**.
It also generates a private key for the client and saves both files.

---

## **Function Signature**

```python
def generate_client_certificate(client_cert_path, client_key_path, ca_cert_path, ca_key_path, client_subject):
```

**Parameters:**

* `client_cert_path` → where the client certificate will be saved
* `client_key_path` → where the client private key will be saved
* `ca_cert_path` → path to the CA certificate (used to sign the client cert)
* `ca_key_path` → path to the CA private key
* `client_subject` → the “Common Name (CN)” for the client certificate

**Returns:**

* Tuple `(client_cert_path, client_key_path)` → paths of saved certificate and key files

---

## **Step 1: Load the CA certificate and key**

```python
ca_key = crypto.load_privatekey(crypto.FILETYPE_PEM, open(ca_key_path, 'rb').read())
ca_cert = crypto.load_certificate(crypto.FILETYPE_PEM, open(ca_cert_path, 'rb').read())
```

* Reads the **CA private key** and **CA certificate** from disk.
* These are used to **sign the client certificate**, proving trust.

---

## **Step 2: Generate a new client key**

```python
client_key = crypto.PKey()
client_key.generate_key(crypto.TYPE_RSA, 2048)
```

* Creates a **2048-bit RSA key** for the client.
* This key is secret and will be stored in a file for the client to use.

---

## **Step 3: Create the client certificate object**

```python
client_cert = crypto.X509()
client_cert.set_version(2)
client_cert.set_serial_number(int(uuid.uuid4()))
client_cert.get_subject().CN = client_subject
client_cert.set_issuer(ca_cert.get_subject())
client_cert.set_pubkey(client_key)
client_cert.gmtime_adj_notBefore(0)
client_cert.gmtime_adj_notAfter(10*365*24*60*60)
```

* `X509()` → create a new certificate object
* `set_version(2)` → X.509 version 3 certificate
* `set_serial_number(int(uuid.uuid4()))` → unique serial number
* `get_subject().CN = client_subject` → sets the client’s name
* `set_issuer(ca_cert.get_subject())` → issuer is the CA
* `set_pubkey(client_key)` → attach client’s public key
* `gmtime_adj_notBefore(0)` → valid from now
* `gmtime_adj_notAfter(...)` → valid for 10 years

---

## **Step 4: Add X.509 extensions**

```python
client_cert.add_extensions([
    crypto.X509Extension(b"basicConstraints", False, b"CA:FALSE"),
    crypto.X509Extension(b"authorityKeyIdentifier", False, b"keyid:always", issuer=ca_cert),
    crypto.X509Extension(b"keyUsage", False, b"Digital Signature, Non Repudiation, Key Encipherment, Data Encipherment"),
])
```

* `basicConstraints` → marks this certificate as **not a CA**
* `authorityKeyIdentifier` → links this cert to the CA that signed it
* `keyUsage` → defines what the certificate can be used for (signing, encryption, etc.)
* Other extensions like `subjectKeyIdentifier` and `extendedKeyUsage` are commented out but could be added for client authentication.

---

## **Step 5: Sign the client certificate**

```python
client_cert.sign(ca_key, 'sha256')
```

* Uses the **CA private key** to sign the client certificate.
* This makes it **trusted by anyone who trusts the CA**.

---

## **Step 6: Save the client certificate and key**

```python
with open(client_cert_path, "wb+") as f:
    f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, client_cert))

with open(client_key_path, "wb+") as f:
    f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, client_key))
```

* Saves both files in **PEM format**.
* These files can be used by the client for authentication.

---

## **Step 7: Return the paths**

```python
return client_cert_path, client_key_path
```

* Returns the file paths so the caller knows where the certificate and key were saved.

---

## Summary

1. Load the CA certificate and private key (the signer).
2. Generate a new RSA key for the client.
3. Create a new certificate object for the client.
4. Fill in client info (name, issuer, public key, validity).
5. Add extensions (not a CA, key usage, link to CA).
6. Sign the certificate with the CA’s key.
7. Save the certificate and private key to files.
8. Return the file paths.

---

# `is_certificate_valid` Explained

This function checks whether a **certificate file** is still valid for at least a given number of days.

---

## **Function Signature**

```python
def is_certificate_valid(cert_path, min_valid_days=10):
```

**Parameters:**

* `cert_path` → path to the certificate file (PEM format)
* `min_valid_days` → the minimum number of days the certificate should still be valid (default is 10)

**Returns:**

* `True` → certificate is valid for at least `min_valid_days`
* `False` → certificate expires sooner than `min_valid_days`

---

## **Step 1: Read the certificate file**

```python
with open(cert_path, 'rb') as cert_file:
    cert_data = cert_file.read()
```

* Opens the certificate file in **binary mode** (`rb`)
* Reads the contents into `cert_data`

---

## **Step 2: Load the certificate**

```python
cert = crypto.load_certificate(crypto.FILETYPE_PEM, cert_data)
```

* Converts the PEM data into an **OpenSSL X.509 certificate object**
* This allows access to the certificate fields like `notAfter` (expiration date)

---

## **Step 3: Parse the expiration date**

```python
not_after = datetime.datetime.strptime(cert.get_notAfter().decode('ascii'), '%Y%m%d%H%M%SZ')
```

* `cert.get_notAfter()` → returns the expiration date in ASN.1 format (bytes like `b'20251127120000Z'`)
* `.decode('ascii')` → convert bytes to string
* `datetime.strptime(..., '%Y%m%d%H%M%SZ')` → convert string to a Python `datetime` object

Now `not_after` is the **exact expiration date and time** of the certificate.

---

## **Step 4: Calculate remaining validity**

```python
remaining_days = (not_after - datetime.datetime.now()).days
```

* Subtracts **current date/time** from the expiration date
* Gets the difference in **days**
* This tells us how many days are left until the certificate expires

---

## **Step 5: Check against minimum required days**

```python
return remaining_days >= min_valid_days
```

* Returns `True` if the certificate is valid for at least `min_valid_days`
* Returns `False` if it will expire sooner

---

## Summary

1. Open the certificate file.
2. Load it into a usable certificate object.
3. Read its expiration date.
4. Calculate how many days remain until it expires.
5. Return `True` if it’s valid for at least the minimum days, otherwise `False`.

---

This function is useful for:

* **Automated certificate renewal checks**
* **Preventing expired certificate errors** in TLS/SSL connections
* **Monitoring certificate health** in network devices

---

# `createSSHClient` Explained

This version of the function is designed to **safely connect to a remote device over SSH** with automatic **retries** and **timeouts**. It’s more robust than the previous version.

---

## **1. Function signature**

```python
def createSSHClient(device, username, password, port=22, retries=3, delay=5, timeout=10):
```

* `device` → IP or hostname of the device
* `username` → SSH username
* `password` → SSH password
* `port` → SSH port, default 22
* `retries` → how many times to try connecting if it fails
* `delay` → seconds to wait between retries
* `timeout` → maximum time (seconds) to wait for SSH connection

---

## **2. Retry loop**

```python
for attempt in range(1, retries + 1):
```

* Tries connecting **multiple times** if the first attempt fails.
* `attempt` counts the current try (1, 2, 3, …).

---

## **3. Create and configure SSH client**

```python
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
```

* `SSHClient()` → creates a new SSH client object.
* `set_missing_host_key_policy(AutoAddPolicy())` → automatically trusts unknown hosts instead of failing.

---

## **4. Connect to the device**

```python
client.connect(
    hostname=device,
    port=port,
    username=username,
    password=password,
    timeout=timeout,
    banner_timeout=timeout,
    auth_timeout=timeout
)
```

* Attempts the actual SSH connection.
* Uses **timeouts** for connection, banner, and authentication to prevent hanging.

---

## **5. Success message**

```python
print(f"Connected to {device} on attempt {attempt}")
return client
```

* If connection succeeds, prints a confirmation and **returns the connected SSH client**.

---

## **6. Handle connection errors**

```python
except (SSHException, AuthenticationException, TimeoutError) as e:
    print(f"Attempt {attempt} failed to connect to {device}: {e}")
```

* Catches **SSH errors, authentication failures, and timeout errors**.
* Prints the error for visibility.

---

## **7. Retry or fail**

```python
if attempt < retries:
    print(f"Retrying in {delay} seconds...")
    time.sleep(delay)
else:
    print(f"Failed to connect to {device} after {retries} attempts.")
    return None
```

* If there are retries left, waits for `delay` seconds and tries again.
* If this was the last attempt, prints a failure message and **returns `None`**.

---

## Summary

1. Try to connect to the device via SSH.
2. If it fails, retry up to `retries` times, waiting `delay` seconds each time.
3. If connection succeeds, return the SSH client object.
4. If all attempts fail, return `None`.
5. Uses timeouts to prevent the program from hanging indefinitely.
6. Automatically accepts unknown host keys.

---

This version is **safe for looping over many devices** because failed connections don’t crash the program, and successful connections can still be used for commands.


# `get_certificates` Explained

This function retrieves **TLS/SSL certificates** for a given device, either from **local files** (on the device itself) or via **SCP from a remote device**.

---

## **Function Signature**

```python
def get_certificates(dev, log, targets_dict):
```

**Parameters:**

* `dev` → a device object (likely from `jnpr.junos.Device`)
* `log` → logging object (for writing logs, not used here in the snippet)
* `targets_dict` → dictionary containing device info and credentials

**Returns:**

* `ca_cert_path` → path to the CA certificate
* `ca_key_path` → path to the CA private key
* `client_cert_path` → path to the client certificate
* `client_key_path` → path to the client private key

---

## **Step 1: Determine if running on the device (onbox)**

```python
if onbox:
    local_name = dev.facts['hostname']
    ca_cert_path = os.path.join(CERTS_DIR, 'client-root-ca.crt')
    ca_key_path = os.path.join(CERTS_DIR, 'client-root-ca.key')
    client_cert_path = os.path.join(CERTS_DIR, f'{local_name}.crt')
    client_key_path = os.path.join(CERTS_DIR, f'{local_name}.key')
```

* If the code is **running directly on the device**:

  * Use the device’s hostname as `local_name`.
  * Certificates are expected to be in `CERTS_DIR`.
  * Build paths for the CA certificate, CA key, client certificate, and client key.

---

## **Step 2: If running off-device (offbox)**

```python
else:
    local_name = dev.facts['hostname'].split('-re')[0]
    client = createSSHClient(local_name, username=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22)
```

* If the code runs elsewhere:

  * Derive `local_name` from the hostname (remove `-re` suffix).
  * Create an SSH connection to the remote device using credentials in `targets_dict`.

---

### **Step 2a: Copy certificates via SCP**

```python
with SCPClient(client.get_transport()) as scp:
    scp.get(remote_path=CERTS_DIR, local_path=OFFBOX_CERTS_DIR, recursive=True)
```

* Uses SCP (Secure Copy) to **download all files from the device’s `CERTS_DIR` to local `OFFBOX_CERTS_DIR`**.
* `recursive=True` ensures directories are copied fully.

---

### **Step 2b: Build certificate paths**

```python
ca_cert_path = os.path.join(OFFBOX_CERTS_DIR, 'account-1286-server-ca-qukaydee-com.crt')
ca_key_path = os.path.join(OFFBOX_CERTS_DIR, 'account-1286-server-ca-qukaydee-com.crt')
client_cert_path = os.path.join(OFFBOX_CERTS_DIR, f'{local_name}.crt')
client_key_path = os.path.join(OFFBOX_CERTS_DIR, f'{local_name}.key')
```

* Sets paths to CA certificate and key (for testing they have the same file).
* Sets paths for client certificate and key using `local_name`.

---

### **Step 2c: Handle SCP errors**

```python
except SCPException as e:
    print(f'SCP get exception error: {e}')
```

* Prints an error if **SCP fails**, e.g., network problem or missing files.

---

## **Step 3: Return certificate paths**

```python
return ca_cert_path, ca_key_path, client_cert_path, client_key_path
```

* Returns all four paths for use elsewhere, e.g., generating client certificates, validation, or TLS connections.

---

## Summary

1. Determine if the script is **running directly on the device** (`onbox`) or elsewhere.
2. If onbox, certificates are read locally.
3. If offbox:

   * Connect via SSH to the device
   * Copy certificate files with SCP to a local folder
   * Build paths to CA certificate, CA key, client certificate, and client key
4. Return the file paths for use by other functions.
5. Errors during SCP are caught and printed.

---

This function is useful for **centralizing certificate management** across multiple network devices, ensuring both **on-device and off-device workflows** are supported.


# `upload_certificates` Explained

This function **uploads certificates to a remote device** using SSH and SCP.

---

## **Function Signature**

```python
def upload_certificates(dev, cert_path, key_path, log, targets_dict):
```

**Parameters:**

* `dev` → device object (likely from `jnpr.junos.Device`)
* `cert_path` → local path to the certificate file
* `key_path` → local path to the private key file
* `log` → logging object (not used in this snippet)
* `targets_dict` → dictionary containing device info and credentials

**Returns:**

* Nothing; it **uploads files** to the remote device.

---

## **Step 1: Determine the device IP**

```python
local_name = dev.facts['hostname'].split('-re')[0]
# TODO: line below used for testing
local_name = targets_dict[local_name]['ip']
```

* Uses the device hostname to look up the **IP address** from `targets_dict`.
* The `split('-re')[0]` removes any `-re` suffix from hostnames (device naming convention).
* For testing, it directly assigns the IP from `targets_dict`.

---

## **Step 2: Create an SSH connection**

```python
client = createSSHClient(local_name, username=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22)
```

* Calls your improved `createSSHClient` function to establish a **secure SSH session** to the device.

---

## **Step 3: Upload files via SCP**

```python
with SCPClient(client.get_transport()) as scp:
    cert_files = [cert_path, key_path]
    scp.put(files=cert_files, remote_path=CERTS_DIR)
```

* Opens an SCP session over the SSH connection.
* Defines `cert_files` → a list of the certificate and key files to upload.
* Uploads the files to the device’s `CERTS_DIR` directory.

---

## **Step 4: Handle errors**

```python
except SCPException as e:
    print(f'SCP put exception error: {e}')
```

* Catches **SCP exceptions**, e.g., network errors, permissions issues.
* Prints a message instead of crashing the program.

---

## Summary

1. Determine the IP of the device from its hostname.
2. Connect to the device via SSH.
3. Upload the certificate and private key files to the device using SCP.
4. Catch and report any errors.

---

💡 **Notes / Suggestions**

* You could add **return values** or logging to indicate success/failure.
* Consider **checking if the files exist locally** before attempting upload.
* Using the `retries` and `timeout` from your SSH client function can make uploads more robust.



# `fetch_ca_certificate` Explained

This function **retrieves the CA (Certificate Authority) certificate and key** from a remote CA server.

---

## **Function Signature**

```python
def fetch_ca_certificate(targets_dict):
```

**Parameters:**

* `targets_dict` → dictionary containing information about the CA server and credentials

**Returns:**

* `ca_cert_path` → local path to the downloaded CA certificate
* `ca_key_path` → local path to the downloaded CA private key

---

## **Step 1: Get CA server details**

```python
ca_server_ip = targets_dict["CA_server"]["ca_server_ip"]
ca_user = targets_dict["CA_server"]["ca_user"]
ca_pass = targets_dict["CA_server"]["ca_pass"]
ca_path = targets_dict["CA_server"]["ca_path"]
ca_cert_name = targets_dict["CA_server"]["ca_cert_name"]
ca_key_name = targets_dict["CA_server"]["ca_key_name"]
```

* Reads IP, username, password, remote path, certificate name, and key name for the CA server from `targets_dict`.
* These are used to **connect to the server and locate the files**.

---

## **Step 2: Create an SSH connection to the CA server**

```python
client = createSSHClient(ca_server_ip, username=ca_user, password=ca_pass, port=22)
```

* Uses the `createSSHClient` function to establish a secure SSH connection.
* This allows the script to access files on the remote CA server.

---

## **Step 3: Determine local download path**

```python
LOCAL_PATH = CERTS_DIR if onbox else OFFBOX_CERTS_DIR
```

* If running **on the device** (`onbox`), save certificates to `CERTS_DIR`.
* If running **off-device**, save to `OFFBOX_CERTS_DIR`.

---

## **Step 4: Copy CA certificate and key via SCP**

```python
with SCPClient(client.get_transport()) as scp:
    scp.get(remote_path=ca_path, local_path=LOCAL_PATH, recursive=True)
    ca_cert_path = os.path.join(LOCAL_PATH, ca_cert_name)
    ca_key_path = os.path.join(LOCAL_PATH, ca_key_name)
```

* Opens an SCP session over SSH.
* Downloads **all files in `ca_path`** from the CA server to the local folder.
* Constructs **local paths** for the certificate and private key.

---

## **Step 5: Handle errors**

```python
except SCPException as e:
    print(f'SCP exception error: {e}')
```

* Catches errors like **network failure, permissions issues, or missing files**.
* Prints an error message instead of crashing.

---

## Summary

1. Read CA server connection details from `targets_dict`.
2. Connect to the CA server via SSH.
3. Determine where to save the downloaded files locally.
4. Use SCP to download the CA certificate and key from the server.
5. Return the paths of the local CA certificate and key.
6. If SCP fails, print an error message.

---

💡 **Notes / Suggestions**

* You could add **checks** to verify that the files actually exist after download.
* Using your **retry-enabled SSH client** here would make it more robust.
* Optionally, log successful downloads for auditing.

---

# `renew_certificates` Explained

This function **ensures that the CA and client certificates are valid** on a device.
It **renews or generates certificates** if they are missing or about to expire, and uploads them to the device if needed.

---

## **Function Signature**

```python
def renew_certificates(dev, log, targets_dict):
```

**Parameters:**

* `dev` → device object (from `jnpr.junos.Device`)
* `log` → logging object for info messages
* `targets_dict` → dictionary with device info, CA server settings, and credentials

**Returns:**

* Nothing; performs actions (generates/renews/upload certificates) on the device.

---

## **Step 1: Get current certificate paths**

```python
if not onbox:
    ca_cert_path, ca_key_path, client_cert_path, client_key_path = get_certificates(dev, log, targets_dict=targets_dict)
```

* If **running off-device**, use `get_certificates` to download certificate paths from the device.
* If running on-device (`onbox`), paths are assumed to be local.

---

## **Step 2: Check if renewal is needed**

```python
if not os.path.isfile(client_cert_path) and not os.path.isfile(ca_cert_path):
    renew = True
elif not is_certificate_valid(client_cert_path):
    renew = True
else:
    renew = False
```

* **Renew if**:

  1. Certificates don’t exist yet, or
  2. The client certificate is about to expire (`is_certificate_valid` returns `False`)
* Otherwise, no renewal is needed.

---

## **Step 3: Renew or generate certificates**

```python
if renew:
    if targets_dict["CA_server"]["CA_cert"]["fetch"]:
        ca_cert_path, ca_key_path = fetch_ca_certificate(targets_dict)
    elif targets_dict["CA_server"]["CA_cert"]["generate"]:
        ca_cert_path, ca_key_path = generate_ca_certificate(ca_cert_path, ca_key_path, 'Juniper CA')
    else:
        log.info('The CA certificate was manually generated and uploaded')
```

* If renewal is needed:

  * **Fetch CA certificate** from a remote CA server if `fetch` is True.
  * **Generate a new CA certificate** locally if `generate` is True.
  * Otherwise, assume the CA certificate was **manually uploaded**.

---

## **Step 4: Generate a client certificate**

```python
generate_client_certificate(client_cert_path, client_key_path, ca_cert_path, ca_key_path, 'client')
```

* Creates a **client certificate signed by the CA**.
* Saves it to `client_cert_path` and `client_key_path`.

---

## **Step 5: Upload certificates to device (if offbox)**

```python
if not onbox:
    upload_certificates(dev, client_cert_path, client_key_path, log, targets_dict=targets_dict)
    if targets_dict["CA_server"]["CA_cert"]["fetch"] or targets_dict["CA_server"]["CA_cert"]["generate"]:
        upload_certificates(dev, ca_cert_path, ca_key_path, log, targets_dict=targets_dict)
```

* Uploads **client certificate and key** to the device using SCP.
* Uploads **CA certificate and key** only if it was fetched or generated automatically.
* Assumes manually uploaded CA certificates do not need re-uploading.

---

## Summary

1. Check if certificates exist on the device.
2. Check if the client certificate is about to expire.
3. If certificates are missing or expiring:

   * Fetch or generate the CA certificate if needed.
   * Generate a new client certificate signed by the CA.
4. Upload the client certificate and key to the device.
5. Upload the CA certificate and key only if they were fetched/generated automatically.

---

This function essentially **automates certificate lifecycle management**: it detects missing/expiring certificates, renews them, and ensures the devices always have valid credentials.

---


# `should_check_certs` Explained

This function determines **whether it’s time to check certificates** based on a schedule: every 5 days, and only during a certain time window.

---

## **Function Signature**

```python
def should_check_certs():
```

**Parameters:** None
**Returns:** `True` if certificates should be checked now, `False` otherwise.

---

## **Step 1: Get the current date and time**

```python
now = datetime.datetime.now()
current_time = now.strftime("%H:%M")
```

* `now` → the current date and time
* `current_time` → formatted as `"HH:MM"` (24-hour clock)

---

## **Step 2: Check if current time is in the allowed window**

```python
if "00:00" <= current_time <= "01:00":
```

* Only proceed if the current time is **between midnight (00:00) and 1 AM (01:00)**.
* This ensures certificate checks happen during off-peak hours.

---

## **Step 3: Calculate the day of the month**

```python
days_passed = (now - now.replace(day=1)).days + 1
```

* Calculates the **number of days since the start of the month**.
* Adds 1 because `replace(day=1)` makes `days_passed = 0` on the first day.

---

## **Step 4: Check if it’s a scheduled day**

```python
if days_passed % 5 == 1:
    return True
```

* Checks if the **current day is “day 1 + multiples of 5”** (1, 6, 11, 16, 21, 26, 31).
* Only these days are eligible for a certificate check.

---

## **Step 5: Return False otherwise**

```python
return False
```

* If the time is outside 00:00–01:00 or it’s not the scheduled day, the function returns `False`.

---

## Summary

1. Get the current date and time.
2. Only allow checks between **midnight and 1 AM**.
3. Calculate how many days have passed since the start of the month.
4. Only proceed if the current day is **day 1, 6, 11, 16, 21, 26, or 31**.
5. Return `True` if both conditions are met, otherwise `False`.

---

💡 **Note:**

* This schedule ensures that certificate checks happen **periodically every 5 days** and **during a safe time window** to avoid interfering with production operations.
* The window is **narrow** (1 hour), so if this script is not running at that exact time, the check will be skipped.

---

# `initialize_logging` Explained

This function sets up **Python logging** for your application, including **custom log levels, output to console and files**, and **verbosity control**.

---

## **Function Signature**

```python
def initialize_logging(args):
```

**Parameters:**

* `args` → arguments object (e.g., from `argparse`)

  * `args.verbose` → controls logging verbosity
  * `args.trace` → if `True`, creates a detailed debug trace file

**Returns:**

* `log` → the initialized `logging.Logger` object

---

## **Step 1: Define a custom log level**

```python
LOG_NOTICE = 25
logging.addLevelName(LOG_NOTICE, "NOTICE")
```

* Adds a custom log level called `NOTICE` between `WARNING (30)` and `INFO (20)`.
* Allows logging messages with medium importance.

---

## **Step 2: Add a `notice` method to Logger**

```python
def log_notice(self, message, *args, **kwargs):
    if self.isEnabledFor(LOG_NOTICE):
        self._log(LOG_NOTICE, message, args, **kwargs)
logging.Logger.notice = log_notice
```

* Adds a new `Logger.notice()` method to log at the `NOTICE` level.

---

## **Step 3: Define log levels list and capture warnings**

```python
LOG_LEVELS = [logging.ERROR, logging.WARNING, LOG_NOTICE, logging.INFO, logging.DEBUG]
logging.captureWarnings(True)
```

* List of possible log levels in increasing verbosity order.
* Converts Python **warnings** to logging messages automatically.

---

## **Step 4: Determine logging level based on verbosity**

```python
verbosity = min(args.verbose, len(LOG_LEVELS) - 1)
log_level = LOG_LEVELS[verbosity]
```

* Uses `args.verbose` to choose the logging level from `LOG_LEVELS`.
* Ensures it stays within valid range.

---

## **Step 5: Create logger and formatter**

```python
log = logging.getLogger()
formatter = logging.Formatter('%(asctime)s %(threadName)-10s %(name)s %(levelname)-8s %(message)s')
stderr = logging.StreamHandler()
stderr.setFormatter(formatter)
log.addHandler(stderr)
```

* Creates the root logger.
* Configures a formatter with timestamp, thread name, logger name, log level, and message.
* Adds a **console handler** (`stderr`) so logs appear on the terminal.

---

## **Step 6: Configure file logging**

```python
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
```

* **Trace mode**:

  * Writes all debug logs to `trace.log`.
  * Console shows logs according to verbosity.
* **On-device (`onbox`) mode**:

  * Writes logs to a rotating file (`LOG_FILENAME`).
  * Each file max 10 MB, keeps 5 backups.
* **Off-device mode**:

  * Logs only go to console, at the selected verbosity.

---

## **Step 7: Final info message**

```python
log.info('Logging modules initialized successfully')
print('Logging modules initialized successfully')
```

* Logs and prints a confirmation message to indicate logging is ready.

---

## Summary

1. Adds a **custom `NOTICE` log level**.
2. Captures Python warnings in the logger.
3. Chooses **log level** based on verbosity argument.
4. Sets up **console logging** and optionally **file logging**:

   * Trace file if `args.trace`
   * Rotating log file if `onbox`
5. Prints/logs a message indicating logging is initialized.
6. Returns the configured `Logger` object for use elsewhere.

---

💡 **Notes / Suggestions**

* Using `RotatingFileHandler` prevents logs from growing indefinitely.
* The custom `NOTICE` level is useful for mid-importance messages between `INFO` and `WARNING`.
* Could extend to **per-module logging** for finer control.

---


# `get_previous_key_ids` Explained

This function **reads previously stored key IDs** from a JSON file. It is useful for tracking which cryptographic keys have already been used or generated.

---

## **Function Signature**

```python
def get_previous_key_ids(log, name):
```

**Parameters:**

* `log` → logging object to write informational messages
* `name` → string used to format the JSON filename

**Returns:**

* A dictionary containing previous key IDs, or an empty dictionary `{}` if none exist.

---

## **Step 1: Try to read the JSON file**

```python
with open(KEYID_JSON_FILENAME.format(name), 'r') as openfile:
    return json.load(openfile)
```

* `KEYID_JSON_FILENAME` is a **template path**, e.g., `'/var/home/admin/{}last_key.json'`.
* `format(name)` replaces `{}` with the provided `name`.
* Reads the JSON file and returns its contents as a Python dictionary.

---

## **Step 2: Handle file not found**

```python
except FileNotFoundError:
    log.info(f"File to read previous keyId(s) not found")
    return {}
```

* If the JSON file **doesn’t exist**, likely because this is the **first time the script runs**:

  * Log an informational message.
  * Return an **empty dictionary**.

---

## **Step 3: Handle empty or invalid file**

```python
except ValueError:
    log.info(f"No previous keyId(s) found in file")
    return {}
```

* If the file exists but is **empty or contains invalid JSON**, `json.load` raises a `ValueError`.
* Logs a message and returns an empty dictionary.

---

## Summary

1. Construct the JSON file path using `name`.
2. Try to read the file and load previous key IDs into a dictionary.
3. If the file **doesn’t exist**, log that it’s missing and return `{}`.
4. If the file exists but is **empty or invalid**, log that no key IDs are found and return `{}`.

---

💡 **Notes / Suggestions**

* This function ensures the script **doesn’t crash on first run**.
* Returning `{}` is safe for subsequent operations that rely on previous keys.
* Could extend with a **file lock** if multiple processes might write to the same JSON file.

---

# `save_key_ids` Explained

This function **saves cryptographic key IDs** to a JSON file for later retrieval. It complements the `get_previous_key_ids` function.

---

## **Function Signature**

```python
def save_key_ids(key_dict, local_name):
```

**Parameters:**

* `key_dict` → dictionary containing key IDs to save
* `local_name` → string used to format the JSON filename

**Returns:** None (performs a file write)

---

## **Step 1: Construct the file path**

```python
KEYID_JSON_FILENAME.format(local_name)
```

* `KEYID_JSON_FILENAME` is a template path, e.g., `'/var/home/admin/{}last_key.json'`.
* Replaces `{}` with `local_name` to create a unique filename for each device or context.

---

## **Step 2: Write key IDs to the file**

```python
with open(KEYID_JSON_FILENAME.format(local_name), 'w+') as outfile:
    outfile.write(json.dumps(key_dict))
```

* Opens the file in **write mode (`w+`)**, creating it if it doesn’t exist.
* Serializes the dictionary (`key_dict`) to JSON format using `json.dumps`.
* Writes the JSON data to the file.
* Overwrites any existing file content.

---

## **Step 3: Print confirmation**

```python
print(f'Saved the key IDs to the JSON file: {KEYID_JSON_FILENAME.format(local_name)}')
```

* Prints a message confirming that the key IDs were successfully saved.
* Useful for debugging or tracking the workflow.

---

## Summary

1. Construct the JSON filename using `local_name`.
2. Open the file (create it if it doesn’t exist).
3. Convert the key dictionary into JSON and write it to the file.
4. Print a confirmation message.

---

💡 **Notes / Suggestions**

* You could **add logging** instead of `print` for consistency with `get_previous_key_ids`.
* Consider **atomic writes** (writing to a temp file then renaming) to prevent data corruption if the script is interrupted.
* Using JSON ensures the file can easily be read back with `get_previous_key_ids`.

---

# `fetch_kme_key` Explained

This function **fetches cryptographic keys from a Key Management Entity (KME)** using HTTPS requests with client and CA certificates for authentication.

---

## **Function Signature**

```python
def fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id, additional_slave_SAE_IDs=None):
```

**Parameters:**

* `session` → a `requests.Session()` object used for making HTTP requests
* `local_name` → the local device name (used to find its certificates)
* `log` → logging object
* `remote_mnmgt_add` → the master device or remote address for key retrieval
* `kme_host` → base URL of the KME server
* `key_id` → optional; if provided, fetch a specific key, otherwise fetch new keys
* `additional_slave_SAE_IDs` → optional list of additional devices for which to fetch keys

**Returns:**

* JSON object containing keys if the request succeeds, or `None` if it fails

---

## **Step 1: Determine certificate paths**

```python
if onbox:
    client_crt = CERTS_DIR + local_name + '.crt'
    client_key = CERTS_DIR + local_name + '.key'
    CLIENT_CERT = (client_crt, client_key)
    CA_CERT = CERTS_DIR + 'client-root-ca.crt'
else:
    client_crt = OFFBOX_CERTS_DIR + '/' + local_name + '.crt'
    client_key = OFFBOX_CERTS_DIR + '/' + local_name + '.key'
    CLIENT_CERT = (client_crt, client_key)
    CA_CERT = OFFBOX_CERTS_DIR + '/' + 'root.crt'
```

* Determines where to find **client certificate, client key, and CA certificate** depending on whether the script is running on-device (`onbox`) or off-device.
* These certificates are used for **mutual TLS authentication**.

---

## **Step 2: Make an HTTPS request to the KME**

```python
if key_id:
    response = session.get(f"{kme_host}/api/v1/keys/{remote_mnmgt_add}/dec_keys?key_ID={key_id}", verify=CA_CERT, cert=CLIENT_CERT, headers={"Content-Type": "application/json"})
else:
    response = session.get(f"{kme_host}/api/v1/keys/{remote_mnmgt_add}/enc_keys", verify=CA_CERT, cert=CLIENT_CERT, headers={"Content-Type": "application/json"})
```

* If `key_id` is provided → fetch a **specific decryption key**.
* If not → fetch **new encryption keys** from the KME.
* Uses `CLIENT_CERT` for client authentication and `CA_CERT` to verify the KME server.
* Sets `Content-Type` header to `"application/json"`.

---

## **Step 3: Check the response**

```python
if response.status_code == 200:
    response_json = response.json()
    return response_json
else:
    print(f'Request failed with status code {response.status_code}')
    print(response.text)
```

* If the server responds with `HTTP 200 OK` → parse the JSON and return it.
* Otherwise → print the status code and response content for debugging.

---

## **Step 4: Handle exceptions**

```python
except requests.RequestException as e:
    log.error(f"KME request failed: {e.response.text}")
    print(f"KME request failed: {e.response.text}")
    return None
```

* If the HTTP request fails due to **network issues, SSL errors, or server errors**, log the error and return `None`.

---

## Summary

1. Determine the **client and CA certificate paths** depending on whether the script is running on the device or off-device.
2. Make a **mutual TLS HTTPS request** to the KME:

   * If `key_id` is given → fetch an existing key.
   * Otherwise → fetch new keys for the master device.
3. Parse and return the JSON response if successful.
4. Log and print errors if the request fails.

---

💡 **Notes / Suggestions**

* `CLIENT_CERT` and `CA_CERT` ensure **mutual authentication** between the client and the KME.
* The function currently prints a lot of debug info; consider using `log.debug()` instead of `print` for cleaner production logs.
* `additional_slave_SAE_IDs` is defined but not used; you could extend the request payload to include it if needed.

---



# `check_and_apply_initial_config` Explained

This function **checks whether the initial MACsec configuration is applied** on a network device and applies it if it isn’t. It is intended to run **only once** on the first execution of the script.

---

## **Function Signature**

```python
def check_and_apply_initial_config(dev, targets_dict, log):
```

**Parameters:**

* `dev` → a `jnpr.junos.Device` object representing the network device
* `targets_dict` → dictionary containing device details, interfaces, KME info, and CA server info
* `log` → logging object for informational and error messages

**Returns:** None (applies configuration directly to the device)

---

## **Step 1: Extract device information**

```python
device_name = dev.facts['hostname'].split('-re')[0]
device_ip = targets_dict[device_name]["ip"]
c_a = targets_dict["CA_server"]["c_a"]
interfaces = targets_dict[device_name]["interfaces"]
kme_name = targets_dict[device_name]["kme"]["kme_name"]
kme_ip = targets_dict[device_name]["kme"]["kme_ip"]
start_time = targets_dict["system"]["event_options"]["start_time"]
```

* Retrieves device name, IP, **connectivity-association (CA) name**, list of interfaces, KME host info, and event start time from `targets_dict`.

---

## **Step 2: Check if MACsec configuration already exists**

```python
config_check = dev.rpc.get_config(filter_xml=E.configuration(E.security(E.macsec(E('connectivity-association', E.name(c_a))))))
if config_check.find('.//name') is not None:
    log.info("Initial macsec configuration already applied on the device: {}.".format(device_name))
    return
```

* Uses Juniper RPC to check the device's configuration.
* If the **connectivity-association already exists**, it logs a message and exits the function.

---

## **Step 3: Build the MACsec commands**

```python
initial_macsec_commands = [
    f"set security macsec connectivity-association {c_a} cipher-suite gcm-aes-xpn-256",
    f"set security macsec connectivity-association {c_a} security-mode static-cak",
    f"set security macsec connectivity-association {c_a} pre-shared-key ckn abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678",
    f"set security macsec connectivity-association {c_a} pre-shared-key cak abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678"
]
```

* Defines the **basic MACsec CA configuration**, including cipher, security mode, and pre-shared keys.

---

### **Step 3a: Add interface-specific commands**

```python
for interface in interfaces:
    initial_macsec_commands.extend([
        f"set security macsec interfaces {interface} apply-macro qkd kme-ca false",
        f"set security macsec interfaces {interface} apply-macro qkd kme-host {kme_name}",
        f"set security macsec interfaces {interface} apply-macro qkd kme-port 443",
        f"set security macsec interfaces {interface} connectivity-association {c_a}",
        f"set security macsec interfaces {interface} apply-macro qkd kme-keyid-check true",
        f"set system static-host-mapping {device_name} inet {device_ip}"
    ])
```

* Configures each network interface to use MACsec, apply QKD macros, associate with KME, and map device IP.

---

### **Step 3b: Add event options for on-device (`onbox`) mode**

```python
if onbox:
    initial_macsec_commands.extend([
        f"set event-options generate-event every10mins time-interval 600 start-time {start_time}",
        f"set event-options policy qkd events every10mins",
        f"set event-options policy qkd then event-script ETSIA_v3.1.0_Turkcell_Phase2_v1.py",
        f"set event-options event-script file ETSIA_v3.1.0_Turkcell_Phase2_v1.py python-script-user admin",
        f"set event-options traceoptions file script.log",
        f"set event-options traceoptions file size 10m",
    ])
```

* Sets up **automated event generation** every 10 minutes to run a QKD Python script.
* Configures **logging and trace options** for the event script.

---

## **Step 4: Apply the configuration**

```python
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
    time.sleep(60)
```

* Locks the device configuration.
* Loads all MACsec commands using **set-style configuration**.
* Commits the configuration.
* Unlocks the configuration and waits **60 seconds** to allow changes to propagate.
* Logs success or error messages.

---

## Summary

1. Extracts device details and KME info.
2. Checks if MACsec configuration already exists; skips if present.
3. Builds a list of **set commands** for MACsec and interface configuration.
4. Adds event scheduling for running the QKD script if on the device.
5. Locks the device configuration, loads all commands, commits, unlocks.
6. Waits for 60 seconds to ensure configuration is applied.
7. Logs success or failure.

---

💡 **Notes / Suggestions**

* Ensures the initial MACsec configuration is **idempotent** (applied only once).
* Uses **QKD macros** and static keys for secure communication.
* Event scripts automate recurring QKD key operations.
* Consider **error handling for individual commands** in large deployments.

---


# `get_key_id_from_master` Explained

This function **retrieves the Key ID file from the master device** in a QKD (Quantum Key Distribution) setup. The Key ID file keeps track of which encryption keys have been generated or used by the master.

---

## **Function Signature**

```python
def get_key_id_from_master(dev, log, targets_dict):
```

**Parameters:**

* `dev` → device object (Juniper Device) — not directly used here but passed for consistency
* `log` → logging object for informational messages (currently `print` is used)
* `targets_dict` → dictionary containing device information, roles, secrets, and paths

**Returns:**

* Path to the Key ID file on the local system, or `None` if it cannot be retrieved.

---

## **Step 1: Determine the master device**

```python
master_name = targets_dict["qkd_roles"]['master']
master_key_id_file = KEYID_JSON_FILENAME.format(master_name)
```

* Looks up the **master device name** from `targets_dict`.
* Constructs the Key ID filename for the master device using `KEYID_JSON_FILENAME` template.

---

## **Step 2: Handle off-device scenario (`not onbox`)**

```python
if not onbox:
    if os.path.exists(master_key_id_file):
        return master_key_id_file
    else:
        print('the master key id file does not exist')
        return None
```

* If running off-device:

  * Checks if the Key ID file already exists locally.
  * If yes → return its path.
  * If no → log a message and return `None`.

---

## **Step 3: Handle on-device scenario (`onbox`)**

```python
client = createSSHClient(master_name, username=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22)
with SCPClient(client.get_transport()) as scp:
    scp.get(remote_path=master_key_id_file, local_path=master_key_id_file, preserve_times=True)
```

* Connects to the **master device over SSH** using `createSSHClient`.
* Uses `SCPClient` to **copy the master Key ID file** from the master device to the local system.
* `preserve_times=True` keeps the original file timestamps.

---

## **Step 4: Error handling**

```python
except SCPException as e:
    print(f'SCP get exception error: {e}')
    return None
```

* If the SCP transfer fails (network issues, missing file, permission errors), logs the exception and returns `None`.

---

## **Step 5: Return local path**

```python
return master_key_id_file
```

* Returns the **path to the retrieved Key ID file** on the local system.
* This file can then be read to get the master’s current key IDs.

---

## Summary

1. Determine the **master device** and its Key ID filename.
2. If running off-device (`not onbox`):

   * Check if the file exists locally → return path or `None`.
3. If running on-device (`onbox`):

   * Connect to the master over SSH.
   * Copy the Key ID file via SCP.
   * Return the local path.
4. Logs any errors encountered during SCP transfer.

---

💡 **Notes / Suggestions**

* Currently uses `print()` for messages; consider using `log.info()` or `log.error()` for consistency.
* Could add retries or timeout handling for SCP in case of transient network issues.
* Ensures that **all devices in the QKD network can access the master’s Key ID** to stay synchronized.

---

# `process` Function Explained

The `process` function is the **core logic for applying MACSEC keys to a Junos device**. It handles fetching keys from the KME (Key Management Entity), updating the device configuration, and storing the key IDs for future runs.

---

## **Function Signature**

```python
def process(dev, targets_dict, log):
```

**Parameters:**

* `dev`: Juniper device object from `jnpr.junos.Device`
* `targets_dict`: Dictionary containing all device info, roles, credentials, interfaces, and KME info
* `log`: Logger object for logging events

---

## **Step 1: Get MACSEC configuration**

```python
conf_filter = E.configuration(E.security(E.macsec()))
config_xml = dev.rpc.get_config(filter_xml=conf_filter)
macsec_xml = config_xml.find('security/macsec')
```

* Fetches the current MACSEC configuration from the device using Junos XML RPC.
* Prepares the configuration object for further modifications.

---

## **Step 2: Prepare key fetching session**

```python
session = requests.Session()
new_key_dict = {}
qkd_macsec_xml = E.macsec()
commit = False
```

* Creates a `requests.Session` for communicating with the KME API.
* `new_key_dict` will store the Key IDs fetched for this device.
* `qkd_macsec_xml` is a placeholder for new MACSEC configuration.
* `commit` flag indicates whether to apply changes.

---

## **Step 3: Determine CA name and local hostname**

```python
ca_name = macsec_xml.findtext('connectivity-association/name')
local_name = dev.facts['hostname'].split('-re')[0]
kme_host = targets_dict[local_name]["kme"]["kme_name"]
```

* Extracts the connectivity association (CA) name from the MACSEC config.
* Determines the local hostname (removes `-re` suffix).
* Finds the KME host associated with this device.

---

## **Step 4: Fetch Key from KME**

* **Master Device Logic:**

```python
if targets_dict["qkd_roles"]['master'] == local_name:
    remote_mnmgt_add = targets_dict["qkd_roles"]['slave']
    r = fetch_kme_key(session, local_name, log, remote_mnmgt_add, kme_host, key_id=None)
    key = r['keys'][0]
    new_key_dict[local_name] = key['key_ID'].strip()
```

* If this device is the **Master**, fetch a new encryption key from the KME for the Slave device.

* Updates `new_key_dict` with the received Key ID.

* **Slave Device Logic:**

```python
else:
    remote_mnmgt_add = targets_dict["qkd_roles"]['master']
    # Retry loop for master key
    master_key_id_file = get_key_id_from_master(dev, log, targets_dict)
    master_key_dict = get_previous_key_ids(log, remote_mnmgt_add)
    new_key_dict[local_name] = master_key_dict[remote_mnmgt_add]
```

* If the device is a **Slave**, fetch the **Key ID from the Master device**.
* Retries every 5 seconds until a new Key ID is available.
* Updates `new_key_dict` with the Master’s Key ID.

---

## **Step 5: Update MACSEC CAK and CKM**

```python
qkd_ca_xml.find('pre-shared-key/ckn').text = CKN_PREFIX + uuid.UUID(key['key_ID']).hex
qkd_ca_xml.find('pre-shared-key/cak').text = str(base64.b64decode(key['key']).hex())[:64]
qkd_macsec_xml.append(qkd_ca_xml)
commit = True
```

* Updates the **connectivity association key name (CKN)** and **connectivity association key (CAK)** in the configuration XML.
* Appends the new MACSEC config to `qkd_macsec_xml`.
* Sets `commit = True` so it will be applied.

---

## **Step 6: Root Authentication**

```python
root_auth_xml = E.system(
    E("root-authentication",
        E("encrypted-password", targets_dict[local_name]["root_enc_pass"])
    )
)
```

* Prepares root authentication XML (encrypted password) to be included in the configuration if needed.

---

## **Step 7: Commit Configuration**

```python
qkd_config_xml = E.configuration(E.security(qkd_macsec_xml))
with Config(dev) as cu:
    cu.lock()
    cu.load(qkd_config_xml, format='xml', merge=True)
    cu.commit()
    cu.unlock()
```

* Converts XML to a Junos configuration object.

* Locks the configuration database.

* Loads the new configuration and **merges it with existing settings**.

* Commits the configuration changes to the device.

* Unlocks the configuration database.

* Logs success or failure of the commit.

---

## **Step 8: Save Key IDs**

```python
save_key_ids(new_key_dict, local_name)
```

* Saves the updated Key IDs to a local JSON file for future reference.

---

## **Step 9: Optional Verification**

```python
mka_session_info_xml = dev.rpc.get_mka_session_information({'format':'text'}, summary=True)
```

* Fetches **MKA (MACsec Key Agreement) session information** for debugging or verification.

---

## Summary

1. Fetch current MACSEC configuration from the device.
2. Determine if the device is **Master or Slave** in the QKD network.
3. **Master:** request a new key from the KME for Slave.
4. **Slave:** fetch the key ID from the Master device. Retry until a new key is available.
5. Update MACSEC keys (`CKN` and `CAK`) in the device configuration.
6. Commit the configuration changes to the device.
7. Save the new key IDs locally for future runs.
8. Optionally, fetch MACSEC session information for verification.

---

💡 **Notes / Suggestions**

* Currently prints a lot of debug information — you could replace most `print` statements with `log.debug` for cleaner logs.
* The retry logic for Slave devices ensures **synchronization between Master and Slave**, but it might hang if the Master never updates the key file.
* `commit = True` is controlled dynamically based on whether new keys are available.

---

# `get_args()` Function Explained

The `get_args` function is responsible for **defining and parsing command-line arguments** for the script. It uses Python’s `argparse` module.

---

## **Function Signature**

```python
def get_args():
```

* No input parameters.
* Returns a **Namespace object** containing the parsed arguments.

---

## **Step 1: Create Argument Parser**

```python
parser = argparse.ArgumentParser()
```

* Creates an `ArgumentParser` object to handle command-line arguments.
* This object will automatically generate help messages.

---

## **Step 2: Define Arguments**

### **1. Threads (`-t` / `--threads`)**

```python
parser.add_argument("-t", "--threads", type=int, help="Number of threads to use")
```

* Optional argument to specify how many threads the script should run.
* Example usage:

```bash
python script.py -t 5
```

---

### **2. Verbose (`-v` / `--verbose`)**

```python
parser.add_argument('-v','--verbose', default=0, action='count', help="increase verbosity level")
```

* Optional flag to increase the verbosity of logs.
* Can be specified multiple times (`-vv` for more verbose).
* Default is `0` (no extra verbosity).

Example:

```bash
python script.py -vv
```

* `action='count'` automatically counts how many times `-v` is used.

---

### **3. Trace (`-tr` / `--trace`)**

```python
parser.add_argument('-tr','--trace', action='store_true', help="dump debug level logs to trace.log file")
```

* Optional flag to enable **trace logging**.
* Stores a boolean `True` if present on the command line, otherwise `False`.

Example:

```bash
python script.py --trace
```

---

## **Step 3: Parse Arguments**

```python
return parser.parse_args()
```

* Reads the command-line arguments provided when the script is run.
* Returns them as a `Namespace` object, which can be accessed like:

```python
args = get_args()
print(args.threads)  # Number of threads
print(args.verbose)  # Verbosity level
print(args.trace)    # True/False
```

---

## Summary

1. Prepares the script to accept command-line options.
2. Supports **threads**, **verbosity**, and **trace logging** flags.
3. Returns a convenient object (`args`) containing all user-specified options.

---


# `main()` Function Explanation

The `main()` function orchestrates the full workflow for **configuring devices with QKD MACSEC and fetching keys from KME**.

---

### 1. **Parse Command-Line Arguments**

```python
args = get_args()
```

* Reads arguments like:

  * Number of threads to use (`--threads`)
  * Verbosity level (`-v`)
  * Trace mode (`--trace`)

---

### 2. **Initialize Logging**

```python
log = initialize_logging(args)
```

* Sets up logging based on verbosity or trace mode.
* Logs to console, file, or rotating log file depending on environment (`onbox` or offbox).

---

### 3. **Define Target Devices and System Info**

```python
targets_dict = {...}
```

* Contains:

  * **System-wide settings** (e.g., max threads, start time for events)
  * **Secrets** (SSH username/password)
  * **CA server info** (certificate fetch/generate options, paths)
  * **QKD roles** (master, slave, additional devices)
  * **Device-specific data** (IP, root password, interfaces, KME details)

Essentially, this is your **entire network and KME configuration** in a single dictionary.

---

### 4. **Offbox vs Onbox Workflow**

```python
if not onbox:
    # Offbox processing
elif onbox:
    # Onbox processing
```

* **Offbox**: Script runs from a separate host (not on the devices themselves).

  * It calculates **threads** and distributes devices across them.
  * Launches `req_thread` for each set of devices.
  * Waits for all threads to finish (`t.join()`).

* **Onbox**: Script runs directly on a device.

  * Connects to the device using `Device()`.
  * Runs the configuration steps **synchronously**:

    1. `check_and_apply_initial_config()` — applies initial MACSEC configuration if not already present.
    2. `process()` — fetches keys from KME and updates MACSEC configuration.
  * Uses profiling (`prof.start` / `prof.stop`) to measure execution time for each step.

---

### 5. **Threading Logic (Offbox)**

* Creates a list of devices to process in threads:

```python
dlist = [master, slave, additional slaves]
```

* Splits the list according to the number of threads.
* Calls `req_thread()` for each batch, which is **decorated with `@background`** to run in a separate thread.
* Waits for all threads to finish before continuing.

---

### 6. **Onbox Logic**

* Runs everything sequentially on the device.
* Uses profiling to measure time taken by:

  * Certificate renewal (if needed)
  * Initial MACSEC configuration
  * Fetching keys and updating MACSEC configuration

---

### 7. **Error Handling**

* Logs and prints exceptions if SSH or device operations fail.

---

### Summary

* **Purpose:** Automate QKD MACSEC configuration on multiple devices.
* **Supports two modes:** Onbox (direct device) or Offbox (external host with threading).
* **Workflow:**

  1. Parse CLI args
  2. Initialize logging
  3. Load device and KME configuration
  4. Apply initial MACSEC config
  5. Fetch keys from KME
  6. Update device configuration
  7. Save new Key IDs for tracking

---