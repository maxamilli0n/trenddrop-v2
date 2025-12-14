import os, time, base64, json, requests
from datetime import datetime, timezone
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once

# Ensure root .env is loaded
ENV_PATH = load_env_once()
from typing import List, Dict

_OAUTH_CACHE: Dict[str, Dict] = {}

def _get_oauth_token() -> str:
    """
    Client Credentials flow for eBay Buy APIs (Production).
    Caches token in-process until expiry.
    """
    global _OAUTH_CACHE
    now = time.time()
    cached = _OAUTH_CACHE.get("token")
    if cached and cached["exp"] - 60 > now:
        return cached["access_token"]

    cid = os.environ.get("EBAY_CLIENT_ID")
    csec = os.environ.get("EBAY_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set")

    token_url = "https://api.ebay.com/identity/v1/oauth2/token"
    auth = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    # minimal scope works for Browse search
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }
    r = requests.post(token_url, headers=headers, data=data, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"OAuth failed {r.status_code}: {r.text[:300]}")
    tok = r.json()
    _OAUTH_CACHE["token"] = {
        "access_token": tok["access_token"],
        "exp": now + int(tok.get("expires_in", 7200))
    }
    return tok["access_token"]

def search_browse(keyword: str, limit: int = 12) -> List[Dict]:
    """
    Use Buy Browse API: /buy/browse/v1/item_summary/search

    We enrich each row with fields that matter for conversion:
      - buying_options (AUCTION / FIXED_PRICE / BEST_OFFER)
      - condition / condition_id
      - item_end_date (auction urgency)
      - shipping_cost (people buy cheaper shipping)
      - returns_accepted (trust)
    """
    token = _get_oauth_token()
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    params = {
        "q": keyword,
        "limit": str(limit),
        "filter": "priceCurrency:USD",
        "sort": "BEST_MATCH",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        "User-Agent": "TrendDropBot/1.0",
    }

    backoffs = [0, 2, 4]
    r = None
    for i, b in enumerate(backoffs, start=1):
        if b:
            time.sleep(b)
        r = requests.get(url, headers=headers, params=params, timeout=25)
        if r.status_code == 200:
            break
        print(f"[browse] HTTP {r.status_code} for '{keyword}', attempt {i}/{len(backoffs)}: {r.text[:200]}")

    if r is None or r.status_code != 200:
        return []

    data = r.json()
    items = data.get("itemSummaries", []) or []
    out: List[Dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for it in items:
        try:
            title = (it.get("title") or "")[:160]

            price_obj = (it.get("price") or {})
            price = float(price_obj.get("value") or 0.0)
            currency = price_obj.get("currency") or "USD"

            image_url = (it.get("image") or {}).get("imageUrl") or ""
            url2 = it.get("itemWebUrl") or it.get("itemAffiliateWebUrl") or ""

            # Buying options (AUCTION is the urgency lever)
            buying_options = it.get("buyingOptions") or []
            if not isinstance(buying_options, list):
                buying_options = []

            # Condition
            condition = it.get("condition") or ""
            condition_id = it.get("conditionId") or None

            # Auction end time (if present, huge ranking power)
            item_end_date = it.get("itemEndDate") or ""

            # Shipping cost (best-effort)
            shipping_cost_val = None
            try:
                ship_opts = it.get("shippingOptions") or []
                if isinstance(ship_opts, list) and ship_opts:
                    ship_cost_obj = (ship_opts[0].get("shippingCost") or {})
                    ship_val = ship_cost_obj.get("value")
                    if ship_val is not None:
                        shipping_cost_val = float(ship_val)
            except Exception:
                shipping_cost_val = None

            # Returns (best-effort)
            returns_accepted = None
            try:
                ra = it.get("returnsAccepted")
                if isinstance(ra, bool):
                    returns_accepted = ra
            except Exception:
                returns_accepted = None

            seller = (it.get("seller") or {})
            feedback = int(seller.get("feedbackScore") or 0)
            seller_username = seller.get("username") or seller.get("sellerId") or ""

            # Browse item summaries do NOT reliably provide "Top Rated Seller".
            top_rated = False

            inserted_raw = (
                it.get("itemCreationDate")
                or it.get("itemStartDate")
                or it.get("itemStartTime")
            )
            inserted_at = inserted_raw or now_iso

            out.append({
                "source": "ebay",
                "provider": "ebay",
                "keyword": keyword,

                "title": title,
                "price": price,
                "currency": currency,
                "image_url": image_url,
                "url": url2,

                "seller_feedback": feedback,
                "seller_username": seller_username,
                "top_rated": top_rated,

                # NEW conversion fields
                "buying_options": buying_options,          # list[str]
                "condition": condition,                    # str
                "condition_id": condition_id,              # int|None
                "item_end_date": item_end_date,            # ISO string or ""
                "shipping_cost": shipping_cost_val,        # float|None
                "returns_accepted": returns_accepted,      # bool|None

                "inserted_at": inserted_at,
            })
        except Exception as e:
            print(f"[browse] item parse error '{keyword}': {e}")
            continue

    print(f"[browse] '{keyword}' -> {len(out)} items")
    return out
