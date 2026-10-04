"""Offline tests for the external hybrid QKD simulator (N KMEs + PostgreSQL)."""

from pathlib import Path
from types import SimpleNamespace
import json
import shlex
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.docker.qkd import docker_hybrid as hybrid
from lib.docker.qkd import docker_hybrid_pki as hybrid_pki
from lib.docker.qkd import docker_phiotx_lifecycle as lifecycle

INVENTORY = ROOT / "config" / "inventory" / "input" / "docker_evo_lab.yaml"


def _devices():
    return {
        "EVO1": {
            "ip": "10.38.97.218", "managed": True,
            "phiotx": {"container": "phiotx01", "oob_ip": "10.38.112.10"},
            "links": [{"peer": "EVO2"}],
        },
        "EVO2": {
            "ip": "10.38.97.228", "managed": True,
            "phiotx": {"container": "phiotx02", "oob_ip": "10.38.112.11"},
            "links": [{"peer": "EVO1"}],
        },
    }


def _phiotx(**overrides):
    raw = {
        "host": "10.38.98.181", "network": "qkd_net",
        "source_dir": "/srv/etsi", "project": "docker-qkd-hybrid",
        "port": 8443, "key_rate": 0.1, "postgres_ip": "10.38.112.20",
        "kme_ips": {"EVO1": "10.38.112.21", "EVO2": "10.38.112.22"},
    }
    raw.update(overrides)
    return {"qkd_simulator": raw}


def test_lab_inventory_simulator_block_is_valid():
    inventory = yaml.safe_load(INVENTORY.read_text())
    raw = inventory["phiotx"]["qkd_simulator"]
    assert set(raw) == hybrid.FIELDS
    assert raw["postgres_ip"] == "10.38.112.20"
    assert raw["kme_ips"] == {"EVO1": "10.38.112.21", "EVO2": "10.38.112.22"}


def test_settings_build_one_kme_per_phiotx_and_lexical_primary():
    settings = hybrid.resolve_settings(_phiotx(), _devices())

    assert settings["pairs"] == [("EVO1", "EVO2")]
    assert settings["deploy_dir"] == Path("/srv/etsi") / hybrid.DEPLOY_SUBDIR
    assert settings["nodes"]["EVO2"] == {
        "container": "phiotx02", "addr": "10.38.112.22", "service": "kme-phiotx02",
    }
    assert hybrid.qkd_sources(settings) == {
        "EVO1": {"addr": "10.38.112.21", "port": 8443, "pki": "qkd"},
        "EVO2": {"addr": "10.38.112.22", "port": 8443, "pki": "qkd"},
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"postgres_ip": "10.38.112.21"}, "unique"),
        ({"postgres_ip": "10.38.98.181"}, "unique"),
        ({"postgres_ip": "10.38.112.10"}, "already used in inventory"),
        ({"kme_ips": {"EVO1": "10.38.112.21"}}, "exactly one KME"),
        ({"project": "andrea"}, "docker-qkd-"),
        ({"source_dir": "relative"}, "absolute"),
        ({"port": 0}, "port"),
    ],
)
def test_settings_reject_unsafe_addresses_and_names(overrides, message):
    with pytest.raises(ValueError, match=message):
        hybrid.resolve_settings(_phiotx(**overrides), _devices())


def test_settings_reject_unknown_fields():
    with pytest.raises(ValueError, match="unknown=\\['extra'\\]"):
        hybrid.resolve_settings(_phiotx(extra=1), _devices())


