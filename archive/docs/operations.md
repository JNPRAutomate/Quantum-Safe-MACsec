# Operations Runbook

> **Archived historical document:** This file preserves superseded design or operational
> context and is not authoritative for the current release. Use the
> [active documentation](../../docs/readme.md) for supported behavior.


## Daily Checks
- KME containers running
- DB populated
- curl enc/dec working

## Restart Procedure

docker compose down -v
docker compose up -d

## KME Rebuild

python kme_orchestrator.py --kme-ip <IP> --restart

## Device Redeploy

python qkd_orchestrator.py deploy
