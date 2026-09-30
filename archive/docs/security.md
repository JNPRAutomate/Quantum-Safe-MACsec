# Security Model

> **Archived historical document:** This file preserves superseded design or operational
> context and is not authoritative for the current release. Use the
> [active documentation](../../docs/readme.md) for supported behavior.


## PKI
- Single root CA
- All entities signed

## mTLS
- SAE authenticates to KME

## Risks
- Root key currently copied (lab only)

## Production Notes
- Use HSM
- Protect CA key
