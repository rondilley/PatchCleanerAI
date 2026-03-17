# Architecture

## Overview

PatchClean AI is a pipeline with four stages: **Scan**, **Query**, **Classify**, and **Act**. Each stage is handled by a dedicated module, with data flowing through shared dataclasses.

```
                    +-----------+
                    |  scanner  |  Scan C:\Windows\Installer
                    +-----+-----+
                          |
                    list[InstallerFile]
                          |
                    +-----v-----+
                    | msi_query |  Query COM + registry
                    +-----+-----+
                          |
                    dict{path: FileInfo}
                          |
                    +-----v-----+
                    |  analyzer |  Cross-reference & classify
                    +-----+-----+
                          |
                       ScanResult
                          |
              +-----------+-----------+
              |                       |
        +-----v-----+          +-----v-----+
        | ai_advisor |          |    cli    |  Display results
        | (optional) |          +-----+-----+
        +-----+-----+                |
              |                 +-----v-----+
         ScanResult             |  actions  |  Move / delete
         (updated)              +-----------+
```

## Data models (`models.py`)

### Enums

- **`FileType`** -- `MSI` or `MSP`
- **`Classification`** -- `KNOWN`, `ORPHANED`, or `UNKNOWN`

### `InstallerFile`

Represents a single `.msi` or `.msp` file found on disk:

| Field | Type | Description |
|-------|------|-------------|
| `path` | `Path` | Full filesystem path |
| `file_type` | `FileType` | MSI or MSP |
| `size_bytes` | `int` | File size |
| `modified` | `datetime` | Last modification time (UTC) |
| `classification` | `Classification` | Starts as UNKNOWN, set by analyzer |
| `product_name` | `str \| None` | From COM/registry if matched |
| `product_guid` | `str \| None` | Product GUID if matched |
| `patch_guid` | `str \| None` | Patch GUID if matched |
| `ai_confidence` | `float \| None` | AI classification confidence (0-1) |
| `ai_reasoning` | `str \| None` | AI explanation |

### `ScanResult`

Aggregation container:

| Field | Type | Description |
|-------|------|-------------|
| `files` | `list[InstallerFile]` | All classified files |
| `known_size` | `int` | Total bytes of KNOWN files |
| `orphaned_size` | `int` | Total bytes of ORPHANED files |
| `unknown_size` | `int` | Total bytes of UNKNOWN files |
| `errors` | `list[str]` | Warnings and errors from all stages |

## Stage 1: Scan (`scanner.py`)

Uses `os.scandir()` for performance (avoids `os.listdir()` + `os.stat()` round-trips). Symlinks are not followed (`follow_symlinks=False`).

**Directories scanned:**
1. `base_dir` (flat scan, defaults to `C:\Windows\Installer`)
2. `base_dir\$PatchCache$` (flat scan, derived from `base_dir` not hardcoded)
3. Each immediate subdirectory of `$PatchCache$` (flat scan)

**Output:** `list[InstallerFile]` with `classification=UNKNOWN`, plus a list of error strings.

**Error handling:** `PermissionError` and `OSError` are caught per-directory and per-file, appended to the errors list. The scan continues past errors.

## Path normalization (`msi_query.normalize_path()`)

Both the query and analyzer stages use a single shared function for path normalization. This is critical for correct matching -- any asymmetry causes false ORPHANED classifications.

The function:
1. Calls `Path.resolve()` for absolute path resolution
2. Calls `GetLongPathNameW` (with proper ctypes `argtypes`/`restype` declarations) to expand 8.3 short names
3. Checks the return value against the buffer size to detect truncation
4. Lowercases the result

## Stage 2: Query (`msi_query.py`)

Builds a dictionary mapping normalized file paths (via `normalize_path()`) to `FileInfo` metadata. Uses two complementary data sources:

### Primary: COM API

```python
installer = win32com.client.Dispatch("WindowsInstaller.Installer")
```

- **Products:** Iterates `installer.Products`, calls `ProductInfo(guid, "LocalPackage")` and `ProductInfo(guid, "ProductName")` for each.
- **Patches:** For each product, tries `PatchesEx(product, None, 7, 0)` first (returns patch objects with `.PatchCode` and `.PatchProperty("LocalPackage")`). Falls back to `Patches(product)` + `PatchInfo(patch, "LocalPackage")`. PatchesEx failure is logged at debug level.

The `PatchesEx` parameters:
- `product_code` -- the product GUID
- `None` -- null user SID (all users, not just current)
- `7` -- `MSIPATCHSTATE_ALL` (applied + superseded + obsoleted)
- `0` -- no additional context filter

### Supplementary: Registry

Walks `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData\<SID>\`:
- `Products\<SQUID>\InstallProperties` -- reads `LocalPackage` and `DisplayName`
- `Patches\<SQUID>` -- reads `LocalPackage`

Enumerates all SIDs under `UserData`, not just `S-1-5-18` (LocalSystem), to catch per-user installations.

SQUIDs are converted to standard GUIDs via `squid.py`.

Registry entries only populate the dict if the path wasn't already found by the COM query (COM takes priority).

### `FileInfo` TypedDict

```python
class FileInfo(TypedDict, total=False):
    product_name: str
    product_guid: str
    patch_guid: str
