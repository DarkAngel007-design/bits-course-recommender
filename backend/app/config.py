"""Load a git-ignored `.env` file from the repository root into os.environ (never overriding
variables that are already set). Keeps secrets such as ANTHROPIC_API_KEY out of the shell history
and out of Git. Imported first by the API so ADMIN_TOKEN / LLM settings are visible at startup."""
from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = ENV_FILE) -> list[str]:
    loaded: list[str] = []
    if os.environ.get("BCR_DISABLE_DOTENV") or not path.is_file():  # tests/CI must never read real keys
        return loaded
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip().removeprefix("export ").strip(), val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val
            loaded.append(key)
    return loaded


load_env()
