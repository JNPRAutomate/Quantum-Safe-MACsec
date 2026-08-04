#!/usr/bin/env bash
set -Eeuo pipefail

# Deploy local HashiCorp Vault service on 127.0.0.1:8200 (lab/dev)
# - Installs vault + jq on RHEL/Rocky/CentOS
# - Writes /etc/vault.d/vault.hcl
# - Creates data dir and ownership
# - Enables and restarts systemd service
# - Verifies service status and API health
#
# Optional: run initial bootstrap flow (init/unseal/policy/approle/secrets)
# via tools/vault/setup_vault_localhost_8200.sh
#
# Usage:
#   bash tools/vault/deploy_vault_localhost_8200.sh
#   bash tools/vault/deploy_vault_localhost_8200.sh --with-bootstrap

WITH_BOOTSTRAP=0
for arg in "$@"; do
  case "$arg" in
    --with-bootstrap) WITH_BOOTSTRAP=1 ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      echo "Usage: $0 [--with-bootstrap]" >&2
      exit 1
      ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing command: $1" >&2
    exit 1
  }
}

need_cmd sudo
need_cmd curl

echo "[*] Installing Vault repository and packages..."
sudo dnf -y install dnf-plugins-core
if [[ ! -f /etc/yum.repos.d/hashicorp.repo ]]; then
  sudo dnf config-manager --add-repo https://rpm.releases.hashicorp.com/RHEL/hashicorp.repo
fi
sudo dnf -y install vault jq

echo "[*] Preparing Vault directories..."
sudo mkdir -p /opt/vault/data
sudo mkdir -p /etc/vault.d
sudo chown -R vault:vault /opt/vault /etc/vault.d
sudo chmod 750 /opt/vault
sudo chmod 700 /opt/vault/data

echo "[*] Writing /etc/vault.d/vault.hcl ..."
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
sudo chown vault:vault /etc/vault.d/vault.hcl
sudo chmod 640 /etc/vault.d/vault.hcl

echo "[*] Enabling and restarting vault service..."
sudo systemctl daemon-reload
sudo systemctl enable vault
sudo systemctl restart vault

echo "[*] Checking service status..."
sudo systemctl --no-pager --full status vault | sed -n '1,20p'

echo "[*] Checking API health endpoint..."
health_code="$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8200/v1/sys/health || true)"
if [[ "$health_code" == "200" || "$health_code" == "429" || "$health_code" == "472" || "$health_code" == "473" || "$health_code" == "501" || "$health_code" == "503" ]]; then
  echo "[+] Vault API reachable (HTTP $health_code)"
else
  echo "ERROR: Vault API not reachable as expected (HTTP $health_code)" >&2
  exit 1
fi

if [[ "$WITH_BOOTSTRAP" -eq 1 ]]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  bootstrap_script="$script_dir/setup_vault_localhost_8200.sh"
  if [[ ! -f "$bootstrap_script" ]]; then
    echo "ERROR: missing bootstrap script: $bootstrap_script" >&2
    exit 1
  fi
  echo "[*] Running bootstrap script (init/unseal/approle/secrets placeholders)..."
  bash "$bootstrap_script"
fi

echo
echo "[+] Vault deploy completed on http://127.0.0.1:8200"
echo "    Next (manual bootstrap): bash tools/vault/setup_vault_localhost_8200.sh"
