# Quantum-Safe MACsec — Video Walkthrough Script (Full, from Zero)

## 1) Video objective

Explain the `Quantum-Safe-MACsec` repository end-to-end:

- context (QKD + MACsec + KME);
- project structure;
- runtime flows;
- operational tooling;
- troubleshooting and post-check workflow.

Target audience:

- engineering team;
- operations / validation;
- technical stakeholders.

Recommended full duration: **60–90 minutes**.

---

## 2) Agenda (chapters)

1. Intro: problem and solution (5 min)
2. Repository structure and components (8 min)
3. Inventory and runtime policy (8 min)
4. Deploy lifecycle (8 min)
5. On-box QKD/MACsec runtime (15 min)
6. Hitless rolling N-2 model (10 min)
7. Logging, snapshots, and link health reports (10 min)
8. Peer SSH key rotation (`etsi_peer_view`) (8 min)
9. Observational post-check T1/T2/FINAL (10 min)
10. Real troubleshooting + best practices (8 min)

---

## 3) Spoken script (speaker notes)

### Chapter 1 — Intro

“In this video we walk through the Quantum-Safe MACsec repository from zero.  
The goal is to automate MACsec encryption with QKD-derived key material while maintaining service continuity and full observability.”

### Chapter 2 — Repository map

Show:

- `artifacts/` (canonical on-box script)
- `config/inventory/`
- `tools/`
- `docs/`
- `test/`

Message:

“This repo clearly separates configuration, operational runtime, tooling, and technical documentation.”

### Chapter 3 — Inventory + policy

Show files:

- `config/inventory/input/ring_mx_acx_unified_link_driven.yml`
- `config/inventory/qkd_policy.yaml`

Message:

“Operational timers are not hardcoded in tools: they are read from policy.”

### Chapter 4 — Deploy lifecycle

Show doc:

- `docs/qkd/qkd_deploy_phases.md`

Message:

“Deploy prepares users, keys, certificates, config, and runtime in a repeatable way.”

### Chapter 5 — On-box runtime

Show:

- `artifacts/qkd_onbox.py`
- state fields `active_key_id`, `pending_key_id`, `next_start_time`

Message:

“Runtime executes on each tick, reconciles software state with router state, and applies safety checks.”

### Chapter 6 — Hitless N-2

Show doc:

- `docs/qkd/hitless_rolling_keyring_ver3.3.2.1.md`

Message:

“With ring size N=4, the system protects active+pending and replaces consumed N-2 slots.”

### Chapter 7 — Log collection + link health

Show tools:

- `tools/collect_device_logs.py`
- `tools/qkd_link_rotation_report.py`

Message:

“Link health is semantic-driven: raw log copy alone is not enough; bilateral evidence is required.”

### Chapter 8 — Peer SSH key rotation

Show tool:

- `tools/qkd_peer_key_rotation_report.py`

Message:

“`etsi_peer_view` key rotation is tracked per cycle, per peer, and per link.”

### Chapter 9 — Post-check T1/T2/FINAL

Show tool:

- `tools/observe_qkd_rotation.py`

Live commands:

```bash
tools/observe_qkd_rotation.py --plan
tools/observe_qkd_rotation.py
```

Message:

“T1/T2/FINAL reduces false positives caused by transient snapshots.”

### Chapter 10 — Troubleshooting / closing

Show:

- `attention_required` in JSON reports
- `missing_peer_renewals_by_device`

Message:

“Operational focus is on persistent final problems, not temporary transactional noise.”

---

## 4) Demo command list (copy/paste)

```bash
# 1) Observation plan
tools/observe_qkd_rotation.py --plan

# 2) Full post-check run
tools/observe_qkd_rotation.py

# 3) Latest observation
latest="$(ls -1dt logs/qkd_observation_* | head -n1)"
echo "$latest"

# 4) Links requiring attention
jq '.attention_required' "$latest/qkd_fleet_comparison_report.json"

# 5) Missing peer-key renewals
jq '.missing_peer_renewals_by_device' \
  "$latest/qkd_peer_key_rotation_observation.json"
```

---

## 5) Visual assets to show

1. Topology inventory (11 devices / 16 links)
2. Ring timeline (active/pending/future)
3. Snapshot timeline T1/T2/FINAL
4. JSON triage examples:
   - `attention_required`
   - `missing_peer_renewals_by_device`

---

## 6) Quick end-of-video Q&A

- “Why not use raw log diffs?”
  - Because logs are append-only and noisy; semantic comparison is more reliable.

- “Does HEALTHY always mean the latest key is already active?”
  - Not always; activation is distinct from transaction completion.

- “How do I verify `etsi_peer_view` renewal for all peers?”
  - `latest_cycle_peer_renewals` + `missing_peer_renewals_by_device`.

---

## 7) Short version (15–20 min)

For a short version, keep only:

1. repo map
2. qkd_policy + inventory
3. observe tool (`--plan` + run)
4. quick interpretation of final JSON reports
5. troubleshooting focus
