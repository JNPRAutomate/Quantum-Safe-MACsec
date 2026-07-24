# Vault Integration LLD on localhost:8200 for QKD Deploy

## Document Classification

- Document type: Low Level Design (LLD) and Architecture Integration Specification
- Architectural layer: secret-management integration for deploy control plane
- Normative scope: Vault authn/authz flow, secret retrieval contract, and deploy injection boundaries
- Out of scope: enterprise Vault HA topologies and production hardening baselines

## Integration Intent

This document specifies the architecture-level integration between deploy workflows and Vault:

1. where credentials are sourced,
2. how they are scoped,
3. how they are injected into deploy runtime,
4. how token lifecycle is terminated after use.

This guide configures HashiCorp Vault locally on the deployment server at:

- Address: http://127.0.0.1:8200
- Mode: single-node (lab/dev)
- TLS: disabled (lab only)

For production, enable TLS and harden authentication.

## 1. Install Vault and jq (RHEL/Rocky/CentOS 9.x)

    sudo dnf -y install dnf-plugins-core
    sudo dnf config-manager --add-repo https://rpm.releases.hashicorp.com/RHEL/hashicorp.repo
    sudo dnf -y install vault jq

## 2. Configure Vault service

    sudo mkdir -p /opt/vault/data
    sudo chown -R vault:vault /opt/vault /etc/vault.d

    sudo tee /etc/vault.d/vault.hcl >/dev/null <<'EOF'
    ui = true
    disable_mlock = true

    listener "tcp" {
      address     = "127.0.0.1:8200"
      tls_disable = 1
    }

    storage "file" {
      path = "/opt/vault/data"
    }

    api_addr     = "http://127.0.0.1:8200"
    cluster_addr = "http://127.0.0.1:8201"
    EOF

    sudo systemctl enable vault
    sudo systemctl restart vault
    sudo systemctl status vault --no-pager

## 3. Initialize, unseal, and login

    export VAULT_ADDR='http://127.0.0.1:8200'

    vault operator init -key-shares=1 -key-threshold=1 | tee ~/vault-init.txt
    chmod 600 ~/vault-init.txt

    UNSEAL_KEY="$(awk -F': ' '/Unseal Key 1/ {print $2}' ~/vault-init.txt)"
    vault operator unseal "$UNSEAL_KEY"

    ROOT_TOKEN="$(awk -F': ' '/Initial Root Token/ {print $2}' ~/vault-init.txt)"
    vault login "$ROOT_TOKEN"

## 4. Enable KV v2 and store QKD secrets

    vault secrets enable -path=secret kv-v2 || true

    vault kv put secret/qkd/live \
      bootstrap_password='Juniper!1' \
      script_password='juniper1' \
      default_password='juniper1'

    vault kv get secret/qkd/live

## 5. Create least-privilege policy and AppRole

This section has two variants:

- root-managed credentials in `/etc/qkd` (legacy/lab)
- non-root user credentials in `~/.config/qkd` (recommended for user `aterren`)

    cat > /tmp/qkd-deploy-policy.hcl <<'EOF'
    path "secret/data/qkd/live" {
      capabilities = ["read"]
    }
    path "secret/metadata/qkd/live" {
      capabilities = ["read"]
    }
    EOF

    vault policy write qkd-deploy /tmp/qkd-deploy-policy.hcl
    vault auth enable approle || true

    vault write auth/approle/role/qkd-deploy \
      token_policies="qkd-deploy" \
      token_ttl="1h" \
      token_max_ttl="4h" \
      secret_id_ttl="24h"

  ### 5A. Legacy/root path (`/etc/qkd`)

    sudo mkdir -p /etc/qkd
    vault read -field=role_id auth/approle/role/qkd-deploy/role-id | sudo tee /etc/qkd/role_id >/dev/null
    vault write -f -field=secret_id auth/approle/role/qkd-deploy/secret-id | sudo tee /etc/qkd/secret_id >/dev/null
    sudo chmod 600 /etc/qkd/role_id /etc/qkd/secret_id

