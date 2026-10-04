"""Resolve application key generation independently of Hive transport PQC."""

import copy
import json

MODES = ("bulk", "pqc", "hybrid")


def resolve_keygen(phiotx, requested=None, state_path=None):
    settings = copy.deepcopy(phiotx)
    selected = requested or settings.get("keygen_mode")
    if selected is None and state_path is not None and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        selected = state["mode"]
    existing = (settings.get("etsi") or {}).get("keygen_method", "bulk")
    if selected is None:
        if existing == "bulk":
            selected = "bulk"
        elif isinstance(existing, str) and existing.startswith("pqc("):
            selected = "pqc"
        elif isinstance(existing, str) and existing.startswith("hybrid("):
            selected = "hybrid"
        else:
            raise ValueError(f"Unsupported ETSI keygen_method: {existing!r}")
    if selected not in MODES:
        raise ValueError(f"Unsupported key-generation mode: {selected!r}")
    algorithm = settings.get("keygen_algorithm", settings.get("pqc", "ML-KEM-1024"))
    if selected != "bulk" and algorithm != "ML-KEM-1024":
        raise ValueError("PQC/hybrid application generation currently supports ML-KEM-1024 only")
    if selected != "bulk" and settings.get("pqc") != "ML-KEM-1024":
        raise ValueError("PQC/hybrid requires the ML-KEM-1024 peer provisioning profile")
    if selected == "hybrid" and not settings.get("qkd_simulator"):
        raise ValueError("Hybrid requires explicit phiotx.qkd_simulator configuration")
    method = "bulk" if selected == "bulk" else f"{selected}({algorithm})"
    settings["keygen_mode"] = selected
    settings["etsi"] = {**(settings.get("etsi") or {}), "keygen_method": method}
    return settings


def save_keygen(phiotx, state_path):
    state_path.write_text(json.dumps({
        "mode": phiotx["keygen_mode"],
        "method": phiotx["etsi"]["keygen_method"],
    }, indent=2) + "\n", encoding="utf-8")
