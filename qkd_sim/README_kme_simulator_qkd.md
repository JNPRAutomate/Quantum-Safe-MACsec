# QKD KME Dual-Node Lab (ETSI 014 Reference Implementation)

This guide rebuilds a full working lab with:

- 2x KME containers
- 2x PostgreSQL databases
- macvlan network
- VQFX mTLS integration
- Working enc_keys / dec_keys flow

---

# 0. PREREQUISITES

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-plugin make openssl
sudo systemctl enable docker
sudo systemctl start docker


sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | \
sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
docker --version
docker compose version
sudo usermod -aG docker $USER
newgrp docker

```

---

# 1. CLONE REPOSITORY

```bash
cd ~

git clone https://github.com/cybermerqury/etsi-gs-qkd-014-referenceimplementation.git
cd etsi-gs-qkd-014-referenceimplementation
```

---

# 2. GENERATE CERTIFICATES

```bash
sudo apt update
sudo apt upgrade
sudo apt install -y make build-essential openssl uuid-runtime
sudo apt install -y build-essential coreutils sed gawk grep expect pkg-config libssl-dev uuid-runtime moreutils
make --version
### slight modification to the Makefile in the folder certs:
root@Ubuntu-20:~/etsi-gs-qkd-014-referenceimplementation/certs# cat Makefile
# SPDX-FileCopyrightText: ¬© 2023 Merqury Cybersecurity Ltd <info@merqury.eu>
# SPDX-License-Identifier: AGPL-3.0-only

.PHONY: certs clean

# NOTE: Use 'make VERBOSE=1 <target>' to show the commands being run
ifndef VERBOSE
.SILENT:
endif

.PRECIOUS: %.crt

LOG = output.log

ROOT_CRT = root.crt
ROOT_KEY  = root.key

SERIAL_NUM = 1

default: certs

certs: kme_001.pem sae_001.pem sae_002.pem sae_003.pem

%.pem: %.crt
        printf "Generating pem file '%s'...\n" $@
        cat $(basename $^).key $^ > $@
        printf "Pem file '%s' generated\n" $@

%.crt: %.csr $(ROOT_CRT)
        printf "Generating certificate '%s' with serial '%d' signed using '%s'...\n" $@ $(SERIAL_NUM) $(ROOT_CRT)

        openssl x509 -req               \
                -in $(word 1,$^)            \
                -CA $(ROOT_CRT)            \
                -CAkey $(ROOT_KEY)          \
                -set_serial $(SERIAL_NUM)   \
                -days 365                   \
                -extfile $(basename $@).ext \
                -out $@                     \
                >> $(LOG) 2>&1

        printf "Certificate '%s' generated\n" $@

        $(eval SERIAL_NUM=$(shell echo $$(($(SERIAL_NUM)+1))))

%.csr: %.ext
        printf "Generating certificate signing request for '%s'...\n" $@

        openssl req                                              \
                -newkey rsa:4096 -nodes                                     \
                -nodes                                               \
                -days 365                                            \
                -subj "/C=MT/O=Acme Ltd/CN=$(basename $(notdir $@))" \
                -keyout $(basename $@).key                           \
                -out $@                                              \
                >> $(LOG) 2>&1

        printf "Certificate '%s' signing request generated\n" $@

%.ext:
        echo "subjectAltName = DNS:localhost,IP:127.0.0.1" >> $@

kme.ext:
        echo "subjectAltName = DNS:localhost,IP:127.0.0.1" >> $@

$(ROOT_CRT):
        printf "Generating the root certificate '%s'...\n" $(ROOT_CRT)

        openssl req -x509                                            \
                -newkey rsa:4096 -nodes                                         \
                -days 365                                                \
                -subj "/C=MT/CN=www.merqury.eu/O=Merqury\ Cybersecurity" \
                -keyout $(ROOT_KEY)                                      \
                -out $@                                                  \
                >> $(LOG) 2>&1

        printf "Root certificate '%s' generated\n" $(ROOT_CRT)

clean:
        rm -f *.crt \
                  *.csr \
                  *.pem \
                  *.ext \
                  *.key \
                  $(LOG)

        printf "Certificates and output log removed\n"
###

sed -i "s/2>&1 | ts '%d-%m-%Y %H:%M:%S' >> \$(LOG) 2>&1/>> \$(LOG) 2>&1/g" certs/Makefile
sed -i 's/newkey rsa:4096/newkey rsa:4096 -nodes/g' certs/Makefile
export OPENSSL_CONF=/etc/ssl/openssl.cnf
openssl version -d

