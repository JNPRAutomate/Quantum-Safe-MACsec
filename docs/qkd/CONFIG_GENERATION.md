# Config Generation & Runtime Directory

## Overview

The `config/runtime/` directory contains **generated output files**. These are created automatically by the build process and should **NEVER be manually edited**.

The **source of truth** is `config/inventory/input/` - all changes must be made there.

---

## Where Code Writes to config/runtime/

### ✅ Authorized Write Operations

These are the **only** code locations that should modify `config/runtime/`:

#### 1. **lib/qkd/onbox_builder.py** (Line 450)
```python
static_path.write_text(json.dumps(static_cfg, indent=2, sort_keys=False) + "\n", encoding="utf-8")
```

**Generates:**
- `config/runtime/<device>/qkd_onbox_config.json`
- `config/runtime/<device>/qkd_onbox_inventory.json`

**Triggered by:**
```bash
python3 qkd_orchestrator.py create
```

**Purpose:** Creates device-specific QKD onbox configuration with:
- Device KME server IP and port (extracted from inventory)
- MACsec policy details
- Enabled/disabled flag based on KME presence

---

#### 2. **lib/qkd/topology_builder.py** (Lines 93-98, 554)
```python
def _yaml_dump(path: Path, data: Dict[str, Any]) -> Path:
    path.write_text(yaml.dump(data, ...))

# Line 554:
return _yaml_dump(path, runtime_topology)
```

**Generates:**
- `config/runtime/topology.yaml`
- `config/runtime/devices.yaml`

**Triggered by:**
```bash
python3 qkd_orchestrator.py create
```

**Purpose:** Flattens the topology structure for runtime consumption by deployment processes

---

#### 3. **qkd_orchestrator.py** (Line 1067)
```python
signature_file.write_text(...)
```

**Purpose:** Deployment signature tracking (minor, safe)

---

## ⚠️ What NOT to Do

### ❌ NEVER Manually Edit:
- `config/runtime/*/qkd_onbox_config.json`
- `config/runtime/*/qkd_onbox_inventory.json`
- `config/runtime/topology.yaml`
- `config/runtime/devices.yaml`
- `config/runtime/*/MACsecConfig.txt`
- Any other files in `config/runtime/`

**Why?** When you run `python3 qkd_orchestrator.py create` again, these files are **completely regenerated** from source. Manual edits are lost.

### ❌ NEVER Commit Runtime Files:
Add to `.gitignore`:
```
config/runtime/
```

Runtime files are outputs, not source code.

---

## ✅ What TO Do Instead

### To Fix Configuration Issues:

1. **Identify the problem** - what's wrong in the generated files?
   ```bash
   cat config/runtime/MX1/qkd_onbox_config.json
   ```

2. **Trace it back to source** - where does it come from?
   - Device KME config → check `config/inventory/input/ring_mx_acx_unified_link_driven.yml`
   - Topology definition → check `config/inventory/input/ring_mx_acx_unified_link_driven.yml`
   - MACsec policy → check `config/qkd_policy.yaml`
   - PKI settings → check `config/pki/*.yml`

3. **Edit the SOURCE file**, not runtime:
   ```bash
   # Edit the inventory input
   vim config/inventory/input/ring_mx_acx_unified_link_driven.yml
   ```

4. **Regenerate** from scratch:
   ```bash
   # Clean all generated files
   rm -rf config/runtime/*
   
   # Rebuild
   python3 qkd_orchestrator.py create
   ```

5. **Verify** the fix:
   ```bash
   cat config/runtime/MX1/qkd_onbox_config.json | jq .enabled
   ```

---

## Critical Pattern: Device KME Configuration

### Source (Inventory):
```yaml
# config/inventory/input/ring_mx_acx_unified_link_driven.yml
- name: MX1
  hostname: mx301-p1
  platform: mx
  ip: 100.123.113.151
  kme:
    ip: 100.123.252.10
    port: 8443
```

### Built (Runtime):
```json
{
  "kme_servers": [
    {
      "host": "100.123.252.10",
      "port": 8443
    }
  ],
  "enabled": true
}
```

**This extraction happens in:** `lib/qkd/onbox_builder.py` functions `_device_kme_ip()` and `_device_kme_port()`

---

## Troubleshooting: Inconsistent State

### Symptom: Some devices have valid config, others are placeholders

**Root Cause:** Manual edits to `config/runtime/` creating state inconsistency

**Fix:**
```bash
# Remove ALL runtime files
cd /Users/aterren/Lavoro\ 2026/quantum\ 2026/newMACSEC39_ready_for_git
rm -rf config/runtime/*

# Regenerate from scratch
python3 qkd_orchestrator.py create

# Deploy
python3 qkd_orchestrator.py deploy --skip-predeploy-validation

# Verify all devices now have consistent config
for dev in MX1 MX2 MX3 MX4 MX5 MX6 ACX1 ACX2 ACX3 ACX4 ACX5; do
  echo "=== $dev ==="
  cat config/runtime/$dev/qkd_onbox_config.json | jq '.enabled'
done
```

---

## Key Principle

```
CONFIG/INVENTORY/INPUT/    ← SOURCE OF TRUTH (EDIT HERE)
         ↓
   [Build Process]
   onbox_builder.py
   topology_builder.py
         ↓
CONFIG/RUNTIME/            ← GENERATED OUTPUT (DO NOT EDIT)
         ↓
   [Deploy Process]
  → Devices
```

**Never edit the bottom level. Always edit the top level and rebuild.**

---

## Reference: Full Build Process

```bash
# Full workflow
python3 qkd_orchestrator.py create       # Generates config/runtime/*
python3 qkd_orchestrator.py deploy       # Uses config/runtime/* to configure devices
python3 qkd_orchestrator.py clean        # Removes device state
```

Each `create` run completely regenerates all runtime files from the inventory inputs.

