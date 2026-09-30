# Troubleshooting Guide

> **Archived historical document:** This file preserves superseded design or operational
> context and is not authoritative for the current release. Use the
> [active documentation](../../docs/readme.md) for supported behavior.


## TLS Errors
Cause: Certificate mismatch
Fix: Verify CA and SAN configuration

## decrypt error
Cause: SAE cert rejected by KME
Fix: Ensure SAN = DNS only

## DEC EMPTY
Cause: Slave using wrong KME
Fix: Use same KME for both nodes

## DB Issues
Cause: Missing schema
Fix: Re-run init_db

## Container Issues
Cause: Cert cache
Fix:

docker compose down -v
docker compose up -d
