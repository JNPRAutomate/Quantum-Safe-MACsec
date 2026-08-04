# QKD Pipeline Analytics Tool

Tool: `tools/qkd_pipeline_analytics.py`

Collects end-to-end pipeline timing data from all QKD devices in the inventory and generates an offline HTML report. The primary goal is to answer one operational question:

> **What is the minimum key retention period (TTL) the KME must be configured with?**

The answer comes from measuring how long a key must survive in the KME between when the master fetches it (ENC call) and when the slave retrieves it (DEC call).

---

## Usage

### Collect from all devices and analyze

```bash
python3 tools/qkd_pipeline_analytics.py
```

Reads the default inventory, fetches timing JSONL files from all devices via SCP, and generates `qkd_pipeline_report.html`.

### Re-analyze existing data (no SCP)

```bash
python3 tools/qkd_pipeline_analytics.py --skip-collect
```

Automatically selects the most recent local snapshot. Use this to regenerate the report after the tool is updated.

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--inventory` | `ring_mx_acx_unified_link_driven.yml` | Device inventory YAML |
| `--base-inventory` | `inventory_base.yaml` | Base inventory (SSH user/key) |
| `--output-dir` | `./qkd_timings` | Local directory for collected files |
| `--output` | `qkd_pipeline_report.html` | Output HTML file |
| `--skip-collect` | — | Skip SCP, analyze latest existing snapshot |
| `--remote-path` | `/var/home/etsi_user/logs/pipeline_timing` | Remote log directory |
| `--jobs` | `4` | Parallel SCP collection jobs |
| `--identity-file` | — | SSH private key path override |

---

## Data Source

Timing records are written by `qkd_onbox.py` on each master device after every successful key rotation:

```
/var/home/etsi_user/logs/pipeline_timing/qkd_rolling_pipeline_timing.jsonl
/var/home/etsi_user/logs/pipeline_timing/qkd_batch_pipeline_timing.jsonl
```

Each line is a JSON record:

```json
{
  "timestamp": "2026-08-04 13:45:55.511",
  "device": "sae-001",
  "iface": "et-0/0/0",
  "status": "ok",
  "timings_ms": {
    "master_commit_ms":           "00:01:09:822",
    "master_peer_send_ms":        "00:01:05:429",
    "master_ack_wait_ms":         "00:01:02:200",
    "master_total_enc_to_ack_ms": "00:01:12:096",
    "slave_dec_total_ms":         "00:00:00:130",
    "slave_commit_ms":            "00:00:02:288",
    "slave_total_ms":             "00:00:07:441",
    "slave_elapsed_from_enqueue_ms": "00:00:58:822"
  }
}
```

All durations use `HH:MM:SS:mmm` format (hours, minutes, seconds, milliseconds).

---

## Understanding the Raw JSONL Fields

### ⚠️ Master fields are cumulative timestamps, not individual durations

This is the most important thing to understand when reading the raw log or the report.

All four master fields are measured **at the same moment** (when the ACK is received) but from **different start points**. They are cumulative elapsed times that all end at ACK received — not individual step durations.

```
enc_batch_start_ms ──────────────────────────────────── ACK received
                   │                                     │
                   └──── master_total_enc_to_ack_ms = 72s

local_install_start_ms ────────────────────────── ACK received
                       │                           │
                       └── master_commit_ms = 69.8s   ⚠ name misleading: also includes SCP + ACK wait!

peer_send_start_ms ─────────────────────── ACK received
                   │                       │
                   └── master_peer_send_ms = 65.4s   ⚠ also includes ACK wait!

ack_wait_start_ms ─────────── ACK received
                  │            │
                  └── master_ack_wait_ms = 62.2s   ✓ true duration (ACK poll only)
