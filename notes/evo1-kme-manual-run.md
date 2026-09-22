# EVO1 manual KME run and PostgreSQL initialization

This document records the exact manual steps used to run the KME and PostgreSQL on EVO1, using the Docker images already loaded on the Junos EVO host.

Date: 2026-09-16

## Goal

Start a local KME on EVO1 at:

```text
https://10.54.133.195:8443
```

and run a local PostgreSQL instance for the KME database.

The KME app image is:

```text
etsi-kme:local
```

The database image is:

```text
postgres:15
```

They are two separate containers.

---

## 1. Verify Docker images are present on EVO1

From the EVO1 shell:

```sh
[vrf:none] root@evo1:~# docker image ls
```

Expected output includes:

```text
REPOSITORY   TAG       IMAGE ID       CREATED          SIZE
etsi-kme     local     ...            ...             ...
postgres     15        ...            ...             ...
```

If the images are not loaded yet, load them from tar files copied over earlier:

```sh
[vrf:none] root@evo1:~# gunzip -c /var/tmp/etsi-kme-local.tar.gz | docker load
[vrf:none] root@evo1:~# gunzip -c /var/tmp/postgres-15.tar.gz | docker load
```

Then confirm again:

```sh
[vrf:none] root@evo1:~# docker image ls | egrep 'etsi-kme|postgres'
```

---

## 2. Choose Docker network

The host already had an existing Docker bridge network:

```sh
[vrf:none] root@evo1:~# docker network ls
NETWORK ID     NAME             DRIVER    SCOPE
d435aebe82f2   host             host      local
f40ce83238dd   jnpr_cntrz_net   bridge    local
f940b210e912   none             null      local
```

The working choice was to use the existing bridge network:

```text
jnpr_cntrz_net
```

This avoids the issue with `ipvlan` requiring a Linux interface name, while `et-0/0/1` is a Junos interface name and is not directly usable as a Docker parent interface.

We did not use `docker network create` because the existing Docker bridge was already available and worked.

---

## 3. Prepare directories on EVO1

Create directories for PostgreSQL data and KME certificates:

```sh
[vrf:none] root@evo1:~# mkdir -p /var/db/kme/postgres /var/db/kme/certs
[vrf:none] root@evo1:~# chmod 700 /var/db/kme/postgres
[vrf:none] root@evo1:~# ls -ld /var/db/kme /var/db/kme/postgres /var/db/kme/certs
```

Expected output similar to:

```text
drwxr-xr-x ... /var/db/kme
drwx------ ... /var/db/kme/postgres
drwxr-xr-x ... /var/db/kme/certs
```

---

## 4. Copy KME certificate files onto EVO1

The KME container needs the server certificate and key, plus the trusted root certificate bundle.

The files created/used were:

```text
/var/db/kme/certs/root.crt
/var/db/kme/certs/kme-001.crt
/var/db/kme/certs/kme-001.key
```

If these files are not already present, copy them from the Ubuntu host to EVO1. Example:

```sh
[ubuntu] root@Ubuntu-20:~# scp \
  /home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs/root.crt \
  /home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs/kme-001.crt \
  /home/aterren/kme-lab/etsi-gs-qkd-014-referenceimplementation/certs/kme-001.key \
  root@10.54.133.195:/var/tmp/
```

Then on EVO1:

```sh
[vrf:none] root@evo1:~# cp /var/tmp/root.crt /var/db/kme/certs/
[vrf:none] root@evo1:~# cp /var/tmp/kme-001.crt /var/db/kme/certs/
[vrf:none] root@evo1:~# cp /var/tmp/kme-001.key /var/db/kme/certs/
[vrf:none] root@evo1:~# chmod 600 /var/db/kme/certs/kme-001.key
[vrf:none] root@evo1:~# chmod 644 /var/db/kme/certs/root.crt /var/db/kme/certs/kme-001.crt
[vrf:none] root@evo1:/var/db/kme/certs# ls -l
```

Expected output:

```text
-rw-r--r--  1 root root ... root.crt
-rw-r--r--  1 root root ... kme-001.crt
-rw-------  1 root root ... kme-001.key
```

---

