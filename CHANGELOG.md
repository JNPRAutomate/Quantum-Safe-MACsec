# Changelog

## v3.2.2 (In Development)

### Removed

- Removed legacy `Profile` dependency from `qkd.py`.
- Removed profiling hooks (`prof.start()`, `prof.stop()`, `prof.close()`).

### Impact

- No functional changes.
- QKD key retrieval workflow unchanged.
- MACsec configuration workflow unchanged.
- On-box and off-box execution logic unchanged.

### Removed

- Removed unused imports:
  - Lock
  - hashlib
  - subprocess
  - ipaddress

