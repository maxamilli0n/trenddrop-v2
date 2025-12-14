import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

# Ensure root .env is loaded for local runs and subfolders
ENV_PATH = load_env_once()

try:
    from supabase import Client, create_client
except Exception:
    create_client = None  # type: ignore
    Client = object  # type: ignore

_sb: Optional[Client] = None


def _read_env_credentials() -> Tuple[str, str]:
    url = (SUPABASE_URL or "").strip()
    key = (SUPABASE_SERVICE_ROLE_KEY or "").strip()
    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY; cannot write to public.products."
        )
    return url, key


def sb() -> Client:
    global _sb
    if _sb is None:
        if not create_client:
            raise RuntimeError("supabase Python client is not available")
        url, key = _read_env_credentials()
        _sb = create_client(url, key)
    return _sb


def _get_supabase_admin() -> Client:
    if not create_client:
        raise RuntimeError("supabase Python client is not available")
    url, key = _read_env_credentials()
    return create_client(url, key)


def log_report_run(
    *,
    run_started_at: datetime,
    data_window_label: Optional[str],
    products_total: int,
    curated_count: int,
    pdf_url: Optional[str],
    csv_url: Optional[str],
    success: bool,
    error_message: Optional[str] = None,
) -> None:
    """
    Insert a row into public.report_runs capturing the outcome of a report generation run.
    Failures here should not interrupt the main reporting flow.
    """
    try:
        client = sb()
    except RuntimeError as exc:
        print(f"[reports] warning: Supabase client unavailable for report log: {exc}")
        return
    payload = {
        "run_started_at": run_started_at.isoformat(),
        "data_window_label": data_window_label,
        "products_total": products_total,
        "curated_count": curated_count,
        "pdf_url": pdf_url or "",
        "csv_url": csv_url or "",
        "success": success,
        "error_message": error_message,
    }
    try:
        client.table("report_runs").insert(payload).execute()
    except Exception as exc:
        print(f"[reports] warning: failed to log report run: {exc}")


def save_run_summary(topic_count: int, item_count: int) -> Optional[str]:
    try:
        client = sb()
    except RuntimeError:
        return None
    now = int(time.time())
    try:
        r = client.table("runs").insert({"ran_at": now, "topics": topic_count, "items": item_count}).execute()
        return str((r.data or [{}])[0].get("id"))
    except Exception:
        return None


_ALLOWED_PROVIDERS = {"ebay", "amazon", "aliexpress", "gumroad", "payhip", "manual"}


def _provider_from_source(source: Optional[str]) -> str:
    s = str(source or "").strip().lower()
    if s in _ALLOWED_PROVIDERS:
        return s
    return "manual"


def _ensure_timezone(dt_value: datetime) -> datetime:
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=timezone.utc)
    return dt_value


def _timestamp_iso(value: Optional[Any]) -> str:
    if isinstance(value, datetime):
        return _ensure_timezone(value).isoformat()
    if isinstance(value, str) and value:
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return _ensure_timezone(parsed).isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _stable_product_id(provider: str, url: str) -> str:
    # Deterministic UUID based on provider+url to enable idempotent upsert on id
    basis = f"{provider}:{url}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, basis))


