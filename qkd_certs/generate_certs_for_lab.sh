master  = vqfx-1   (10.54.13.14)
slave   = vqfx-2   (10.54.12.193)

KME1    = 100.100.100.10
KME2    = 100.100.100.11

root.crt
root.key

vqfx-1.crt
vqfx-1.key

vqfx-2.crt
vqfx-2.key

######
openssl genrsa -out root.key 4096

openssl req \
  -new \
  -x509 \
  -days 3650 \
  -key root.key \
  -out root.crt \
  -subj "/CN=QKD-LAB-ROOT"
######


### vqfx1 cert
openssl genrsa -out vqfx-1.key 2048

openssl req \
  -new \
  -key vqfx-1.key \
  -out vqfx-1.csr \
  -subj "/CN=vqfx-1"

openssl x509 \
  -req \
  -in vqfx-1.csr \
  -CA root.crt \
  -CAkey root.key \
  -CAcreateserial \
  -out vqfx-1.crt \
  -days 3650
  #####

  ### vqfx2 cert
  openssl genrsa -out vqfx-2.key 2048

openssl req \
  -new \
  -key vqfx-2.key \
  -out vqfx-2.csr \
  -subj "/CN=vqfx-2"

openssl x509 \
  -req \
  -in vqfx-2.csr \
  -CA root.crt \
  -CAkey root.key \
  -CAcreateserial \
  -out vqfx-2.crt \
  -days 3650
  