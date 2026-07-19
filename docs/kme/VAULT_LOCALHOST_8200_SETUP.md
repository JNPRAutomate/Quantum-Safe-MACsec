# Vault Local Setup on localhost:8200 for QKD Deploy

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

    sudo mkdir -p /etc/qkd
    vault read -field=role_id auth/approle/role/qkd-deploy/role-id | sudo tee /etc/qkd/role_id >/dev/null
    vault write -f -field=secret_id auth/approle/role/qkd-deploy/secret-id | sudo tee /etc/qkd/secret_id >/dev/null
    sudo chmod 600 /etc/qkd/role_id /etc/qkd/secret_id

## 6. Test AppRole access

    ROLE_ID="$(sudo cat /etc/qkd/role_id)"
    SECRET_ID="$(sudo cat /etc/qkd/secret_id)"

    APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")"
    vault kv get -token="$APP_TOKEN" secret/qkd/live

## 7. Run QKD deploy without persistent cleartext exports

    ROLE_ID="$(sudo cat /etc/qkd/role_id)"
    SECRET_ID="$(sudo cat /etc/qkd/secret_id)"
    APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")"

    QKD_BOOTSTRAP_PASSWORD="$(vault kv get -token="$APP_TOKEN" -field=bootstrap_password secret/qkd/live)" \
    QKD_SCRIPT_PASSWORD="$(vault kv get -token="$APP_TOKEN" -field=script_password secret/qkd/live)" \
    QKD_DEFAULT_PASSWORD="$(vault kv get -token="$APP_TOKEN" -field=default_password secret/qkd/live)" \
    python3 qkd_orchestrator.py deploy --skip-predeploy-validation --skip-postdeploy-validation

## 8. Security notes for production

- Do not use tls_disable = 1 in production.
- Use HTTPS listener with proper server certificate.
- Use short TTL tokens and least-privilege policies.
- Store role_id and secret_id with strict file permissions.
- Avoid shell history leaks and avoid printing secrets.
- Consider Vault Agent auto-auth and templating for non-interactive runs.
