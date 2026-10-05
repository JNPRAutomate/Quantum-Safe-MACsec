#!/usr/bin/env python3
"""QBT image preparation on Linux and delivery to Junos EVO."""

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tarfile
import tempfile

import paramiko
import yaml
from scp import SCPClient

from lib.qbt.deployment import render_profile
from lib.qbt.network import attach_networks, check_networks, probe_address
from lib.qbt.pki import prepare_pki, import_pki
from lib.qbt.hierarchical import prepare_hierarchical
from lib.qbt.peer import configure_peers
from lib.qbt.probe import paired_probe

LINUX_HOST = "10.38.98.181"
DEVICES = {"EVO1": "10.38.97.218", "EVO2": "10.38.97.228"}
IMAGE = "registry.qubridge.io/qki/qbt-core/qbt-kme:2.10.0-alpha.4-mgmt"
IMAGE_SHA256 = "5b547af51e2caeab28c9b93bb36a12e41a0de43e4643dbec7cd2ebc462d0f0ed"
DEPLOY_SHA256 = "ad35e2972e7a28cf29fcc84aa01a34c16ad1e179cfd89d3011d89889a5b35bc9"
STAGING = "/var/tmp/qbt-vendor-2.10.0-alpha.4"


class QbtError(RuntimeError):
    pass


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path, expected_digest):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or file_digest(path) != expected_digest:
        raise QbtError(f"Archive checksum mismatch: {path.name}")
    with tarfile.open(path) as archive:
        for member in archive:
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise QbtError(f"Unsafe archive member: {member.name}")
    return path


def run(client, command, *, timeout=180):
    _, stdout, stderr = client.exec_command(
        "/bin/sh -c " + shlex.quote(command), timeout=timeout
    )
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    status = stdout.channel.recv_exit_status()
    if status:
        raise QbtError(f"Remote command failed ({status}): {command}\n{error}\n{output}")
    return output.strip()


def connect(host, username, password, known_hosts):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if known_hosts:
        client.load_host_keys(known_hosts)
    try:
        client.connect(
            host, username=username, password=password, timeout=20,
            auth_timeout=20, banner_timeout=20,
        )
    except (paramiko.SSHException, OSError):
        client.close()
        raise
    return client


def transfer(client, source, destination):
    # Junos EVO provides SCP but not necessarily an SFTP subsystem.
    with SCPClient(client.get_transport(), socket_timeout=120) as scp:
        scp.put(str(source), destination)
    run(client, "chmod 600 " + shlex.quote(destination))


def check_remote_digest(client, path, expected):
    output = run(client, "sha256sum " + shlex.quote(path))
    if output.split()[0] != expected:
        raise QbtError(f"Remote archive checksum mismatch: {path}")


def inspect_image(client):
    image = json.loads(run(client, "docker image inspect " + shlex.quote(IMAGE)))[0]
    if image["Architecture"] != "amd64" or image["Os"] != "linux":
        raise QbtError("QBT image must be Linux/amd64")
    return image["Id"]


def check_environment(client):
    architecture = run(client, "uname -m")
    if architecture != "x86_64":
        raise QbtError("Supplied QBT image requires x86_64")
    return {
        "architecture": architecture,
        "docker": run(client, 'docker version --format "{{.Server.Version}}"'),
        "memory": run(client, "head -5 /proc/meminfo"),
        "storage": run(client, "df -Pk /var/db /var/tmp"),
        "containers": run(client, 'docker ps -a --format "{{.Names}} {{.Status}}"'),
    }


