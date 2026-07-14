#!/bin/bash

set -e

CERT_DIR="/root/etsi-gs-qkd-014-referenceimplementation/certs"

cd $CERT_DIR

echo "== CLEAN OLD KME CERTS =="
rm -f kme_*.crt kme_*.csr kme_*.ext kme_*.key kme_*.pem

echo "== INSTALL OFFBOX CA =="
cp offbox_rootCA.crt root.crt
cp offbox_rootCA.key root.key

# ----------------------------
# FUNCTION TO BUILD KME CERT
# ----------------------------
build_kme() {

    NAME=$1
    IP=$2

    echo "== BUILD $NAME =="

    cat > ${NAME}.ext <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:${IP},DNS:${NAME}
EOF

    openssl req \
      -newkey rsa:4096 -nodes \
      -keyout ${NAME}.key \
      -out ${NAME}.csr \
      -subj "/C=IT/O=Juniper Networks/CN=${IP}"

    openssl x509 -req \
      -in ${NAME}.csr \
      -CA root.crt \
      -CAkey root.key \
      -CAcreateserial \
      -days 365 \
      -extfile ${NAME}.ext \
      -out ${NAME}.crt

    cat ${NAME}.key ${NAME}.crt > ${NAME}.pem
}

# ----------------------------
# BUILD BOTH KMEs
# ----------------------------

build_kme kme_001 100.100.100.10
build_kme kme_002 100.100.100.11

echo "== RESTART KME CONTAINERS =="

docker restart kme1 kme2

echo "DONE"
