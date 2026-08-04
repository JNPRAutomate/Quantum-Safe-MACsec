#!/usr/bin/env bash
set -Eeuo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
SECRET_PATH="${SECRET_PATH:-secret/qkd/live}"
ROLE_ID_FILE="${ROLE_ID_FILE:-$HOME/.config/qkd/role_id}"
SECRET_ID_FILE="${SECRET_ID_FILE:-$HOME/.config/qkd/secret_id}"
PAUSE_SECONDS="${PAUSE_SECONDS:-5}"
INVENTORY="${INVENTORY:-ring_mx_acx_unified_link_driven.yml}"
PKI_PROFILE="${PKI_PROFILE:-hierarchical_ca}"

RUN_CREATE=0
RUN_DEPLOY=0
RUN_VALIDATE=0

usage() {
  cat <<'EOF'
Usage: demo_qkd_vault_env_flow.sh [options]

Options:
  --pause-seconds N      Seconds to wait between demo phases (default: 5)
  --secret-path PATH     Vault secret path (default: secret/qkd/live)
  --role-id-file PATH    AppRole role_id file (default: ~/.config/qkd/role_id)
  --secret-id-file PATH  AppRole secret_id file (default: ~/.config/qkd/secret_id)
  --inventory NAME       Inventory for qkd_orchestrator create
  --pki-profile NAME     PKI profile for qkd_orchestrator create
  --run-create           Run qkd_orchestrator create
  --run-deploy           Run qkd_orchestrator deploy
  --run-validate         Run qkd_orchestrator validate --phase postdeploy
  --run-full-demo        Run create + deploy + validate
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
    --run-create) RUN_CREATE=1; shift ;;
    --run-deploy) RUN_DEPLOY=1; shift ;;
    --run-validate) RUN_VALIDATE=1; shift ;;
    --run-full-demo) RUN_CREATE=1; RUN_DEPLOY=1; RUN_VALIDATE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

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

phase "PHASE 1/5 - Read AppRole files"
[[ -f "$ROLE_ID_FILE" ]] || { echo "ERROR: missing role_id file: $ROLE_ID_FILE" >&2; exit 1; }
[[ -f "$SECRET_ID_FILE" ]] || { echo "ERROR: missing secret_id file: $SECRET_ID_FILE" >&2; exit 1; }
ROLE_ID="$(tr -d '\r\n' < "$ROLE_ID_FILE")"
SECRET_ID="$(tr -d '\r\n' < "$SECRET_ID_FILE")"
[[ -n "$ROLE_ID" && -n "$SECRET_ID" ]] || { echo "ERROR: empty role_id/secret_id" >&2; exit 1; }
echo "      role_id file:   $ROLE_ID_FILE"
echo "      secret_id file: $SECRET_ID_FILE"
pause_demo "$PAUSE_SECONDS"

phase "PHASE 2/5 - Login to Vault with AppRole"
APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")"
[[ -n "$APP_TOKEN" ]] || { echo "ERROR: AppRole login returned empty token" >&2; exit 1; }
echo "      login OK (token acquired)"
pause_demo "$PAUSE_SECONDS"

phase "PHASE 3/5 - Retrieve QKD passwords from Vault"
QKD_BOOTSTRAP_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=bootstrap_password "$SECRET_PATH")"
QKD_SCRIPT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=script_password "$SECRET_PATH")"
QKD_DEFAULT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=default_password "$SECRET_PATH")"
[[ -n "$QKD_BOOTSTRAP_PASSWORD" && -n "$QKD_SCRIPT_PASSWORD" && -n "$QKD_DEFAULT_PASSWORD" ]] || {
  echo "ERROR: one or more Vault fields are empty under $SECRET_PATH" >&2
  exit 1
}
echo "      secrets retrieved from: $SECRET_PATH"
pause_demo "$PAUSE_SECONDS"

phase "PHASE 4/5 - Export environment variables (without printing secrets)"
export QKD_BOOTSTRAP_PASSWORD QKD_SCRIPT_PASSWORD QKD_DEFAULT_PASSWORD
echo "      exported: QKD_BOOTSTRAP_PASSWORD (len=${#QKD_BOOTSTRAP_PASSWORD})"
echo "      exported: QKD_SCRIPT_PASSWORD (len=${#QKD_SCRIPT_PASSWORD})"
echo "      exported: QKD_DEFAULT_PASSWORD (len=${#QKD_DEFAULT_PASSWORD})"
echo "      vars visible to orchestrator process in this shell session."
pause_demo "$PAUSE_SECONDS"

phase "PHASE 5/5 - Run orchestrator (optional)"
if [[ "$RUN_CREATE" -eq 1 ]]; then
  echo "      running: python3 qkd_orchestrator.py create --inventory $INVENTORY --pki-profile $PKI_PROFILE"
  python3 qkd_orchestrator.py create --inventory "$INVENTORY" --pki-profile "$PKI_PROFILE"
  pause_demo "$PAUSE_SECONDS"
fi
if [[ "$RUN_DEPLOY" -eq 1 ]]; then
  echo "      running: python3 qkd_orchestrator.py deploy"
  python3 qkd_orchestrator.py deploy
  pause_demo "$PAUSE_SECONDS"
fi
if [[ "$RUN_VALIDATE" -eq 1 ]]; then
  echo "      running: python3 qkd_orchestrator.py validate --phase postdeploy"
  python3 qkd_orchestrator.py validate --phase postdeploy
  pause_demo "$PAUSE_SECONDS"
fi

if [[ "$RUN_CREATE" -eq 0 && "$RUN_DEPLOY" -eq 0 && "$RUN_VALIDATE" -eq 0 ]]; then
  echo "      no orchestrator command selected (demo-only mode)."
  echo "      Example full run:"
  echo "      bash tools/demo_qkd_vault_env_flow.sh --run-full-demo --pause-seconds 8"
fi

echo
echo "[DONE] Demo completed. Vault token revoked and QKD env vars unset."