def prepare_images(args, linux, evo_password):
    image_archive = verify_archive(args.image_archive, IMAGE_SHA256)
    deployment_archive = verify_archive(args.deployment_archive, DEPLOY_SHA256)
    run(linux, "docker compose version")
    run(linux, f"mkdir -p {STAGING} && chmod 700 {STAGING}")
    for source in (image_archive, deployment_archive):
        remote = STAGING + "/" + source.name
        transfer(linux, source, remote)
        check_remote_digest(linux, remote, file_digest(source))
    run(linux, "docker load -i " + shlex.quote(STAGING + "/" + image_archive.name))
    image_id = inspect_image(linux)
    print(f"[OK] Linux QBT image loaded: {image_id}")
    if args.linux_only:
        return
    exported = STAGING + "/qbt-kme-export.tar"
    run(linux, "docker save -o " + exported + " " + shlex.quote(IMAGE), timeout=300)
    run(linux, "chmod 600 " + exported)
    remote_digest = run(linux, "sha256sum " + exported).split()[0]
    with tempfile.TemporaryDirectory(prefix="qbt-image-") as folder:
        local_export = Path(folder) / "qbt-kme-export.tar"
        with SCPClient(linux.get_transport(), socket_timeout=120) as scp:
            scp.get(exported, str(local_export))
        if file_digest(local_export) != remote_digest:
            raise QbtError("Export download checksum mismatch")
        for name in args.only or DEVICES:
            with connect(DEVICES[name], args.username, evo_password, args.known_hosts) as evo:
                check_environment(evo)
                run(evo, f"mkdir -p {STAGING} && chmod 700 {STAGING}")
                transfer(evo, local_export, exported)
                check_remote_digest(evo, exported, remote_digest)
                run(evo, "docker load -i " + exported, timeout=300)
                if inspect_image(evo) != image_id:
                    raise QbtError(f"{name}: loaded image ID differs from Linux")
                print(f"[OK] {name} image ready: {image_id}; no container started")


def status(args, linux, password):
    print("Linux image:", inspect_image(linux))
    for name in args.only or DEVICES:
        with connect(DEVICES[name], args.username, password, args.known_hosts) as evo:
            print(name, json.dumps(check_environment(evo), indent=2))
            print(name, "image:", inspect_image(evo))


def network(args, password):
    """Check the fleet before attaching the existing containers in place."""
    from contextlib import ExitStack

    with ExitStack() as stack:
        linux = stack.enter_context(connect(
            args.linux_host, args.linux_username,
            os.getenv("QBT_LINUX_PASSWORD") or password, args.known_hosts
        ))
        def probe(address):
            print(probe_address(linux, address, run))

        clients = {
            name: stack.enter_context(connect(
                DEVICES[name], args.username, password, args.known_hosts
            ))
            for name in args.only or DEVICES
        }
        for name, client in clients.items():
            check_networks(client, name, run, probe)
        for name, client in clients.items():
            print(name, json.dumps(attach_networks(client, name, run, probe)))


def clean(args, linux, password):
    """Remove only explicitly named QBT resources, never Juniper infrastructure."""
    for name in args.only or DEVICES:
        with connect(DEVICES[name], args.username, password, args.known_hosts) as evo:
            container = "qbt-" + name.lower()
            names = run(evo, "docker ps -a --format '{{.Names}}'").splitlines()
            if container in names:
                info = json.loads(run(evo, "docker inspect " + container))[0]
                labels = info["Config"].get("Labels") or {}
                if labels.get("io.qbt.lab.owner") != "qbt-orchestrator":
                    raise QbtError(f"Refusing to remove unowned container {container}")
                run(evo, "docker rm -f " + container)
            remaining = run(evo, "docker ps -a --format '{{.Names}}'").splitlines()
            if container in remaining:
                raise QbtError(f"{name}: container remains after cleanup")
            print(f"[OK] {name}: managed QBT container absent; infrastructure untouched")
    print("[INFO] Images, staging, PKI and persistent data retained")


def require_bootstrap_inputs(args):
    if args.dry_run:
        return
    if not args.license_file:
        raise QbtError("bootstrap requires --license-file; no KME licence was supplied")
    licence = args.license_file.resolve(strict=True)
    if not licence.is_file() or not licence.read_bytes().strip():
        raise QbtError("Licence file is empty or invalid")
    raise QbtError(
        "Bootstrap deployment is not implemented yet: remote Compose access, "
        "EVO networking and activation mode must be validated before starting KME"
    )


def check_image(args, password):
    """Run diagnostics only, without network, persistent volumes or activation."""
    for name in args.only or DEVICES:
        with connect(DEVICES[name], args.username, password, args.known_hosts) as evo:
            inspect_image(evo)
            prefix = (
                "docker run --rm --network none --read-only "
                "--cap-drop ALL --security-opt no-new-privileges:true "
                "--entrypoint qbt-kme " + shlex.quote(IMAGE)
            )
            print(f"=== {name}: QBT version ===")
            print(run(evo, prefix + " version"))
            print(f"=== {name}: licence CLI ===")
            print(run(evo, prefix + " license --help"))


