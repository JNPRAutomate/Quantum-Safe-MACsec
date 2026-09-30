# KME Orchestrator Design Evolution

## Prototype

The prototype documented manual host preparation, image building, Compose
commands, certificate copies, and per-service database assumptions. This
proved the KME simulator but did not provide resumable ownership.

The September 2026 lab records preserve the concrete migration evidence:
manual Docker setup exposed image/build ordering, host privilege, network
gateway, OpenSSL ABI, and missing-schema failure modes. Those findings led to
explicit build, certificate, database, deploy, and validation stages rather
than a single opaque "start containers" operation. The detailed chronology is
in [KME and QKD Lab History](lab_history/index.md); the canonical run order is
in [Configuration and Lifecycle](configuration_and_lifecycle.md).

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

A separate 2026 EVO1 experiment manually ran KME and PostgreSQL containers on
Junos EVO, installed test certificates, and demonstrated mTLS `enc_keys`
access plus database persistence. It is useful feasibility evidence, but it
was not an orchestrated product deployment, platform qualification, or
production support declaration. Its complete network, storage, certificate,
database, API, and verification sequence is preserved in the
[manual EVO1 experiment](lab_history/evo1_manual_kme_postgres.md). The
supported implementation boundary remains the external Linux host; embedded
EVO work follows the [roadmap](../roadmap.md).

See [Architecture Evolution](../architecture_evolution.md) and
[Roadmap](../roadmap.md).
