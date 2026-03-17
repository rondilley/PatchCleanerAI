"""GUID <-> SQUID (compressed GUID) conversion.

Windows Installer stores GUIDs in a compressed form called a SQUID.
The transformation is its own inverse — applying it twice returns the original.

Algorithm:
  GUID: {A638BC3B-72C3-4EEF-90DD-0683232E396C}
  Strip braces/hyphens: A638BC3B72C34EEF90DD0683232E396C
  Group1 (8 chars): reverse entire group
  Group2 (4 chars): reverse entire group
  Group3 (4 chars): reverse entire group
  Group4 (4 chars): pairwise character swap
  Group5 (12 chars): pairwise character swap
"""

from __future__ import annotations


def _reverse(s: str) -> str:
    return s[::-1]


def _pair_swap(s: str) -> str:
    out: list[str] = []
    for i in range(0, len(s), 2):
        out.append(s[i + 1])
        out.append(s[i])
    return "".join(out)


def _validate_hex(s: str, label: str) -> None:
    try:
        int(s, 16)
    except ValueError:
        raise ValueError(f"Invalid {label} (non-hex characters): {s}") from None


def guid_to_squid(guid: str) -> str:
    """Convert a standard GUID like ``{XXXXXXXX-...}`` to a SQUID."""
    stripped = guid.removeprefix("{").removesuffix("}")
    raw = stripped.replace("-", "").upper()
    if len(raw) != 32:
        raise ValueError(f"Invalid GUID (expected 32 hex chars): {guid}")
    _validate_hex(raw, "GUID")
    g1, g2, g3, g4, g5 = raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:]
    return _reverse(g1) + _reverse(g2) + _reverse(g3) + _pair_swap(g4) + _pair_swap(g5)


def squid_to_guid(squid: str) -> str:
    """Convert a SQUID back to a standard ``{XXXXXXXX-...}`` GUID."""
    squid = squid.upper()
    if len(squid) != 32:
        raise ValueError(f"Invalid SQUID (expected 32 hex chars): {squid}")
    _validate_hex(squid, "SQUID")
    g1, g2, g3, g4, g5 = squid[:8], squid[8:12], squid[12:16], squid[16:20], squid[20:]
    raw = _reverse(g1) + _reverse(g2) + _reverse(g3) + _pair_swap(g4) + _pair_swap(g5)
    return "{%s-%s-%s-%s-%s}" % (raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:])
