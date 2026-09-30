# Quantum-Safe MACsec

**QKD-assisted MACsec automation for Juniper devices.**

This repository brings together the KME and QKD orchestrators, the
router-side `qkd_onbox.py` runtime, PKI and identity management, and
operational tools for secure key rotation and service validation.

> **New to the project?** Start with the
> [First Steps guide](docs/getting_started.md). The searchable HTML site can be
> built locally using the instructions below; public publishing needs GitHub
> Pages enabled for this repository.

## What the suite does

- Runs ETSI GS QKD 014-compatible KME services and manages their lifecycle.
- Generates link-oriented QKD configuration, certificates, and deployment
  artifacts.
- Deploys an on-box runtime to Juniper devices to coordinate matching QKD
  key IDs and maintain a hitless MACsec rolling keyring.
- Supports operational observation, certificate and SSH identity checks,
  MACsec/MKA health monitoring, log collection, analytics, and troubleshooting.

The system is organized into four operational components:

| Component | Role |
|---|---|
| `kme_orchestrator.py` | Creates and operates the KME service environment |
| `qkd_orchestrator.py` | Validates inventory and generates, deploys, and checks the QKD device configuration |
| `artifacts/qkd_onbox.py` | Runs on devices and handles key acquisition, peer coordination, and runtime rotation |
| `tools/` | Provides deployment helpers, observation, reporting, monitoring, and recovery tools |

For the protocol roles behind these components, see
[MKA, QKD, KME, and SAE](docs/pqc/mka_qkd_kme.md). For current responsibilities
and system boundaries, see [QKD architecture](docs/qkd/architecture.md) and
[KME architecture](docs/kme/architecture.md).

## Documentation

The documentation preserves the project's detailed design and version
history while providing a beginner-oriented route through it:

1. [First Steps](docs/getting_started.md) — component overview and safe
   deployment sequence.
2. [Documentation map](docs/readme.md) — the canonical `pqc`, `kme`, `qkd`,
   `onbox`, and `tools` domains.
3. [Architecture evolution](docs/architecture_evolution.md) — why transport,
   PKI, topology, and runtime state evolved.
4. [Release history](docs/qkd/release_history.md) — changes by release.

## Building the documentation site locally

```bash
python3 -m venv .venv-docs
source .venv-docs/bin/activate
python -m pip install -r docs-requirements.txt
mkdocs serve
```

Open the local address printed by MkDocs to browse the site. To validate the
production build:

```bash
mkdocs build --strict
```

The generated `site/` directory is build output; Markdown under `docs/` remains
the canonical editable source. GitHub Actions builds and deploys the site when
documentation changes reach `ver3.3.4.2`. GitHub Actions builds and validates
the site on every matching change. Publishing requires GitHub Pages to be
configured with **GitHub Actions** and the repository variable
`GITHUB_PAGES_ENABLED=true`; until then, the build remains green without
attempting a deployment.

## Source layout

- `qkd_orchestrator.py` — QKD/MACsec deployment entry point
- `kme_orchestrator.py` — KME service lifecycle entry point
- `artifacts/qkd_onbox.py` — router-side runtime source
- `config/` — inventory, policy, and KME configuration inputs
- `tools/` — deployment, monitoring, collection, reporting, and analysis
- `docs/` — canonical theory, component guides, operations, and evolution
- `test/` — automated tests and representative fixtures

## Project status and roadmap

The documentation describes `ver3.3.4.2`. Planned work, including class-based
orchestrator/runtime refactoring and embedded KME deployment on Junos EVO
ACX/PTX devices, is tracked in the
[product and architecture roadmap](docs/roadmap.md).