### 5B. Non-root path (recommended for `aterren`)

  mkdir -p ~/.config/qkd
  chmod 700 ~/.config/qkd

  vault read -field=role_id auth/approle/role/qkd-deploy/role-id > ~/.config/qkd/role_id
  vault write -f -field=secret_id auth/approle/role/qkd-deploy/secret-id > ~/.config/qkd/secret_id
  chmod 600 ~/.config/qkd/role_id ~/.config/qkd/secret_id

## 6. Test AppRole access

Important: on some Vault CLI versions `vault kv get -token=...` is not supported.
Use `VAULT_TOKEN` in environment instead.

### 6A. Test with non-root files (recommended)

  ROLE_ID="$(cat ~/.config/qkd/role_id)"
  SECRET_ID="$(cat ~/.config/qkd/secret_id)"

  APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")"
  VAULT_TOKEN="$APP_TOKEN" vault kv get secret/qkd/live

### 6B. Test with root-managed files (legacy)

    ROLE_ID="$(sudo cat /etc/qkd/role_id)"
    SECRET_ID="$(sudo cat /etc/qkd/secret_id)"

    APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")"
    VAULT_TOKEN="$APP_TOKEN" vault kv get secret/qkd/live

## 7. Run QKD deploy without persistent cleartext exports

  ### 7A. One-shot manual deploy command

    ROLE_ID="$(cat ~/.config/qkd/role_id)"
    SECRET_ID="$(cat ~/.config/qkd/secret_id)"
    APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")"

    QKD_BOOTSTRAP_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=bootstrap_password secret/qkd/live)" \
    QKD_SCRIPT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=script_password secret/qkd/live)" \
    QKD_DEFAULT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=default_password secret/qkd/live)" \
    python3 qkd_orchestrator.py deploy --skip-predeploy-validation --skip-postdeploy-validation

    VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true

  ### 7B. Repository wrapper script (`tools/deploy_with_vault.sh`)

  Use the wrapper committed in this repository:

    tools/deploy_with_vault.sh

  Default behavior:

  - reads role_id/secret_id from `~/.config/qkd/role_id` and `~/.config/qkd/secret_id`
  - logs in via AppRole
  - loads QKD env vars from Vault
  - runs `qkd_orchestrator.py deploy` from project root
  - revokes token (`revoke-self`) and unsets secrets on exit

  Run it from anywhere:

    cd /path/to/repo
    chmod 750 tools/deploy_with_vault.sh
    ./tools/deploy_with_vault.sh

  Optional overrides:

    VAULT_SECRET_PATH=secret/qkd/aterren ./tools/deploy_with_vault.sh
    QKD_DEPLOY_ARGS="--devices MX1 --skip-predeploy-validation --skip-postdeploy-validation" ./tools/deploy_with_vault.sh

  ## 8. Optional shell function for user-only env loading

  Add to `~/.bashrc`:

    qkd_env_from_vault() {
      export VAULT_ADDR="http://127.0.0.1:8200"
      local SECRET_PATH="${1:-secret/qkd/live}"
      local ROLE_ID SECRET_ID APP_TOKEN
      local BOOTSTRAP_PASSWORD SCRIPT_PASSWORD DEFAULT_PASSWORD

      ROLE_ID="$(tr -d '\r\n' < "$HOME/.config/qkd/role_id")" || return 1
      SECRET_ID="$(tr -d '\r\n' < "$HOME/.config/qkd/secret_id")" || return 1
      [[ -n "$ROLE_ID" && -n "$SECRET_ID" ]] || { echo "role_id/secret_id missing"; return 1; }

      APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")" || return 1
      [[ -n "$APP_TOKEN" ]] || { echo "AppRole login failed"; return 1; }

      BOOTSTRAP_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=bootstrap_password "$SECRET_PATH")" || { VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true; return 1; }
      SCRIPT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=script_password "$SECRET_PATH")" || { VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true; return 1; }
      DEFAULT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=default_password "$SECRET_PATH")" || { VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true; return 1; }

      export QKD_BOOTSTRAP_PASSWORD="$BOOTSTRAP_PASSWORD"
      export QKD_SCRIPT_PASSWORD="$SCRIPT_PASSWORD"
      export QKD_DEFAULT_PASSWORD="$DEFAULT_PASSWORD"

      VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true
      [[ -n "$QKD_BOOTSTRAP_PASSWORD" && -n "$QKD_SCRIPT_PASSWORD" && -n "$QKD_DEFAULT_PASSWORD" ]] || { echo "empty QKD secret value"; return 1; }

      echo "QKD env loaded in current shell from $SECRET_PATH"
    }

  Reload and test:

    source ~/.bashrc
    type qkd_env_from_vault
    qkd_env_from_vault
    env | grep '^QKD_' | cut -d= -f1

  If your policy only grants access to `secret/qkd/live`, do not use `secret/qkd/aterren` unless policy was extended.

  ## 9. Troubleshooting

  ### `aterren is not in the sudoers file`

  - This is an authorization issue, not a password typo.
  - Use the non-root path (`~/.config/qkd`) to avoid sudo dependency for daily operations.

  ### `failed to determine alias name from login request`

  - Usually `ROLE_ID` or `SECRET_ID` is empty.
  - Verify lengths:

    echo "role_id_len=${#ROLE_ID} secret_id_len=${#SECRET_ID}"

  ### `flag provided but not defined: -token`

  - Your Vault CLI does not support `-token` on `vault kv get`.
  - Use `VAULT_TOKEN=... vault kv get ...` instead.

  ### `qkd_env_from_vault: command not found`

  - Run `source ~/.bashrc` after adding the function.
  - If login shell does not auto-load `.bashrc`, source it from `.bash_profile`.

  ### `invalid role or secret ID` during `auth/approle/login`

  - Root cause is usually one of these:
    - `secret_id` expired (configured `secret_id_ttl` reached),
    - role was recreated, making old `role_id`/`secret_id` invalid,
    - wrong credential files are being read.
  - Regenerate AppRole credentials and overwrite local files:

    ROLE_ID_FILE="$HOME/.config/qkd/role_id"
    SECRET_ID_FILE="$HOME/.config/qkd/secret_id"
    mkdir -p "$HOME/.config/qkd"
    chmod 700 "$HOME/.config/qkd"
    vault read -field=role_id auth/approle/role/qkd-deploy/role-id > "$ROLE_ID_FILE"
    vault write -f -field=secret_id auth/approle/role/qkd-deploy/secret-id > "$SECRET_ID_FILE"
    chmod 600 "$ROLE_ID_FILE" "$SECRET_ID_FILE"

  ### `permission denied` on `secret/data/qkd/aterren` (HTTP 403)

  - The default `qkd-deploy` policy in this guide grants read on `secret/qkd/live`, not `secret/qkd/aterren`.
  - Use the default path unless policy was explicitly extended:

    export VAULT_SECRET_PATH=secret/qkd/live

  - Verify token capabilities quickly:

    APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$(cat ~/.config/qkd/role_id)" secret_id="$(cat ~/.config/qkd/secret_id)")"
    VAULT_TOKEN="$APP_TOKEN" vault token capabilities secret/data/qkd/live
    VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true

  ### `./tools/deploy_with_vault.sh: Permission denied`

  - The wrapper is not executable by default if execute bit is missing.
  - Fix with:

    chmod 750 tools/deploy_with_vault.sh
    ./tools/deploy_with_vault.sh

  ### `qkd_env_from_vault` prints success after Vault errors

  - If your local shell function echoes success even after `vault kv get` 403 errors, the function is missing strict failure checks.
  - Prefer the repository wrapper `tools/deploy_with_vault.sh`, which exits on failures (`set -Eeuo pipefail`).
  - If using a custom shell function, ensure every `vault kv get` is guarded and aborts the function on failure.
  ## 10. Security notes for production

- Do not use tls_disable = 1 in production.
- Use HTTPS listener with proper server certificate.
- Use short TTL tokens and least-privilege policies.
- Store role_id and secret_id with strict file permissions.
- Avoid shell history leaks and avoid printing secrets.
- Consider Vault Agent auto-auth and templating for non-interactive runs.
