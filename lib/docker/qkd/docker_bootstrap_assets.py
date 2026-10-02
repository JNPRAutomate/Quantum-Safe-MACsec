"""Local handling for customer-supplied PhioTX image and licence bundles."""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from lib.docker.qkd.docker_paths import DOCKER_RUNTIME_DIR


BUNDLE_STAGE = DOCKER_RUNTIME_DIR / "bootstrap_bundle"
IMAGE_SUFFIXES = (".tar", ".tar.gz", ".tgz")
CHECKSUM_SUFFIXES = (".sha256", ".sha512")


class BootstrapAssetError(RuntimeError):
    """Raised when a supplied vendor bundle is unsafe or incomplete."""


def _is_image_filename(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(IMAGE_SUFFIXES)


def _is_container_image_archive(path: Path) -> bool:
    if not _is_image_filename(path):
        return False
    try:
        with tarfile.open(path, "r:*") as archive:
            roots = {
                member.name.lstrip("./").split("/", 1)[0]
                for member in archive.getmembers()
            }
    except (tarfile.TarError, OSError):
        return False
    return "manifest.json" in roots or "oci-layout" in roots


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksum(image: Path, algorithm: str) -> bool:
    sidecar = image.with_name(f"{image.name}.{algorithm}")
    if not sidecar.is_file():
        return False
    fields = sidecar.read_text(encoding="utf-8").strip().split()
    if not fields:
        raise BootstrapAssetError(f"Empty checksum sidecar: {sidecar}")
    expected = fields[0].lower()
    actual = _digest(image, algorithm)
    if actual != expected:
        raise BootstrapAssetError(
            f"{algorithm.upper()} mismatch for supplied image {image.name}"
        )
    return True


def _secure_stage_directory() -> Path:
    if BUNDLE_STAGE.exists():
        shutil.rmtree(BUNDLE_STAGE)
    BUNDLE_STAGE.mkdir(parents=True, mode=0o700)
    os.chmod(BUNDLE_STAGE, 0o700)
    return BUNDLE_STAGE


def _extract_zip_bundle(bundle: Path) -> Path:
    stage = _secure_stage_directory()
    selected: Dict[str, zipfile.ZipInfo] = {}

    with zipfile.ZipFile(bundle) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise BootstrapAssetError(
                    f"Unsafe path in vendor ZIP bundle: {member.filename}"
                )
            name = member_path.name
            lower = name.lower()
            if not (
                lower.endswith(IMAGE_SUFFIXES)
                or lower.endswith(CHECKSUM_SUFFIXES)
                or lower.endswith(".lic")
            ):
                continue
            if name in selected:
                raise BootstrapAssetError(
                    f"Duplicate bootstrap asset name in ZIP: {name}"
                )
            selected[name] = member

        if not selected:
            raise BootstrapAssetError(
                f"No image, checksum, or licence assets found in {bundle}"
            )

        for name, member in selected.items():
            destination = stage / name
            with archive.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            os.chmod(destination, 0o600)

    return stage


def prepare_bootstrap_bundle(bundle_path) -> Dict[str, object]:
    """
    Prepare a ZIP or directory containing an image and per-node licences.

    ZIP files are selectively extracted under ignored Docker runtime state.
    Directories are read in place. Exactly one valid Docker/OCI image archive
    must be present; unrelated deployment archives are ignored.
    """
    bundle = Path(bundle_path).expanduser().resolve()
    if not bundle.exists():
        raise FileNotFoundError(f"Bootstrap bundle not found: {bundle}")

    extracted: Optional[Path] = None
    if bundle.is_file() and bundle.suffix.lower() == ".zip":
        source = _extract_zip_bundle(bundle)
        extracted = source
    elif bundle.is_dir():
        source = bundle
    else:
        raise BootstrapAssetError(
            "Bootstrap bundle must be a .zip file or a directory containing "
            "the supplied image and licences"
        )

    files = [path for path in source.rglob("*") if path.is_file()]
    images = [path for path in files if _is_container_image_archive(path)]
    if len(images) != 1:
        raise BootstrapAssetError(
            f"Expected exactly one Docker/OCI image archive in {bundle}; "
            f"found {len(images)}"
        )

    image = images[0]
    verified = [
        algorithm
        for algorithm in ("sha256", "sha512")
        if _verify_checksum(image, algorithm)
    ]
    if not verified:
        raise BootstrapAssetError(
            f"No checksum sidecar found for supplied image {image.name}"
        )

    licenses: List[Path] = sorted(
        path for path in files if path.suffix.lower() == ".lic"
    )
    if not licenses:
        raise BootstrapAssetError(f"No PhioTX licence files found in {bundle}")

    print(
        f"Prepared vendor bundle: image={image.name}, "
        f"checksums={'+'.join(verified)}, licences={len(licenses)}"
    )
    return {
        "image_archive": image,
        "license_dir": source,
        "license_files": licenses,
        "extracted_dir": extracted,
    }


def cleanup_bootstrap_bundle(prepared: Optional[Dict[str, object]]) -> None:
    """Remove only the ignored extraction directory created by this module."""
    if not prepared:
        return
    extracted = prepared.get("extracted_dir")
    if not extracted:
        return
    extracted = Path(extracted).resolve()
    stage = BUNDLE_STAGE.resolve()
    if extracted != stage:
        raise BootstrapAssetError(
            f"Refusing to remove unexpected bootstrap path: {extracted}"
        )
    shutil.rmtree(extracted, ignore_errors=True)
