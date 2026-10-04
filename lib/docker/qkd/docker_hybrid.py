"""External ETSI QKD 014 simulator for PhioTX hybrid key generation.

N reference KMEs (one per managed PhioTX) plus one shared PostgreSQL run on
the Linux orchestrator host inside the project folder used by the legacy
kme_orchestrator, in a dedicated phiotx-hybrid/ subfolder:

    <source_dir>/phiotx-hybrid/docker-compose-phiotx-hybrid.yml
    <source_dir>/phiotx-hybrid/{postgres.env,kme.env}   mode 0600
    <source_dir>/phiotx-hybrid/certs/<kme>/              server PKI copies

Containers attach with static addresses to the pre-existing OOB ipvlan/macvlan
network (default qkd_net), which this module never creates or removes. Every
address is checked for conflicts before start. The simulated keys are
software random material shared through PostgreSQL, not quantum entropy.
"""

import hashlib
import ipaddress
import json
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import time

import yaml

from lib.docker.qkd.docker_hybrid_pki import build_hybrid_pki
from lib.docker.qkd.docker_simulator_etsi import validate_correlated_keys

OWNER_LABEL = "io.quantum-safe.docker-qkd.project"
DEVICE_LABEL = "io.quantum-safe.docker-qkd.phiotx"
DEPLOY_SUBDIR = "phiotx-hybrid"
COMPOSE_FILE = "docker-compose-phiotx-hybrid.yml"
KME_IMAGE = "etsi-kme:local"
POSTGRES_IMAGE = "postgres:15"
REFERENCE_IMAGE_ID = "sha256:31d5e84142730f29a75d0324079b2aedcf90193d97eaab74e073dbdc491d1e9b"
POSTGRES_IMAGE_ID = "sha256:afdbd967cf653afc901851ccf14bc5a2f310748303577963e99dd117c1554527"
MIGRATION = "migrations/20230113082423_create_keys.sql"
# Inspected source revision 56474322b9180f5f7fb59770442112d4e8b06e43.
SOURCE_HASHES = {
    MIGRATION: "a16019adabcadf4d541278802e51c2871aca5ef382275dfe7b42469acec79d79",
    "src/handlers/enc_keys.rs": "b22048f2dc5fc56c547afc25f7c8fc25cd79dedf6ac8bca61695d66f389de2c7",
    "src/handlers/dec_keys.rs": "ebc1462fff9dc2824ee52c0eda6a08237737e458d59df74a9c83cdf5410951c8",
    "src/ops/server.rs": "fd499731023e4d6b2d9f6a96350f89a933d4e6209c0dd55a0746bfa32ef4347d",
    "src/config.rs": "c7bac012fe98129fb900da31503f9f7f1b9b1495da8325e2f5046e6d7c6040d3",
}
SCHEMA_SIGNATURE = (
    "id:uuid,master_sae_id:text,slave_sae_id:text,size:integer,content:text,"
    "active:boolean,last_modified_at:timestamp with time zone,created_at:timestamp with time zone"
)
FIELDS = {
    "host", "network", "source_dir", "project", "port", "key_rate",
    "postgres_ip", "kme_ips",
}


class HybridError(RuntimeError):
    """Raised when the external hybrid QKD simulator cannot be provisioned."""


def _run(argv, *, check=True, timeout=120):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise HybridError(
            f"{' '.join(argv[:3])} failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[-400:]}"
        )
    return result


def managed_devices(devices):
    return {
        name: device for name, device in sorted(devices.items())
        if device.get("managed") is not False
    }


def link_pairs(devices):
    """Directly connected PhioTX pairs; the lexically lower device is primary."""
    managed = managed_devices(devices)
    by_container = {d["phiotx"]["container"]: n for n, d in managed.items()}
    pairs = set()
    for name, device in managed.items():
        # Same peer resolution as docker_key_fetch.render_key_fetch.
        node = device.get("phiotx") or {}
        peers = node.get("peers")
        if peers is None and node.get("peer"):
            peers = [node["peer"]]
        if peers is None:
            peers = [link.get("peer") for link in device.get("links", []) if isinstance(link, dict)]
        for peer in peers:
            reference = peer["name"] if isinstance(peer, dict) else peer
            remote = reference if reference in managed else by_container.get(reference)
            if remote and remote != name:
                pairs.add(tuple(sorted((name, remote))))
    if not pairs:
        raise HybridError("Hybrid requires at least one link between managed PhioTX nodes")
    return sorted(pairs)