def upsert_products(products: List[Dict]):
    if not products:
        raise RuntimeError("No products provided to upsert.")
    client = sb()
    rows = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for p in products:
        title = p.get("title")
        url = p.get("url")
        if not title or not url:
            continue

        # Map provider from source with a safe default (used only for stable id + source)
        provider = _provider_from_source(p.get("provider") or p.get("source") or "manual")
        name = p.get("name") or title
        source_value = p.get("source") or f"{provider}-scraper"
        inserted_at_iso = _timestamp_iso(p.get("inserted_at") or p.get("created_at"))
        created_at_iso = _timestamp_iso(p.get("created_at") or now_iso)

        price_value = p.get("price")
        if price_value is None:
            price_value = 0.0

        currency_value = str(p.get("currency") or "USD").upper()
        image_url_value = str(p.get("image_url") or "")

        try:
            seller_feedback_value = int(float(p.get("seller_feedback") or 0))
        except Exception:
            seller_feedback_value = 0

        try:
            signals_raw = float(p.get("signals") or 0.0)
        except Exception:
            signals_raw = 0.0
        signals_value = int(round(signals_raw))

        # Stable id based on provider+url
        pid = _stable_product_id(provider, url)

        # ==========================
        # NEW conversion fields
        # ==========================
        seller_username = str(p.get("seller_username") or "")

        # buying_options: store as JSON-ish (list preferred). If missing, store empty list.
        buying_options = p.get("buying_options")
        if not isinstance(buying_options, list):
            # allow comma-delimited string -> list
            if isinstance(buying_options, str) and buying_options.strip():
                buying_options = [x.strip() for x in buying_options.split(",") if x.strip()]
            else:
                buying_options = []

        condition = str(p.get("condition") or "")

        # condition_id: safest to store as text because APIs vary ("1000", "NEW", etc.)
        condition_id = p.get("condition_id")
        if condition_id is None:
            condition_id_str = None
        else:
            condition_id_str = str(condition_id).strip() or None

        # item_end_date: timestamptz accepts ISO string; store None if blank/unset
        item_end_date = p.get("item_end_date")
        if isinstance(item_end_date, str):
            item_end_date = item_end_date.strip()
            if item_end_date == "":
                item_end_date = None
        elif item_end_date is None:
            item_end_date = None
        else:
            # if it's some unknown type, don't risk breaking insert
            item_end_date = None

        shipping_cost = p.get("shipping_cost")
        try:
            shipping_cost_val = float(shipping_cost) if shipping_cost is not None and shipping_cost != "" else None
        except Exception:
            shipping_cost_val = None

        returns_accepted = p.get("returns_accepted")
        if isinstance(returns_accepted, bool):
            returns_accepted_val = returns_accepted
        elif isinstance(returns_accepted, str):
            v = returns_accepted.strip().lower()
            if v in ("1", "true", "yes", "y"):
                returns_accepted_val = True
            elif v in ("0", "false", "no", "n"):
                returns_accepted_val = False
            else:
                returns_accepted_val = None
        else:
            returns_accepted_val = None

        # Prepare row aligned with public.products schema
        rows.append(
            {
                "id": pid,
                "title": title,
                "name": name,
                "provider": provider,
                "source": source_value,
                "price": price_value,
                "currency": currency_value,
                "image_url": image_url_value,
                "url": url,
                "keyword": p.get("keyword"),
                "seller_feedback": seller_feedback_value,
                "seller_username": seller_username,
                "top_rated": bool(p.get("top_rated", False)),
                "signals": signals_value,
                "inserted_at": inserted_at_iso,
                "created_at": created_at_iso,
                # NEW columns
                "buying_options": buying_options,
                "condition": condition,
                "condition_id": condition_id_str,
                "item_end_date": item_end_date,
                "shipping_cost": shipping_cost_val,
                "returns_accepted": returns_accepted_val,
            }
        )

    if not rows:
        raise RuntimeError("No valid products with title+url to upsert.")

    print(f"[TD-products] attempting upsert of {len(rows)} products to Supabase")

    supabase_url, _ = _read_env_credentials()
    provider_label = rows[0].get("provider") if rows else "unknown"
    print(
        f"[scraper] upserting {len(rows)} {provider_label} products to Supabase project {supabase_url}"
    )

    try:
        res = client.table("products").upsert(rows, on_conflict="id").execute()
    except Exception as exc:
        print(
            "[TD-products] SUPABASE ERROR (exception) during upsert.\n"
            f"Payload: {json.dumps(rows, default=str)[:2000]}\nError: {exc}",
            file=sys.stderr,
        )
        raise

    error = getattr(res, "error", None)
    if error:
        print(
            "[TD-products] SUPABASE ERROR.\n"
            f"Payload: {json.dumps(rows, default=str)[:2000]}\nError: {error}",
            file=sys.stderr,
        )
        raise RuntimeError("Supabase products upsert failed.")

    print(f"[TD-products] upsert success ({len(rows)} rows).")


def load_clean_products_for_providers(providers: List[str], limit: int = 500) -> List[Dict]:
    if not providers:
        return []
    try:
        client = _get_supabase_admin()
    except RuntimeError as exc:
        print(f"[reports] unable to load clean products: {exc}")
        return []
    try:
        res = (
            client.table("v_products_clean")
            .select(
                "title, price, currency, image_url, url, seller_feedback, seller_username, "
                "top_rated, source, inserted_at, keyword, signals, buying_options, condition, "
                "condition_id, item_end_date, shipping_cost, returns_accepted"
            )
            .in_("source", providers)
            .order("signals", desc=True)
            .limit(max(1, limit))
            .execute()
        )
        return res.data or []
    except Exception as exc:
        print(f"[reports] error loading providers {providers}: {exc}")
        return []
