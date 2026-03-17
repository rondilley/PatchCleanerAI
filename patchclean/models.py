"""Data models for PatchClean AI."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


class FileType(enum.Enum):
    MSI = "msi"
    MSP = "msp"


class Classification(enum.Enum):
    KNOWN = "KNOWN"
    ORPHANED = "ORPHANED"
    UNKNOWN = "UNKNOWN"


@dataclass
class InstallerFile:
    path: Path
    file_type: FileType
    size_bytes: int
    modified: datetime
    classification: Classification = Classification.UNKNOWN
    product_name: str | None = None
    product_guid: str | None = None
    patch_guid: str | None = None
    ai_confidence: float | None = None
    ai_reasoning: str | None = None


@dataclass
class ScanResult:
    files: list[InstallerFile] = field(default_factory=list)
    known_size: int = 0
    orphaned_size: int = 0
    unknown_size: int = 0
    errors: list[str] = field(default_factory=list)

    def recompute_sizes(self) -> None:
        self.known_size = sum(
            f.size_bytes for f in self.files if f.classification == Classification.KNOWN
        )
        self.orphaned_size = sum(
            f.size_bytes for f in self.files if f.classification == Classification.ORPHANED
        )
        self.unknown_size = sum(
            f.size_bytes for f in self.files if f.classification == Classification.UNKNOWN
        )
