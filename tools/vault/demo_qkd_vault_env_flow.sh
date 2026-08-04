#!/usr/bin/env bash
set -Eeuo pipefail
# Prevent accidental secret exposure if caller uses "bash -x ..."
set +x 2>/dev/null || true

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
SECRET_PATH="${SECRET_PATH:-secret/qkd/live}"
ROLE_ID_FILE="${ROLE_ID_FILE:-$HOME/.config/qkd/role_id}"
SECRET_ID_FILE="${SECRET_ID_FILE:-$HOME/.config/qkd/secret_id}"
PAUSE_SECONDS="${PAUSE_SECONDS:-5}"
INVENTORY="${INVENTORY:-ring_mx_acx_unified_link_driven.yml}"
PKI_PROFILE="${PKI_PROFILE:-hierarchical_ca}"

RUN_CREATE=1
PROMPT_SECRETS=0
WRITE_SECRETS=0

usage() {
  cat <<'EOF'
Usage: tools/vault/demo_qkd_vault_env_flow.sh [options]

Options:
  --pause-seconds N      Seconds to wait between demo phases (default: 5)
  --secret-path PATH     Vault secret path (default: secret/qkd/live)
  --role-id-file PATH    AppRole role_id file (default: ~/.config/qkd/role_id)
  --secret-id-file PATH  AppRole secret_id file (default: ~/.config/qkd/secret_id)
  --inventory NAME       Inventory for qkd_orchestrator create
  --pki-profile NAME     PKI profile for qkd_orchestrator create
  --skip-create          Do not run qkd_orchestrator create (demo-only mode)
  --prompt-secrets       Prompt hidden passwords from terminal (read -s)
  --write-secrets        Write prompted secrets to Vault before retrieval
                         (requires --prompt-secrets and VAULT_WRITER_TOKEN or VAULT_TOKEN with write permissions)
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pause-seconds) PAUSE_SECONDS="$2"; shift 2 ;;
    --secret-path) SECRET_PATH="$2"; shift 2 ;;
    --role-id-file) ROLE_ID_FILE="$2"; shift 2 ;;
    --secret-id-file) SECRET_ID_FILE="$2"; shift 2 ;;
    --inventory) INVENTORY="$2"; shift 2 ;;
    --pki-profile) PKI_PROFILE="$2"; shift 2 ;;
    --skip-create) RUN_CREATE=0; shift ;;
    --prompt-secrets) PROMPT_SECRETS=1; shift ;;
    --write-secrets) WRITE_SECRETS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$WRITE_SECRETS" -eq 1 && "$PROMPT_SECRETS" -eq 0 ]]; then
  echo "ERROR: --write-secrets requires --prompt-secrets" >&2
  exit 1
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 1; }
}

phase() {
  printf '\n[%s] ==== %s ====\n' "$(date '+%H:%M:%S')" "$1"
}

pause_demo() {
  local s="$1"
  if [[ "$s" -gt 0 ]]; then
    echo "      waiting ${s}s..."
    sleep "$s"
  fi
}

prompt_secret() {
  local prompt="$1"
  local outvar="$2"
  local value
  read -r -s -p "$prompt: " value
  echo
  if [[ -z "$value" ]]; then
    echo "ERROR: empty value not allowed for $outvar" >&2
    exit 1
  fi
  printf -v "$outvar" '%s' "$value"
}

cleanup() {
  if [[ -n "${APP_TOKEN:-}" ]]; then
    VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true
  fi
  unset APP_TOKEN ROLE_ID SECRET_ID
  unset QKD_BOOTSTRAP_PASSWORD QKD_SCRIPT_PASSWORD QKD_DEFAULT_PASSWORD
}
trap cleanup EXIT

need_cmd vault
need_cmd python3

export VAULT_ADDR

phase "PHASE 1/6 - Read AppRole files"
[[ -f "$ROLE_ID_FILE" ]] || { echo "ERROR: missing role_id file: $ROLE_ID_FILE" >&2; exit 1; }
[[ -f "$SECRET_ID_FILE" ]] || { echo "ERROR: missing secret_id file: $SECRET_ID_FILE" >&2; exit 1; }
ROLE_ID="$(tr -d '\r\n' < "$ROLE_ID_FILE")"
SECRET_ID="$(tr -d '\r\n' < "$SECRET_ID_FILE")"
[[ -n "$ROLE_ID" && -n "$SECRET_ID" ]] || { echo "ERROR: empty role_id/secret_id" >&2; exit 1; }
echo "      role_id file:   $ROLE_ID_FILE"
echo "      secret_id file: $SECRET_ID_FILE"
pause_demo "$PAUSE_SECONDS"