## 5. Remove any stale KME/Postgres containers

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker rm -f qkd-postgres kme01 2>/dev/null || true
```

This avoids port or name conflicts.

---

## 6. Start PostgreSQL on EVO1

Run the database container:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker run -d \
  --name qkd-postgres \
  --restart unless-stopped \
  --network jnpr_cntrz_net \
  -e POSTGRES_USER=db_user \
  -e POSTGRES_PASSWORD=db_password \
  -e POSTGRES_DB=key_store \
  -v /var/db/kme/postgres:/var/lib/postgresql/data \
  postgres:15
```

Check status:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker ps
```

Expected output includes:

```text
CONTAINER ID   IMAGE          COMMAND                  CREATED            STATUS            PORTS      NAMES
...            postgres:15    "docker-entrypoint.s…"  ...               Up ...            5432/tcp   qkd-postgres
```

Check DB readiness:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker exec qkd-postgres pg_isready -U db_user -d key_store
```

Expected output:

```text
accepting connections
```

---

## 7. Initialize the PostgreSQL table `keys`

This table is required by the KME server.

Run:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -i qkd-postgres psql -U db_user -d key_store <<'SQL'
CREATE TABLE IF NOT EXISTS keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
SQL
```

Verify schema:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -it qkd-postgres psql -U db_user -d key_store -c '\d keys'
```

Expected output includes table definition.

Verify it is empty:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -it qkd-postgres psql -U db_user -d key_store -c 'select count(*) from keys;'
```

Expected output:

```text
 count
-------
 0
```

---

## 8. Start KME container on EVO1

Start the KME container on the same Docker bridge network:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker run -d \
  --name kme01 \
  --restart unless-stopped \
  --network jnpr_cntrz_net \
  -p 8443:8443 \
  -v /var/db/kme/certs:/certs:ro \
  -e ETSI_014_REF_IMPL_DB_URL=postgres://db_user:db_password@qkd-postgres:5432/key_store \
  -e ETSI_014_REF_IMPL_IP_ADDR=0.0.0.0 \
  -e ETSI_014_REF_IMPL_PORT_NUM=8443 \
  -e ETSI_014_REF_IMPL_NUM_WORKER_THREADS=2 \
  -e ETSI_014_REF_IMPL_TLS_CERT=/certs/kme-001.crt \
  -e ETSI_014_REF_IMPL_TLS_PRIVATE_KEY=/certs/kme-001.key \
  -e ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt \
  etsi-kme:local
```

This produced the container ID:

```text
5373c98b2c0d7506322ed985138963bc89634fe7c9105e2f711d8c6d33dee5a0
```

Check container state:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker ps
```

Expected output:

```text
CONTAINER ID   IMAGE                          COMMAND                  CREATED         STATUS         PORTS                                      NAMES
5373c98b2c0d   etsi-kme:local                 "/app/etsi_gs_qkd_01…"   4 seconds ago   Up 3 seconds   0.0.0.0:8443->8443/tcp, :::8443->8443/tcp   kme01
16bef4c019c8   postgres:15                    "docker-entrypoint.s…"   2 minutes ago   Up 2 minutes   5432/tcp                                    qkd-postgres
0bc36f19e377   jnpr_cntrz_infra_cntr:latest   "/bin/sleep infinity"    31 hours ago    Up 31 hours                                                jnpr_cntrz_infra_cntr
```

This confirms both KME and Postgres are running.

---

## 9. Check the KME container logs

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker logs --tail 100 kme01
```

Observed successful output:

```text
[2026-09-16T16:50:32Z INFO  etsi_gs_qkd_014_referenceimplementation] Server starting on 0.0.0.0:8443
[2026-09-16T16:50:32Z INFO  actix_server::builder] starting 2 workers
[2026-09-16T16:50:32Z INFO  actix_server::server] Actix runtime found; starting in Actix runtime
[2026-09-16T16:50:32Z INFO  actix_server::server] starting service: "actix-web-service-0.0.0.0:8443", workers: 2, listening on: 0.0.0.0:8443
```

This confirms the KME service is listening on 8443.

---

## 10. Port mapping details

