# Installation Guide (Enterprise Setup)

## 1. Prerequisites

- Ubuntu Linux host
- Docker + Docker Compose
- Python 3.10+
- Network connectivity to devices

---

## 2. PKI Generation

Run offbox:

python3 pki.py

Output:
- Root CA
- SAE certificates

---

## 3. KME Deployment

python3 kme_orchestrator.py --kme-ip <IP> --restart

This performs:
- Certificate copy
- KME cert generation
- Container restart
- Database initialization

---

## 4. Device Deployment

python3 qkd_orchestrator.py create
python3 qkd_orchestrator.py deploy

---

## 5. Validation

From device:

curl enc_keys
curl dec_keys

