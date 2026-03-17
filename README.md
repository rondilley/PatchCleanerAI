# PatchClean AI

A Python replacement for [PatchCleaner](https://www.homedev.com.au/Free/PatchCleaner) that scans `C:\Windows\Installer`, identifies orphaned `.msi` and `.msp` files no longer referenced by any installed product, and lets you safely reclaim disk space by archiving or deleting them.

Optionally uses Claude AI to analyze files that can't be classified through normal means.

## Why?

Windows never cleans up `C:\Windows\Installer`. Over time it accumulates gigabytes of orphaned installer files from products that have been uninstalled or superseded. PatchClean AI identifies which files are still needed and which are safe to remove.

## Requirements

- Windows 10/11
- Python 3.10+
- Administrator privileges (for move/delete operations)

## Installation

```bash
git clone <repo-url>
cd PatchClean_AI
pip install -e .
```

## Usage

### Scan and classify

```bash
python -m patchclean scan
```

Displays a Rich table showing every file in `C:\Windows\Installer` with its classification:

- **KNOWN** (green) -- referenced by an installed product or patch
- **ORPHANED** (red) -- not referenced by anything; safe to remove
- **UNKNOWN** (yellow) -- could not be determined (scan/query errors)

A summary panel shows file counts and potential disk savings.

### AI-assisted analysis

```bash
python -m patchclean scan --ai
```

Files classified as UNKNOWN are sent to Claude AI for analysis. The AI reads MSI metadata (product name, manufacturer, version, etc.) and provides a classification with confidence score.

Requires an Anthropic API key in `claude.key.txt`, `.env`, or the `ANTHROPIC_API_KEY` environment variable.

### Move orphans to archive

```bash
python -m patchclean move                             # interactive confirmation
python -m patchclean move --archive-dir D:\msi_backup  # custom archive location
python -m patchclean move --dry-run                    # preview only
```

Moves orphaned files to an archive directory (default: `C:\Windows\Installer\_archive`). Requires admin. Files can be restored by moving them back.

### Delete orphans permanently

```bash
python -m patchclean delete            # requires typing "YES" to confirm
python -m patchclean delete --dry-run  # preview only
```

Permanently deletes orphaned files. Requires admin. **This cannot be undone.**

### JSON output

```bash
python -m patchclean scan --json
```

Outputs structured JSON with file details, classifications, sizes, and any errors. Useful for scripting or piping to other tools.

### All flags

| Flag | Subcommands | Description |
|------|-------------|-------------|
| `--ai` | scan, move, delete | Enable Claude AI analysis for UNKNOWN files |
| `--json` | scan, move, delete | Output results as JSON |
| `--dry-run` | scan, move, delete | Preview changes without modifying files |
| `--archive-dir PATH` | move | Set archive directory (default: `C:\Windows\Installer\_archive`) |

## How it works

1. **Scan** -- `os.scandir()` collects all `.msi`/`.msp` files from `C:\Windows\Installer` and its `$PatchCache$` subdirectory.

2. **Query** -- The Windows Installer COM API (`WindowsInstaller.Installer`) enumerates every registered product and patch, collecting their `LocalPackage` paths. A supplementary registry walk covers entries the COM API may miss.

3. **Classify** -- Each scanned file is matched against the registered set. Matches are KNOWN; unmatched files are ORPHANED; files with errors stay UNKNOWN.

4. **AI (optional)** -- UNKNOWN files have their installer metadata extracted (MSI Property table or MSP MsiPatchMetadata table) and sent to Claude for classification. Metadata values are truncated to limit prompt injection surface.

5. **Act** -- Orphaned files can be moved to an archive directory or permanently deleted. Safety checks validate that files are within the Installer directory, reject symlinks, and handle filename collisions in the archive. Confirmation prompts are required before any destructive action.

## Project structure

```
PatchClean_AI/
  pyproject.toml          Package metadata and dependencies
  CLAUDE.md               AI assistant project context
  README.md               This file
  VIBE_HISTORY.md         Development history
  doc/
    ARCHITECTURE.md       Detailed architecture documentation
  patchclean/
    __init__.py
    __main__.py           Entry point
    models.py             Data models and enums
    squid.py              GUID <-> SQUID conversion
    config.py             API key loading
    scanner.py            Filesystem scanning
    msi_query.py          COM + registry queries
    analyzer.py           File classification
    ai_advisor.py         Claude AI integration
    actions.py            Move/delete operations
    cli.py                CLI and terminal UI
```

## License

This project is provided as-is for personal use. Use at your own risk. Always verify classifications before deleting files from `C:\Windows\Installer`.