Check exact port mapping:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker port kme01
```

Observed output:

```text
8443/tcp -> 0.0.0.0:8443
8443/tcp -> :::8443
```

Also check the Docker bridge internal IP of the KME container:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker inspect kme01 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

Observed output:

```text
172.17.0.3
```

This is the internal Docker IP of the KME container. The application itself is listening on 0.0.0.0:8443 inside the container.

---

## 11. Test without client certificate: expected failure

The KME requires mTLS. Sending a request without the client certificate should fail.

```sh
[vrf:none] root@evo1:/var/db/kme/certs# curl -k -sS -vvv https://10.54.133.195:8443/
```

Observed output ended with:

```text
* TLSv1.3 (IN), TLS alert, unknown (628):
* OpenSSL SSL_read: error:0A00045C:SSL routines::tlsv13 alert certificate required, errno 0
curl: (56) OpenSSL SSL_read: error:0A00045C:SSL routines::tlsv13 alert certificate required, errno 0
```

This is the expected behavior because the server requests a client certificate.

---

## 12. Successful `enc_keys` call with SAE client certificate

The correct command was:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# curl -k -sS -vvv \
  --cert /var/db/scripts/certs/sae-001.crt \
  --key /var/db/scripts/certs/sae-001.key \
  --cacert /var/db/scripts/certs/trusted-kme-ca-bundle.crt \
  "https://10.54.133.195:8443/api/v1/keys/sae-002/enc_keys?number=1&size=256"
```

Observed successful output:

```text
* TLSv1.3 (OUT), TLS handshake, Client hello (1):
* TLSv1.3 (IN), TLS handshake, Server hello (2):
* TLSv1.3 (IN), TLS handshake, Encrypted Extensions (8):
* TLSv1.3 (IN), TLS handshake, Request CERT (13):
* TLSv1.3 (IN), TLS handshake, Certificate (11):
* TLSv1.3 (IN), TLS handshake, CERT verify (15):
* TLSv1.3 (IN), TLS handshake, Finished (20):
* TLSv1.3 (OUT), TLS change cipher, Change cipher spec (1):
* TLSv1.3 (OUT), TLS handshake, Certificate (11):
* TLSv1.3 (OUT), TLS handshake, CERT verify (15):
* TLSv1.3 (OUT), TLS handshake, Finished (20):
> GET /api/v1/keys/sae-002/enc_keys?number=1&size=256 HTTP/1.1
> Host: 10.54.133.195:8443
> User-Agent: curl/7.82.0
> Accept: */*
* TLSv1.3 (IN), TLS handshake, Newsession Ticket (4):
* TLSv1.3 (IN), TLS handshake, Newsession Ticket (4):
< HTTP/1.1 200 OK
< content-length: 113
< content-type: application/json
< date: Wed, 16 Sep 2026 16:53:13 GMT
<
{"keys":[{"key_ID":"bb7523c9-47d2-409e-b52e-cef99a066c41","key":"7+Md6pjnQdSev2Ua/lbpS7DL4TvO3hnvxvbEq7qK678="}]}
```

This proves all of the following:

- KME TLS listener is reachable on `10.54.133.195:8443`
- the KME receives mTLS requests
- the client cert is accepted
- the backend database is accessible
- a key record was generated successfully

---

## 13. Verify the database recorded the generated key

Run:

```sh
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -it qkd-postgres psql -U db_user -d key_store -c "select id, master_sae_id, slave_sae_id, size, active, last_modified_at from keys;"
```

Expected output is a row similar to:

```text
                                   id                                   | master_sae_id | slave_sae_id | size | active |         last_modified_at
------------------------------------------------------------------------+---------------+--------------+------+--------+----------------------------
bb7523c9-47d2-409e-b52e-cef99a066c41 | sae-001       | sae-002      | 256  | t      | 2026-09-16 ...
```

This confirms the KME persisted the generated key in Postgres.

---

## 14. Full command sequence used on EVO1 (copy/paste ready)

This is the exact sequence used in practice:

