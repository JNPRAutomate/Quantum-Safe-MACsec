# KME Operations and Troubleshooting

## 1. Daily checks

Run `status` and inspect:

- remote SSH reachability;
- Docker/Compose availability;
- PostgreSQL health;
- expected KME containers and addresses;
- restart counts;
- certificate validity/SAN;
- HTTPS endpoint response;
- ETSI API authentication and key retrieval.

Correlate KME health with router logs. A running container can still fail TLS,
authorization, database, or key inventory operations.

## 2. Restart

Use KME-only restart after certificate or application refresh. Do not use
Compose down/up as the default because it unnecessarily disrupts PostgreSQL
and makes a certificate operation a database lifecycle event.

## 3. Rebuild

Use destroy/create only when state is intentionally replaceable and backup
requirements are satisfied. Capture:

- state YAML;
- Compose rendering;
- container logs;
- database backup where required;
- certificate profile and expiry;
- selected image/source revision.

## 4. Common failures

### TLS verification

Check SAN against the exact IP/hostname, chain order, installed trust bundle,
clock synchronization, leaf/key match, and whether the container restarted.
The historical EVO1 test demonstrated the expected mTLS boundary: a request
without a client certificate failed, while an SAE client certificate
successfully called `enc_keys`. See the
[manual experiment record](lab_history/evo1_manual_kme_postgres.md#11-test-without-client-certificate-expected-failure)
for the observed exchange; current production checks must use the deployed
trust bundle and SAE address rather than the experiment's `curl -k`.

### DEC empty or unknown key

Confirm the master requested the expected peer SAE pair, KME replication is
healthy, the key retention interval has not expired, and the key was not
already consumed under product semantics.

### Database

Inspect PostgreSQL container health and logs, schema initialization state,
credentials, volume availability, and network resolution from KME services.
Do not repeatedly restart all services before preserving evidence.
A historic KME HTTP 500 included PostgreSQL error `42P01` because the `keys`
relation had not been created. The current lifecycle separates `db-init` from
`deploy` so this precondition is explicit; initialize/validate through the
orchestrator rather than hand-creating schema in a managed deployment. The
full observed error and verification are in the
[lab chronology](lab_history/ubuntu_kme_qkd_lab_full_record.md#23-kme-http-500-root-cause-missing-keys-table).

### Container/network

Verify service versus container name, external network existence, parent
interface, subnet/gateway, address collision, host route, and firewall.
A previous lab also found two ordering/compatibility traps: Compose attempted
to pull `etsi-kme:local` before a local image had been built, and a Docker
gateway value collided with a physical switch address. The current order is
`build-env --no-up`, `build-image`, certificate install, `db-init`, then
`deploy`; select an unused gateway according to the host's actual subnet.
See the [KME configuration/lifecycle guide](configuration_and_lifecycle.md)
and the [historical lab record](lab_history/ubuntu_kme_qkd_lab_full_record.md).

### Build/runtime library mismatch

A lab image exited with status 127 because its KME binary required OpenSSL
1.1 (`libssl.so.1.1`) that was absent from the runtime container. Build and
runtime stages must use compatible libraries; inspect dynamic dependencies
and container logs before restarting services. The
[full chronology](lab_history/ubuntu_kme_qkd_lab_full_record.md#13-docker-restart-root-cause-missing-openssl-11-library)
preserves the recorded signature and workaround.

### Host privilege and legacy SCP options

The lab's `install-host` stage failed under noninteractive `sudo -n` when its
account lacked the expected sudo capability. Configure only the privilege
scope approved for the host; the historical `NOPASSWD:ALL` command is not a
general recommendation. An early transfer also used `scp -O`, unsupported by
the lab's Ubuntu 20.04 OpenSSH client; the current orchestrator does not
require that legacy-mode option. See the exact
[sudo failure](lab_history/ubuntu_kme_qkd_lab_full_record.md#6-first-kme_orchestratorpy-create-failure-sudo-password-required)
and [SCP failure](lab_history/ubuntu_kme_qkd_lab_full_record.md#scp-o-ubuntu-20)
records.

### Partial `create`

Resume the failed explicit command after correcting the cause. Do not mark
state complete manually without live validation.

## 5. Router-side API test

A successful test must use the same address, trust, client identity, and API
path as the deployed SAE. A host-side curl with disabled verification is not
equivalent.

## 6. Evidence bundle

Collect:

- orchestrator command log;
- deployment profile with secrets redacted;
- state YAML;
- `docker compose config`;
- container list/health/IPs;
- KME and PostgreSQL logs;
- certificate metadata and chain verification;
- router-side TLS/API error;
- time on controller, host, containers, and routers.

For the complete historical command/output sequence, including seven-service
lab mapping and initial vQFX limitations, use the
[KME/QKD lab-history index](lab_history/index.md). Do not treat its
environment-specific values as current configuration.
