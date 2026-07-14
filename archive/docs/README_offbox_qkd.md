# QKD Offbox Builder

## Overview

This project provides an offbox automation framework to:

- Build inventory
- Generate topology
- Generate PKI (certificates)
- Deploy configurations to Junos devices
- Copy certificates to devices

It prepares the system for on-box QKD logic.

---

## Directory Structure

```
offbox/
├── certs/
├── config/
│   ├── inventory/
│   ├── runtime/
│   └── templates/
├── provisioning.py
├── qkd_builder.py
├── rendering.py
├── pki.py
├── inventory_builder.py
└── settings.py
```

---

## Workflow

### 1. Create

```
python3 qkd_builder.py create \
  --mode dynamic \
  --topology pair \
  --devices vqfx1 vqfx2 \
  --ips 10.54.13.14 10.54.12.193 \
  --interfaces xe-0/0/0 xe-0/0/0 \
  --kmes 100.100.100.10 100.100.100.11
```

Generates:
- devices.yaml
- topology.yaml
- certificates under certs/

---

### 2. Deploy

```
python3 qkd_builder.py deploy
```

Per device:
- Connect via NETCONF
- Copy certificates
- Apply configuration

---

## Certificates

Local structure:

```
certs/
 ├── rootCA.crt
 ├── vqfx1/
 │   ├── client.crt
 │   ├── client.key
 │   └── client.pem
```

On device:

```
/var/db/scripts/certs/
  client.crt
  client.key
  rootCA.crt
```

---

## Verification

On device:

```
start shell
ls -l /var/db/scripts/certs
```

---

## Configuration

config/inventory/inventory_base.yaml:

```
secrets:
  default_user: admin
  default_password: juniper1
```

---

## Debug

```
python3 qkd_builder.py deploy --debug
```

---

## Current Capabilities

- Inventory generation
- Topology generation
- PKI generation
- Config deployment
- Cert distribution

---

## Next Step

Implement on-box logic:

- call KME APIs
- retrieve keys
- configure MACsec

