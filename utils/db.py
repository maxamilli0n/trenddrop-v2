import os, time, uuid
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY

# Ensure root .env is loaded for local runs and subfolders
ENV_PATH = load_env_once()

try:
    from supabase import create_client, Client
except Exception:
    create_client = None  # type: ignore
    Client = object  # type: ignore

_sb: Optional[Client] = None


def _read_env_credentials() -> Tuple[Optional[str], Optional[str]]:
    url = SUPABASE_URL
    # Prefer service role for server-side tasks like uploads; fall back to anon
    key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    return url, key


def sb() -> Optional[Client]:
    global _sb
    if _sb is None:
        url, key = _read_env_credentials()
        if create_client and url and key:
            try:
                _sb = create_client(url, key)
            except Exception:
                _sb = None
    return _sb


def save_run_summary(topic_count: int, item_count: int) -> Optional[str]:
    if not _sb:
        return None
    now = int(time.time())
    try:
        r = _sb.table("runs").insert({"ran_at": now, "topics": topic_count, "items": item_count}).execute()
        return str((r.data or [{}])[0].get("id"))
    except Exception:
        return None


def _provider_from_source(source: Optional[str]) -> str:
    s = str(source or "").strip().lower()
    # Restrict to allowed providers
    if s in ("ebay", "gumroad", "payhip", "manual"):
        return s
    # Default to 'manual' for any unknown source to avoid NULL
    return "manual"


def _stable_product_id(provider: str, url: str) -> str:
    # Deterministic UUID based on provider+url to enable idempotent upsert on id
    basis = f"{provider}:{url}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, basis))


def upsert_products(products: List[Dict]):
    if not _sb or not products:
        return
    rows = []
    for p in products:
        title = p.get("title")
        url = p.get("url")
        if not title or not url:
            continue
        # Map provider from source with a safe default
        provider = _provider_from_source(p.get("provider") or p.get("source") or "manual")
        # Timestamps in ISO 8601 UTC
        now_iso = datetime.now(timezone.utc).isoformat()
        # Stable id based on provider+url
        pid = _stable_product_id(provider, url)
        # Prepare full row with explicit columns expected by DB
        rows.append({
            "id": pid,
            "inserted_at": now_iso,
            "source": p.get("source", provider),
            "title": title,
            "price": p.get("price"),
            "currency": p.get("currency", "USD"),
            "image_url": p.get("image_url"),
            "url": url,
            "keyword": p.get("keyword"),
            "seller_feedback": p.get("seller_feedback"),
            "top_rated": bool(p.get("top_rated", False)),
            "active": bool(p.get("active", True)),
            "name": p.get("name") or title,
            "provider": provider,
            "created_at": now_iso,
        })
    if rows:
        try:
            # Upsert on id to match explicit SQL semantics
            _sb.table("products").upsert(rows, on_conflict="id").execute()
        except Exception:
            pass


