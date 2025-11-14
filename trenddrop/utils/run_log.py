import os
import json
import time
from typing import Any, Dict, Literal, Optional
from pathlib import Path
from dotenv import load_dotenv
from trenddrop.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY

# Ensure root .env is loaded for local runs
env_path = Path(__file__).resolve().parents[1] / ".env"
if not env_path.exists():
    raise FileNotFoundError(f"Missing .env file at {env_path}")
load_dotenv(env_path, override=False)

try:
    from supabase import create_client, Client
except Exception:
    create_client = None  # type: ignore
    Client = object  # type: ignore


def _client() -> Optional[Client]:
    url = SUPABASE_URL
    key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    if not (create_client and url and key):
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def _workflow_url() -> Optional[str]:
    run_id = os.environ.get("GITHUB_RUN_ID")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if run_id and repo:
        return f"https://github.com/{repo}/actions/runs/{run_id}"
    return None


def save_run_summary(
    status: Literal["success", "failure"],
    started_at: float,
    finished_at: float,
    artifacts: Dict[str, Any],
    message: str = "",
    meta: Dict[str, Any] | None = None,
) -> Optional[str]:
    """Insert a row into the public.runs table.

    Fields: status, started_at, finished_at, duration_ms, pdf_url, csv_url,
    commit_sha, workflow_run_url, message, meta.

    Returns the inserted id as a string, or None on failure. Never raises.
    """
    client = _client()
    if not client:
        print("[run_log] no supabase client; skipping run log")
        return None

    duration_ms = int(max(0.0, (finished_at - started_at)) * 1000.0)
    commit_sha = os.environ.get("GITHUB_SHA")
    workflow_run_url = _workflow_url()

    pdf_url = artifacts.get("pdf_url_latest") or artifacts.get("pdf_url_dated")
    csv_url = artifacts.get("csv_url_latest") or artifacts.get("csv_url_dated")

    row = {
        "status": status,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at)),
        "duration_ms": duration_ms,
        "pdf_url": pdf_url,
        "csv_url": csv_url,
        "commit_sha": commit_sha,
        "workflow_run_url": workflow_run_url,
        "message": message,
        "meta": meta or {},
    }

    try:
        res = client.table("runs").insert(row).execute()
        try:
            inserted = (res.data or [{}])[0]
            return str(inserted.get("id"))
        except Exception:
            return None
    except Exception as e:
        print(f"[run_log] insert failed: {e}")
        # Avoid crashing workflows; table might not exist yet
        return None


