import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


_EPOCH_MIN = datetime.min.replace(tzinfo=timezone.utc)


def canonical_title_key(title: str) -> str:
    """
    Normalize a product title so near-duplicate listings collapse together.
    """
    if not title:
        return ""

    t = title.lower()
    t = re.sub(
        r"\b(new|brand new|used|very good|good|acceptable|like new|hardcover|paperback|"
        r"good condition|very good condition|for your|for yo|for you)\b",
        " ",
        t,
    )
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80]


def _coerce_inserted_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str) and value:
        try:
            normalized = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return _EPOCH_MIN
    return _EPOCH_MIN


def rank_key(product: Dict[str, Any]) -> Tuple[float, float, float, datetime]:
    """
    Global ranking tuple: signals DESC, seller feedback DESC, price ASC, recency DESC.
    """
    try:
        signals = float(product.get("signals") or 0.0)
    except Exception:
        signals = 0.0
    try:
        seller_fb = float(product.get("seller_feedback") or 0.0)
    except Exception:
        seller_fb = 0.0
    try:
        price = float(product.get("price") or 0.0)
    except Exception:
        price = 0.0
    inserted_at = _coerce_inserted_at(product.get("inserted_at"))
    return (signals, seller_fb, -price, inserted_at)


def _seller_identifier(product: Dict[str, Any]) -> str:
    return (
        product.get("seller_username")
        or product.get("seller_id")
        or product.get("seller")
        or product.get("seller_name")
        or ""
    )


def dedupe_near_duplicates(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse near-duplicate listings (same source+seller+title key) to the best-ranked entry.
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for product in products:
        source = (product.get("source") or "unknown").lower()
        seller = _seller_identifier(product)
        title_key = canonical_title_key(product.get("title") or "")
        key = f"{source}|{seller}|{title_key}"
        best = buckets.get(key)
        if best is None or rank_key(product) > rank_key(best):
            buckets[key] = product
    return list(buckets.values())


def ensure_rank_fields(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure a product has the minimum required fields for ranking.
    """
    if not product.get("provider"):
        provider = product.get("source") or "manual"
        product["provider"] = str(provider).lower()
    if not product.get("source"):
        product["source"] = product.get("provider") or "unknown"
    if not product.get("inserted_at"):
        product["inserted_at"] = datetime.now(timezone.utc)
    return product

