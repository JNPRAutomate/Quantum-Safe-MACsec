#!/usr/bin/env bash
set -Eeuo pipefail

# Local Vault bootstrap for this repository (lab/dev)
# - Initializes and unseals Vault (if needed)
# - Writes QKD secrets with placeholder defaults
# - Creates/updates qkd-deploy AppRole and local role_id/secret_id files

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
export VAULT_ADDR

QKD_SECRET_PATH="${QKD_SECRET_PATH:-secret/qkd/live}"
QKD_ROLE_NAME="${QKD_ROLE_NAME:-qkd-deploy}"
QKD_CONFIG_DIR="${QKD_CONFIG_DIR:-$HOME/.config/qkd}"
VAULT_INIT_FILE="${VAULT_INIT_FILE:-$QKD_CONFIG_DIR/vault-init.txt}"

QKD_BOOTSTRAP_PASSWORD="${QKD_BOOTSTRAP_PASSWORD:-YOUR_BOOTSTRAP_PASSWORD_HERE}"
QKD_SCRIPT_PASSWORD="${QKD_SCRIPT_PASSWORD:-YOUR_SCRIPT_PASSWORD_HERE}"
QKD_DEFAULT_PASSWORD="${QKD_DEFAULT_PASSWORD:-YOUR_DEFAULT_PASSWORD_HERE}"

if [[ "$QKD_SECRET_PATH" != secret/* ]]; then
  echo "ERROR: QKD_SECRET_PATH must start with 'secret/' (got: $QKD_SECRET_PATH)" >&2
  exit 1
fi

SECRET_REL_PATH="${QKD_SECRET_PATH#secret/}"
SECRET_DATA_PATH="secret/data/$SECRET_REL_PATH"
SECRET_META_PATH="secret/metadata/$SECRET_REL_PATH"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing command: $1" >&2
    exit 1
  }
}

need_cmd vault
need_cmd jq

discover_init_file() {
  local candidates=(
    "$VAULT_INIT_FILE"
    "$HOME/vault-init.txt"
    "$HOME/.vault-init.txt"
    "$HOME/.config/qkd/vault-init.txt"
  )
  local f
  for f in "${candidates[@]}"; do
    [[ -f "$f" ]] && { echo "$f"; return 0; }
  done
  return 1
}

mkdir -p "$QKD_CONFIG_DIR"
chmod 700 "$QKD_CONFIG_DIR"

status_json="$(vault status -format=json)"
initialized="$(jq -r '.initialized' <<<"$status_json")"
sealed="$(jq -r '.sealed' <<<"$status_json")"

if [[ "$initialized" != "true" ]]; then
  echo "[*] Vault not initialized. Initializing..."
  vault operator init -key-shares=1 -key-threshold=1 | tee "$VAULT_INIT_FILE" >/dev/null
  chmod 600 "$VAULT_INIT_FILE"
  echo "[+] Saved init material to: $VAULT_INIT_FILE"
fi

if init_file_found="$(discover_init_file)"; then
  VAULT_INIT_FILE="$init_file_found"
fi
UNSEAL_KEY="${VAULT_UNSEAL_KEY:-}"
ROOT_TOKEN="${VAULT_ROOT_TOKEN:-}"

if [[ -f "$VAULT_INIT_FILE" ]]; then
  [[ -n "$UNSEAL_KEY" ]] || UNSEAL_KEY="$(awk -F': ' '/Unseal Key 1/ {print $2}' "$VAULT_INIT_FILE" | tr -d '\r\n')"
  [[ -n "$ROOT_TOKEN" ]] || ROOT_TOKEN="$(awk -F': ' '/Initial Root Token/ {print $2}' "$VAULT_INIT_FILE" | tr -d '\r\n')"
fi

status_json="$(vault status -format=json)"
sealed="$(jq -r '.sealed' <<<"$status_json")"
if [[ "$sealed" == "true" ]]; then
  if [[ -z "$UNSEAL_KEY" ]]; then
    echo "ERROR: Vault is sealed and no unseal key is available." >&2
    echo "Provide VAULT_UNSEAL_KEY, or set VAULT_INIT_FILE to a valid init file (e.g. ~/vault-init.txt)." >&2
    exit 1
  fi
  echo "[*] Unsealing Vault..."
  vault operator unseal "$UNSEAL_KEY" >/dev/null
fi

if [[ -n "${VAULT_TOKEN:-}" ]] && vault token lookup >/dev/null 2>&1; then
  echo "[*] Using existing VAULT_TOKEN from environment."
else
  if [[ -z "$ROOT_TOKEN" ]]; then
    echo "ERROR: no usable Vault token found." >&2
    echo "Set VAULT_TOKEN (already authenticated) or VAULT_ROOT_TOKEN, or provide VAULT_INIT_FILE with Initial Root Token." >&2
    exit 1
  fi
  echo "[*] Logging in with root token..."
  vault login "$ROOT_TOKEN" >/dev/null
fi

echo "[*] Enabling KV v2 at path 'secret' (if missing)..."
vault secrets enable -path=secret kv-v2 >/dev/null 2>&1 || true

echo "[*] Writing QKD secret values to $QKD_SECRET_PATH ..."
vault kv put "$QKD_SECRET_PATH" \
  bootstrap_password="$QKD_BOOTSTRAP_PASSWORD" \
  script_password="$QKD_SCRIPT_PASSWORD" \
  default_password="$QKD_DEFAULT_PASSWORD" >/dev/null

policy_file="$(mktemp)"
cat >"$policy_file" <<EOF
path "$SECRET_DATA_PATH" {
  capabilities = ["read"]
}
path "$SECRET_META_PATH" {
  capabilities = ["read"]
}
EOF

echo "[*] Creating/updating Vault policy and AppRole..."
vault policy write "$QKD_ROLE_NAME" "$policy_file" >/dev/null
vault auth enable approle >/dev/null 2>&1 || true
vault write "auth/approle/role/$QKD_ROLE_NAME" \
  token_policies="$QKD_ROLE_NAME" \
  token_ttl="1h" \
  token_max_ttl="4h" \
  secret_id_ttl="24h" >/dev/null
rm -f "$policy_file"

role_id_file="$QKD_CONFIG_DIR/role_id"
secret_id_file="$QKD_CONFIG_DIR/secret_id"
vault read -field=role_id "auth/approle/role/$QKD_ROLE_NAME/role-id" >"$role_id_file"
vault write -f -field=secret_id "auth/approle/role/$QKD_ROLE_NAME/secret-id" >"$secret_id_file"
chmod 600 "$role_id_file" "$secret_id_file"

echo
echo "[+] Vault setup completed."
echo "    VAULT_ADDR=$VAULT_ADDR"
echo "    Secret path: $QKD_SECRET_PATH"
echo "    role_id:    $role_id_file"
echo "    secret_id:  $secret_id_file"
echo
echo "Next: replace placeholder passwords with real values, then run deploy wrapper."
echo "Example:"
echo "  export QKD_BOOTSTRAP_PASSWORD='YOUR_REAL_PASSWORD'"
echo "  export QKD_SCRIPT_PASSWORD='YOUR_REAL_PASSWORD'"
echo "  export QKD_DEFAULT_PASSWORD='YOUR_REAL_PASSWORD'"
echo "  ./tools/setup_vault_localhost_8200.sh"
