"""
External Linux OpenSSL CA driver for the PhioTX workflow.

The PhioTX deployment deliberately does not use the in-repo PKI generators
(lib/qkd/pki_self_signed.py, lib/qkd/pki_hierarchical.py). All trust material
is issued by an operator-controlled OpenSSL CA that lives outside this
repository, normally on an offline Linux host:

    /root/linuxCA/phiotx
    ├── certs/phiotx-lab-ca.crt      public CA certificate
    ├── private/phiotx-lab-ca.key    CA private key, never leaves this host
    ├── csr/
    └── openssl.cnf

Three identities are issued per router:

    qxc   store  -> PhioTX peer/Hive mutual-TLS identity (TCP 9002)
    etsi  store  -> local ETSI QKD 014 service identity (TCP 443)
    sae   client -> ETSI client identity used by phiotx_qkd_onbox.py

The CA private key is never read, copied, or transmitted by this module. Only
`openssl ca` running on the CA host touches it.
"""

import shlex
import shutil
import subprocess
import base64
from pathlib import Path

from lib.common.settings import CONFIG
from lib.docker.qkd.docker_paths import DOCKER_RUNTIME_DIR


BASE_DIR = Path(__file__).resolve().parents[3]
RUNTIME_DIR = DOCKER_RUNTIME_DIR


# Defaults mirror the manually validated lab CA. Every value can be overridden
# from the inventory under phiotx.ca.
DEFAULT_CA = {
    "dir": "/root/linuxCA/phiotx",
    "config": "openssl.cnf",
    "cert": "certs/phiotx-lab-ca.crt",
    "ca_key": "private/phiotx-lab-ca.key",
    "ca_key_bits": 4096,
    "ca_days": 3650,
    "key_bits": 3072,
    "digest": "sha384",
    "days": 825,
    "subject_base": "/C=IT/O=HPE Lab/OU=PhioTX Lab",
}

# Logical identity -> (PhioTX store name, openssl extension section).
# The store name is what tx_install_crt expects via "-pki <store>"; "sae" is a
# client identity installed on the router, not inside the container.
PKI_STORES = {
    "qxc": {"store": "qxc", "extensions": "phiotx_peer"},
    "etsi": {"store": "etsi", "extensions": "phiotx_etsi"},
    "sae": {"store": None, "extensions": "phiotx_client"},
}


class ExternalCaError(RuntimeError):
    """Raised when the external CA cannot issue or return trust material."""


def resolve_ca_settings(phiotx):
    """Merge inventory phiotx.ca overrides onto the validated defaults."""
    settings = dict(DEFAULT_CA)
    overrides = (phiotx or {}).get("ca") or {}

    for key, value in overrides.items():
        if value is not None:
            settings[key] = value

    settings["dir"] = str(settings["dir"]).rstrip("/")
    return settings