```

### Computing actual step durations (deltas)

To get the real duration of each individual step, subtract adjacent cumulative values:

| Step | Formula | Example |
|------|---------|---------|
| **ENC** (KME HTTP call) | `master_total − master_commit_ms` | 72 − 69.8 = **2.2s** |
| **COMMIT** (Junos keychain install) | `master_commit_ms − master_peer_send_ms` | 69.8 − 65.4 = **4.4s** |
| **SCP send** (upload to slave) | `master_peer_send_ms − master_ack_wait_ms` | 65.4 − 62.2 = **3.2s** |
| **ACK poll** (wait for slave ACK file) | `master_ack_wait_ms` (no delta needed) | **62.2s** |
| **TOTAL** | `master_total_enc_to_ack_ms` (no delta needed) | **72s** |

The report computes and displays these deltas automatically. The "Computed as (JSONL delta)" column in the HTML table shows the exact formula for each row.

### Slave fields are already true durations

Slave-side fields are measured from the moment the slave script starts processing, so they are individual durations — no delta computation needed:

| Field | What it measures | t=0 |
|-------|-----------------|-----|
| `slave_dec_total_ms` | KME HTTP GET calls to decrypt all key_ids | slave script start |
| `slave_commit_ms` | Junos netconf commit on slave | after DEC complete |
| `slave_total_ms` | DEC + COMMIT + state write + ACK write | slave script start |
| `slave_elapsed_from_enqueue_ms` | From master SCP write to slave ACK written | master SCP write time (`created_at` in envelope) |

Note: `slave_elapsed_from_enqueue_ms` has a different t=0 than the others — it starts from when the master wrote the SCP file, so it includes network transit time. It is used in the ENC→DEC formula.

---

## The Master Pipeline (sequential, blocking)

```
enc_batch_start
      │
      ├──[ENC ~2s]──► local_install_start
      │
      ├──[COMMIT ~4s]──► peer_send_start
      │
      ├──[SCP send ~3s]──► ack_wait_start
      │
      └──[ACK poll ~62s]──► ACK received
```

The ACK poll (~62s) is the **rotation interval** — the master polls every N seconds for the slave's written ACK file. It dominates the total time but is **not** part of the KME retention window (the slave has already called DEC before this wait ends).

---

## KME TTL Budget — The Critical Metric

### What to ask the KME operator

> *"How long does the KME keep a key after it is first fetched?"*

That retention period must be ≥ the ENC→DEC time measured by this tool.

### Formula

```
ENC→DEC = master_total_enc_to_ack − slave_elapsed_from_enqueue + slave_dec_total
         = 72s − 59s + 0.13s
         ≈ 13 seconds
```

This works because `slave_elapsed_from_enqueue` starts from the master SCP write time — subtracting it cancels out the SCP upload time and the ACK poll wait, leaving only the window from ENC call to slave DEC call.

### Why the TOTAL (72s) is not the answer

The 72s total includes ~62s of ACK polling, which happens **after** the slave has already decrypted. The key only needs to exist in the KME for the ~13s window shown above.

### Recommended KME TTL

```
Recommended TTL = ceil(ENC→DEC 99th percentile) + 1s safety margin
```

| ENC→DEC 99% | Verdict | Action |
|-------------|---------|--------|
| < 8s | ✅ Green | Current KME TTL is adequate |
| 8–10s | ⚠️ Orange | Review KME TTL, monitor closely |
| > 10s | 🔴 Red | Increase KME TTL immediately to avoid HTTP 404 failures |

### Example from real data

```
ENC→DEC Median: ~11s
ENC→DEC 99%:    ~13s
→ Recommended KME TTL: ≥ 14 seconds
```

---

## HTML Report Sections

1. **Pipeline Summary** — Total records, HTTP 200/404 counts, success rate
2. **Master-Side Timing** — Per-step statistics with "What it measures" and "Computed as (JSONL delta)" columns
   - Includes two SVG diagrams: the step timeline and the raw-field waterfall
3. **Slave-Side Timing** — Per-step statistics with "JSONL field (raw)" column
   - Includes SVG diagram showing slave t=0 and `slave_elapsed_from_enqueue` span
4. **KME TTL Budget** — ENC→DEC statistics with combined master+slave timeline SVG
   - Red bracket showing the actual key retention window
   - Recommended TTL highlighted in amber banner
5. **KME Key Validity** — HTTP 200 vs 404 breakdown with pass/warn/fail verdict

---

## Related Files

- `tools/collect_device_logs.py` — SCP collection backend used internally
- `artifacts/qkd_onbox.py` — Writes timing records on-device (see `write_pipeline_timing_record()`)
- `docs/qkd/kme_key_retention_and_commit_ordering.md` — KME TTL design rationale
- `docs/qkd/logging_and_customer_reporting.md` — Logging architecture overview
