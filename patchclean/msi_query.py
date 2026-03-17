"""Query registered MSI products and patches via Windows Installer COM + registry.

Builds a dict mapping normalized file paths to metadata (product name, GUIDs).
"""

from __future__ import annotations

import ctypes
import logging
import winreg
from pathlib import Path
from typing import TypedDict

import win32com.client

from patchclean.squid import squid_to_guid

log = logging.getLogger(__name__)

USER_DATA_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData"

# Properly declare ctypes signatures for GetLongPathNameW
_GetLongPathNameW = ctypes.windll.kernel32.GetLongPathNameW  # type: ignore[attr-defined]
_GetLongPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
_GetLongPathNameW.restype = ctypes.c_ulong


class FileInfo(TypedDict, total=False):
    product_name: str
    product_guid: str
    patch_guid: str


def normalize_path(p: Path) -> str:
    """Lowercase, resolve, and expand 8.3 short names for consistent comparison."""
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    try:
        buf = ctypes.create_unicode_buffer(512)
        length = _GetLongPathNameW(str(resolved), buf, 512)
        if 0 < length <= 512:
            resolved = Path(buf.value)
    except Exception:
        pass
    return str(resolved).lower()


def query_registered_files() -> tuple[dict[str, FileInfo], list[str]]:
    """Return ({normalized_path: FileInfo}, errors)."""
    registered: dict[str, FileInfo] = {}
    errors: list[str] = []

    _query_com(registered, errors)
    _query_registry(registered, errors)

    return registered, errors


# ---------------------------------------------------------------------------
# COM-based enumeration
# ---------------------------------------------------------------------------

def _query_com(registered: dict[str, FileInfo], errors: list[str]) -> None:
    try:
        installer = win32com.client.Dispatch("WindowsInstaller.Installer")
    except Exception as exc:
        errors.append(f"Cannot create WindowsInstaller.Installer COM object: {exc}")
        return

    # --- Products ---
    try:
        products = installer.Products
    except Exception as exc:
        errors.append(f"Cannot enumerate products: {exc}")
        products = []

    for product_code in products:
        try:
            local_package = installer.ProductInfo(product_code, "LocalPackage")
        except Exception:
            continue
        if not local_package:
            continue

        try:
            product_name = installer.ProductInfo(product_code, "ProductName")
        except Exception:
            product_name = ""

        key = normalize_path(Path(local_package))
        registered[key] = FileInfo(
            product_name=product_name or "",
            product_guid=product_code,
        )

        # --- Patches for this product ---
        _query_patches_for_product(installer, product_code, product_name, registered, errors)


def _query_patches_for_product(
    installer: object,
    product_code: str,
    product_name: str,
    registered: dict[str, FileInfo],
    errors: list[str],
) -> None:
    # Try PatchesEx first (richer API), fall back to Patches
    try:
        patches = installer.PatchesEx(product_code, None, 7, 0)  # type: ignore[attr-defined]
        for patch in patches:
            try:
                local_pkg = patch.PatchProperty("LocalPackage")
            except Exception as exc:
                log.debug("Cannot read patch LocalPackage: %s", exc)
                continue
            if not local_pkg:
                continue
            key = normalize_path(Path(local_pkg))
            if key not in registered:
                registered[key] = FileInfo(
                    product_name=product_name or "",
                    product_guid=product_code,
                    patch_guid=patch.PatchCode,
                )
        return
    except Exception as exc:
        log.debug("PatchesEx failed for %s, falling back to Patches: %s", product_code, exc)

    # Fallback: Patches (returns just patch codes)
    try:
        patches = installer.Patches(product_code)  # type: ignore[attr-defined]
    except Exception:
        return

    for patch_code in patches:
        try:
            local_pkg = installer.PatchInfo(patch_code, "LocalPackage")  # type: ignore[attr-defined]
        except Exception:
            continue
        if not local_pkg:
            continue
        key = normalize_path(Path(local_pkg))
        if key not in registered:
            registered[key] = FileInfo(
                product_name=product_name or "",
                product_guid=product_code,
                patch_guid=patch_code,
            )


# ---------------------------------------------------------------------------
# Registry-based enumeration (supplementary)
# ---------------------------------------------------------------------------

def _query_registry(registered: dict[str, FileInfo], errors: list[str]) -> None:
    try:
        ud_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, USER_DATA_KEY)
    except OSError:
        errors.append("Cannot open Installer UserData registry key")
        return

    with ud_key:
        idx = 0
        while True:
            try:
                sid = winreg.EnumKey(ud_key, idx)
            except OSError:
                break
            idx += 1
            _query_sid_products(sid, registered, errors)
            _query_sid_patches(sid, registered, errors)


def _query_sid_products(sid: str, registered: dict[str, FileInfo], errors: list[str]) -> None:
    products_path = rf"{USER_DATA_KEY}\{sid}\Products"
    try:
        products_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, products_path)
    except OSError:
        return

    with products_key:
        idx = 0
        while True:
            try:
                squid = winreg.EnumKey(products_key, idx)
            except OSError:
                break
            idx += 1

            install_path = rf"{products_path}\{squid}\InstallProperties"
            try:
                ip_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, install_path)
            except OSError:
                continue

            with ip_key:
                local_package = _read_reg_value(ip_key, "LocalPackage")
                if not local_package:
                    continue
                key = normalize_path(Path(local_package))
                if key in registered:
                    continue
                try:
                    guid = squid_to_guid(squid)
                except ValueError:
                    continue
                display_name = _read_reg_value(ip_key, "DisplayName") or ""
                registered[key] = FileInfo(
                    product_name=display_name,
                    product_guid=guid,
                )


def _query_sid_patches(sid: str, registered: dict[str, FileInfo], errors: list[str]) -> None:
    patches_path = rf"{USER_DATA_KEY}\{sid}\Patches"
    try:
        patches_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, patches_path)
    except OSError:
        return

    with patches_key:
        idx = 0
        while True:
            try:
                squid = winreg.EnumKey(patches_key, idx)
            except OSError:
                break
            idx += 1

            patch_sub = rf"{patches_path}\{squid}"
            try:
                pk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, patch_sub)
            except OSError:
                continue

            with pk:
                local_package = _read_reg_value(pk, "LocalPackage")
                if not local_package:
                    continue
                key = normalize_path(Path(local_package))
                if key in registered:
                    continue
                try:
                    guid = squid_to_guid(squid)
                except ValueError:
                    continue
                registered[key] = FileInfo(patch_guid=guid)


def _read_reg_value(key: winreg.HKEYType, name: str) -> str | None:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value if isinstance(value, str) else None
    except OSError:
        return None