```

## Stage 3: Classify (`analyzer.py`)

For each `InstallerFile`:

1. **Normalize the path** via `msi_query.normalize_path()` (same function used when building the registered dict).
2. **Look up** in the registered dict.
3. **Match found** -> `KNOWN`, copy product name and GUIDs to the `InstallerFile`.
4. **No match** -> `ORPHANED`.

Calls `ScanResult.recompute_sizes()` to tally bytes per classification.

## Stage 4a: AI analysis (`ai_advisor.py`, optional)

Only runs when `--ai` flag is passed and `ANTHROPIC_API_KEY` is available.

For each UNKNOWN file:

1. **Extract metadata** -- For MSI files, opens the database via COM (`installer.OpenDatabase(path, 0)`) and queries the `Property` table. For MSP (patch) files, queries the `MsiPatchMetadata` table instead. All metadata values are truncated to 200 characters to limit prompt injection surface.
2. **Send to Claude** -- Constructs a prompt with the sanitized metadata JSON. Uses `claude-sonnet-4-20250514` with a system prompt that requests JSON output: `{"classification": "...", "confidence": 0.0-1.0, "reasoning": "..."}`.
3. **Parse response** -- Uses `_parse_json_response()` which handles markdown-fenced responses by stripping fences and extracting the first JSON object via regex. Updates `InstallerFile.classification`, `ai_confidence` (clamped to 0.0-1.0), and `ai_reasoning`.

**Error handling:**
- `RateLimitError` / `APIConnectionError` -- stops processing remaining files (circuit breaker)
- `APIError` -- skips individual file, records only status code in `ai_reasoning` (not full exception to avoid leaking request context)
- `ValueError` / `TypeError` -- handles unexpected AI output types (e.g., `"confidence": "high"`)
- JSON parse errors -- leaves classification as UNKNOWN

## Stage 4b: Act (`actions.py`)

### Safety checks (applied before every move/delete)

1. **Symlink rejection** -- `f.path.is_symlink()` returns True -> skip with error message. Prevents following symlinks to files outside the Installer directory.
2. **Path validation** -- `_is_under_installer_dir()` resolves the path and confirms it is within `C:\Windows\Installer`. Rejects any file whose resolved path escapes the expected directory.
3. **Admin check** -- `ctypes.windll.shell32.IsUserAnAdmin()` -- the CLI exits immediately if not elevated.

### Move

- Creates archive directory if needed
- Handles filename collisions with counter-based deduplication (`file.msi` -> `file_1.msi` -> `file_2.msi`)
- Uses `shutil.move()` for each file
- Returns per-file success/failure report

### Delete

- Uses `Path.unlink()` for each file
- Returns per-file success/failure report

## SQUID conversion (`squid.py`)

Windows Installer stores GUIDs in a compressed format called SQUID in the registry. Both functions validate that input characters are hexadecimal and that braces are stripped correctly (using `removeprefix`/`removesuffix` rather than `strip` to avoid over-stripping). The conversion algorithm:

```
Input GUID:  {A638BC3B-72C3-4EEF-90DD-0683232E396C}
Stripped:     A638BC3B 72C3 4EEF 90DD 0683232E396C

Group 1 (8 chars):  A638BC3B  -> reverse string  -> B3CB836A
Group 2 (4 chars):  72C3      -> reverse string  -> 3C27
Group 3 (4 chars):  4EEF      -> reverse string  -> FEE4
Group 4 (4 chars):  90DD      -> pairwise swap   -> 09DD
Group 5 (12 chars): 0683232E396C -> pairwise swap -> 603832E293C6

SQUID: B3CB836A3C27FEE409DD603832E293C6
```

The operation is self-inverse: applying the same transformations to a SQUID produces the original GUID (after re-inserting braces and hyphens).

## CLI (`cli.py`)

### Subcommands

| Command | Description | Admin required |
|---------|-------------|:-:|
| `scan` (default) | Scan, classify, display | No |
| `move` | Scan, classify, move orphans | Yes |
| `delete` | Scan, classify, delete orphans | Yes |

### Output modes

- **Rich table** (default) -- Color-coded table with file name, type, size, classification, and product name. Summary panel with totals.
- **JSON** (`--json`) -- Structured output with all fields including AI confidence/reasoning and error list.

### Safety

- `move` asks "Proceed? (y/N)" before acting
- `delete` requires typing "YES" (case-sensitive) to confirm
- `--dry-run` shows what would happen without modifying files
- Admin check exits immediately if not elevated
- All untrusted strings (filenames, product names) are escaped via `rich.markup.escape()` before rendering to prevent Rich markup injection

## Configuration (`config.py`)

API key loading with three-tier priority (highest wins):

1. **Environment variables** -- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.
2. **`.env` file** -- loaded via `python-dotenv` with `override=False` (existing env vars take precedence; `.env` cannot overwrite `PATH` or other system variables)
3. **`*.key.txt` files** -- `claude.key.txt` -> `ANTHROPIC_API_KEY`, etc.

All key files are gitignored.

## Error philosophy

Every external call (COM, registry, filesystem, API) is wrapped in try/except. Errors are collected, not raised. This means:

- A scan always produces results, even if some files or queries fail
- The error list in `ScanResult` provides visibility into what went wrong
- The CLI displays an error count at the bottom when errors exist
- AI analysis degrades gracefully: API failures leave files as UNKNOWN
