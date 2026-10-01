# Inventory Generation and Customer Deployment Tools

## 1. Purpose

The setup tools turn customer/lab inputs into repository configuration and
print or execute the ordered KME/QKD deployment.

## 2. Rule: generated files are always new

`customer_setup.py` and `generate_lab_config.py` never modify a file that
already exists in `config/`. Inventories, KME profiles and
`inventory_base.yaml` in use stay exactly as they are.

- Every file they write gets a new name with a UTC timestamp,
  `<name>_<YYYYmmddTHHMMSSZ>.<ext>`, also when an output path is given.
- If that name already exists, the tool stops and writes nothing.
- `config/inventory/inventory_base.yaml` is only read. When its defaults
  differ from the generated values, the tool prints the change
  (`secrets.<key>: <current> -> <new>`, `kme.port: ...`); apply it by hand
  only if those values must become the defaults.
- Passwords go only to the new environment file, created with mode 0600.
  They are never written to an inventory or to `inventory_base.yaml`. The
  orchestrators read `QKD_*_USER` and `QKD_*_PASSWORD` from the environment
  before `inventory_base.yaml`, so sourcing the file is enough.
- Generated `.env` files and generated KME YAML files (which contain the KME
  database password) are excluded by `.gitignore`. A generated inventory is
  not ignored: review it and add it to Git explicitly if it must be kept.

## 3. `customer_setup.py`

Interactive. It asks for the devices, links, user names, passwords (hidden
input), the KME host and the KME database, then writes three new files:

| File | Content |
|---|---|
| `config/inventory/input/<name>_<UTC>.yaml` | Link-driven inventory |
| `config/kme/<name>_<UTC>.yaml` | KME profile: `config/kme/lab.yaml` plus the answers, including the KME database password |
| `config/kme/<name>_<UTC>.env` | `export QKD_*` users and passwords, mode 0600 |

It ends by printing the next command,
`python tools/customer_deploy.py --name <name>_<UTC>`.

## 4. `generate_lab_config.py`

Non-interactive. It reads an existing inventory (`--inventory`) or a spec
with `inventory`, `kme` and `credentials` sections (`--spec`), validates it
with the runtime normalizer (`lib.qkd.topology_builder.normalize_inventory`),
and writes three new files:

```sh
python tools/generate_lab_config.py --inventory config/inventory/input/lab_vmm.yaml
# Wrote inventory: config/inventory/input/lab_vmm_<UTC>.yaml
# Wrote KME config: config/kme/lab_vmm_<UTC>.yaml
# Wrote environment: config/kme/lab_vmm_<UTC>.env (mode 0600, not tracked by Git)
# Next: python tools/customer_deploy.py --name lab_vmm_<UTC>
```

| Option | Effect |
|---|---|
| `--inventory PATH` / `--spec PATH` | Input, read only |
| `--inventory-out`, `--kme-out`, `--env-out PATH` | Base name of the output; `_<UTC>` is always added |
| `--kme-template PATH` | KME base profile (default `config/kme/lab.yaml`), read only |
| `--inventory-base PATH` | Source of default user names, read only |
| `--prompt-secrets` | Ask for passwords missing from the input, hidden input |
| `--init-spec PATH` | Write a starter spec as `PATH` with `_<UTC>` added, and stop |

The generated inventory keeps the devices and links of the input, adds the
missing defaults (`hostname`, link `id`, `ca_name`, `keychain_name`), and
drops YAML comments. Compare it with the input before using it.

After generation:

1. validate unique device and link names;
2. verify every interface belongs to the intended endpoint;
3. verify one deterministic master per link;
4. verify KME/SAE mapping;
5. inspect platform and credential references;
6. run QKD create dry-run/validation.

## 5. `customer_deploy.py` {#customer-deploy}

The tool requires `--name`, prints the planned KME and QKD commands, and does
not execute them unless `--run` is provided. `--skip-kme` is valid only when
the existing KME environment has already been validated.

The high-level sequence is:

```text
kme_orchestrator create
qkd_orchestrator create
qkd predeploy validation
bootstrap/deploy as selected by the generated workflow
qkd postdeploy validation
```

Reviewing the printed plan before `--run` is an intentional safety gate.

## 6. Vault helper flow {#vault-localhost-flow}

`tools/vault/` contains localhost setup/deploy/demo helpers. They demonstrate:

- installing and starting Vault;
- initialization/unseal status handling;
- writing placeholder QKD secrets;
- handing secret values to orchestration.

They are not a production Vault architecture. Do not copy root tokens or
unseal keys into inventory.

## 7. Failure handling

Generation errors should identify the invalid input. Deployment errors stop at
the failing orchestrator rather than presenting later validation as success.
Resume from the explicit KME or QKD phase after fixing the cause.
