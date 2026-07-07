openssl genrsa -out root.key 4096

openssl req \
  -new \
  -x509 \
  -days 3650 \
  -key root.key \
  -out root.crt \
  -subj "/CN=QKD-LAB-ROOT"

  openssl genrsa -out acx7348-p1.key 2048

openssl req \
  -new \
  -key acx7348-p1.key \
  -out acx7348-p1.csr \
  -subj "/CN=acx7348-p1"

openssl x509 \
  -req \
  -in acx7348-p1.csr \
  -CA root.crt \
  -CAkey root.key \
  -CAcreateserial \
  -out acx7348-p1.crt \
  -days 3650

  openssl genrsa -out acx7332-p1.key 2048

openssl req \
  -new \
  -key acx7332-p1.key \
  -out acx7332-p1.csr \
  -subj "/CN=acx7332-p1"

openssl x509 \
  -req \
  -in acx7332-p1.csr \
  -CA root.crt \
  -CAkey root.key \
  -CAcreateserial \
  -out acx7332-p1.crt \
  -days 3650



# MASTER
scp -O root.crt admin@100.123.170.202:/var/home/admin/certs/client-root-ca.crt
scp -O acx7348-p1.crt admin@100.123.170.202:/var/home/admin/certs/
scp -O acx7348-p1.key admin@100.123.170.202:/var/home/admin/certs/

touch /var/home/admin/acx7348-p1last_key.json
chmod 666 /var/home/admin/acx7348-p1last_key.json

# SLAVE
scp -O root.crt admin@100.123.170.201:/var/home/admin/certs/client-root-ca.crt
scp -O acx7332-p1.crt admin@100.123.170.201:/var/home/admin/certs/
scp -O acx7332-p1.key admin@100.123.170.201:/var/home/admin/certs/

touch /var/home/admin/acx7332-p1last_key.json
touch /var/home/admin/acx7348-p1last_key.json

chmod 666 /var/home/admin/acx7332-p1last_key.json
chmod 666 /var/home/admin/acx7348-p1last_key.json
