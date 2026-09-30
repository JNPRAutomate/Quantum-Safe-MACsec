# KME Orchestrator Design Evolution

## Prototype

The prototype documented manual host preparation, image building, Compose
commands, certificate copies, and per-service database assumptions. This
proved the KME simulator but did not provide resumable ownership.

## Responsibility split

PKI generation moved to QKD ownership; KME retained installation. Host SSH
bootstrap was separated from package installation. Build environment and image
became explicit phases. Status and validation were separated so visibility is
not confused with correctness.

## Database evolution

The old dual/per-KME database model was replaced by shared PostgreSQL plus
multiple KME containers. The reasons were:

- avoid duplicated database infrastructure;
- preserve database during KME restart;
- centralize schema initialization;
- make service/container naming predictable.

## Restart evolution

Early workflows used broad Compose recreation after certificate changes.
The current restart operation touches KME containers only. Full destroy is a
separate explicit lifecycle action.

## State evolution

Long monolithic create sequences were difficult to resume. Per-environment
state and stage commands allow recovery at the failed boundary, while live
validation detects drift.

## Future class architecture

`kme_orchestrator.py` remains a CLI entry point but is planned to delegate to
classes for configuration, deployment backend, PKI installation, database
lifecycle, status/validation, persistence, and presentation.

The embedded Junos EVO KME must be another explicit backend rather than
conditionals spread across Linux-host modules.

See [Architecture Evolution](../architecture_evolution.md) and
[Roadmap](../roadmap.md).
