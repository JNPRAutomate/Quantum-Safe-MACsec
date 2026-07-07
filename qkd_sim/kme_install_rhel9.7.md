# from root: 
dnf install -y dnf-plugins-core git make gcc gcc-c++ openssl openssl-devel pkgconfig curl tar

dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker

docker --version
docker compose version

# put user in docker
usermod -aG docker aterren


```bash
:%d
:set paste

# QKD lab certificate Makefile
# No TABs needed. Recipe lines start with "|".

.RECIPEPREFIX := |
SHELL := /bin/bash

OPENSSL := /usr/bin/openssl

ROOT_CRT := root.crt
ROOT_KEY := root.key

KME_IDS := kme_001 kme_002
SAE_IDS := sae_001 sae_002 sae_003 sae_004 sae_005

ALL_IDS := $(KME_IDS) $(SAE_IDS)
ALL_PEMS := $(addsuffix .pem,$(ALL_IDS))

KME1_IP ?= 100.123.252.10
KME2_IP ?= 100.123.252.11

DAYS ?= 3650
KEY_BITS ?= 4096

.PHONY: certs clean list verify check-tools

default: certs

check-tools:
| @if [ ! -x "$(OPENSSL)" ]; then echo "ERROR: missing $(OPENSSL). Run: sudo dnf install -y openssl"; exit 1; fi
| @$(OPENSSL) version

certs: check-tools $(ROOT_CRT) $(ALL_PEMS)
| @echo "All certificates generated."

clean:
| @rm -f *.crt *.csr *.pem *.ext *.key *.srl output.log
| @echo "Certificates and output log removed."

list:
| @ls -l *.key *.csr *.crt *.pem *.ext 2>/dev/null || true

verify:
| @echo "### Certificate subjects"
| @for f in *.crt; do echo "=== $$f ==="; $(OPENSSL) x509 -in "$$f" -noout -subject -issuer -dates; done

$(ROOT_KEY):
| @echo "Generating root private key $(ROOT_KEY)"
| @$(OPENSSL) genrsa -out $(ROOT_KEY) $(KEY_BITS)
| @chmod 600 $(ROOT_KEY)

$(ROOT_CRT): $(ROOT_KEY)
| @echo "Generating root certificate $(ROOT_CRT)"
| @$(OPENSSL) req -x509 -new -key $(ROOT_KEY) -days $(DAYS) -sha256 -subj "/C=IT/O=QKD Lab/CN=QKD Lab Root CA" -out $(ROOT_CRT)
| @chmod 644 $(ROOT_CRT)

%.key:
| @echo "Generating private key $@"
| @$(OPENSSL) genrsa -out $@ $(KEY_BITS)
| @chmod 600 $@

kme_001.ext:
| @printf '%s\n' "basicConstraints = CA:FALSE" "keyUsage = digitalSignature,keyEncipherment" "extendedKeyUsage = serverAuth" "subjectAltName = DNS:localhost,IP:127.0.0.1,IP:$(KME1_IP)" > kme_001.ext

kme_002.ext:
| @printf '%s\n' "basicConstraints = CA:FALSE" "keyUsage = digitalSignature,keyEncipherment" "extendedKeyUsage = serverAuth" "subjectAltName = DNS:localhost,IP:127.0.0.1,IP:$(KME2_IP)" > kme_002.ext

sae_%.ext:
| @printf '%s\n' "basicConstraints = CA:FALSE" "keyUsage = digitalSignature,keyEncipherment" "extendedKeyUsage = clientAuth" "subjectAltName = DNS:localhost,IP:127.0.0.1" > $@

%.csr: %.key %.ext
| @echo "Generating CSR $@"
| @$(OPENSSL) req -new -key $*.key -subj "/C=IT/O=QKD Lab/CN=$*" -out $@

%.crt: %.csr %.ext $(ROOT_CRT) $(ROOT_KEY)
| @echo "Generating certificate $@"
| @SERIAL=$$($(OPENSSL) rand -hex 8); $(OPENSSL) x509 -req -in $*.csr -CA $(ROOT_CRT) -CAkey $(ROOT_KEY) -set_serial 0x$$SERIAL -days $(DAYS) -sha256 -extfile $*.ext -out $@
| @chmod 644 $@

%.pem: %.key %.crt
| @echo "Generating PEM $@"
| @cat $*.key $*.crt > $@
| @chmod 600 $@
```

```bash
grep -n 'gt;\|lt;\|amp;' certs/Makefile || true # this should return nothing!

head -40 certs/Makefile
grep -n 'OFXT\|&gt;\|&lt;\|EOF\|`' certs/Makefile || true
make -C certs clean
make -C certs certs
make -C certs list
make -C certs verify
```

```bash
curl https://sh.rustup.rs -sSf | sh
(select 1 )
source $HOME/.cargo/env

export DATABASE_URL=postgres://db_user:db_password@localhost:5432/key_store
export SQLX_OFFLINE=true
cargo build --release
ls target/release/
```


# change Dockerfile inside the repo

```sh
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

```bash
# docker network create -d ipvlan --subnet=100.123.0.0/16 --gateway=100.123.0.1 -o parent=eth0 qkd_net
f8315913e80766a7a155bc9d3b55fdac58fa52e9420097e1ace6a4c0f8653151
# docker network inspect qkd_net 
[
    {
        "Name": "qkd_net",
        "Id": "f8315913e80766a7a155bc9d3b55fdac58fa52e9420097e1ace6a4c0f8653151",
        "Created": "2026-07-06T19:28:41.416142036+02:00",
        "Scope": "local",
        "Driver": "ipvlan",
        "EnableIPv4": true,
        "EnableIPv6": false,
        "IPAM": {
            "Driver": "default",
            "Options": {},
            "Config": [
                {
                    "Subnet": "100.123.0.0/16",
                    "Gateway": "100.123.0.1"
                }
            ]
        },
        "Internal": false,
        "Attachable": false,
        "Ingress": false,
        "ConfigFrom": {
            "Network": ""
        },
        "ConfigOnly": false,
        "Options": {
            "parent": "eth0"
        },
        "Labels": {},
        "Containers": {},
        "Status": {
            "IPAM": {
                "Subnets": {
                    "100.123.0.0/16": {
                        "IPsInUse": 3,
                        "DynamicIPsAvailable": 65533
                    }
                }
            }
        }
    }
]
```


```sh
usermod -aG docker aterren
systemctl enable docker
systemctl start docker
exit
```

```sh
newgrp docker
cd ~/kme-lab/etsi-gs-qkd-014-referenceimplementation
docker ps
docker network inspect qkd_net || docker network create -d ipvlan --subnet=100.123.0.0/16 --gateway=100.123.0.1 -o parent=eth0 -o ipvlan_mode=l2 qkd_net
rm -rf target
docker build --no-cache -t aterren-etsi-kme:local .

docker compose -p qkd_net -f docker-compose-kme.yml up -d
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Networks}}"
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Networks}}"
```

Enter the postgres docker container and create the main table: 
```sh
docker exec -it aterren-postgres-kme psql -U db_user key_store
CREATE TABLE keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
```

