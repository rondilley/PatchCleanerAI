"""Cross-reference scanned files against registered products/patches."""

from __future__ import annotations

import logging

from patchclean.models import Classification, InstallerFile, ScanResult
from patchclean.msi_query import FileInfo, normalize_path, query_registered_files

log = logging.getLogger(__name__)


def analyze(files: list[InstallerFile]) -> ScanResult:
    """Classify each file as KNOWN or ORPHANED and return a ScanResult."""
    registered, reg_errors = query_registered_files()

    result = ScanResult(errors=list(reg_errors))

    for f in files:
        key = normalize_path(f.path)
        info: FileInfo | None = registered.get(key)

        if info is not None:
            f.classification = Classification.KNOWN
            f.product_name = info.get("product_name")
            f.product_guid = info.get("product_guid")
            f.patch_guid = info.get("patch_guid")
        else:
            f.classification = Classification.ORPHANED

        result.files.append(f)

    result.recompute_sizes()
    return result
