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
    "check-env": "Read-only Linux/EVO environment and compatibility checks",
    "check-image": "Run QBT version/licence help on EVO without network or volumes",
    "images": "Verify archives, load on Linux, export and copy/load on EVO",
    "copy": "Alias for images; does not start containers",
    "status": "Inspect loaded images and current EVO containers",
    "clean": "Remove owned QBT containers only; retain images/data/config",
    "bootstrap": "Validate deployment with --dry-run; live bootstrap remains gated",
    "pki": "Reserved certificate and SAE provisioning phase",
    "peer": "Reserved bilateral KME mesh/AKE provisioning phase",
    "probe": "Reserved authenticated paired ETSI enc/dec acceptance phase",
    "deploy": "Reserved four-slot Junos keyring deployment phase",
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

Commands can also be positional, e.g. 'check-env' instead of '--check-env'.
Use --only EVO1 to target one router; --linux-only with copy/images stops
after loading the Linux image. --clean removes only owned QBT containers,
NOT the Junos configuration, volumes, licences, staging or images.

Live bootstrap/pki/peer/probe/deploy are NOT working deployment helpers yet.
They return an explicit error until implemented and validated.
No container is started by --copy. No bulk/PQC/hybrid selector is used.
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=HOWTO + "\nPhases:\n" + "\n".join(
            f"  {name}: {description}" for name, description in COMMANDS.items()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    parser.add_argument("--dry-run", action="store_true", help="Validate bootstrap profiles without deployment")
    args = parser.parse_args(argv)
    if bool(args.command) == bool(args.action):
        parser.error("Select exactly one command or action flag; see --help")
    args.command = args.command or args.action
    if args.dry_run and args.command != "bootstrap":
        parser.error("--dry-run is currently supported only for bootstrap")
    if args.linux_only and args.command not in ("images", "copy"):
        parser.error("--linux-only is supported only for images/copy")
    if args.command in ("pki", "peer", "probe", "deploy"):
        print(
            f"ERROR: {args.command} is not implemented or validated yet; no changes made",
            file=sys.stderr,
        )
        return 1
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
    password = os.getenv("EVO_PASSWORD")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("EVO password: ")
    linux_password = os.getenv("QBT_LINUX_PASSWORD") or password
    if not password or not linux_password:
        parser.error("Set EVO_PASSWORD and, if different, QBT_LINUX_PASSWORD")
    try:
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
    except (QbtError, paramiko.SSHException, OSError, tarfile.TarError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