class CaRunner:
    """
    Executes shell commands on the CA host.

    When `host` is None the CA directory is assumed to be reachable on the
    machine running the orchestrator; otherwise every command is wrapped in
    ssh. Keeping this in one place means the openssl recipes below are written
    exactly once.
    """

    def __init__(self, ca_settings, host=None, user="root", ssh_key=None):
        self.ca_dir = ca_settings["dir"]
        self.host = host
        self.user = user
        self.ssh_key = ssh_key

    def _wrap(self, command):
        inner = f"cd {shlex.quote(self.ca_dir)} && {command}"
        if not self.host:
            return ["sh", "-lc", inner]

        ssh = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
        if self.ssh_key:
            ssh.extend(["-i", self.ssh_key, "-o", "IdentitiesOnly=yes"])
        ssh.append(f"{self.user}@{self.host}")
        ssh.append(inner)
        return ssh

    def run_host_checked(self, command, what, timeout=120):
        """Run a command on the CA host without requiring the CA directory."""
        if self.host:
            argv = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
            if self.ssh_key:
                argv.extend(["-i", self.ssh_key, "-o", "IdentitiesOnly=yes"])
            argv.extend([f"{self.user}@{self.host}", command])
        else:
            argv = ["sh", "-lc", command]
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise ExternalCaError(
                f"{what} failed on CA host {self.host or 'localhost'}\n"
                f"command={command}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        return result

    def write_if_missing(self, relative_path, content, mode=0o600):
        """Create a CA file atomically without overwriting operator state."""
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        path = shlex.quote(relative_path)
        self.run_checked(
            f"if test ! -f {path}; then "
            f"printf %s {shlex.quote(encoded)} | base64 -d > {path}; "
            f"chmod {mode:o} {path}; fi",
            f"CA file creation for {relative_path}",
        )

    def append_section_if_missing(self, relative_path, section, content):
        """Append one OpenSSL section without rewriting an existing config."""
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        path = shlex.quote(relative_path)
        pattern = shlex.quote(
            rf"^[[:space:]]*\[[[:space:]]*{section}[[:space:]]*\]"
        )
        self.run_checked(
            f"if ! grep -Eq {pattern} {path}; then "
            f"printf %s {shlex.quote(encoded)} | base64 -d >> {path}; fi",
            f"OpenSSL profile migration for {section}",
        )

    def run(self, command, timeout=120):
        argv = self._wrap(command)
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise ExternalCaError(f"CA command timed out: {command}") from error

    def run_checked(self, command, what, timeout=120):
        result = self.run(command, timeout=timeout)
        if result.returncode != 0:
            raise ExternalCaError(
                f"{what} failed on CA host "
                f"{self.host or 'localhost'}:{self.ca_dir}\n"
                f"command={command}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        return result

    def fetch(self, remote_rel_path, local_path, mode=0o644):
        """Copy one artifact out of the CA directory into local staging."""
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        remote = f"{self.ca_dir}/{remote_rel_path}"

        if not self.host:
            source = Path(remote)
            if not source.exists():
                raise ExternalCaError(f"CA artifact not found: {source}")
            shutil.copyfile(source, local_path)
        else:
            # scp -O forces the legacy protocol; the modern SFTP subsystem is
            # not always enabled on hardened CA hosts.
            argv = ["scp", "-O", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
            if self.ssh_key:
                argv.extend(["-i", self.ssh_key, "-o", "IdentitiesOnly=yes"])
            argv.append(f"{self.user}@{self.host}:{remote}")
            argv.append(str(local_path))

            result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise ExternalCaError(
                    f"Cannot fetch {remote} from CA host {self.host}\n"
                    f"stdout={result.stdout}\nstderr={result.stderr}"
                )

        local_path.chmod(mode)
        return local_path


def verify_ca(runner, ca_settings):
    """Confirm the CA directory is usable before issuing anything."""
    ca_cert = ca_settings["cert"]
    ca_config = ca_settings["config"]

    runner.run_checked(
        f"test -f {shlex.quote(ca_cert)} && test -f {shlex.quote(ca_config)}",
        "External CA layout check",
    )

    result = runner.run_checked(
        f"openssl x509 -in {shlex.quote(ca_cert)} -noout -subject -dates",
        "External CA certificate read",
    )

    print(f"[OK] external CA at {runner.host or 'localhost'}:{runner.ca_dir}")
    for line in result.stdout.strip().splitlines():
        print(f"       {line.strip()}")

    return result.stdout.strip()


def openssl_ca_config(ca_settings):
    """Render the minimal OpenSSL CA configuration required by PhioTX."""
    return f"""[ ca ]
default_ca = CA_default

[ CA_default ]
dir = .
database = $dir/index.txt
new_certs_dir = $dir/newcerts
certificate = $dir/{ca_settings['cert']}
private_key = $dir/{ca_settings['ca_key']}
serial = $dir/serial
default_md = {ca_settings['digest']}
default_days = {int(ca_settings['days'])}
policy = policy_loose
unique_subject = no
copy_extensions = copy

[ policy_loose ]
countryName = optional
stateOrProvinceName = optional
localityName = optional
organizationName = optional
organizationalUnitName = optional
commonName = supplied
emailAddress = optional

[ req ]
prompt = no
distinguished_name = req_dn
default_md = {ca_settings['digest']}
x509_extensions = v3_ca

[ req_dn ]
C = IT
O = HPE Lab
OU = PhioTX Lab
CN = PhioTX Lab CA

[ v3_ca ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints = critical,CA:true,pathlen:0
keyUsage = critical,keyCertSign,cRLSign

[ phiotx_peer ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth,clientAuth

[ phiotx_etsi ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth,clientAuth

[ phiotx_client ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = clientAuth
"""


def required_leaf_profiles():
    """Profiles required by both new and pre-existing PhioTX lab CAs."""
    return {
        "phiotx_peer": """

[ phiotx_peer ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth,clientAuth
""",
        "phiotx_etsi": """

[ phiotx_etsi ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth,clientAuth
""",
        "phiotx_client": """

[ phiotx_client ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = clientAuth
""",
    }


def ensure_ca(runner, ca_settings):
    """Create the external CA when absent, or safely reuse it when present."""
    ca_dir = shlex.quote(ca_settings["dir"])
    runner.run_host_checked(
        f"umask 077; mkdir -p {ca_dir}/certs {ca_dir}/private "
        f"{ca_dir}/csr {ca_dir}/newcerts",
        "External CA directory creation",
    )
    runner.write_if_missing("index.txt", "", mode=0o600)
    runner.write_if_missing("serial", "1000\n", mode=0o600)
    runner.write_if_missing(
        ca_settings["config"],
        openssl_ca_config(ca_settings),
        mode=0o600,
    )
    for section, content in required_leaf_profiles().items():
        runner.append_section_if_missing(
            ca_settings["config"],
            section,
            content,
        )

    ca_key = shlex.quote(ca_settings["ca_key"])
    ca_cert = shlex.quote(ca_settings["cert"])
    runner.run_checked(
        f"if test -f {ca_cert} && test ! -f {ca_key}; then "
        "echo 'CA certificate exists but private key is missing' >&2; exit 21; fi",
        "External CA consistency check",
    )
    runner.run_checked(
        f"if test ! -f {ca_key}; then "
        "openssl genpkey -algorithm RSA "
        f"-pkeyopt rsa_keygen_bits:{int(ca_settings['ca_key_bits'])} "
        f"-out {ca_key}; chmod 600 {ca_key}; fi",
        "External CA private key creation",
        timeout=300,
    )
    runner.run_checked(
        f"if test ! -f {ca_cert}; then "
        "openssl req -new -x509 "
        f"-config {shlex.quote(ca_settings['config'])} "
        f"-extensions v3_ca -days {int(ca_settings['ca_days'])} "
        f"-key {ca_key} -out {ca_cert}; chmod 644 {ca_cert}; fi",
        "External CA certificate creation",
        timeout=300,
    )
    runner.run_checked(
        f"test \"$(openssl x509 -in {ca_cert} -noout -pubkey | "
        "openssl pkey -pubin -outform DER | openssl dgst -sha256)\" = "
        f"\"$(openssl pkey -in {ca_key} -pubout -outform DER | "
        "openssl dgst -sha256)\"",
        "External CA key/certificate match",
    )
    print(f"[OK] external CA initialized at {runner.host or 'localhost'}:{runner.ca_dir}")


def _san_value(common_name, ip_addresses):
    parts = [f"DNS:{common_name}"]
    for address in ip_addresses:
        if address:
            parts.append(f"IP:{address}")
    return ",".join(parts)


def issue_certificate(runner, ca_settings, common_name, ip_addresses, extensions):
    """
    Issue one leaf certificate on the CA host.

    Returns the CA-relative paths of the private key and certificate. The
    private key is generated on the CA host and must afterwards be moved, not
    copied, to its single destination.
    """
    key_rel = f"private/{common_name}.key"
    csr_rel = f"csr/{common_name}.csr"
    crt_rel = f"certs/{common_name}.crt"

    subject = f"{ca_settings['subject_base']}/CN={common_name}"
    san = _san_value(common_name, ip_addresses)

    runner.run_checked(
        "openssl genpkey -algorithm RSA "
        f"-pkeyopt rsa_keygen_bits:{int(ca_settings['key_bits'])} "
        f"-out {shlex.quote(key_rel)}",
        f"Private key generation for {common_name}",
        timeout=300,
    )
    runner.run_checked(
        f"chmod 600 {shlex.quote(key_rel)}",
        f"Private key permission hardening for {common_name}",
    )

    runner.run_checked(
        f"openssl req -new -{ca_settings['digest']} "
        f"-key {shlex.quote(key_rel)} "
        f"-subj {shlex.quote(subject)} "
        f"-addext {shlex.quote('subjectAltName=' + san)} "
        f"-out {shlex.quote(csr_rel)}",
        f"CSR generation for {common_name}",
    )

    runner.run_checked(
        "openssl ca -batch "
        f"-config {shlex.quote(ca_settings['config'])} "
        f"-extensions {shlex.quote(extensions)} "
        f"-days {int(ca_settings['days'])} "
        f"-in {shlex.quote(csr_rel)} "
        f"-out {shlex.quote(crt_rel)}",
        f"Certificate issuance for {common_name}",
    )

    runner.run_checked(
        f"openssl verify -CAfile {shlex.quote(ca_settings['cert'])} "
        f"{shlex.quote(crt_rel)}",
        f"Chain verification for {common_name}",
    )

    for address in ip_addresses:
        if not address:
            continue
        runner.run_checked(
            f"openssl x509 -in {shlex.quote(crt_rel)} "
            f"-checkip {shlex.quote(str(address))} -noout",
            f"SAN/IP verification for {common_name} ({address})",
        )

    print(f"[OK] issued {common_name} ({extensions}) SAN={san}")

    return {"key": key_rel, "csr": csr_rel, "crt": crt_rel}


def _device_identities(name, device, phiotx_defaults):
    """
    Resolve the three identities required by one router.

    Both the container addresses and the SAE identity come from the inventory,
    so the SANs always match the addresses the runtime actually dials. The
    number of issued certificates therefore scales automatically with the
    number of EVO routers present in the inventory: three leaves per managed
    device, plus the single shared CA certificate.
    """
    node = device.get("phiotx") or {}
    container = node.get("container")
    if not container:
        raise ExternalCaError(f"Device {name} has no phiotx.container in inventory")

    internal_ip = node.get("internal_ip") or (device.get("kme") or {}).get("ip")
    oob_ip = node.get("oob_ip")
    ip_addresses = [ip for ip in (internal_ip, oob_ip) if ip]

    if not ip_addresses:
        raise ExternalCaError(
            f"Device {name} has no phiotx.internal_ip/oob_ip; "
            "cannot build certificate SANs"
        )

    sae_id = node.get("etsi_client") or (device.get("qkd") or {}).get("sae_id")
    if not sae_id:
        raise ExternalCaError(f"Device {name} has no SAE identity in inventory")

    etsi_cn = node.get("etsi_cn") or f"{container}-etsi"

    return [
        {
            "role": "qxc",
            "common_name": container,
            "ip_addresses": ip_addresses,
            "extensions": PKI_STORES["qxc"]["extensions"],
            "store": PKI_STORES["qxc"]["store"],
        },
        {
            "role": "etsi",
            "common_name": etsi_cn,
            "ip_addresses": ip_addresses,
            "extensions": PKI_STORES["etsi"]["extensions"],
            "store": PKI_STORES["etsi"]["store"],
        },
        {
            "role": "sae",
            "common_name": str(sae_id),
            "ip_addresses": [],
            "extensions": PKI_STORES["sae"]["extensions"],
            "store": PKI_STORES["sae"]["store"],
        },
    ]


def plan_pki(devices, phiotx, only=None):
    """
    Resolve every identity for every managed device before touching the CA.

    With more than two routers a single duplicated container name, SAE id, or
    container address silently produces certificates that authenticate the
    wrong node. The whole plan is therefore validated up front, and issuance
    only starts once the plan is proven collision-free.
    """
    selected = {
        name: device
        for name, device in devices.items()
        if device.get("managed") is not False
        and (not only or name in set(only))
    }

    if only:
        unknown = sorted(set(only) - set(devices))
        if unknown:
            raise ExternalCaError(
                f"Unknown device(s) requested: {', '.join(unknown)}"
            )

    if not selected:
        raise ExternalCaError("No managed EVO devices selected for PKI issuance")

    plan = {}
    seen_cn = {}
    seen_ip = {}

    for name, device in selected.items():
        identities = _device_identities(name, device, phiotx)
        plan[name] = identities

        for identity in identities:
            common_name = identity["common_name"]
            owner = seen_cn.setdefault(common_name, name)
            if owner != name:
                raise ExternalCaError(
                    f"Duplicate certificate CN {common_name!r} requested by "
                    f"{owner} and {name}; container, ETSI, and SAE names must "
                    "be unique across all EVO routers"
                )

            for address in identity["ip_addresses"]:
                holder = seen_ip.setdefault(str(address), name)
                if holder != name:
                    raise ExternalCaError(
                        f"Address {address} is claimed by both {holder} and "
                        f"{name}; each PhioTX container needs its own "
                        "internal_ip and oob_ip"
                    )

    leaf_count = sum(len(items) for items in plan.values())
    print(
        f"[PLAN] {len(plan)} EVO router(s), {leaf_count} leaf certificate(s) "
        f"({len(PKI_STORES)} per router) plus 1 shared CA certificate"
    )
    for name, identities in plan.items():
        names = ", ".join(f"{i['role']}={i['common_name']}" for i in identities)
        print(f"       {name}: {names}")

    return plan


def certificate_is_usable(runner, ca_settings, common_name):
    """
    Return True when a valid, chain-verified leaf and its key already exist.

    Re-running the workflow across a large EVO fleet must not revoke and
    reissue material for routers that are already provisioned, so existing
    usable certificates are reused unless the caller forces reissuance.
    """
    key_rel = f"private/{common_name}.key"
    crt_rel = f"certs/{common_name}.crt"

    present = runner.run(
        f"test -f {shlex.quote(key_rel)} && test -f {shlex.quote(crt_rel)}"
    )
    if present.returncode != 0:
        return False

    verified = runner.run(
        f"openssl verify -CAfile {shlex.quote(ca_settings['cert'])} "
        f"{shlex.quote(crt_rel)}"
    )
    if verified.returncode != 0:
        return False

    # -checkend 0 fails once the certificate is expired.
    fresh = runner.run(
        f"openssl x509 -in {shlex.quote(crt_rel)} -noout -checkend 0"
    )
    return fresh.returncode == 0


def build_external_pki(
    devices,
    phiotx,
    ca_host=None,
    ca_user="root",
    ssh_key=None,
    only=None,
    force=False,
):
    """
    Issue and stage all PhioTX trust material.

    Scales to any number of EVO routers: three leaves are issued per managed
    device, and the shared CA certificate is staged alongside each of them.

    Staging layout, one directory per device:

        config/runtime/<device>/pki/ca.pem
        config/runtime/<device>/pki/<role>.crt
        config/runtime/<device>/pki/<role>.key

    Returns a per-device map consumed by docker_phiotx_lifecycle.py.
    """
    ca_settings = resolve_ca_settings(phiotx)
    runner = CaRunner(ca_settings, host=ca_host, user=ca_user, ssh_key=ssh_key)

    ensure_ca(runner, ca_settings)
    verify_ca(runner, ca_settings)
    plan = plan_pki(devices, phiotx, only=only)

    outputs = {}

    for name, identities in plan.items():
        staging = RUNTIME_DIR / name / "pki"
        staging.mkdir(parents=True, exist_ok=True)

        ca_local = runner.fetch(ca_settings["cert"], staging / "ca.pem", mode=0o644)

        resolved = {}
        for identity in identities:
            common_name = identity["common_name"]

            if not force and certificate_is_usable(runner, ca_settings, common_name):
                print(f"[SKIP] {common_name} already has a valid certificate")
                issued = {
                    "key": f"private/{common_name}.key",
                    "crt": f"certs/{common_name}.crt",
                }
            else:
                issued = issue_certificate(
                    runner,
                    ca_settings,
                    common_name=common_name,
                    ip_addresses=identity["ip_addresses"],
                    extensions=identity["extensions"],
                )

            role = identity["role"]
            key_local = runner.fetch(issued["key"], staging / f"{role}.key", mode=0o600)
            crt_local = runner.fetch(issued["crt"], staging / f"{role}.crt", mode=0o644)

            resolved[role] = {
                "common_name": common_name,
                "store": identity["store"],
                "key": key_local,
                "crt": crt_local,
            }

        outputs[name] = {"ca": ca_local, "identities": resolved}
        print(f"[OK] staged PhioTX PKI for {name} in {staging}")

    return outputs
