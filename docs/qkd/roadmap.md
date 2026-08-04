# QKD On-Box Roadmap

## Planned architecture evolution (not implemented yet)

- Refactor [qkd_onbox.py](</Users/aterren/Lavoro 2026/quantum 2026/MACSEC3.3.3.worktrees/qkd-troubleshooting-next-slot-fix/artifacts/qkd_onbox.py>) into classes with clear public/private responsibilities.
- Introduce explicit stateful components (for example: key pipeline manager, peer transport manager, commit manager) with typed interfaces.
- Prepare for parallel execution of independent stages (ENC, DEC, commit, transport, ACK processing) while preserving ordering guarantees and safety gates for MACsec activation.
