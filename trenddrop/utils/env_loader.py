from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional
    load_dotenv = None  # type: ignore[assignment]


@lru_cache()
def load_env_once() -> str | None:
    """
    Load a .env file from the repo root *if it exists*.

    - In local dev: you typically have a .env; we'll load it once.
    - In CI: env vars come from GitHub Secrets; .env is optional.
      If no .env exists, we do NOTHING and DO NOT raise.
    """
    # Find repo root by going two levels up from this file:
    # trenddrop/utils/env_loader.py -> trenddrop -> repo root
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / ".env"

    if env_path.exists() and load_dotenv is not None:
        load_dotenv(dotenv_path=env_path)
        return str(env_path)

    # No .env (typical on CI) -> just rely on existing environment
    return None