def bootstrap_plan(args, linux, password):
    """Validate generated Compose profiles only; do not deploy them."""
    with tempfile.TemporaryDirectory(prefix="qbt-compose-") as folder:
        for name in args.only or DEVICES:
            with connect(DEVICES[name], args.username, password, args.known_hosts) as evo:
                check_environment(evo)
                image_id = inspect_image(evo)
            local = Path(folder) / f"{name}.yml"
            local.write_text(yaml.safe_dump(render_profile(name, DEVICES[name], image_id)))
            os.chmod(local, 0o600)
            # Unique staging avoids clobbering a simultaneous invocation.
            remote = run(linux, "mktemp /var/tmp/qbt-compose.XXXXXX.yml")
            try:
                transfer(linux, local, remote)
                run(linux, "docker compose -f " + shlex.quote(remote) + " config --quiet")
            finally:
                run(linux, "rm -f " + shlex.quote(remote))
            print(f"[PLAN] {name}: Compose syntax validated; image pinned to {image_id}")
    print(
        "[DRY-RUN] No containers, secrets, networks or Junos config created. "
        "Host-port mappings and remote Compose transport remain unverified."
    )


COMMANDS = {
    "network": "Attach existing EVO containers to internal ETSI and OOB macvlan networks",
    "check-env": "Read-only Linux/EVO environment and compatibility checks",
    "check-image": "Run QBT version/licence help on EVO without network or volumes",
    "images": "Verify archives, load on Linux, export and copy/load on EVO",
    "copy": "Alias for images; does not start containers",
    "status": "Inspect loaded images and current EVO containers",
    "clean": "Remove owned QBT containers only; retain images/data/config",
    "bootstrap": "Validate deployment with --dry-run; live bootstrap remains gated",
    "pki": "Prepare external lab PKI and import server credentials into existing EVO KMEs",
    "peer": "Configure reciprocal QBT peers, AKE public keys and local/remote SAEs",
    "probe": "Verify an authenticated paired batch of four 256-bit ETSI keys",
    "create": "Generate the standalone on-box script and EVO1/EVO2 profiles from router inventory",
    "deploy": "Install the EVO1/EVO2 on-box runtime and configure the Junos rotation timer",
}

HOWTO = """Quick start:
  1. Trust the Linux/EVO SSH host keys through a verified known_hosts file.
  2. Set EVO_PASSWORD; set QBT_LINUX_PASSWORD if the Linux password differs.
     Passwords are prompted interactively if EVO_PASSWORD is not set.
  3. Run: python qbt_orchestrator.py --check-env
  4. Prepare/copy images (vendor files stay OUTSIDE the repository):
     python qbt_orchestrator.py --copy \\
       --image-archive /tmp/qbt-vendor/image.tar \\
       --deployment-archive /tmp/qbt-vendor/deployment.tar.gz
  5. Inspect: python qbt_orchestrator.py --status
  6. Test binary: python qbt_orchestrator.py --check-image
  7. Validate profile: python qbt_orchestrator.py --bootstrap --dry-run

Existing licensed EVO containers (no container recreation):
  Connect the approved internal ETSI and OOB peer networks:
    python qbt_orchestrator.py --network
  Generate/import dual Root -> Issuing -> Leaf PKI:
    python qbt_orchestrator.py --pki --pki-profile hierarchical_ca \\
      --pki-config config/pki/hierarchical_ca.yml \\
      --pki-dir /private/qbt-hierarchical
  Alternatively, generate/import single-CA lab PKI:
    python qbt_orchestrator.py --pki --pki-profile self_signed \\
      --pki-dir /private/qbt-single-ca
  Explicitly replace already tracked server PKI:
    python qbt_orchestrator.py --pki --pki-profile hierarchical_ca \\
      --pki-dir /private/qbt-hierarchical --rotate-pki

Replace /private/... with a private writable path OUTSIDE this repository.
Use separate directories for the two profiles. Retries preserve existing keys;
changing tracked server PKI requires --rotate-pki. No licence keys are printed.
PKI import alone does not prove that the running TLS listener is ready.

Commands can also be positional, e.g. 'check-env' instead of '--check-env'.
Use --only EVO1 to target one router; --linux-only with copy/images stops
after loading the Linux image. --clean removes only owned QBT containers,
NOT the Junos configuration, volumes, licences, staging or images.

The on-box pair always targets EVO1 and EVO2 from
config/inventory/input/lab_vmm.yaml; `create` writes the standalone script and
two JSON profiles, and `deploy` installs them without recreating containers:
  python qbt_orchestrator.py create
  python qbt_orchestrator.py deploy --pki-dir /private/qbt-lab-pki
Use `deploy --dry-run` to validate the local profiles and PKI without connecting.
`create` and `deploy` always operate on the pair; do not pass `--only`.
Bootstrap remains gated and does not start containers. Peer, probe and deploy
have separate prerequisites and acceptance checks in their action help.
PKI: --pki --pki-profile self_signed|hierarchical_ca --pki-dir /private/pki
Hierarchical generation uses --pki-config; --rotate-pki explicitly replaces
tracked server credentials. Paired SAE/mTLS and four-key ENC/DEC are verified;
live MACsec rotation still requires observation after deploy.
Network: --network attaches approved lab networks without container recreation.
No container is started by --copy. No bulk/PQC/hybrid selector is used.
Detailed reproduction: docs/qbt_lab_reproduction.md
"""


