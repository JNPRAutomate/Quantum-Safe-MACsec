# QBT KME lab

This branch, `docker/qbt_ver1.0`, starts a separate QBT KME integration.
The PhioTX deployment implementation, inventory, runtime, dedicated tests
and documentation have been removed from this branch. The validated PhioTX
implementation remains on `docker/phiotx_ver1.1`.

## Current status

The previous lab deployment has been cleaned from EVO1, EVO2 and the Linux
host, including the simulated KMEs, PostgreSQL containers and dedicated data
volumes. Juniper infrastructure containers and networks were preserved.

QBT installation and ETSI GS QKD 014 integration are not implemented or
validated yet. Generic code and tests inherited from the original project
remain for evaluation; their presence does not establish QBT compatibility.

## Embedded EVO integration plan

See [QBT EVO lab plan](docs/qbt_evo_lab_plan.md) for feasibility gates,
the two-router topology, the four-slot keyring parameters and the phased
implementation and acceptance sequence.

The [bundle assessment](docs/qbt_bundle_assessment.md) records the supplied
image metadata, documented KME capabilities, licensing requirements and
outstanding EVO compatibility checks. Temporary diagnostic containers have
executed successfully on both EVOs; no persistent QBT service is running.

The initial [QBT orchestrator](qbt_orchestrator.py) supports `--help`,
`--check-env`, `--copy`, `--check-image`, `--status`, scoped container
`--clean` and Compose validation via `--bootstrap --dry-run`.
Live KME bootstrap, PKI/peering and the MACsec batch runtime are not ready.

## Vendor material handling

Keep QBT image archives, extracted vendor scripts/documentation and licence
material outside the repository. Use a private temporary staging directory;
never commit or redistribute the vendor bundle through Git. Git ignore rules
provide an additional guard against accidental staging, not a licence grant.
Retain the original archives separately because temporary storage may be
cleared by the operating system.

## Vendor installation instructions received

The supplied instructions identify the offline deployment bundle as
`qbt-kme-deploy-2.10.0-alpha.4.tar.gz` and require Docker with the Compose
plugin:

```sh
tar -xzf qbt-kme-deploy-2.10.0-alpha.4.tar.gz
./scripts/load-images.sh
./scripts/install.sh --rootless --compose "docker compose"
```

These are vendor-provided instructions, not a tested procedure for Junos EVO.
Inspect the bundle, installer, image architectures and host requirements
before running them. The meaning and prerequisites of `--rootless` must be
confirmed from the installer; do not assume it works with the EVO Docker
daemon.

The installation does not initially serve ETSI 014: certificates must be
installed and SAEs registered. Bundle runbooks describe PKI, SAE registration
and peer mesh/AKE provisioning; exact CLI behaviour and paired-key delivery
still require validation.
The supplied `qbtbuildtool.com` documentation describes an unrelated build
tool, not this KME.
