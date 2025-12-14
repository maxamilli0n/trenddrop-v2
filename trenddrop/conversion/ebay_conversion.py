# trenddrop/conversion/ebay_conversion.py
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from trenddrop.reports.product_quality import canonical_title_key

# Hard filters: kill junk listings that burn clicks
_BAD_TITLE_PATTERNS = [
    r"\b(parts|for parts|broken|repair|spares|untested|read description)\b",
    r"\b(ic lock|icloud|mdm|google lock|locked)\b",
    r"\b(case only|box only|empty box|manual only)\b",
    r"\b(lot of|bulk|wholesale|bundle of)\b",
    r"\b(digital code|account|subscription)\b",
]

# Soft de-prioritize: low conversion categories/keywords (curiosity clicks)
_LOW_CONVERSION_PATTERNS = [
    r"\b(charger|charging|usb[- ]?c|cable|adapter|case|screen protector)\b",
]

def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default

def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default

def _parse_end_time(p: Dict[str, Any]) -> float | None:
    """
    Best-effort: if your eBay source provides end time, use it.
    Supports:
      - p["end_time"] as ISO string
      - p["end_time_ts"] as epoch seconds
      - p["itemEndDate"] as ISO
    """
    if "end_time_ts" in p:
        try:
            return float(p["end_time_ts"])
        except Exception:
            pass

    for key in ("end_time", "itemEndDate"):
        v = p.get(key)
        if not v:
            continue
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                s = v.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:
                continue
    return None

def passes_hard_filters(p: Dict[str, Any]) -> Tuple[bool, str]:
    title = str(p.get("title") or "").lower()
    if not title:
        return False, "missing_title"

    for pat in _BAD_TITLE_PATTERNS:
        if re.search(pat, title, flags=re.IGNORECASE):
            return False, "bad_title"

    price = _as_float(p.get("price"), 0.0)
    if price <= 0:
        return False, "bad_price"

    # Avoid ultra-low stuff (often scammy / non-converting)
    if price < 6:
        return False, "too_cheap"

    return True, "ok"

def conversion_score(p: Dict[str, Any]) -> float:
    """
    Score tuned for first conversions:
      - trust (seller feedback count + top rated)
      - price band (sweet spot)
      - urgency if end_time exists (auctions ending soon)
      - penalty for low-conversion keywords
      - small boost for your existing 'signals'
    """
    ok, _reason = passes_hard_filters(p)
    if not ok:
        return -1e9

    title = str(p.get("title") or "")
    title_l = title.lower()

    price = _as_float(p.get("price"), 0.0)
    signals = _as_float(p.get("signals"), 0.0)

    # In your current data, seller_feedback is effectively feedbackScore (count).
    fb_count = _as_int(p.get("seller_feedback"), 0)
    top_rated = bool(p.get("top_rated"))

    # Trust: log curve so 500 -> strong, 5k -> stronger but not crazy
    trust = math.log1p(max(fb_count, 0))
    if top_rated:
        trust += 1.2

    # Price sweet spot: boosts convertable “impulse but meaningful” buys
    if 15 <= price <= 120:
        price_fit = 2.4
    elif 120 < price <= 250:
        price_fit = 1.4
    elif 6 <= price < 15:
        price_fit = 0.6
    else:
        price_fit = 0.2

    # Urgency: if we have end_time, favor ending soon (<=2h best)
    urgency = 0.0
    end_ts = _parse_end_time(p)
    if end_ts:
        hrs_left = max(0.0, (end_ts - datetime.now(timezone.utc).timestamp()) / 3600.0)
        if hrs_left <= 2:
            urgency = 2.3
        elif hrs_left <= 6:
            urgency = 1.4
        elif hrs_left <= 24:
            urgency = 0.6

    # Penalize commodity keywords that waste clicks
    low_conv_penalty = 0.0
    for pat in _LOW_CONVERSION_PATTERNS:
        if re.search(pat, title_l, flags=re.IGNORECASE):
            low_conv_penalty -= 1.4
            break

    # Small signals contribution (don’t let it dominate)
    sig_term = min(max(signals, 0.0), 12.0) * 0.22

    # Final
    score = (trust * 0.85) + price_fit + urgency + sig_term + low_conv_penalty

    # Minor diversity: don’t show 5 near-identical items
    # (canonical key injects a tiny deterministic offset)
    ck = canonical_title_key(title)
    if ck:
        score += (hash(ck) % 100) / 10000.0

    return score
