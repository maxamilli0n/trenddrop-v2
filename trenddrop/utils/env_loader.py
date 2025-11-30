from __future__ import annotations
from pathlib import Path
from functools import lru_cache
from typing import Optional
import os


@lru_cache(maxsize=1)
def load_env_once() -> Path:
    """
    Locate the repository root and load ONLY the root `.env` file.
    Never read `.env.example`. Returns the resolved .env path if found,
    otherwise raises FileNotFoundError on first use.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception as e:
        raise RuntimeError("python-dotenv is required") from e

    # Start from this file and walk up looking for repo markers
    start = Path(__file__).resolve()
    cur = start
    for _ in range(12):
        env_path = cur / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
            return env_path
        if cur.parent == cur:
            break
        cur = cur.parent

    # Fallback: try CWD .env if present
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=False)
        return cwd_env

    raise FileNotFoundError("Root .env not found. Place a .env at the repo root.")