ACTION_HELP = {
    "check-env": """Read-only checks:
  python qbt_orchestrator.py check-env
  python qbt_orchestrator.py check-env --only EVO1
Requires trusted SSH host keys and EVO/Linux credentials.
Checks architecture, Docker, Compose, storage and existing containers.""",
    "check-image": """Isolated binary diagnostics:
  python qbt_orchestrator.py check-image --only EVO1
Requires the supplied image already loaded. Runs temporary diagnostics without
network or persistent mounts; does not remove the persistent KME containers.""",
    "images": """Prepare on Linux and deliver to EVO:
  python qbt_orchestrator.py images --image-archive /private/qbt-image.tar \\
    --deployment-archive /private/qbt-deploy.tar.gz
Add --linux-only to stop after loading Linux, or --only EVO1 for one router.
Requires the original supplied archives and verified checksums.
Loads images only; does not start, recreate or activate KME containers.""",
    "status": """Read-only image and container inspection:
  python qbt_orchestrator.py status
  python qbt_orchestrator.py status --only EVO2
Container running state is not proof of licensing or ETSI readiness.""",
    "clean": """DESTRUCTIVE: removes owned persistent QBT containers:
  python qbt_orchestrator.py clean --only EVO1
Retains data, secrets, licences, images and Junos configuration.
Do NOT run during the current integration without explicit operator approval.
This is not a full lab teardown or a backup procedure.""",
    "bootstrap": """Compose profile validation only:
  python qbt_orchestrator.py bootstrap --dry-run
Requires Linux Compose, SSH access and the loaded EVO images.
Live bootstrap is NOT implemented; it fails explicitly.
Use existing licensed containers for the current integration.""",
    "network": """Attach approved networks to existing containers without recreation:
  python qbt_orchestrator.py network
  python qbt_orchestrator.py network --only EVO1
Requires owned running KMEs and Linux root access for ARP conflict probes.
Internal ETSI: 9.1.1.10 / 9.1.1.11 on jnpr_cntrz_net.
Peer macvlan: 10.38.112.10 / 10.38.112.11 on vmb0.
Host-to-own-macvlan traffic is not a valid connectivity test.
See docs/qbt_lab_reproduction.md for safety and retry details.""",
    "pki": """Generate and import server PKI into existing licensed KMEs:
  python qbt_orchestrator.py pki --pki-profile hierarchical_ca \\
    --pki-config config/pki/hierarchical_ca.yml --pki-dir /private/qbt-hierarchical
  python qbt_orchestrator.py pki --pki-profile self_signed \\
    --pki-dir /private/qbt-single-ca
Use separate private output directories OUTSIDE the repository.
Retries preserve generated keys. To replace tracked server credentials,
explicitly add --rotate-pki. Untracked credentials are not overwritten.
Does not recreate containers, change licences or deploy the SAE runtime.
Import success alone does not establish a running TLS listener.""",
    "peer": """Configure the reciprocal peer/AKE relationship:
  python qbt_orchestrator.py peer
Requires licensed KMEs, verified inter-container connectivity, registered
SAEs and valid PKI. It registers reciprocal KME identities, configures the
selected AKE exchange and exchanges public-key material; it does not transfer
application key bytes.""",
    "probe": """Run the paired ETSI key acceptance check:
  python qbt_orchestrator.py probe
Requires verified PKI/SAE registration, networking and bilateral peering.
Acceptance is four authenticated 256-bit ENC keys and their matching DEC
results by Key-ID on the peer. Key bytes are never logged.""",
    "create": """Generate local runtime artifacts only:
  python qbt_orchestrator.py create
  python qbt_orchestrator.py create --router-inventory config/inventory/input/lab_vmm.yaml
Reads the source router inventory, selects only EVO1/EVO2 and their direct
MACsec link, then writes artifacts/qbt_onbox.py and the two profiles under
config/runtime/EVO1 and config/runtime/EVO2. It does not connect to routers,
change the input inventory or overwrite unrelated runtime profiles.""",
    "deploy": """Install the existing-container EVO1/EVO2 on-box runtime:
  python qbt_orchestrator.py create
  python qbt_orchestrator.py deploy --pki-dir /private/qbt-lab-pki
  python qbt_orchestrator.py deploy --pki-dir /private/qbt-lab-pki --dry-run
Requires trusted SSH host keys, EVO credentials, the external PKI bundle and
profiles generated by `create`. Live deployment installs one standalone
qbt_onbox.py plus two JSON sidecars, the separate ETSI socket helper, and a
60-second Junos event timer. It uses the QBT_EVO/QKD_QBT_EVO association,
preserves a matching existing key-0 seed, and refuses asymmetric/partial state.
It removes only the three known old QBT helper scripts from `op`; any other
Python file left there stops deployment rather than being deleted. It does not
restart, recreate or remove either KME container. Rotation/MKA must be observed
after deployment before being reported as verified.""",
}
ACTION_HELP["copy"] = ACTION_HELP["images"]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=HOWTO + "\nPhases:\n" + "\n".join(
            f"  {name}: {description}" for name, description in COMMANDS.items()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", help="Show general or selected-action instructions and exit")
    parser.add_argument("command", nargs="?", choices=list(COMMANDS))
    actions = parser.add_mutually_exclusive_group()
    for command in COMMANDS:
        actions.add_argument(
            "--" + command, dest="action", action="store_const", const=command,
            help=COMMANDS[command],
        )
    parser.add_argument("--linux-host", default=LINUX_HOST)
    parser.add_argument("--username", default=os.getenv("EVO_USERNAME", "root"))
    parser.add_argument("--linux-username", default="root")
    parser.add_argument("--known-hosts", help="Additional verified SSH known_hosts file")
    parser.add_argument("--only", nargs="+", choices=list(DEVICES))
    parser.add_argument("--linux-only", action="store_true")
    parser.add_argument("--image-archive", type=Path)
    parser.add_argument("--deployment-archive", type=Path)
    parser.add_argument("--license-file", type=Path, help="External licence file; never committed")
    parser.add_argument("--pki-dir", type=Path, help="Private PKI directory OUTSIDE the repository")
    parser.add_argument("--pki-profile", choices=["self_signed", "hierarchical_ca"], default="hierarchical_ca")
    parser.add_argument("--pki-config", type=Path, default=Path(__file__).resolve().parent / "config/pki/hierarchical_ca.yml")
    parser.add_argument("--rotate-pki", action="store_true", help="Explicitly replace tracked server PKI; preserves container and licence")
    parser.add_argument(
        "--router-inventory",
        type=Path,
        default=Path(__file__).resolve().parent / "config/inventory/input/lab_vmm.yaml",
        help="Source router inventory; generated config/runtime files are not inputs",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path(__file__).resolve().parent / "config/runtime",
        help="Directory for generated runtime profiles",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate bootstrap or local QBT deployment inputs without remote changes",
    )
    args = parser.parse_args(argv)
    if args.help:
        selected = args.command or args.action
        if selected:
            parser.description = COMMANDS[selected]
            parser.epilog = ACTION_HELP[selected]
        parser.print_help()
        parser.exit()
    if bool(args.command) == bool(args.action):
        parser.error("Select exactly one command or action flag; see --help")
    args.command = args.command or args.action
    if args.dry_run and args.command not in ("bootstrap", "deploy"):
        parser.error("--dry-run is supported only for bootstrap and deploy")
    if args.linux_only and args.command not in ("images", "copy"):
        parser.error("--linux-only is supported only for images/copy")
    if args.command in ("create", "deploy") and args.only:
        parser.error(f"{args.command} always targets the EVO1/EVO2 pair; omit --only")
    if args.command == "bootstrap":
        try:
            require_bootstrap_inputs(args)
        except (QbtError, OSError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
    if args.command == "copy":
        args.command = "images"
    if args.command == "images" and (not args.image_archive or not args.deployment_archive):
        parser.error("images requires --image-archive and --deployment-archive")
    if args.command == "pki":
        if not args.pki_dir:
            parser.error("pki requires --pki-dir outside the repository")
        if args.pki_dir.resolve().is_relative_to(Path(__file__).resolve().parent):
            parser.error("PKI private keys must be stored outside the repository")
    if args.command == "deploy":
        if not args.pki_dir:
            parser.error("deploy requires --pki-dir outside the repository")
        if args.pki_dir.resolve().is_relative_to(Path(__file__).resolve().parent):
            parser.error("PKI private keys must be stored outside the repository")
    if args.command == "create":
        try:
            from lib.qbt.macsec import create_runtime_profiles

            profiles = create_runtime_profiles(
                runtime_root=args.runtime_root,
                inventory_path=args.router_inventory,
            )
            for name, directory in profiles.items():
                print(f"[OK] {name}: generated standalone runtime and JSON profiles in {directory}")
            return 0
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
    if args.command == "deploy" and args.dry_run:
        try:
            from lib.qbt.macsec import (
                load_target_devices,
                validate_pki_bundle,
                validate_runtime_profiles,
            )

            load_target_devices(args.router_inventory)
            validate_runtime_profiles(args.runtime_root, args.router_inventory)
            pki = validate_pki_bundle(args.pki_dir)
            print(
                "[DRY-RUN] EVO1/EVO2 profiles and PKI are valid; "
                f"inventory={args.router_inventory}, pki={pki}. No remote connection made."
            )
            return 0
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
    password = os.getenv("EVO_PASSWORD")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("EVO password: ")
    linux_password = os.getenv("QBT_LINUX_PASSWORD") or password
    if not password or not linux_password:
        parser.error("Set EVO_PASSWORD and, if different, QBT_LINUX_PASSWORD")
    try:
        if args.command == "deploy":
            from contextlib import ExitStack
            from lib.qbt.macsec import deploy_runtime, load_target_devices

            devices = load_target_devices(args.router_inventory)
            with ExitStack() as stack:
                clients = {
                    name: stack.enter_context(connect(
                        devices[name]["ip"],
                        args.username,
                        password,
                        args.known_hosts,
                    ))
                    for name in ("EVO1", "EVO2")
                }
                deploy_runtime(
                    clients,
                    args.router_inventory,
                    password,
                    args.pki_dir,
                    args.runtime_root,
                    run,
                    transfer,
                )
            return 0
        if args.command in ("peer", "probe"):
            from contextlib import ExitStack
            with ExitStack() as stack:
                clients = {
                    name: stack.enter_context(connect(
                        DEVICES[name], args.username, password, args.known_hosts
                    )) for name in args.only or DEVICES
                }
                if args.command == "peer":
                    configure_peers(clients, run)
                else:
                    paired_probe(clients, run, transfer)
            return 0
        if args.command == "pki":
            directory = (
                prepare_hierarchical(args.pki_dir, args.pki_config)
                if args.pki_profile == "hierarchical_ca" else prepare_pki(args.pki_dir)
            )
            for name in args.only or DEVICES:
                with connect(DEVICES[name], args.username, password, args.known_hosts) as evo:
                    import_pki(evo, name, directory, run, transfer, rotate=args.rotate_pki)
            return 0
        if args.command == "network":
            network(args, password)
            return 0
        with connect(args.linux_host, args.linux_username, linux_password, args.known_hosts) as linux:
            if args.command == "images":
                prepare_images(args, linux, password)
            elif args.command == "clean":
                clean(args, linux, password)
            elif args.command == "status":
                status(args, linux, password)
            elif args.command == "check-image":
                check_image(args, password)
            elif args.command == "bootstrap":
                bootstrap_plan(args, linux, password)
            else:
                print("Linux Compose:", run(linux, "docker compose version"))
                for name in args.only or DEVICES:
                    with connect(DEVICES[name], args.username, password, args.known_hosts) as evo:
                        print(name, json.dumps(check_environment(evo), indent=2))
        return 0
    except (RuntimeError, paramiko.SSHException, OSError, tarfile.TarError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
