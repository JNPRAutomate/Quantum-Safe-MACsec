# QKD Pipeline Analytics Tool

Tool: `tools/qkd_pipeline_analytics.py`

Collects end-to-end pipeline timing data from all QKD devices in the inventory and generates an offline HTML or JSON report. The primary goal is to verify that **≥99% of key fetches succeed within the KME TTL window** (i.e., decryption receives HTTP 200, not HTTP 404).

---

## What It Measures

Each key rotation cycle generates a timing record on the master device. The tool collects and analyzes these records across all devices.

### Master-side timings

| Field | Description |
|-------|-------------|
| **Encryption** | Time for master to call KME and encrypt the key batch |
| **Commit (install keys)** | Time to commit MACsec keys to the Junos keychain |
| **Peer Send (SCP)** | Time to send the encrypted batch to the slave device |
| **ACK Wait** | Time waiting for the slave to acknowledge |
| **Total (ENC→ACK)** | End-to-end time from encryption start to ACK received |

### Slave-side timings

| Field | Description |
|-------|-------------|
| **Decryption** | Time for slave to call KME and decrypt the key batch |
| **Commit** | Time to commit decrypted keys to the Junos keychain |
| **Total Processing** | Total slave processing time |
| **Elapsed (enqueue→ACK)** | Total time from batch received to ACK written |

---

## KME TTL Budget — The Critical Metric

The most important derived metric is: **how long must a key survive in KME** between the moment the master fetches it (ENC) and the moment the slave retrieves it (DEC)?

```
ENC → DEC time = master_total_enc_to_ack − slave_elapsed_from_enqueue + slave_dec_time
```

The report includes a dedicated section for this showing Min/Avg/Median/95%/99%/Max with a color-coded verdict:

| Worst case (99%) | Verdict |
|------------------|---------|
| < 8 seconds | ✅ Green — Well within KME TTL |
| 8–10 seconds | ⚠️ Orange — Close to boundary |
| > 10 seconds | 🔴 Red — Expect HTTP 404 failures |

**Example from real data:**
```
master_total  ≈ 72s
slave_elapsed ≈ 59s
slave_dec     ≈ 0.13s
──────────────────────
ENC → DEC     ≈ 13 seconds  ← exceeds 10s KME TTL!
```

This tells you the KME TTL needs to be at least 13 seconds, or the master commit must be faster.



- **HTTP 200** — Key found and valid (success)
- **HTTP 404** — Key not found, expired or already deleted (failure)

The KME deletes keys after a configured TTL (typically 10+ seconds). If the full pipeline takes too long, keys may expire before decryption.

**Goal:** ≥99% of operations must complete within the KME TTL window.

---

## Usage

### Collect from all devices and analyze

```bash
python3 tools/qkd_pipeline_analytics.py
```

Uses the default inventory (`ring_mx_acx_unified_link_driven.yml`) and base inventory to discover all devices, fetches timing files via SCP, and generates `qkd_pipeline_report.html`.

### Custom inventory or output

```bash
python3 tools/qkd_pipeline_analytics.py \
  --inventory config/inventory/input/my_inventory.yml \
  --output report.html
```

### Re-analyze existing data (skip SCP)

```bash
python3 tools/qkd_pipeline_analytics.py --skip-collect
```

Automatically selects the most recent snapshot in `--output-dir`.

### JSON output

```bash
python3 tools/qkd_pipeline_analytics.py --json --output timing_stats.json
```

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--inventory` | `ring_mx_acx_unified_link_driven.yml` | Device inventory YAML |
| `--base-inventory` | `inventory_base.yaml` | Base inventory (for SSH user) |
| `--output-dir` | `./qkd_timings` | Local directory for collected files |
| `--output` | `qkd_pipeline_report.html` | Output report file |
| `--json` | — | Output JSON instead of HTML |
| `--skip-collect` | — | Skip SCP, analyze latest existing snapshot |
| `--remote-path` | `/var/home/etsi_user/logs/pipeline_timing` | Remote directory on devices |
| `--jobs` | `4` | Parallel SCP collection jobs |
| `--user` | from base inventory | SSH user override |
| `--identity-file` | — | SSH private key path |

---

## Data Source

Timing records are written by `qkd_onbox.py` on each device to:

```
/var/home/etsi_user/logs/pipeline_timing/qkd_rolling_pipeline_timing.jsonl
/var/home/etsi_user/logs/pipeline_timing/qkd_batch_pipeline_timing.jsonl
```

Each record is a JSON line with the following structure:

```json
{
  "timestamp": "2026-08-04 13:45:55.511",
  "device": "sae-001",
  "iface": "et-0/0/0",
  "operation": "ROLLING_REPLACEMENT",
  "ack_id": "8ca21fee...",
  "status": "ok",
  "timings_ms": {
    "master_enc_total_ms": "00:00:00:123",
    "master_commit_ms": "00:01:09:822",
    "master_peer_send_ms": "00:01:05:429",
    "master_ack_wait_ms": "00:01:02:200",
    "master_total_enc_to_ack_ms": "00:01:12:096",
    "slave_dec_total_ms": "00:00:00:130",
    "slave_commit_ms": "00:00:02:288",
    "slave_total_ms": "00:00:07:441",
    "slave_elapsed_from_enqueue_ms": "00:00:58:822"
  }
}
```

All durations are in `HH:MM:SS:mmm` format (hours, minutes, seconds, milliseconds).

---

## Interpreting the Report

### HTML Report sections

1. **Pipeline Summary** — Total records, success/fail counts, KME success rate
2. **Master-Side Timing** — Statistics table with Min/Avg/Median(50%)/95%/99%/Max for each operation (in `MM:SS.mmm` format)
3. **Slave-Side Timing** — Same statistics for slave-side operations
4. **KME Key Validity** — Success rate verdict with recommendations

### Example output (real data)

```
Master Commit (install keys): Avg 01:09.728, Max 01:09.822
Slave Decryption:             Avg 00.130,   Max 00.191
KME Success Rate:             100%
```

**Interpretation:** Master commit (~69s) is the main bottleneck; slave decryption is fast (<200ms). All keys were still valid in KME at time of decryption.

---

## Related Files

- `tools/collect_device_logs.py` — Used internally for SCP collection
- `artifacts/qkd_onbox.py` — Writes timing records on-device
- `docs/qkd/logging_and_customer_reporting.md` — Logging architecture overview