def _openssl(*args):
    return subprocess.run(["openssl", *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    settings = hybrid.resolve_settings(_phiotx(), _devices())
    base = tmp_path_factory.mktemp("pki")
    return base, hybrid_pki.build_hybrid_pki(settings["nodes"], base_dir=base)


def test_hierarchical_pki_chains_and_usages(pki):
    base, paths = pki
    evo1 = paths["EVO1"]

    for leaf, trust in (
        (evo1["server_chain"], evo1["client_trust"]),
        (evo1["client_crt"], evo1["server_trust"]),
    ):
        verify = subprocess.run(
            ["openssl", "verify", "-CAfile", str(trust), str(leaf)],
            capture_output=True, text=True,
        )
        assert verify.returncode == 0, verify.stdout + verify.stderr

    server = _openssl("x509", "-in", str(evo1["server_chain"]), "-noout", "-text")
    client = _openssl("x509", "-in", str(evo1["client_crt"]), "-noout", "-text")
    assert "TLS Web Server Authentication" in server
    assert "IP Address:10.38.112.21" in server
    assert "TLS Web Client Authentication" in client
    assert "CN=phiotx01" in _openssl("x509", "-in", str(evo1["client_crt"]), "-noout", "-subject").replace(" ", "")
    assert len(evo1["client_cas"]) == 4
    assert oct(Path(evo1["client_key"]).stat().st_mode & 0o777) == "0o600"


class FakeDocker:
    def __init__(self, containers=(), neighbours=()):
        self.containers = list(containers)
        self.neighbours = set(neighbours)
        self.calls = []

    def __call__(self, argv, check=True, timeout=120):
        self.calls.append(argv)
        out = ""
        if argv[:3] == ["docker", "ps", "-aq"]:
            if "--filter" in argv:
                out = "\n".join(c["Id"] for c in self.containers if c["mine"])
            else:
                out = "\n".join(c["Id"] for c in self.containers)
        elif argv[:2] == ["docker", "inspect"]:
            out = json.dumps([c["inspect"] for c in self.containers if c["Id"] in argv])
        elif argv[:3] == ["ip", "neigh", "show"]:
            if argv[3] in self.neighbours:
                out = f"{argv[3]} dev eth0 lladdr 54:04:0a:26:70:15 REACHABLE"
        elif argv[:3] == ["docker", "volume", "ls"]:
            out = "docker-qkd-hybrid_pgdata"
        return SimpleNamespace(returncode=0, stdout=out, stderr="")


def _container(cid, name, address, mine):
    labels = {hybrid.OWNER_LABEL: "docker-qkd-hybrid"} if mine else {}
    return {
        "Id": cid, "mine": mine,
        "inspect": {
            "Name": f"/{name}", "Config": {"Labels": labels},
            "NetworkSettings": {"Networks": {"qkd_net": {"IPAddress": "", "IPAMConfig": {"IPv4Address": address}}}},
        },
    }


def test_conflicts_detect_foreign_stopped_container():
    fake = FakeDocker([_container("a1", "andrea-kme01", "10.38.112.21", False)])
    simulator = hybrid.HybridSimulator(hybrid.resolve_settings(_phiotx(), _devices()), run=fake)
    with pytest.raises(hybrid.HybridError, match="andrea-kme01"):
        simulator.check_conflicts()


def test_conflicts_detect_live_lan_host_but_accept_owned_addresses():
    owned = [_container("o1", "docker-qkd-hybrid-postgres", "10.38.112.20", True)]
    fake = FakeDocker(owned, neighbours={"10.38.112.22"})
    simulator = hybrid.HybridSimulator(hybrid.resolve_settings(_phiotx(), _devices()), run=fake)
    with pytest.raises(hybrid.HybridError, match="10.38.112.22 answers"):
        simulator.check_conflicts()
    pinged = [argv[-1] for argv in fake.calls if argv[0] == "ping"]
    assert "10.38.112.20" not in pinged

    fake.neighbours.clear()
    simulator.check_conflicts()


def test_render_writes_static_ips_labels_and_private_secrets(tmp_path, pki):
    _, paths = pki
    settings = hybrid.resolve_settings(_phiotx(source_dir=str(tmp_path)), _devices())
    (tmp_path / hybrid.DEPLOY_SUBDIR).mkdir()
    simulator = hybrid.HybridSimulator(settings, run=FakeDocker())

    compose = yaml.safe_load(simulator.render(paths).read_text())
    services = compose["services"]
    assert compose["networks"] == {"qkd_net": {"external": True}}
    assert services["postgres"]["networks"]["qkd_net"]["ipv4_address"] == "10.38.112.20"
    kme = services["kme-phiotx01"]
    assert kme["networks"]["qkd_net"]["ipv4_address"] == "10.38.112.21"
    assert kme["labels"][hybrid.DEVICE_LABEL] == "EVO1"
    assert kme["environment"]["ETSI_014_REF_IMPL_PORT_NUM"] == "8443"
    assert kme["read_only"] is True and kme["cap_drop"] == ["ALL"]
    deploy = tmp_path / hybrid.DEPLOY_SUBDIR
    for secret in ("postgres.env", "kme.env", "certs/kme-phiotx01/server.key"):
        assert oct((deploy / secret).stat().st_mode & 0o777) == "0o600"
    password = (deploy / "postgres.env").read_text()
    simulator.render(paths)
    assert (deploy / "postgres.env").read_text() == password


def test_cleanup_uses_labels_and_never_touches_network(tmp_path):
    deploy = tmp_path / hybrid.DEPLOY_SUBDIR
    deploy.mkdir()
    fake = FakeDocker([_container("o1", "docker-qkd-hybrid-postgres", "10.38.112.20", True)])
    hybrid.HybridSimulator(
        {"project": "docker-qkd-hybrid", "deploy_dir": deploy}, run=fake,
    ).cleanup()

    assert ["docker", "rm", "-f", "o1"] in fake.calls
    assert ["docker", "volume", "rm", "docker-qkd-hybrid_pgdata"] in fake.calls
    assert not any("network" in argv for argv in fake.calls)
    assert not deploy.exists()
    assert tmp_path.exists()


def test_attach_qkd_pki_adds_identity_with_four_cas(pki):
    _, paths = pki
    bundles = {"EVO1": {"identities": {}}, "EVO2": {"identities": {}}}
    hybrid.attach_qkd_pki(bundles, paths)
    identity = bundles["EVO1"]["identities"]["qkd"]
    assert identity["store"] == "qkd" and len(identity["cas"]) == 4


def test_install_pki_passes_every_hierarchical_ca(tmp_path, monkeypatch):
    cas = []
    for index in range(4):
        path = tmp_path / f"ca{index}.pem"
        path.write_text(f"ca-{index}")
        cas.append(path)
    key, crt, ca = tmp_path / "q.key", tmp_path / "q.crt", tmp_path / "ca.pem"
    for path in (key, crt, ca):
        path.write_text(path.name)
    bundle = {"ca": ca, "identities": {"qkd": {"store": "qkd", "key": key, "crt": crt, "cas": cas}}}
    commands, pushed = [], []

    monkeypatch.setattr(lifecycle, "_push_files", lambda _d, t: pushed.extend(t))
    monkeypatch.setattr(lifecycle, "_docker", lambda _d, c, _w, **_k: commands.append(c))
    monkeypatch.setattr(lifecycle, "_run", lambda _d, c, _w, **_k: commands.append(c))
    monkeypatch.setattr(lifecycle, "_exec", lambda _d, _c, c, _w, **_k: commands.append(c))

    lifecycle.install_pki({"name": "EVO1"}, {"container": "phiotx01"}, bundle)

    install = next(c for c in commands if c.startswith("tx_install_crt"))
    assert shlex.split(install).count("-ca") == 4
    assert {Path(source) for source, _ in pushed} >= set(cas)


@pytest.mark.parametrize(("listed", "removed"), [("layers:\n  650-key-fetch\n", True), ("layers:\n  700-etsi-bulk\n", False)])
def test_stale_key_fetch_layer_removed_only_when_present(monkeypatch, listed, removed):
    commands = []

    def execute(_device, _container, command, _what, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=listed if command.endswith("-list") else "", stderr="")

    monkeypatch.setattr(lifecycle, "_exec", execute)
    assert lifecycle.remove_layer({}, {"container": "phiotx01"}, lifecycle.LAYER_KEY_FETCH) is removed
    assert ("tx_install_cf -y -del -layer 650-key-fetch" in commands) is removed


def test_pqc_public_key_fetch_retries_only_transient_invalid_peer(monkeypatch):
    outputs = [
        "Error: PUT /api/v1/ML-KEM-1024/pqc_pubkey: 400 (invalid peer)",
        "stored PQC pub key",
    ]
    calls = []

    def execute(_device, _container, command, _what, **kwargs):
        assert kwargs.get("allow_fail") is True
        calls.append(command)
        text = outputs.pop(0)
        return SimpleNamespace(returncode=0 if text.startswith("stored") else 1, stdout=text, stderr="")

    monkeypatch.setattr(lifecycle, "_exec", execute)
    monkeypatch.setattr(lifecycle, "PEER_READY_INTERVAL", 0)
    lifecycle._fetch_pqc_public_key({"name": "EVO1"}, "phiotx01", "phiotx02", "ML-KEM-1024")
    assert len(calls) == 2

    monkeypatch.setattr(lifecycle, "_exec", lambda *_a, **_k: SimpleNamespace(
        returncode=1, stdout="Error: no PQC pub key", stderr=""))
    with pytest.raises(lifecycle.PhiotxLifecycleError, match="no PQC pub key"):
        lifecycle._fetch_pqc_public_key({"name": "EVO1"}, "phiotx01", "phiotx02", "ML-KEM-1024")


def test_qkd_store_counts_parse_both_sides_of_the_pair():
    primary = "TX uptime:5m3s Keys:16\n[phiotx01]&>&phiotx02: 16 [3.8s 0.02ms|0.04ms]\n"
    remote = "TX uptime:3m49s Keys:16\nphiotx01&>&[phiotx02]: 16 [6.3s 0.02ms|0.04ms]\n"
    pair = frozenset(("phiotx01", "phiotx02"))
    assert lifecycle.qkd_store_counts(primary) == {pair: 16}
    assert lifecycle.qkd_store_counts(remote) == {pair: 16}
    assert lifecycle.qkd_store_counts("TX uptime:1s Q:0\nQ Pool: 0 rcv nvr ago\n") == {}


def test_wait_for_qkd_pool_requires_every_peer_store(monkeypatch):
    outputs = {"phiotx01": iter(["Keys:0\n", "[phiotx01]&>&phiotx02: 3 [x]\n"]),
               "phiotx02": iter(["phiotx01&>&[phiotx02]: 3 [x]\n"])}
    monkeypatch.setattr(lifecycle, "_exec", lambda _d, c, *_a, **_k: SimpleNamespace(
        returncode=0, stdout=next(outputs[c]), stderr=""))
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _s: None)
    prepared = {
        "EVO1": ({}, {"container": "phiotx01", "peers": [{"name": "phiotx02"}]}),
        "EVO2": ({}, {"container": "phiotx02", "peers": [{"name": "phiotx01"}]}),
    }
    lifecycle.wait_for_qkd_pool(prepared)


@pytest.mark.parametrize("prune", [False, True])
def test_etsi_activation_keeps_key_fetch_unless_pruning(monkeypatch, tmp_path, prune):
    layer = tmp_path / "700.yaml"
    layer.write_text("etsi: {}\n")
    removed = []
    monkeypatch.setattr(lifecycle, "_run", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_push_files", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_docker", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_exec", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(lifecycle, "remove_layer", lambda _d, _s, name: removed.append(name))
    lifecycle.install_layers({}, {"container": "phiotx01"}, {lifecycle.LAYER_ETSI: layer}, prune_key_fetch=prune)
    assert removed == ([lifecycle.LAYER_KEY_FETCH] if prune else [])
