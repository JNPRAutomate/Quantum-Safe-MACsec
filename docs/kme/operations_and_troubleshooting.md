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

### DEC empty or unknown key

Confirm the master requested the expected peer SAE pair, KME replication is
healthy, the key retention interval has not expired, and the key was not
already consumed under product semantics.

### Database

Inspect PostgreSQL container health and logs, schema initialization state,
credentials, volume availability, and network resolution from KME services.
Do not repeatedly restart all services before preserving evidence.

### Container/network

Verify service versus container name, external network existence, parent
interface, subnet/gateway, address collision, host route, and firewall.

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
