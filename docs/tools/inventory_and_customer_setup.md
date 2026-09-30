# Inventory Generation and Customer Deployment Tools

## 1. Purpose

The setup tools turn customer/lab inputs into repository configuration and
print or execute the ordered KME/QKD deployment.

## 2. `customer_setup.py`

This guided generator creates named customer artifacts rather than editing base
files in place. Review generated inventory, KME profile, environment, and Vault
placeholders before deployment. Secrets remain placeholders or are supplied
through the approved secret path; generated examples are not a reason to
commit passwords.

## 3. `generate_lab_config.py`

This tool renders lab configuration from explicit devices, links, KME
addresses, interfaces, roles, and platform information. The output must be
link-driven: topology shape may help generate input, but every runtime link
must become explicit and deterministic.

After generation:

1. validate unique device and link names;
2. verify every interface belongs to the intended endpoint;
3. verify one deterministic master per link;
4. verify KME/SAE mapping;
5. inspect platform and credential references;
6. run QKD create dry-run/validation.

## 4. `customer_deploy.py` {#customer-deploy}

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

## 5. Vault helper flow {#vault-localhost-flow}

`tools/vault/` contains localhost setup/deploy/demo helpers. They demonstrate:

- installing and starting Vault;
- initialization/unseal status handling;
- writing placeholder QKD secrets;
- handing secret values to orchestration.

They are not a production Vault architecture. Do not copy root tokens or
unseal keys into inventory.

## 6. Failure handling

Generation errors should identify the invalid input. Deployment errors stop at
the failing orchestrator rather than presenting later validation as success.
Resume from the explicit KME or QKD phase after fixing the cause.