```sh
[vrf:none] root@evo1:~# mkdir -p /var/db/kme/postgres /var/db/kme/certs
[vrf:none] root@evo1:~# chmod 700 /var/db/kme/postgres
[vrf:none] root@evo1:~# ls -ld /var/db/kme /var/db/kme/postgres /var/db/kme/certs
[vrf:none] root@evo1:~# docker rm -f qkd-postgres kme01 2>/dev/null || true
[vrf:none] root@evo1:/var/db/kme/certs# docker run -d \
  --name qkd-postgres \
  --restart unless-stopped \
  --network jnpr_cntrz_net \
  -e POSTGRES_USER=db_user \
  -e POSTGRES_PASSWORD=db_password \
  -e POSTGRES_DB=key_store \
  -v /var/db/kme/postgres:/var/lib/postgresql/data \
  postgres:15
[vrf:none] root@evo1:/var/db/kme/certs# docker exec qkd-postgres pg_isready -U db_user -d key_store
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -i qkd-postgres psql -U db_user -d key_store <<'SQL'
CREATE TABLE IF NOT EXISTS keys (
    id UUID PRIMARY KEY,
    master_sae_id TEXT NOT NULL,
    slave_sae_id TEXT NOT NULL,
    size INT NOT NULL,
    content TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    last_modified_at TIMESTAMP DEFAULT NOW()
);
SQL
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -it qkd-postgres psql -U db_user -d key_store -c '\d keys'
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -it qkd-postgres psql -U db_user -d key_store -c 'select count(*) from keys;'
[vrf:none] root@evo1:/var/db/kme/certs# docker run -d \
  --name kme01 \
  --restart unless-stopped \
  --network jnpr_cntrz_net \
  -p 8443:8443 \
  -v /var/db/kme/certs:/certs:ro \
  -e ETSI_014_REF_IMPL_DB_URL=postgres://db_user:db_password@qkd-postgres:5432/key_store \
  -e ETSI_014_REF_IMPL_IP_ADDR=0.0.0.0 \
  -e ETSI_014_REF_IMPL_PORT_NUM=8443 \
  -e ETSI_014_REF_IMPL_NUM_WORKER_THREADS=2 \
  -e ETSI_014_REF_IMPL_TLS_CERT=/certs/kme-001.crt \
  -e ETSI_014_REF_IMPL_TLS_PRIVATE_KEY=/certs/kme-001.key \
  -e ETSI_014_REF_IMPL_TLS_ROOT_CRT=/certs/root.crt \
  etsi-kme:local
[vrf:none] root@evo1:/var/db/kme/certs# docker ps
[vrf:none] root@evo1:/var/db/kme/certs# docker logs --tail 100 kme01
[vrf:none] root@evo1:/var/db/kme/certs# docker port kme01
[vrf:none] root@evo1:/var/db/kme/certs# curl -k -sS -vvv https://10.54.133.195:8443/
[vrf:none] root@evo1:/var/db/kme/certs# curl -k -sS -vvv \
  --cert /var/db/scripts/certs/sae-001.crt \
  --key /var/db/scripts/certs/sae-001.key \
  --cacert /var/db/scripts/certs/trusted-kme-ca-bundle.crt \
  "https://10.54.133.195:8443/api/v1/keys/sae-002/enc_keys?number=1&size=256"
[vrf:none] root@evo1:/var/db/kme/certs# docker exec -it qkd-postgres psql -U db_user -d key_store -c "select id, master_sae_id, slave_sae_id, size, active, last_modified_at from keys;"
```

---

## 15. Notes about the environment

The reason the earlier `docker network create -d ipvlan ... -o parent=et-0/0/1` failed is that `et-0/0/1` is a Junos interface name, not a Linux interface name visible to Docker. The Linux host namespace exposes interfaces such as `vmb0`, `eth0`, `eth1`, etc., but not the Junos formatted names.

Because `10.54.133.195` is present in the Linux namespace via `vmb0`, using the Docker bridge and host port mapping was the working approach.

This configuration was used successfully and is therefore the working manual deployment for EVO1.

---

## 16. Final status

At the end of the successful test:

```text
KME running on EVO1 : https://10.54.133.195:8443
KME port            : 8443
Postgres running    : qkd-postgres container
DB initialized      : keys table created
enc_keys success    : HTTP 200
Key created         : bb7523c9-47d2-409e-b52e-cef99a066c41
```

This completes the manual run of the KME and DB on EVO1.
