# QKD Logging and Customer Reporting

## Problem

Raw `qkd_debug*.log` files are operationally detailed but noisy for customer-facing status reviews.

## Implemented solution

Two utilities now support customer summary generation:

- `lib/qkd/log_summary.py`
- `test/generate_qkd_customer_summary.sh`

## Summary metrics produced

- rotations started/completed/skipped
- key lifecycle evidence (`ENC OK`, `DEC OK`, `MKA KEY CONFIRMED`, promotions)
- per-interface health counters
- SSH and MKA failure indicators
- sampled error lines

## Recommended workflow

Because some Junos environments restrict on-box Python execution:

1. copy `qkd_debug*.log` off-box
2. generate summary on HelperVM/Linux host
3. attach generated summary to customer report

Example:

```bash
mkdir -p /tmp/qkd_logs
scp admin@<mx-ip>:/var/tmp/qkd_debug*.log /tmp/qkd_logs/
python3 lib/qkd/log_summary.py \
  --logs /tmp/qkd_logs/qkd_debug*.log \
  --output /tmp/qkd_customer_summary.log \
  --title "Customer QKD Health Summary"
```

Wrapper form:

```bash
test/generate_qkd_customer_summary.sh /tmp/qkd_logs /tmp
```