# now it's generating the certs 

make -C certs clean
make -C certs certs
```


Verify:
```bash
ls certs/
```

---

# 3. BUILD KME IMAGE

```bash
# Install Rust 
curl https://sh.rustup.rs -sSf | sh
(select 1 )
source $HOME/.cargo/env

export DATABASE_URL=postgres://db_user:db_password@localhost:5432/key_store
export SQLX_OFFLINE=true
cargo build --release
ls target/release/
```

## 3.1 change Dockerfile

```bash
# build the local docker image 
# change Dockerfile (rename the existing as .orig) since it is using libssl1.1 which is legacy.
# -------------------
# BUILD STAGE
# -------------------
FROM rust:latest as builder

RUN apt update && apt install -y \
    pkg-config \
    libssl-dev \
    libpq-dev

WORKDIR /app

COPY . .

# Force SQLx offline
ENV SQLX_OFFLINE=true

RUN cargo build --release

# -------------------
# RUNTIME STAGE
# -------------------
FROM ubuntu:22.04

RUN apt update && apt install -y \
    ca-certificates \
    libssl-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/target/release/etsi_gs_qkd_014_referenceimplementation .

ENTRYPOINT ["/app/etsi_gs_qkd_014_referenceimplementation"]
```

## docker build
```bash
#remove target before rebuilding
rm -rf target

docker build --no-cache -t etsi-kme:local .
docker images
```

---

# 4. CREATE LOCAL ETH1 INTERFACE

```bash
ip addr flush dev eth1
ip addr add 100.100.100.50/24 dev eth1
ip link set eth1 up
```

# 5. CREATE MACVLAN NETWORK

```bash
# creates the qkd_net
# i have created an irb in he connecting star switch, otherwise set your gw according to the schema
docker network create -d macvlan   --subnet=100.100.100.0/24   --gateway=100.100.100.100   -o parent=eth1   qkd_net

ip link add qkd-macvlan link eth1 type macvlan mode bridge
ip addr add 100.100.100.200/24 dev qkd-macvlan
ip link set qkd-macvlan up



## IPVLAN instead of macvlan: 
docker compose -f docker-compose-kme-shared-db.yml down -v
docker network rm qkd_net
ip link delete qkd-macvlan
docker network create -d ipvlan \
  --subnet=100.123.252.0/24 \
  --gateway=100.123.252.1 \
  -o parent=ens18 \
  qkd_net
docker compose -f docker-compose-kme-shared-db.yml up -d

```

---

# 5. CREATE docker-compose-kme.yml

```yaml
services:

  postgres-kme1:
    image: postgres:15
    container_name: postgres-kme1
    environment:
      POSTGRES_USER: db_user
      POSTGRES_PASSWORD: db_password
      POSTGRES_DB: key_store
    networks:
      qkd_net:
        ipv4_address: 100.100.100.20

  postgres-kme2:
    image: postgres:15
    container_name: postgres-kme2
    environment:
      POSTGRES_USER: db_user
      POSTGRES_PASSWORD: db_password
      POSTGRES_DB: key_store
    networks:
      qkd_net:
        ipv4_address: 100.100.100.21

  kme1:
    image: etsi-kme:local
    container_name: kme1
    depends_on:
      - postgres-kme1
    volumes:
      - ./certs:/certs:ro
    environment:
      ETSI_014_REF_IMPL_DB_URL: postgres://db_user:db_password@100.100.100.20:5432/key_store
      ETSI_014_REF_IMPL_IP_ADDR: 0.0.0.0
      ETSI_014_REF_IMPL_PORT_NUM: 8443
      ETSI_014_REF_IMPL_NUM_WORKER_THREADS: 2
      ETSI_014_REF_IMPL_TLS_CERT: /certs/kme_001.crt
      ETSI_014_REF_IMPL_TLS_PRIVATE_KEY: /certs/kme_001.key
      ETSI_014_REF_IMPL_TLS_ROOT_CRT: /certs/root.crt
    networks:
      qkd_net:
        ipv4_address: 100.100.100.10

  kme2:
    image: etsi-kme:local
    container_name: kme2
    depends_on:
      - postgres-kme2
    volumes:
      - ./certs:/certs:ro
    environment:
      ETSI_014_REF_IMPL_DB_URL: postgres://db_user:db_password@100.100.100.21:5432/key_store
      ETSI_014_REF_IMPL_IP_ADDR: 0.0.0.0
      ETSI_014_REF_IMPL_PORT_NUM: 8443
      ETSI_014_REF_IMPL_NUM_WORKER_THREADS: 2
      ETSI_014_REF_IMPL_TLS_CERT: /certs/kme_001.crt
      ETSI_014_REF_IMPL_TLS_PRIVATE_KEY: /certs/kme_001.key
      ETSI_014_REF_IMPL_TLS_ROOT_CRT: /certs/root.crt
    networks:
      qkd_net:
        ipv4_address: 100.100.100.11

