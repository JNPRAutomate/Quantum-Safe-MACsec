"""Render one primary PhioTX key-fetch job per directly connected pair."""

import ipaddress


def qkd_connection(name, phiotx):
    sources = phiotx.get("qkd_sources") or {}
    if name not in sources:
        raise ValueError(f"Missing QKD simulator endpoint for {name}")
    source = sources[name]
    address = str(ipaddress.ip_address(source["addr"]))
    port = int(source["port"])
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid QKD endpoint port for {name}: {port}")
    if source.get("pki") != "qkd":
        raise ValueError(f"QKD endpoint for {name} requires the qkd mTLS store")
    return {"addr": address, "port": port, "pki": "qkd"}


def render_key_fetch(name, device, devices, phiotx):
    rate = float((phiotx.get("qkd_simulator") or {}).get("key_rate", 0.1))
    if not 0.001 <= rate <= 111:
        raise ValueError("QKD key_rate must be between 0.001 and 111 keys/second")
    local = qkd_connection(name, phiotx)
    sources = []
    seen = set()
    by_container = {
        record["phiotx"]["container"]: device_name
        for device_name, record in devices.items()
        if record.get("managed") is not False
    }
    explicit = (device.get("phiotx") or {}).get("peers")
    if explicit is None and (device.get("phiotx") or {}).get("peer"):
        explicit = [device["phiotx"]["peer"]]
    peers = explicit if explicit is not None else [
        link["peer"] for link in device.get("links", [])
    ]
    for peer in peers:
        reference = peer["name"] if isinstance(peer, dict) else peer
        remote_name = reference if reference in devices else by_container.get(reference)
        if remote_name is None:
            raise ValueError(f"Unknown key-fetch peer {reference!r} for {name}")
        remote = qkd_connection(remote_name, phiotx)
        if remote_name <= name or remote_name in seen:
            continue
        seen.add(remote_name)
        sources.append({
            "proto": "etsi", "key_rate": rate,
            "local": {"qkd": local},
            "remote": {"name": devices[remote_name]["phiotx"]["container"], "qkd": remote},
        })
    return {"key_fetch": {"sources": sources}}
