### FROM THE ORCHESTRATOR!
cd ~/etsi-gs-qkd-014-referenceimplementation/certs


## KME_001
openssl genrsa -out kme_001.key 2048

openssl req \
  -new \
  -key kme_001.key \
  -out kme_001.csr \
  -subj "/CN=kme_001"


  ## create file SAN
  cat > kme_001.ext <<'EOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names

[alt_names]
IP.1 = 100.100.100.10
DNS.1 = kme_001
EOF


### sign kme_001
openssl x509 \
  -req \
  -in kme_001.csr \
  -CA root.crt \
  -CAkey root.key \
  -CAcreateserial \
  -out kme_001.crt \
  -days 3650 \
  -extfile kme_001.ext

  ### kme_002
  openssl genrsa -out kme_002.key 2048

openssl req \
  -new \
  -key kme_002.key \
  -out kme_002.csr \
  -subj "/CN=kme_002"

  ### create san
  cat > kme_002.ext <<'EOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names

[alt_names]
IP.1 = 100.100.100.11
DNS.1 = kme_002
EOF

### sign kme_002
openssl x509 \
  -req \
  -in kme_002.csr \
  -CA root.crt \
  -CAkey root.key \
  -CAcreateserial \
  -out kme_002.crt \
  -days 3650 \
  -extfile kme_002.ext



  root@Ubuntu-20:~/etsi-gs-qkd-014-referenceimplementation/certs# openssl verify -CAfile root.crt kme_001.crt
kme_001.crt: OK
root@Ubuntu-20:~/etsi-gs-qkd-014-referenceimplementation/certs# openssl verify -CAfile root.crt kme_002.crt
kme_002.crt: OK
root@Ubuntu-20:~/etsi-gs-qkd-014-referenceimplementation/certs# openssl x509 -in kme_001.crt -text -noout | grep -A5 "Subject Alternative Name"
            X509v3 Subject Alternative Name: 
                IP Address:100.100.100.10, DNS:kme_001
    Signature Algorithm: sha256WithRSAEncryption
         00:78:a2:c2:4d:10:0f:e2:05:a8:a6:cf:f8:d0:5e:65:fb:d3:
         2d:10:2c:84:f1:3f:e9:fc:0c:2a:0c:3f:21:f4:cf:e8:80:58:
         c3:a0:10:9f:7b:d3:a4:be:a2:77:33:9b:aa:54:79:93:f1:78:
root@Ubuntu-20:~/etsi-gs-qkd-014-referenceimplementation/certs# 
root@Ubuntu-20:~/etsi-gs-qkd-014-referenceimplementation/certs# openssl x509 -in kme_002.crt -text -noout | grep -A5 "Subject Alternative Name"
            X509v3 Subject Alternative Name: 
                IP Address:100.100.100.11, DNS:kme_002
    Signature Algorithm: sha256WithRSAEncryption
         0f:f7:82:c0:cb:2b:2f:81:68:09:9a:7a:af:76:07:a0:c8:a3:
         41:52:09:f9:41:df:6e:8d:75:9d:fe:a0:64:92:3f:cb:2c:7f:
         4d:90:2f:8a:65:ca:24:03:2c:41:2f:b1:7b:80:56:3d:8b:d3:
root@Ubuntu-20:~/etsi-gs-qkd-014-referenceimplementation/certs# 
