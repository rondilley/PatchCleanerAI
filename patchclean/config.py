"""Load API keys from environment variables, .env files, and *.key.txt files."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Mapping from key-file name to environment variable
_KEY_FILE_MAP: dict[str, str] = {
    "claude.key.txt": "ANTHROPIC_API_KEY",
    "openai.key.txt": "OPENAI_API_KEY",
    "mistral.key.txt": "MISTRAL_API_KEY",
    "xai.key.txt": "XAI_API_KEY",
}


def load_keys(project_root: Path | None = None) -> dict[str, str]:
    """Return a dict of ``{ENV_VAR: value}`` for every available API key.

    Priority (highest wins): environment variables > .env > *.key.txt files.
    """
    keys: dict[str, str] = {}

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    # 1. *.key.txt files (lowest priority)
    for filename, env_var in _KEY_FILE_MAP.items():
        key_path = project_root / filename
        if key_path.is_file():
            value = key_path.read_text(encoding="utf-8").strip()
            if value:
                keys[env_var] = value

    # 2. .env file (overrides key files)
    env_path = project_root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)

    # 3. Environment variables (highest priority, includes .env values just loaded)
    for env_var in _KEY_FILE_MAP.values():
        val = os.environ.get(env_var)
        if val:
            keys[env_var] = val

    return keys
