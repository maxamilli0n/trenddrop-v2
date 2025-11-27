import os, time
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once
from typing import List, Dict

# Load environment variables only from root .env
ENV_PATH = load_env_once()
from utils.trends import top_topics
from utils.sources import search_ebay
from utils.epn import affiliate_wrap
from utils.publish import update_storefront, post_telegram
from trenddrop.reports.product_quality import (
    dedupe_near_duplicates,
    rank_key,
    ensure_rank_fields,
)

def _get_int_env(name: str, default: int) -> int:
    try:
        value_str = os.environ.get(name)
        if value_str is None or value_str == "":
            return default
        value = int(value_str)
        return value if value >= 0 else default
    except Exception:
        return default

def _get_float_env(name: str, default: float) -> float:
    try:
        value_str = os.environ.get(name)
        if value_str is None or value_str == "":
            return default
        value = float(value_str)
        return value if value >= 0 else default
    except Exception:
        return default

def _get_float_env_between(name: str, default: float, min_value: float, max_value: float) -> float:
    val = _get_float_env(name, default)
    if val < min_value:
        return min_value
    if val > max_value:
        return max_value
    return val

def _synthetic_signal(p: Dict) -> float:
    base = 0.0
    try:
        if p.get("top_rated"):
            base += 5.0
        fb = float(p.get("seller_feedback") or 0)
        base += min(fb / 1000.0, 5.0)
    except Exception:
        pass
    try:
        price = float(p.get("price") or 0.0)
        if 15 <= price <= 150:
            base += 4.0
        elif 5 <= price < 15:
            base += 2.0
        elif 150 < price <= 400:
            base += 1.0
    except Exception:
        pass
    return base

def dedupe(products: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for p in products:
        key = p.get("url")
        if key and key not in seen:
            seen.add(key); out.append(p)
    return out

def main():
    topics_limit = _get_int_env("TREND_TOPICS_LIMIT", 1)
    per_page = _get_int_env("TREND_PER_PAGE", 5)
    sleep_secs = _get_float_env("TREND_SLEEP_SECS", 5.0)
    sleep_jitter = _get_float_env_between("TREND_SLEEP_JITTER", 0.0, 0.0, 10.0)
    picks_limit = _get_int_env("TREND_PICKS_LIMIT", 5)
    telegram_limit = _get_int_env("TREND_TELEGRAM_LIMIT", 5)

    topics = top_topics(limit=topics_limit)
    print(f"[bot] topics: {topics}")
    raw_candidates: List[Dict] = []
    for t in topics:
        try:
            found = search_ebay(t, per_page=per_page)
            print(f"[bot] found {len(found)} for topic '{t}'")
            for item in found:
                item["signals"] = _synthetic_signal(item)
                item["tags"] = [t]
                item["url"] = affiliate_wrap(item["url"], custom_id=t.replace(" ", "_")[:40])
                ensure_rank_fields(item)
            raw_candidates += found
        except Exception as e:
            print(f"[bot] WARN search failed '{t}': {e}")
        if sleep_secs > 0:
            jitter = 0.0
            try:
                import random
                jitter = random.uniform(0.0, sleep_jitter) if sleep_jitter > 0 else 0.0
            except Exception:
                jitter = 0.0
            time.sleep(sleep_secs + jitter)  # configurable pause between calls

    candidates = dedupe(raw_candidates)
    prepared = [ensure_rank_fields(p) for p in candidates]
    collapsed = dedupe_near_duplicates(prepared)
    ranked = sorted(collapsed, key=rank_key, reverse=True)
    picks = ranked[:picks_limit]
    print(
        f"[bot] raw={len(raw_candidates)} url_deduped={len(candidates)} "
        f"title_seller_deduped={len(collapsed)} picks={len(picks)}"
    )
    update_storefront(picks, raw_products=raw_candidates)
    post_telegram(picks, limit=telegram_limit)
    print(f"[bot] posted {len(picks)} items from {len(topics)} topics")
    
if __name__ == "__main__":
    main()