phase "PHASE 2/6 - Login to Vault with AppRole"
APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")"
[[ -n "$APP_TOKEN" ]] || { echo "ERROR: AppRole login returned empty token" >&2; exit 1; }
echo "      login OK (token acquired)"
pause_demo "$PAUSE_SECONDS"

if [[ "$PROMPT_SECRETS" -eq 1 ]]; then
  phase "PHASE 3/6 - Prompt hidden passwords from terminal"
  prompt_secret "      Enter QKD_BOOTSTRAP_PASSWORD" INPUT_BOOTSTRAP_PASSWORD
  prompt_secret "      Enter QKD_SCRIPT_PASSWORD" INPUT_SCRIPT_PASSWORD
  prompt_secret "      Enter QKD_DEFAULT_PASSWORD" INPUT_DEFAULT_PASSWORD
  echo "      passwords captured securely (hidden input)."
  pause_demo "$PAUSE_SECONDS"
fi

if [[ "$WRITE_SECRETS" -eq 1 ]]; then
  phase "PHASE 4/6 - Write prompted passwords into Vault"
  WRITER_TOKEN="${VAULT_WRITER_TOKEN:-${VAULT_TOKEN:-}}"
  if [[ -z "$WRITER_TOKEN" ]]; then
    echo "ERROR: --write-secrets requested but no writer token available." >&2
    echo "Set VAULT_WRITER_TOKEN (preferred) or VAULT_TOKEN with write access to $SECRET_PATH." >&2
    exit 1
  fi
  VAULT_TOKEN="$WRITER_TOKEN" vault kv put "$SECRET_PATH" \
    bootstrap_password="$INPUT_BOOTSTRAP_PASSWORD" \
    script_password="$INPUT_SCRIPT_PASSWORD" \
    default_password="$INPUT_DEFAULT_PASSWORD" >/dev/null
  unset INPUT_BOOTSTRAP_PASSWORD INPUT_SCRIPT_PASSWORD INPUT_DEFAULT_PASSWORD WRITER_TOKEN
  echo "      Vault secret updated at: $SECRET_PATH"
  pause_demo "$PAUSE_SECONDS"
fi

phase "PHASE 5/6 - Retrieve QKD passwords from Vault"
QKD_BOOTSTRAP_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=bootstrap_password "$SECRET_PATH")"
QKD_SCRIPT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=script_password "$SECRET_PATH")"
QKD_DEFAULT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=default_password "$SECRET_PATH")"
[[ -n "$QKD_BOOTSTRAP_PASSWORD" && -n "$QKD_SCRIPT_PASSWORD" && -n "$QKD_DEFAULT_PASSWORD" ]] || {
  echo "ERROR: one or more Vault fields are empty under $SECRET_PATH" >&2
  exit 1
}
echo "      secrets retrieved from: $SECRET_PATH"
pause_demo "$PAUSE_SECONDS"

phase "PHASE 6/6 - Export environment variables (without printing secrets)"
export QKD_BOOTSTRAP_PASSWORD QKD_SCRIPT_PASSWORD QKD_DEFAULT_PASSWORD
echo "      exported: QKD_BOOTSTRAP_PASSWORD (len=${#QKD_BOOTSTRAP_PASSWORD})"
echo "      exported: QKD_SCRIPT_PASSWORD (len=${#QKD_SCRIPT_PASSWORD})"
echo "      exported: QKD_DEFAULT_PASSWORD (len=${#QKD_DEFAULT_PASSWORD})"
echo "      vars visible to orchestrator process in this shell session."
pause_demo "$PAUSE_SECONDS"

phase "RUN ORCHESTRATOR CREATE"
if [[ "$RUN_CREATE" -eq 1 ]]; then
  echo "      running: python3 qkd_orchestrator.py create --inventory $INVENTORY --pki-profile $PKI_PROFILE"
  python3 qkd_orchestrator.py create --inventory "$INVENTORY" --pki-profile "$PKI_PROFILE"
  pause_demo "$PAUSE_SECONDS"
fi
if [[ "$RUN_CREATE" -eq 0 ]]; then
  echo "      create skipped (--skip-create)."
fi

echo
echo "[DONE] Demo completed."
echo "       Vault token revoked and QKD env vars unset."
