# MKA SAK Rekey Flow & Keychain Slot Ordering

## Overview

**MKA (Media Access Control (MAC) Security Entity)** runs a **Secure Association Key (SAK) Rekey** election every 2 seconds on each interface. This process selects which CAK (Connectivity Association Key) from the keychain is currently "active" for encrypting traffic, based on `start_time` scheduling.

---

## SAK Rekey Cycle (Every 2 seconds)

```
┌─────────────────────────────────────────────────────────────┐
│ MKA Cycle (transmit_interval = 2000ms)                      │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ 1. Query Keychain: "Which CAK can I use right now?"         │
│    - Read all CAK entries from QKD_CA_MX1_MX2 keychain      │
│    - Extract start_time for each slot (0, 1, 2, 3)         │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Filter Active: start_time ≤ now                          │
│    - Discard future-scheduled keys (start_time > now)       │
│    - Keep only keys ready for use                           │
│    - Example: if now = 08:45                                │
│      • Slot 0 (08:43) ✓ ready                               │
│      • Slot 1 (08:44) ✓ ready                               │
│      • Slot 2 (08:48) ✗ future                              │
│      • Slot 3 (08:49) ✗ future                              │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Select Winner: max(start_time) from ready keys           │
│    - Pick the LATEST available key (highest start_time)     │
│    - This ensures "newest key in use" without gaps          │
│    - In example above: Slot 1 (08:44) is elected active    │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. SAK Rekey: If winner ≠ current_active_key                │
│    - Trigger SAK rekey to the elected key                   │
│    - Generate new Session Key (SAK) encryption material     │
│    - Mark old SAK as "previous" (for replay protection)     │
│    - New SAK becomes "latest"                               │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Transmit MKPDU: Broadcast to peer                        │
│    - Include elected CAK CKN (Connectivity Key Name)        │
│    - Include new SAK Key Identifier (KI)                    │
│    - Include SAK AN (Association Number)                    │
│    - Peer must agree on same active CAK to confirm          │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. Peer Confirmation (MKA Participant List)                 │
│    - If peer MKPDU contains same CKN → match! (live)        │
│    - If peer MKPDU contains different CKN → mismatch (wait) │
│    - "Peer list: (1)" = peer confirmed this CAK             │
└─────────────────────────────────────────────────────────────┘
```

---

## Critical: Keychain Slot Ordering by Start-Time

### ❌ WRONG (generation-based ordering):

```
Slot 0: generation=0, start_time=2026-7-28.08:49:00  ← quarta in ordine!
Slot 1: generation=1, start_time=2026-7-28.08:43:00  ← Prima
Slot 2: generation=2, start_time=2026-7-28.08:44:00  ← Seconda
Slot 3: generation=3, start_time=2026-7-28.08:48:00  ← Terza
```

**Problem:**
- MKA sees slot 0 is oldest (08:49), slot 1 has 08:43
- MKA election logic: "which is latest ready?"
- Gets confused when times don't correlate with slot indices
- Falls back to fallback-key because no QKD key "makes sense"
- Tx count skyrockets (MKA indecisive, keeps broadcasting old CAK)

### ✅ CORRECT (chronological start_time ordering):

```
Slot 0: generation=1, start_time=2026-7-28.08:43:00
Slot 1: generation=2, start_time=2026-7-28.08:44:00
Slot 2: generation=3, start_time=2026-7-28.08:48:00
Slot 3: generation=0, start_time=2026-7-28.08:49:00
```

**Benefit:**
- Slot indices now correspond to chronological order
- MKA election: "latest ≤ now" picks the right slot
- SAK rekey happens predictably at scheduled times
- Peer and local agree on active CAK CKN
- Tx count stable, convergence fast

---

## Installation Flow in qkd_onbox.py

### Phase 3A: Sort by Start-Time (NEW FIX)

Before installing keys into keychain slots, **sort entries chronologically**:

```python
# PHASE 3A: Sort entries by start_time chronologically
def parse_start_time_for_sort(entry):
    st = entry.get("start_time", "")
    # Parse to datetime, sort ascending
    return datetime.datetime.strptime(...)

sorted_entries = sorted(entries, key=parse_start_time_for_sort)

log(f"KEYCHAIN INSTALL SORT_BY_START_TIME start_times=[...]", ...)
```

