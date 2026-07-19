#!/usr/bin/env bash
set -Eeuo pipefail
set +x

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
ROLE_ID_FILE="${ROLE_ID_FILE:-$HOME/.config/qkd/role_id}"
SECRET_ID_FILE="${SECRET_ID_FILE:-$HOME/.config/qkd/secret_id}"
VAULT_SECRET_PATH="${VAULT_SECRET_PATH:-secret/qkd/live}"

ORCHESTRATOR="${ORCHESTRATOR:-$REPO_ROOT/qkd_orchestrator.py}"
LOG_DIR="${QKD_DEPLOY_LOG_DIR:-$REPO_ROOT/test-logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="${QKD_DEPLOY_LOG_FILE:-$LOG_DIR/deploy_with_vault_$(date +%Y%m%d_%H%M%S).log}"

DEFAULT_DEPLOY_ARGS=(
  --skip-predeploy-validation
  --skip-postdeploy-validation
)

if [[ -n "${QKD_DEPLOY_ARGS:-}" ]]; then
  # Intentionally split by shell words to preserve expected CLI behavior.
  read -r -a DEPLOY_ARGS <<<"$QKD_DEPLOY_ARGS"
else
  DEPLOY_ARGS=("${DEFAULT_DEPLOY_ARGS[@]}")
fi

APP_TOKEN=""
BOOTSTRAP_PASSWORD=""
SCRIPT_PASSWORD=""
DEFAULT_PASSWORD=""

log() {
  local level="$1"
  shift
  printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$*" | tee -a "$LOG_FILE"
}

die() {
  log ERROR "$*"
  exit 1
}

cleanup() {
  local rc=$?

  if [[ -n "$APP_TOKEN" ]]; then
    VAULT_ADDR="$VAULT_ADDR" VAULT_TOKEN="$APP_TOKEN" vault token revoke -self >/dev/null 2>&1 || true
  fi

  unset APP_TOKEN
  unset BOOTSTRAP_PASSWORD
  unset SCRIPT_PASSWORD
  unset DEFAULT_PASSWORD
  unset QKD_BOOTSTRAP_PASSWORD
  unset QKD_SCRIPT_PASSWORD
  unset QKD_DEFAULT_PASSWORD

  if [[ $rc -eq 0 ]]; then
    log INFO "Cleanup complete (token revoked, secrets unset)."
  else
    log ERROR "Cleanup complete after failure (token revoked when possible, secrets unset)."
  fi
}
trap cleanup EXIT

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "Missing required command: $cmd"
}

read_secret_file() {
  local path="$1"
  [[ -r "$path" ]] || die "Cannot read file: $path"
  tr -d '\r\n' <"$path"
}

main() {
  log INFO "Starting Vault-backed QKD deploy"
  log INFO "Vault address: $VAULT_ADDR"
  log INFO "Secret path: $VAULT_SECRET_PATH"
  log INFO "Log file: $LOG_FILE"

  require_cmd vault
  require_cmd python3

  [[ -f "$ORCHESTRATOR" ]] || die "Orchestrator not found: $ORCHESTRATOR"

  export VAULT_ADDR

  local role_id secret_id
  role_id="$(read_secret_file "$ROLE_ID_FILE")"
  secret_id="$(read_secret_file "$SECRET_ID_FILE")"

  [[ -n "$role_id" ]] || die "Empty role_id from $ROLE_ID_FILE"
  [[ -n "$secret_id" ]] || die "Empty secret_id from $SECRET_ID_FILE"

  APP_TOKEN="$(vault write -field=token auth/approle/login role_id="$role_id" secret_id="$secret_id")"
  [[ -n "$APP_TOKEN" ]] || die "Failed to obtain Vault token via AppRole"

  log INFO "Vault login successful via AppRole"

  BOOTSTRAP_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=bootstrap_password "$VAULT_SECRET_PATH")"
  SCRIPT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=script_password "$VAULT_SECRET_PATH")"
  DEFAULT_PASSWORD="$(VAULT_TOKEN="$APP_TOKEN" vault kv get -field=default_password "$VAULT_SECRET_PATH")"

  [[ -n "$BOOTSTRAP_PASSWORD" ]] || die "bootstrap_password is empty in $VAULT_SECRET_PATH"
  [[ -n "$SCRIPT_PASSWORD" ]] || die "script_password is empty in $VAULT_SECRET_PATH"
  [[ -n "$DEFAULT_PASSWORD" ]] || die "default_password is empty in $VAULT_SECRET_PATH"

  log INFO "Secrets loaded from Vault"
  log INFO "Running deploy command"

  (
    cd "$REPO_ROOT"
    QKD_BOOTSTRAP_PASSWORD="$BOOTSTRAP_PASSWORD" \
    QKD_SCRIPT_PASSWORD="$SCRIPT_PASSWORD" \
    QKD_DEFAULT_PASSWORD="$DEFAULT_PASSWORD" \
    python3 "$ORCHESTRATOR" deploy "${DEPLOY_ARGS[@]}"
  ) 2>&1 | tee -a "$LOG_FILE"

  log INFO "Deploy completed successfully"
}

main "$@"