def _inventory_addresses(devices):
    found = {}
    for name, device in devices.items():
        values = [device.get("ip"), (device.get("kme") or {}).get("ip")]
        phiotx = device.get("phiotx") or {}
        values += [phiotx.get(key) for key in ("oob_ip", "internal_ip", "peer_oob_ip")]
        for value in values:
            if value:
                found[str(value)] = name
    return found


def resolve_settings(phiotx, devices):
    """Validate phiotx.qkd_simulator against the managed fleet."""
    raw = phiotx.get("qkd_simulator")
    if not isinstance(raw, dict):
        raise ValueError("Hybrid requires a phiotx.qkd_simulator mapping")
    unknown, missing = set(raw) - FIELDS, FIELDS - set(raw)
    if unknown or missing:
        raise ValueError(
            "phiotx.qkd_simulator fields mismatch; "
            f"missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    managed = managed_devices(devices)
    if set(raw["kme_ips"]) != set(managed):
        raise ValueError(
            "qkd_simulator.kme_ips must define exactly one KME per managed PhioTX: "
            + ", ".join(managed)
        )
    host = str(ipaddress.IPv4Address(raw["host"]))
    postgres_ip = str(ipaddress.IPv4Address(raw["postgres_ip"]))
    kme_ips = {name: str(ipaddress.IPv4Address(ip)) for name, ip in raw["kme_ips"].items()}
    assigned = [postgres_ip, *kme_ips.values()]
    if len(set(assigned)) != len(assigned) or host in assigned:
        raise ValueError("qkd_simulator addresses must be unique and differ from the host")
    reserved = _inventory_addresses(devices)
    clashes = [ip for ip in assigned if ip in reserved]
    if clashes:
        raise ValueError(f"qkd_simulator addresses already used in inventory: {clashes}")
    source = Path(raw["source_dir"])
    if not source.is_absolute():
        raise ValueError("qkd_simulator.source_dir must be absolute")
    project = str(raw["project"])
    if not re.fullmatch(r"docker-qkd-[a-z0-9][a-z0-9-]{0,40}", project):
        raise ValueError("qkd_simulator.project must look like docker-qkd-<name>")
    port = int(raw["port"])
    rate = float(raw["key_rate"])
    if not 1 <= port <= 65535 or not 0.001 <= rate <= 111:
        raise ValueError("Invalid qkd_simulator port or key_rate")
    nodes = {
        name: {
            "container": device["phiotx"]["container"],
            "addr": kme_ips[name],
            "service": f"kme-{device['phiotx']['container']}",
        }
        for name, device in managed.items()
    }
    return {
        "host": host, "network": str(raw["network"]), "source_dir": source,
        "deploy_dir": source / DEPLOY_SUBDIR, "project": project, "port": port,
        "key_rate": rate, "postgres_ip": postgres_ip, "nodes": nodes,
        "pairs": link_pairs(devices),
    }


def qkd_sources(settings):
    return {
        name: {"addr": node["addr"], "port": settings["port"], "pki": "qkd"}
        for name, node in settings["nodes"].items()
    }


def issue_pki(settings, *, fresh=False):
    return build_hybrid_pki(settings["nodes"], fresh=fresh)


def attach_qkd_pki(bundles, pki):
    for name, paths in pki.items():
        if name not in bundles:
            raise HybridError(f"No staged PhioTX PKI bundle for {name}")
        bundles[name]["identities"]["qkd"] = {
            "common_name": name, "store": "qkd",
            "key": paths["client_key"], "crt": paths["client_crt"],
            "cas": list(paths["client_cas"]),
        }


class HybridSimulator:
    """Host-local lifecycle for the hybrid KMEs and their shared database."""

    def __init__(self, settings, run=_run):
        self.settings = settings
        self.run = run
        self.project = settings["project"]
        self.deploy_dir = settings["deploy_dir"]
        self.compose_path = self.deploy_dir / COMPOSE_FILE
        self.postgres = f"{self.project}-postgres"

    def _docker(self, *args, check=True):
        return self.run(["docker", *args], check=check)

    # ---------------------------------------------------------------- checks
    def preflight(self):
        settings = self.settings
        if platform.system() != "Linux":
            raise HybridError("The hybrid simulator must be provisioned on the Linux host")
        local = self.run(["ip", "-4", "-o", "addr", "show"]).stdout
        if f" {settings['host']}/" not in local:
            raise HybridError(
                f"Run the orchestrator on {settings['host']}: the KMEs are host-local"
            )
        for relative, digest in SOURCE_HASHES.items():
            path = settings["source_dir"] / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise HybridError(f"Unexpected ETSI reference source file: {path}")
        for image, expected in ((KME_IMAGE, REFERENCE_IMAGE_ID), (POSTGRES_IMAGE, POSTGRES_IMAGE_ID)):
            found = self._docker("image", "inspect", image, "--format", "{{.Id}}", check=False)
            if found.returncode or found.stdout.strip() != expected:
                raise HybridError(f"Local image {image} is missing or differs from the inspected build")
        network = self._docker("network", "inspect", settings["network"], check=False)
        if network.returncode:
            raise HybridError(f"Docker network {settings['network']} does not exist")
        info = json.loads(network.stdout)[0]
        if info.get("Driver") not in ("ipvlan", "macvlan"):
            raise HybridError(f"{settings['network']} must be an OOB ipvlan/macvlan network")
        subnets = [ipaddress.ip_network(item["Subnet"]) for item in info["IPAM"]["Config"]]
        gateways = {item.get("Gateway") for item in info["IPAM"]["Config"]}
        for address in self.addresses().values():
            if not any(ipaddress.ip_address(address) in subnet for subnet in subnets):
                raise HybridError(f"{address} is outside {settings['network']} {subnets}")
            if address in gateways:
                raise HybridError(f"{address} is the {settings['network']} gateway")
        self.check_conflicts()

    def addresses(self):
        found = {"postgres": self.settings["postgres_ip"]}
        found.update({node["service"]: node["addr"] for node in self.settings["nodes"].values()})
        return found

    def _containers(self, owned=None):
        args = ["ps", "-aq"]
        if owned:
            args += ["--filter", f"label={OWNER_LABEL}={self.project}"]
        ids = self._docker(*args).stdout.split()
        return json.loads(self._docker("inspect", *ids).stdout) if ids else []

    def check_conflicts(self):
        """Refuse any address used by another container, the host or the LAN."""
        wanted = set(self.addresses().values())
        owned = set()
        for item in self._containers():
            labels = item["Config"].get("Labels") or {}
            mine = labels.get(OWNER_LABEL) == self.project
            for network in (item["NetworkSettings"].get("Networks") or {}).values():
                used = {network.get("IPAddress")} | {
                    (network.get("IPAMConfig") or {}).get("IPv4Address")
                }
                for address in used & wanted:
                    if mine:
                        owned.add(address)
                    else:
                        raise HybridError(
                            f"{address} is already assigned to container {item['Name'].lstrip('/')}"
                        )
        for address in sorted(wanted - owned):
            self.run(["ping", "-c", "2", "-W", "1", address], check=False)
            neighbour = self.run(["ip", "neigh", "show", address], check=False).stdout
            if "lladdr" in neighbour:
                raise HybridError(f"{address} answers on the lab network; choose a free address")
        print(f"[OK] simulator addresses free or already owned: {', '.join(sorted(wanted))}")

    # --------------------------------------------------------------- render
    def _secret_file(self, path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)

    def render(self, pki):
        settings = self.settings
        deploy = self.deploy_dir
        deploy.mkdir(mode=0o700, exist_ok=True)
        deploy.chmod(0o700)
        postgres_env = deploy / "postgres.env"
        password = None
        if postgres_env.exists():
            match = re.search(r"^POSTGRES_PASSWORD=(.+)$", postgres_env.read_text(), re.M)
            password = match.group(1) if match else None
        # Reuse the password while the database volume may still hold it.
        password = password or secrets.token_urlsafe(32)
        self._secret_file(postgres_env, (
            "POSTGRES_USER=phiotx_hybrid\nPOSTGRES_DB=key_store\n"
            f"POSTGRES_PASSWORD={password}\n"
        ))
        self._secret_file(deploy / "kme.env", (
            "ETSI_014_REF_IMPL_DB_URL=postgres://phiotx_hybrid:"
            f"{password}@{settings['postgres_ip']}:5432/key_store\n"
        ))
        certs = deploy / "certs"
        services = {
            "postgres": {
                "image": POSTGRES_IMAGE, "container_name": self.postgres,
                "pull_policy": "never", "restart": "unless-stopped",
                "labels": {OWNER_LABEL: self.project},
                "env_file": ["postgres.env"],
                "volumes": [
                    "pgdata:/var/lib/postgresql/data",
                    f"{settings['source_dir'] / MIGRATION}:/docker-entrypoint-initdb.d/001-keys.sql:ro",
                ],
                "networks": {settings["network"]: {"ipv4_address": settings["postgres_ip"]}},
            }
        }
        for name, node in settings["nodes"].items():
            target = certs / node["service"]
            target.mkdir(parents=True, mode=0o700, exist_ok=True)
            certs.chmod(0o700)
            target.chmod(0o700)
            for source, filename in (
                (pki[name]["server_key"], "server.key"),
                (pki[name]["server_chain"], "server.crt"),
                (pki[name]["server_trust"], "ca.pem"),
            ):
                shutil.copyfile(source, target / filename)
                (target / filename).chmod(0o600 if filename.endswith(".key") else 0o644)
            services[node["service"]] = {
                "image": KME_IMAGE, "container_name": f"{self.project}-{node['service']}",
                "pull_policy": "never", "restart": "unless-stopped",
                "depends_on": ["postgres"], "user": "0:0", "read_only": True,
                "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                "labels": {OWNER_LABEL: self.project, DEVICE_LABEL: name},
                "env_file": ["kme.env"],
                "environment": {
                    "ETSI_014_REF_IMPL_IP_ADDR": "0.0.0.0",
                    "ETSI_014_REF_IMPL_PORT_NUM": str(settings["port"]),
                    "ETSI_014_REF_IMPL_NUM_WORKER_THREADS": "2",
                    "ETSI_014_REF_IMPL_TLS_CERT": "/certs/server.crt",
                    "ETSI_014_REF_IMPL_TLS_PRIVATE_KEY": "/certs/server.key",
                    "ETSI_014_REF_IMPL_TLS_ROOT_CRT": "/certs/ca.pem",
                },
                "volumes": [f"./certs/{node['service']}:/certs:ro"],
                "networks": {settings["network"]: {"ipv4_address": node["addr"]}},
            }
        compose = {
            "name": self.project,
            "services": services,
            "volumes": {"pgdata": {"labels": {OWNER_LABEL: self.project}}},
            "networks": {settings["network"]: {"external": True}},
        }
        self.compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
        self.compose_path.chmod(0o600)
        print(f"[OK] simulator rendered in {deploy}")
        return self.compose_path

    # ------------------------------------------------------------ lifecycle
    def _compose(self, *args):
        return self.run([
            "docker", "compose", "--project-directory", str(self.deploy_dir),
            "-p", self.project, "-f", str(self.compose_path), *args,
        ], timeout=180)

    def _psql(self, sql):
        return self._docker(
            "exec", self.postgres, "psql", "-v", "ON_ERROR_STOP=1", "-At",
            "-U", "phiotx_hybrid", "-d", "key_store", "-c", sql, check=False,
        )

    def start(self, timeout=90):
        self._compose("up", "-d", "--pull", "never", "postgres")
        deadline = time.monotonic() + timeout
        query = (
            "SELECT string_agg(column_name || ':' || data_type, ',' ORDER BY ordinal_position) "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name='keys';"
        )
        while True:
            schema = self._psql(query)
            # The init server listens only on the socket; wait for the final restart too.
            ready = self._docker(
                "exec", self.postgres, "pg_isready", "-h", self.settings["postgres_ip"], check=False,
            )
            if schema.returncode == 0 and schema.stdout.strip() == SCHEMA_SIGNATURE and ready.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise HybridError("PostgreSQL did not expose the native ETSI keys schema in time")
            time.sleep(2)
        print(f"[OK] shared PostgreSQL ready on {self.settings['postgres_ip']}")
        # Leaves are re-issued on every provision; recreate so KMEs reload them.
        self._compose("up", "-d", "--pull", "never", "--force-recreate",
                      *(node["service"] for node in self.settings["nodes"].values()))

    def _fetch(self, local_device, path, client, pki):
        """HTTPS GET from inside the PostgreSQL namespace (ipvlan hides children from the host)."""
        node = self.settings["nodes"][local_device]
        address, port = node["addr"], self.settings["port"]
        request = f"GET {path} HTTP/1.0\\r\\nHost: {address}\\r\\n\\r\\n"
        script = (
            f"printf '{request}' | openssl s_client -quiet -verify_return_error "
            f"-verify_ip {address} -CAfile /pki/ca.pem -cert /pki/client.crt "
            f"-key /pki/client.key -connect {address}:{port} 2>/dev/null"
        )
        result = self._docker(
            "run", "--rm", "--network", f"container:{self.postgres}",
            "-v", f"{pki[client]['client_trust']}:/pki/ca.pem:ro",
            "-v", f"{pki[client]['client_crt']}:/pki/client.crt:ro",
            "-v", f"{pki[client]['client_key']}:/pki/client.key:ro",
            "--entrypoint", "sh", POSTGRES_IMAGE, "-c", script, check=False,
        )
        # s_client may normalise CRLF to LF; accept either header terminator.
        parts = re.split(r"\r?\n\r?\n", result.stdout, maxsplit=1)
        head, body = parts[0], parts[1] if len(parts) > 1 else ""
        status = re.match(r"HTTP/1\.\d (\d{3})", head)
        if not status:
            raise HybridError(f"No HTTPS response from KME {address}:{port}")
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        return int(status.group(1)), data

    def probe(self, pki):
        """Fresh mTLS enc/dec equality per link; never prints key material."""
        nodes = self.settings["nodes"]
        report = []
        for primary, remote in self.settings["pairs"]:
            enc_status, enc = self._fetch(
                primary, f"/api/v1/keys/{nodes[remote]['container']}/enc_keys?number=1&size=256",
                primary, pki,
            )
            if enc_status != 200 or not enc:
                raise HybridError(f"enc_keys on {nodes[primary]['addr']} returned HTTP {enc_status}")
            key_id = enc["keys"][0]["key_ID"]
            dec_status, dec = self._fetch(
                remote, f"/api/v1/keys/{nodes[primary]['container']}/dec_keys?key_ID={key_id}",
                remote, pki,
            )
            validate_correlated_keys(enc_status, enc, dec_status, dec)
            report.append({"primary": primary, "remote": remote, "key_id": key_id, "bytes": 32})
            print(f"[OK] ETSI enc/dec correlated {primary}->{remote} key_ID={key_id} (32 bytes)")
        return report

    def wait_ready(self, pki, timeout=90):
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.probe(pki)
            except (HybridError, ValueError, KeyError, IndexError, TypeError) as error:
                if time.monotonic() > deadline:
                    raise HybridError(f"Simulator readiness failed: {error}") from error
                time.sleep(3)

    def cleanup(self):
        """Remove only containers/volumes labelled for this project and its folder."""
        ids = self._docker("ps", "-aq", "--filter", f"label={OWNER_LABEL}={self.project}").stdout.split()
        if ids:
            self._docker("rm", "-f", *ids)
        volumes = self._docker(
            "volume", "ls", "-q", "--filter", f"label={OWNER_LABEL}={self.project}",
        ).stdout.split()
        if volumes:
            self._docker("volume", "rm", *volumes)
        if self.deploy_dir.name == DEPLOY_SUBDIR and self.deploy_dir.is_dir():
            shutil.rmtree(self.deploy_dir)
        print(f"[OK] hybrid simulator {self.project} removed "
              f"({len(ids)} container(s), {len(volumes)} volume(s)); network untouched")


def provision(phiotx, devices, *, fresh_pki=False, dry_run=False):
    """Issue PKI, start N KMEs + PostgreSQL, prove correlated keys, return wiring."""
    settings = resolve_settings(phiotx, devices)
    simulator = HybridSimulator(settings)
    simulator.preflight()
    pki = issue_pki(settings, fresh=fresh_pki)
    if dry_run:
        print("[DRY-RUN] hybrid simulator validated; containers not started")
        return {**phiotx, "qkd_sources": qkd_sources(settings)}, pki
    simulator.render(pki)
    simulator.start()
    simulator.wait_ready(pki)
    return {**phiotx, "qkd_sources": qkd_sources(settings)}, pki


def cleanup(phiotx):
    """Clean-time removal; needs only the project and folder, not the fleet."""
    raw = phiotx.get("qkd_simulator")
    if not raw or platform.system() != "Linux" or shutil.which("docker") is None:
        return False
    project = str(raw.get("project", ""))
    if not re.fullmatch(r"docker-qkd-[a-z0-9][a-z0-9-]{0,40}", project):
        raise ValueError("qkd_simulator.project must look like docker-qkd-<name>")
    settings = {"project": project, "deploy_dir": Path(raw["source_dir"]) / DEPLOY_SUBDIR}
    HybridSimulator(settings).cleanup()
    return True
