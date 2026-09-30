# MACsec Runtime Logic

> **Archived historical document:** This file preserves superseded design or operational
> context and is not authoritative for the current release. Use the
> [active documentation](../../docs/readme.md) for supported behavior.


## Principle

MACsec is not statically configured.

## Flow

1. Key generated (QKD)
2. Onbox retrieves key
3. CA applied dynamically

## No Static Config

- No apply-macro
- No static CA

## Interface Separation

- KME interface
- MACsec interface