networks:
  qkd_net:
    external: true
```

---

# 6. START LAB

```bash
# if needed
# docker compose -f docker-compose-kme.yml down
docker compose -f docker-compose-kme.yml up -d
```

---

# 7. INIT DATABASES

```bash


cat schema.sql | docker exec -i postgres-kme1 psql -U db_user key_store
cat schema.sql | docker exec -i postgres-kme2 psql -U db_user key_store

# If you don't have the schema, then  
# enter the docker container postgres-kme1:
docker exec -it postgres-kme1 psql -U db_user key_store

psql (15.18 (Debian 15.18-1.pgdg13+1))
Type "help" for help.

key_store=# CREATE TABLE keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE
key_store=# exit

# Again, with docker container postgres-kme2:
docker exec -it postgres-kme2 psql -U db_user key_store
psql (15.18 (Debian 15.18-1.pgdg13+1))
Type "help" for help.

key_store=# CREATE TABLE keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content BYTEA NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE
key_store=# exit
```

---

# 8. TEST FROM VQFX

## COPY certs in sae1 and sae2
```bash
# from Linux host
# secure copy from '~/etsi-gs-qkd-014-referenceimplementation/certs/ 
# to vQFX1 (make sure you have the folder mkdir -p /var/home/admin/certs inside the vQFXs)
scp root.crt root@100.100.100.1:/var/home/admin/certs/
scp sae_001.key root@100.100.100.1:/var/home/admin/certs/
scp sae_001.crt root@100.100.100.1:/var/home/admin/certs/

# copy to vQFX2 (make sure you have a folder /var/home/admin/certs)
scp root.crt root@100.100.100.2:/var/home/admin/certs/
scp sae_002.key root@100.100.100.2:/var/home/admin/certs/
scp sae_002.crt root@100.100.100.2:/var/home/admin/certs/
```

## ENC

```bash
curl -sk   --cacert /var/home/admin/certs/root.crt   --cert /var/home/admin/certs/sae_001.crt   --key /var/home/admin/certs/sae_001.key   "https://100.100.100.10:8443/api/v1/keys/sae_002/enc_keys?number=1&size=256"

# Example from vQFX1:
root@vqfx-1:RE:0% curl -sk --cacert /var/home/admin/certs/root.crt --cert /var/home/admin/certs/sae_001.crt --key /var/home/admin/certs/sae_001.key "https://100.100.100.10:8443/api/v1/keys/sae_002/enc_keys?number=1&size=256"
{"keys":[{"key_ID":"0b8dc18f-1e05-4d93-97d2-ba3181535d29","key":"hFKdKtMrSAKOqxLkvUnbtM6rjECZ8q8NLDNYAW94BzM="}]}
```

## DEC

```bash
curl -sk   --cacert /var/home/admin/certs/root.crt   --cert /var/home/admin/certs/sae_002.crt   --key /var/home/admin/certs/sae_002.key   "https://100.100.100.10:8443/api/v1/keys/sae_001/dec_keys?key_ID=<ID>"

# Example with previous encrypted ID, curl executed in vQFX2:
root@vqfx-2:RE:0% curl -sk   --cacert /var/home/admin/certs/root.crt   --cert /var/home/admin/certs/sae_002.crt   --key /var/home/admin/certs/sae_002.key   "https://100.100.100.10:8443/api/v1/keys/sae_001/dec_keys?key_ID=0b8dc18f-1e05-4d93-97d2-ba3181535d29"
{"keys":[{"key_ID":"0b8dc18f-1e05-4d93-97d2-ba3181535d29","key":"hFKdKtMrSAKOqxLkvUnbtM6rjECZ8q8NLDNYAW94BzM="}]}

