# Quantum-Safe MACsec — Speaker Script Short (20 min)

## 0) Objective

Ready-to-read script for a quick video (~20 min) that explains the repo from zero:

- what it does;
- how it is organized;
- how to run operational post-checks with the tools;
- how to read final JSON outputs.

---

## 1) Timeline (20 min)

1. Intro and problem statement (2 min)
2. Repo structure (3 min)
3. Inventory + policy (3 min)
4. Post-check tool T1/T2/FINAL (5 min)
5. Link health + peer-key renewal reports (5 min)
6. Closing and takeaways (2 min)

---

## 2) Ready speaker script

## Chapter 1 — Intro (2 min)

“Hi, in this video we’ll walk through how the Quantum-Safe MACsec repo works in practice.
The project goal is to orchestrate MACsec with QKD keys in a hitless, observable, and repeatable way across MX and ACX devices.”

“By the end of this video you’ll know:
how the repo is structured, how to launch the automated post-check, and how to read JSON reports to quickly understand whether the network is healthy.”

---

## Chapter 2 — Repo structure (3 min)

“Let’s start from the structure:
`artifacts/` contains the on-box runtime script,
`config/inventory/` contains topology and policy,
`tools/` contains operational tooling,
`docs/` contains technical documentation,
`test/` contains automated tests.”

“This separation is important: policy and inventory drive behavior, and tools read those parameters instead of hardcoding timers.”

---

## Chapter 3 — Inventory and policy (3 min)

“Here we have inventory for devices and links, and here we have `qkd_policy` with timing values:
execution interval, key activation interval, grace, batch, and peer key rotation interval.”

“Key point: post-check does not use fixed constants.
If policy changes tomorrow, the tool adapts automatically.”

---

## Chapter 4 — Observational post-check (5 min)

“The main post-check tool is `observe_qkd_rotation.py`.
It works with three snapshots:
T1 baseline,
T2 post-transaction,
FINAL post-activation.”

“First we run the plan, so we can see schedule and windows computed from policy.”

### Demo commands

```bash
tools/observe_qkd_rotation.py --plan
```

“Now we run the full observation.
During waiting stages, we also get a live progress bar/countdown.”

```bash
tools/observe_qkd_rotation.py
```

“The tool collects log snapshots, generates stage reports, and then produces the final semantic comparison.”

---

## Chapter 5 — How to read reports (5 min)

“Let’s open the latest observation.”

```bash
latest="$(ls -1dt logs/qkd_observation_* | head -n1)"
echo "$latest"
```

“First file: link health and attention focus.”

```bash
jq '.attention_required, .color_counts' \
  "$latest/qkd_fleet_comparison_report.json"
```

“Second file: `etsi_peer_view` key rotation.”

```bash
jq '.all_links_rotated_successfully, .missing_peer_renewals_by_device' \
  "$latest/qkd_peer_key_rotation_observation.json"
```

“If one device has 3 MACsec peers, the JSON shows per-peer renewal details.
So you can immediately see which renewals happened and which are missing.”

---

## Chapter 6 — Closing (2 min)

“In summary:
the repo cleanly separates runtime, config, and tooling;
the T1/T2/FINAL post-check reduces transient false positives;
and JSON reports immediately focus operations on real issues and missing peer renewals.”

“If you want a full 60–90 minute deep dive, use the extended script version as well.”

---

## 3) Useful files to show on screen

- `docs/tools/qkd_post_check_observation_tools.md`
- `config/inventory/input/ring_mx_acx_unified_link_driven.yml`
- `config/inventory/qkd_policy.yaml`
- `tools/observe_qkd_rotation.py`
- `tools/qkd_peer_key_rotation_report.py`

---

## 4) Backup Q&A (quick)

- “Why not raw diffs of log folders?”
  - Because logs are append-only and noisy; semantic comparison is more reliable.

- “What if I see degraded at T2 but healthy at FINAL?”
  - That is typically a transient condition, so it is considered recovered.

- “How do I verify peer-key renewals across all peers?”
  - `latest_cycle_peer_renewals` and `missing_peer_renewals_by_device`.
