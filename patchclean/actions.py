"""Move/delete operations for orphaned installer files."""

from __future__ import annotations

import ctypes
import logging
import shutil
from pathlib import Path

from patchclean.models import InstallerFile
from patchclean.scanner import INSTALLER_DIR

log = logging.getLogger(__name__)


def is_admin() -> bool:
    """Return True if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _is_under_installer_dir(path: Path) -> bool:
    """Check that *path* resolves to a location within the Installer directory."""
    try:
        resolved = path.resolve()
        installer_resolved = INSTALLER_DIR.resolve()
        return str(resolved).lower().startswith(str(installer_resolved).lower() + "\\")
    except OSError:
        return False


def move_files(
    files: list[InstallerFile],
    archive_dir: Path,
    dry_run: bool = False,
) -> list[tuple[InstallerFile, bool, str]]:
    """Move files to *archive_dir*. Returns list of (file, success, message)."""
    results: list[tuple[InstallerFile, bool, str]] = []

    if not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        # Safety: reject symlinks and paths outside Installer dir
        if f.path.is_symlink():
            results.append((f, False, "Skipped: symlink"))
            continue
        if not _is_under_installer_dir(f.path):
            results.append((f, False, f"Skipped: path outside {INSTALLER_DIR}"))
            continue

        # Avoid filename collisions in the archive
        dest = archive_dir / f.path.name
        if dest.exists():
            stem = f.path.stem
            suffix = f.path.suffix
            counter = 1
            while dest.exists():
                dest = archive_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        if dry_run:
            results.append((f, True, f"[dry-run] Would move to {dest}"))
            continue
        try:
            shutil.move(str(f.path), str(dest))
            results.append((f, True, f"Moved to {dest}"))
            log.info("Moved %s -> %s", f.path, dest)
        except Exception as exc:
            results.append((f, False, str(exc)))
            log.error("Failed to move %s: %s", f.path, exc)

    return results


def delete_files(
    files: list[InstallerFile],
    dry_run: bool = False,
) -> list[tuple[InstallerFile, bool, str]]:
    """Permanently delete files. Returns list of (file, success, message)."""
    results: list[tuple[InstallerFile, bool, str]] = []

    for f in files:
        # Safety: reject symlinks and paths outside Installer dir
        if f.path.is_symlink():
            results.append((f, False, "Skipped: symlink"))
            continue
        if not _is_under_installer_dir(f.path):
            results.append((f, False, f"Skipped: path outside {INSTALLER_DIR}"))
            continue

        if dry_run:
            results.append((f, True, "[dry-run] Would delete"))
            continue
        try:
            f.path.unlink()
            results.append((f, True, "Deleted"))
            log.info("Deleted %s", f.path)
        except Exception as exc:
            results.append((f, False, str(exc)))
            log.error("Failed to delete %s: %s", f.path, exc)

    return results
