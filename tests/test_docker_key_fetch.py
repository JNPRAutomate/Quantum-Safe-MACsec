import pytest

from lib.docker.qkd.docker_key_fetch import render_key_fetch
from lib.docker.qkd import docker_phiotx_lifecycle as lifecycle


def fleet():
    return {
        name: {"phiotx": {"container": container}, "links": [{"peer": peer}]}
        for name, container, peer in [
            ("EVO1", "phiotx01", "EVO2"), ("EVO2", "phiotx02", "EVO1")
        ]
    }


def settings():
    return {
        "qkd_simulator": {"key_rate": 0.1},
        "qkd_sources": {
            name: {"addr": "192.0.2.10", "port": port, "pki": "qkd"}
            for name, port in [("EVO1", 8441), ("EVO2", 8442)]
        },
    }


def test_only_primary_fetches_for_pair():
    devices = fleet()
    primary = render_key_fetch("EVO1", devices["EVO1"], devices, settings())
    remote = render_key_fetch("EVO2", devices["EVO2"], devices, settings())
    assert remote == {"key_fetch": {"sources": []}}
    source = primary["key_fetch"]["sources"][0]
    assert source["proto"] == "etsi"
    assert source["local"]["qkd"]["port"] == 8441
    assert source["remote"]["qkd"]["port"] == 8442
    assert source["remote"]["name"] == "phiotx02"


def test_missing_remote_endpoint_fails():
    devices = fleet()
    config = settings()
    del config["qkd_sources"]["EVO2"]
    with pytest.raises(ValueError, match="EVO2"):
        render_key_fetch("EVO1", devices["EVO1"], devices, config)


def test_qkd_pool_must_be_nonempty_on_every_node(monkeypatch):
    prepared = {"EVO1": ({}, {"container": "phiotx01", "peers": [{"name": "phiotx02"}]})}
    monkeypatch.setattr(lifecycle, "_exec", lambda *args: type("R", (), {"stdout": "Keys:0\nQ Pool: 0 rcv nvr ago"})())
    with pytest.raises(lifecycle.PhiotxLifecycleError, match="EVO1"):
        lifecycle.wait_for_qkd_pool(prepared, timeout=0)
    monkeypatch.setattr(lifecycle, "_exec", lambda *args: type("R", (), {"stdout": "[phiotx01]&>&phiotx02: 2 [1s]"})())
    lifecycle.wait_for_qkd_pool(prepared, timeout=0)
