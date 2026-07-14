# Troubleshooting Guide

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

