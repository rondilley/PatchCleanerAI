"""Scan C:\\Windows\\Installer for .msi and .msp files."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from patchclean.models import FileType, InstallerFile

INSTALLER_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Installer"


def scan_installer_dir(
    base_dir: Path = INSTALLER_DIR,
) -> tuple[list[InstallerFile], list[str]]:
    """Return (files, errors) from scanning the Installer directory."""
    files: list[InstallerFile] = []
    errors: list[str] = []

    patch_cache_dir = base_dir / "$PatchCache$"

    for scan_dir in (base_dir, patch_cache_dir):
        if not scan_dir.is_dir():
            continue
        _scan_flat(scan_dir, files, errors)
        # Also scan immediate subdirectories of $PatchCache$
        if scan_dir == patch_cache_dir:
            try:
                for entry in os.scandir(scan_dir):
                    if entry.is_dir():
                        _scan_flat(Path(entry.path), files, errors)
            except PermissionError as exc:
                errors.append(f"Permission denied: {exc}")

    return files, errors


def _scan_flat(
    directory: Path,
    files: list[InstallerFile],
    errors: list[str],
) -> None:
    try:
        entries = os.scandir(directory)
    except PermissionError as exc:
        errors.append(f"Permission denied: {exc}")
        return
    except OSError as exc:
        errors.append(f"OS error scanning {directory}: {exc}")
        return

    with entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            lower = entry.name.lower()
            if lower.endswith(".msi"):
                ft = FileType.MSI
            elif lower.endswith(".msp"):
                ft = FileType.MSP
            else:
                continue
            try:
                stat = entry.stat()
                files.append(
                    InstallerFile(
                        path=Path(entry.path),
                        file_type=ft,
                        size_bytes=stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    )
                )
            except OSError as exc:
                errors.append(f"Cannot stat {entry.path}: {exc}")