### Phase 3B: Assign Slots by Position (NEW FIX)

Use **position in sorted list**, not generation number:

```python
for idx, entry in enumerate(sorted_entries):  # idx now = 0, 1, 2, 3
    ...
    # WRONG: key_index = int(generation) % 4
    # RIGHT: key_index = idx % 4
    key_index = idx % max_installed_keys()
    
    cli_cmds.append(f"set security ... key {key_index} start-time {start_time}")
```

---

## Example Timeline

**At MX1 08:44:45 (Master role, generating rotations):**

1. **08:44:00** - Generation 1 installed (slot 0, start_time 08:43) by bootstrap
   - MKA picks: slot 0 is latest ready → active
   
2. **08:44:01** - MKA heartbeat: CAK from slot 0 active, CKN = 9771889a5b78...
   - Sends MKPDU with this CKN to peer (MX2)

3. **08:44:02** - Peer MKA receives MKPDU
   - Checks: do I have CAK with CKN = 9771889a5b78?
   - YES → Status: live, Peer list: (1) ✓
   - NO → Status: in-progress, Peer list: (0) ✗

4. **08:44:49** - New batch: generations 3, 4 installed
   - Sorted by start_time → slot 2, slot 3
   - Generation 3 (08:48) → slot 2
   - Generation 4 (08:49) → slot 3

5. **08:48:00** - Time advances, generation 3 start_time arrives
   - MKA election: latest ready key = slot 2 (08:48)
   - SAK rekey → new SAK with CKN from slot 2
   - Transmit new MKPDU to peer

6. **08:48:01** - Peer receives MKPDU with slot 2 CKN
   - Has slot 2 in keychain → Status: live
   - Peer list: (1) ✓ → confirmed!

7. **08:48:02** - Master reads peer state: active_key matches local
   - STRICT SYNC OK → can promote next generation

---

## Configuration Parameters (qkd_policy.yaml)

```yaml
# Rotation cycle
interval_seconds: 60              # New key pair generated every 60s

# Pending key confirmation timing
pending_confirm_grace_seconds: 60  # Wait 60s for peer confirmation
pending_stuck_recovery_seconds: 180 # Drop stuck pending after 180s (3 cycles)

# MKA rekey settings (Junos)
mka_transmit_interval: 2000        # MKPDU every 2 seconds
mka_sak_rekey_interval: 300        # Allow SAK rekey every 300s = 5 min

# Batch mode
batch_enabled: true
key_batch_size: 4                  # 4 keys in rotation always
max_installed_keys: 4              # Junos keychain slots 0-3
```

---

## Troubleshooting: When MKA Stays on Fallback

### Symptom
- `show security mka sessions summary`: primary = "live", fallback = "active"
- Tx on fallback: 1600+ (should be ≤ 100 on active)
- Tx on primary: <100 (not yet active)

### Root Causes

| Issue | Sign | Fix |
|-------|------|-----|
| Slot out-of-order | start_time not ascending by slot | Sort by start_time before install ✓ |
| CKN mismatch | `ckn_match=False` in logs | Ensure peer installed same keys |
| Peer state stale | `STATE RECONCILE NO_ROUTER_MATCH` | Check peer's `show sec mka` command works |
| Pending too old | `pending_age_seconds > recovery_window` | Lower `pending_stuck_recovery_seconds` |
| SAK rekey disabled | `sak_rekey_interval` too high | Set to 300s or lower |

---

## Files Reference

- **Runtime orchestrator**: [artifacts/qkd_onbox.py](../artifacts/qkd_onbox.py) (lines 2824-2950, `install_keychain_batch()`)
- **Policy config**: [config/inventory/qkd_policy.yaml](../config/inventory/qkd_policy.yaml)
- **MKA confirmation logic**: [artifacts/qkd_onbox.py](../artifacts/qkd_onbox.py) (lines 2515-2664, `promote_pending_key_if_mka_confirmed()`)

---

## Related Documentation

- [architecture.md](./architecture.md) - Overall system design
- [lld_ver_334_latest.md](./lld_ver_334_latest.md) - Low-level design details
- [QKD_MACsec_Link_Driven_Refactor_Update.md](./QKD_MACsec_Link_Driven_Refactor_Update.md) - Link-driven model
