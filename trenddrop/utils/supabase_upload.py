import os
import time
from typing import Optional
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

ENV_PATH = load_env_once()

try:
    from supabase import create_client, Client
except Exception:
    create_client = None  # type: ignore
    Client = object  # type: ignore


def _service_client() -> Optional[Client]:
    """Return a Supabase client using the service role key.

    Uses env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
    Returns None if credentials are missing or client cannot be created.
    """
    url = SUPABASE_URL
    key = SUPABASE_SERVICE_ROLE_KEY
    if not (create_client and url and key):
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def _ensure_bucket_public(client: Client, bucket: str) -> None:
    """Create bucket if missing and ensure it's public-read.

    Idempotent: failures are ignored silently so uploads can proceed.
    """
    try:
        try:
            client.storage.create_bucket(bucket, public=True)
        except Exception:
            # Bucket may already exist; try to set public flag via update (best-effort)
            try:
                client.storage.update_bucket(bucket, {'public': True})  # type: ignore[arg-type]
            except Exception:
                pass
    except Exception:
        # Best-effort; proceed to upload which may still work if bucket exists
        pass


def upload_file(bucket: str, local_path: str, dest_path: str, content_type: str) -> Optional[str]:
    """Upload a local file to Supabase Storage and return a public URL.

    - Ensures the bucket exists and is public read (best-effort/idempotent)
    - Upserts the object at dest_path
    - Retries up to 3 times with exponential backoff

    Returns None if upload fails or client is unavailable.
    """
    client = _service_client()
    if not client:
        return None

    _ensure_bucket_public(client, bucket)

    attempts = 0
    delay = 1.0
    while attempts < 3:
        attempts += 1
        try:
            with open(local_path, "rb") as f:
                client.storage.from_(bucket).upload(
                    path=dest_path,
                    file=f,
                    file_options={"content-type": content_type, "upsert": "true"},
                )
            try:
                pub = client.storage.from_(bucket).get_public_url(dest_path)
                return pub.get("publicUrl") if isinstance(pub, dict) else pub
            except Exception:
                # If public URL API fails, construct best-effort URL
                url = (SUPABASE_URL or "").rstrip("/")
                if url:
                    # https://<proj>.supabase.co/storage/v1/object/public/<bucket>/<path>
                    return f"{url}/storage/v1/object/public/{bucket}/{dest_path}"
                return None
        except Exception:
            if attempts >= 3:
                break
            time.sleep(delay)
            delay *= 2
    return None


