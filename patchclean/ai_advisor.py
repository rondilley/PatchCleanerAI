"""Use Claude AI to analyze UNKNOWN installer files."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import win32com.client
from anthropic import APIConnectionError, APIError, Anthropic, RateLimitError

from patchclean.models import Classification, FileType, InstallerFile

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """\
You are a Windows Installer analysis expert. Given metadata about an MSI/MSP file \
from C:\\Windows\\Installer, determine whether it is likely still needed (KNOWN) or \
safe to remove (ORPHANED).

Respond with a JSON object only, no markdown fences or extra text:
{"classification": "KNOWN" or "ORPHANED", "confidence": 0.0-1.0, "reasoning": "brief explanation"}
"""

# Regex to extract the first JSON object from a response that may have markdown fences
_JSON_RE = re.compile(r"\{[^{}]*\}")


def _extract_msi_metadata(path: Path) -> dict[str, str]:
    """Open an MSI database via COM and read the Property table."""
    props: dict[str, str] = {}
    try:
        installer = win32com.client.Dispatch("WindowsInstaller.Installer")
        # msiOpenDatabaseModeReadOnly = 0
        db = installer.OpenDatabase(str(path), 0)
        view = db.OpenView("SELECT Property, Value FROM Property")
        view.Execute(None)
        while True:
            record = view.Fetch()
            if record is None:
                break
            props[record.StringData(1)] = record.StringData(2)
        view.Close()
    except Exception:
        props["_error"] = "Could not read MSI database"
    return props


def _extract_msp_metadata(path: Path) -> dict[str, str]:
    """Open an MSP database via COM and read the MsiPatchMetadata table."""
    props: dict[str, str] = {}
    try:
        installer = win32com.client.Dispatch("WindowsInstaller.Installer")
        # msiOpenDatabaseModeReadOnly = 0
        db = installer.OpenDatabase(str(path), 0)
        view = db.OpenView(
            "SELECT Company, Property, Value FROM MsiPatchMetadata"
        )
        view.Execute(None)
        while True:
            record = view.Fetch()
            if record is None:
                break
            company = record.StringData(1)
            prop = record.StringData(2)
            key = f"{company}.{prop}" if company else prop
            props[key] = record.StringData(3)
        view.Close()
    except Exception:
        props["_error"] = "Could not read MSP database"
    return props


def _parse_json_response(text: str) -> dict:
    """Parse a JSON object from an AI response, handling markdown fences."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Extract first JSON object from fenced/wrapped response
    match = _JSON_RE.search(text)
    if match:
        return json.loads(match.group())
    raise json.JSONDecodeError("No JSON object found in response", text, 0)


def _sanitize_metadata(metadata: dict[str, str]) -> dict[str, str]:
    """Truncate long values to limit prompt injection surface."""
    sanitized: dict[str, str] = {}
    for k, v in metadata.items():
        # Keep only reasonable-length values, skip internal error keys
        if len(v) > 200:
            v = v[:200] + "...(truncated)"
        sanitized[k] = v
    return sanitized


def analyze_unknown_files(
    files: list[InstallerFile],
    api_key: str,
) -> None:
    """For each UNKNOWN file, query Claude and update classification in-place."""
    unknowns = [f for f in files if f.classification == Classification.UNKNOWN]
    if not unknowns:
        return

    client = Anthropic(api_key=api_key)

    for f in unknowns:
        if f.file_type == FileType.MSP:
            metadata = _extract_msp_metadata(f.path)
        else:
            metadata = _extract_msi_metadata(f.path)
        metadata["_file_name"] = f.path.name
        metadata["_file_size"] = str(f.size_bytes)
        metadata["_file_type"] = f.file_type.value

        metadata = _sanitize_metadata(metadata)

        user_msg = (
            "Analyze this Windows Installer file and classify it.\n\n"
            f"Metadata:\n```json\n{json.dumps(metadata, indent=2)}\n```"
        )

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = response.content[0].text
            result = _parse_json_response(text)
            classification = result.get("classification", "").upper()
            if classification in ("KNOWN", "ORPHANED"):
                f.classification = Classification(classification)
            confidence = result.get("confidence", 0)
            f.ai_confidence = max(0.0, min(1.0, float(confidence)))
            f.ai_reasoning = str(result.get("reasoning", ""))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            log.warning("Failed to parse AI response for %s: %s", f.path.name, exc)
            f.ai_reasoning = f"Parse error: {type(exc).__name__}"
        except RateLimitError:
            log.warning("Rate limited by API -- skipping remaining AI analysis")
            break
        except APIConnectionError as exc:
            log.warning("API connection error: %s", exc)
            break
        except APIError as exc:
            log.warning("API error for %s: status=%s", f.path.name, getattr(exc, "status_code", "unknown"))
            f.ai_reasoning = f"API error (status {getattr(exc, 'status_code', 'unknown')})"