```

---

# ✅ RESULT

```
{"keys":[...]}
```

---

# ✅ FINAL STATE

- Dual KME working
- mTLS working
- QKD key exchange working
- Ready for MACsec integration





----------------------
✅ macvlan L2 network (real connectivity)
✅ 2 independent KME containers
✅ 2 independent PostgreSQL DBs
✅ SQLx migrations correctly applied
✅ TLS + mTLS working (client auth enforced)
✅ cert trust chain correct (root CA, SAE, KME)
✅ VQFX OpenSSL compatibility solved (key format)
✅ ETSI QKD-014 API working
✅ key lifecycle (enc → dec) consistent


You have fully working QKD control plane:
✅ On VQFX‑1
sae_001 → enc_keys → key_ID ✅

✅ On VQFX‑2
sae_002 → dec_keys → same key ✅


root@vqfx-1:RE:0% curl -sk \
?   --cacert /var/home/admin/certs/root.crt \
?   --cert /var/home/admin/certs/sae_001.crt \
?   --key /var/home/admin/certs/sae_001.key \
?   "https://100.100.100.10:8443/api/v1/keys/sae_002/enc_keys?number=1&size=256"
{"keys":[{"key_ID":"666588f8-6e49-4eaa-8706-d8cf38517622","key":"maGVUXBHSnGsmNulHf4ebVWmFJJAuJsgLD48mNrBcPk="}]}

root@vqfx-2:RE:0% curl -sk \
?   --cacert /var/home/admin/certs/root.crt \
?   --cert /var/home/admin/certs/sae_002.crt \
?   --key /var/home/admin/certs/sae_002.key \
?   "https://100.100.100.10:8443/api/v1/keys/sae_001/dec_keys?key_ID=666588f8-6e49-4eaa-8706-d8cf38517622"
{"keys":[{"key_ID":"666588f8-6e49-4eaa-8706-d8cf38517622","key":"maGVUXBHSnGsmNulHf4ebVWmFJJAuJsgLD48mNrBcPk="}]}root@vqfx-2:RE:0% 
root@vqfx-2:RE:0%

this is e2e infrastructure
KEY GENERATED HERE      KEY CONSUMED THERE
(VQFX-1 / KME1)   →→→   (VQFX-2 / KME1 or KME2)

✅ QKD API working
✅ dual endpoint ready
✅ keys usable




Preview config only
python3 qkd_builder.py --preview

👉 Output:
DEVICE: acx-1
set security macsec ...
...


✅ Dry-run (simulation)
python3 qkd_builder.py --dry-run

👉 Output:
[DRY-RUN] Skipping push for acx-1


✅ Full deploy
python3 qkd_builder.py


✅ Debug mode
python3 qkd_builder.py --preview -vv


✅ ✅ 6. FINAL RESULT
You now have:
✅ template engine
✅ modular inventory
✅ multi-platform support
✅ topology awareness
✅ dry-run
✅ preview
✅ clean debugging
✅ production-ready offbox tool


✅ ✅ TL;DR
Your tree:
config/templates → ✅ correct

New features:
--preview → show config
--dry-run → simulate deployment









# start deployment
## build inventory
python3 qkd_builder.py create \
  --devices vqfx1 vqfx2 \
  --ips 10.0.0.1 10.0.0.2 \
  --interfaces xe-0/0/0 xe-0/0/0 \
  --kmes 100.100.100.10 100.100.100.11

## preview config
python3 qkd_builder.py deploy --preview

## dry-run
python3 qkd_builder.py deploy --dry-run

## deploy

CLI → builds ANY topology:
✔ pair
✔ chain
✔ ring
✔ hub

Inventory → consistent
Templates → unchanged
Provisioning → unchanged

# PAIR
```bash
python3 qkd_builder.py create \
  --topology pair \
  --devices vqfx1 vqfx2 \
  --ips 10.0.0.1 10.0.0.2 \
  --interfaces xe-0/0/0 xe-0/0/0 \
  --kmes 100.100.100.10 100.100.100.11
```

# chain 3 devices
```bash
python3 qkd_builder.py create \python3 qkd_builder \
  --devices vqfx1 vqfx2 vqfx3 \
  --ips 10.0.0.1 10.0.0.2 10.0.0.3 \
  --interfaces xe-0/0/0 xe-0/0/0 xe-0/0/0 \
  --kmes 100.100.100.10 100.100.100.11 100.100.100.12
# vqfx1 ↔ vqfx2
# vqfx2 ↔ vqfx3
```

#ring
```bash
# vqfx1 ↔ vqfx2
# vqfx2 ↔ vqfx3
# vqfx3 ↔ vqfx1
# 
```

# HUB
```bash
--topology hub --hub vqfx1
vqfx1 ↔ vqfx2
vqfx1 ↔ vqfx3
...
```
