import os, json, time, requests, pathlib, html
import hashlib
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Dict, Optional

from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import (
    CLICK_REDIRECT_BASE,
    BOT_TOKEN,
    gumroad_cta_url,
    TELEGRAM_DEDUPE_HOURS,
    TELEGRAM_MAX_PER_KEYWORD,
    TELEGRAM_MIN_UNIQUE_KEYWORDS,
    TELEGRAM_MAX_PER_SELLER,
    TELEGRAM_CTA_EVERY_N_POSTS,
    TELEGRAM_CTA_COOLDOWN_MINUTES,
    TELEGRAM_PIN_CTA,
)
from trenddrop.telegram_utils import send_text, send_photo
from utils.db import save_run_summary, upsert_products, fetch_recent_posted_keys, mark_posted_item
from utils.epn import affiliate_wrap
from utils.ai import caption_for, marketing_copy_for
from trenddrop.reports.product_quality import dedupe_near_duplicates, ensure_rank_fields

ENV_PATH = load_env_once()

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")
DOCS_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "data")
PRODUCTS_PATH = os.path.join(DOCS_DATA, "products.json")
OG_PATH = os.path.join(DOCS_DIR, "og.png")

FREE_SAMPLE_URL = "https://trenddropstudio.gumroad.com/l/free-sample"

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, None)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, None)
    if raw is None:
        return default
    raw = str(raw).strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "y", "on")


def format_feedback_number(feedback) -> str:
    try:
        if feedback is None:
            return ""
        if isinstance(feedback, str):
            s = feedback.strip().replace(",", "")
            if not s:
                return ""
            n = float(s)
        elif isinstance(feedback, (int, float)):
            n = float(feedback)
        else:
            n = float(str(feedback).strip().replace(",", ""))
    except Exception:
        return str(feedback)

    if n >= 1_000_000:
        val = n / 1_000_000.0
        txt = f"{val:.1f}".rstrip("0").rstrip(".")
        return f"{txt}M"
    if n >= 1_000:
        val = n / 1_000.0
        txt = f"{val:.1f}".rstrip("0").rstrip(".")
        return f"{txt}k"
    try:
        return str(int(n)) if n.is_integer() else str(n)
    except Exception:
        return str(n)


def _canonicalize_url(raw_url: str) -> str:
    try:
        u = (raw_url or "").strip()
        if not u:
            return ""
        parsed = urlparse(u)
        scheme = parsed.scheme or "https"
        netloc = (parsed.netloc or "").lower()
        path = parsed.path or ""
        return f"{scheme}://{netloc}{path}"
    except Exception:
        return (raw_url or "").strip()


def _url_key(canonical_url: str) -> str:
    try:
        return hashlib.md5((canonical_url or "").encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _topic_key_for_product(p: Dict) -> str:
    try:
        k = str(p.get("keyword") or "").strip().lower()
        if k:
            return k
        tags = p.get("tags") or []
        if isinstance(tags, list) and tags:
            return str(tags[0] or "").strip().lower() or "other"
    except Exception:
        pass
    return "other"


def _select_with_variety(scored: List[Dict], limit: int, *, max_per_keyword: int, min_unique_keywords: int) -> List[Dict]:
    if limit <= 0:
        return []

    max_per_keyword = max(1, int(max_per_keyword))
    min_unique_keywords = max(1, int(min_unique_keywords))
    target_unique = min(min_unique_keywords, limit)

    picked: List[Dict] = []
    counts: Dict[str, int] = {}

    for p in scored:
        if len(picked) >= limit:
            break
        k = _topic_key_for_product(p)
        if counts.get(k, 0) >= max_per_keyword:
            continue
        picked.append(p)
        counts[k] = counts.get(k, 0) + 1

    def uniq_count(items: List[Dict]) -> int:
        return len({_topic_key_for_product(x) for x in items})

    if not picked or uniq_count(picked) >= target_unique:
        return picked

    existing = {_topic_key_for_product(x) for x in picked}
    new_keyword_candidates = [p for p in scored if _topic_key_for_product(p) not in existing]

    i = 0
    while uniq_count(picked) < target_unique and i < len(new_keyword_candidates):
        candidate = new_keyword_candidates[i]
        cand_k = _topic_key_for_product(candidate)

        removable_idx = None
        for j in range(len(picked) - 1, -1, -1):
            pk = _topic_key_for_product(picked[j])
            if counts.get(pk, 0) > 1:
                removable_idx = j
                break
        if removable_idx is None:
            break

        removed = picked.pop(removable_idx)
        rem_k = _topic_key_for_product(removed)
        counts[rem_k] = max(0, counts.get(rem_k, 1) - 1)

        picked.append(candidate)
        counts[cand_k] = counts.get(cand_k, 0) + 1
        existing.add(cand_k)
        i += 1

    return picked[:limit]


def _seller_key_for_product(p: Dict) -> str:
    try:
        su = str(p.get("seller_username") or "").strip().lower()
        if su:
            return su
    except Exception:
        pass

    try:
        u = str(p.get("url") or "").strip()
        if u:
            parsed = urlparse(u)
            if parsed.netloc:
                return parsed.netloc.lower()
    except Exception:
        pass

    return "unknown"


def _enforce_seller_diversity(items: List[Dict], *, max_per_seller: int) -> List[Dict]:
    max_per_seller = max(1, int(max_per_seller))
    picked: List[Dict] = []
    counts: Dict[str, int] = {}

    for p in items:
        sk = _seller_key_for_product(p)
        if counts.get(sk, 0) >= max_per_seller:
            continue
        picked.append(p)
        counts[sk] = counts.get(sk, 0) + 1

    return picked


def ensure_dirs():
    pathlib.Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)
    pathlib.Path(DOCS_DATA).mkdir(parents=True, exist_ok=True)


def update_storefront(products: List[Dict], raw_products: Optional[List[Dict]] = None):
    raw_for_upsert = raw_products if raw_products else products
    print(f"[scraper] fetched {len(raw_for_upsert)} raw eBay products before filtering/dedup")
    upsert_products(raw_for_upsert)

    for p in products:
        try:
            p["caption"] = caption_for(p)
            mc = marketing_copy_for(p)
            p["headline"] = mc.get("headline")
            p["blurb"] = mc.get("blurb")
            p["emojis"] = mc.get("emojis")
            try:
                first_tag = (p.get("tags") or [p.get("keyword") or "trend"])[0]
                p["url"] = affiliate_wrap(p.get("url", ""), custom_id=str(first_tag).replace(" ", "_")[:40])
            except Exception:
                pass
        except Exception:
            p["caption"] = p.get("title", "")

    ensure_dirs()

    base = CLICK_REDIRECT_BASE
    if base:
        try:
            from urllib.parse import urlencode
            for p in products:
                target = p.get("url") or ""
                if target:
                    p["click_url"] = f"{base}?" + urlencode({"url": target})
        except Exception:
            pass

    with open(PRODUCTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"updated_at": int(time.time()), "products": products}, f, indent=2)


def _cta_key(scope: str) -> str:
    return f"CTA::{scope}"


def _build_public_cta() -> str:
    paid_url = gumroad_cta_url() or ""
    lines = [
        "📦 <b>Flip-ready product list</b>",
        "If you resell on eBay / Marketplace / Amazon / TikTok Shop — grab the free sample (PDF + CSV).",
        "",
        f"✅ Free sample (5 items): {FREE_SAMPLE_URL}",
    ]
    if paid_url:
        lines += [f"🔥 Full Top 50 pack: {paid_url}"]
    lines += ["", "Tip: same-day posting wins."]
    return "\n".join(lines)


def _public_caption(p: Dict, final_url: str) -> str:
    title_raw = str(p.get("title") or "")
    title = html.escape(title_raw[:170])

    price = p.get("price")
    currency = p.get("currency", "USD")
    price_text = f"{currency} {price:.2f}" if isinstance(price, (int, float)) else f"{currency} {price}"

    headline = "⚡ TRENDING NOW"
    cta = "🛒 View deal"

    return "\n".join([
        headline,
        f"<b>{title}</b>",
        f"💰 {price_text}",
        "",
        f"<a href=\"{final_url}\">{cta}</a>",
    ])


def _paid_caption(p: Dict, final_url: str) -> str:
    title_raw = str(p.get("title") or "")
    title = html.escape(title_raw[:170])

    price = p.get("price")
    currency = p.get("currency", "USD")
    price_text = f"{currency} {price:.2f}" if isinstance(price, (int, float)) else f"{currency} {price}"

    fb = p.get("seller_feedback")
    top_rated = p.get("top_rated")
    trust = ""
    if fb:
        trust = f"⭐ Seller feedback: {format_feedback_number(fb)}"
        if top_rated:
            trust += " · Top Rated"

    extras = []
    ra = p.get("returns_accepted")
    if ra is True:
        extras.append("✅ Returns accepted")
    ship = p.get("shipping_cost")
    if ship is not None:
        try:
            ship_f = float(ship)
            extras.append("🚚 Free shipping" if ship_f <= 0.0001 else f"🚚 Shipping: {currency} {ship_f:.2f}")
        except Exception:
            pass

    headline = "💎 TrendDrop+ Member Pick"
    cta = "🔗 Open listing"

    parts = [
        headline,
        f"<b>{title}</b>",
        f"💰 {price_text}",
    ]
    if trust:
        parts.append(trust)
    if extras:
        parts.append("\n".join(extras))
    parts += ["", f"<a href=\"{final_url}\">{cta}</a>"]
    return "\n".join(parts)


def post_telegram(products: List[Dict], limit=5, scope: str = "broadcast"):
    """
    scope:
      - admin / public / paid / broadcast / legacy
    broadcast posts products to public+paid if those are configured.
    """
    if not BOT_TOKEN or not products or limit <= 0:
        return

    import random
    from trenddrop.conversion.ebay_conversion import conversion_score, passes_hard_filters

    # Tunables (safe even if Actions sets blank)
    dedupe_hours = env_int("TELEGRAM_DEDUPE_HOURS", TELEGRAM_DEDUPE_HOURS)
    max_per_keyword = env_int("TELEGRAM_MAX_PER_KEYWORD", TELEGRAM_MAX_PER_KEYWORD)
    min_unique_keywords = env_int("TELEGRAM_MIN_UNIQUE_KEYWORDS", TELEGRAM_MIN_UNIQUE_KEYWORDS)
    max_per_seller = env_int("TELEGRAM_MAX_PER_SELLER", TELEGRAM_MAX_PER_SELLER)

    cta_every_n_posts = env_int("TELEGRAM_CTA_EVERY_N_POSTS", TELEGRAM_CTA_EVERY_N_POSTS)
    cta_cooldown_minutes = env_int("TELEGRAM_CTA_COOLDOWN_MINUTES", TELEGRAM_CTA_COOLDOWN_MINUTES)
    pin_cta = env_bool("TELEGRAM_PIN_CTA", TELEGRAM_PIN_CTA)

    recent_keys = fetch_recent_posted_keys(dedupe_hours) or []
    if recent_keys:
        print(f"[telegram] dedupe active: {len(recent_keys)} items posted in last {dedupe_hours}h")

    prepared = [ensure_rank_fields(dict(p)) for p in products]
    collapsed = dedupe_near_duplicates(prepared)

    scored = []
    for p in collapsed:
        raw_url = str(p.get("url") or "")
        canonical = _canonicalize_url(raw_url)
        key = _url_key(canonical)

        if key and key in set(recent_keys):
            continue

        p["_canonical_url"] = canonical
        p["_url_key"] = key

        ok, _ = passes_hard_filters(p)
        if not ok:
            continue

        s = conversion_score(p)
        if s <= -1e8:
            continue

        p["_conv_score"] = s
        scored.append(p)

    scored.sort(key=lambda x: float(x.get("_conv_score", 0.0)), reverse=True)

    varied = _select_with_variety(
        scored,
        max(1, int(limit)),
        max_per_keyword=max_per_keyword,
        min_unique_keywords=min_unique_keywords,
    )

    pick = _enforce_seller_diversity(varied, max_per_seller=max_per_seller)

    posted = 0

    # Persistent CTA cooldown (per scope)
    cta_cooldown_hours = max(1, int((int(cta_cooldown_minutes) + 59) // 60))
    cta_recent_keys = fetch_recent_posted_keys(cta_cooldown_hours) or []
    cta_recently_sent = (_cta_key(scope) in set(cta_recent_keys))

    # Only do CTA on PUBLIC/BROADCAST (not paid, not admin)
    allow_cta = scope in ("public", "broadcast", "legacy")

    for p in pick:
        try:
            try:
                first_tag = (p.get("tags") or [p.get("keyword") or "trend"])[0]
                url = affiliate_wrap(p.get("url", ""), custom_id=str(first_tag).replace(" ", "_")[:40])
            except Exception:
                url = p.get("url", "")

            final_url = p.get("click_url") or url
            img = p.get("image_url")

            # Build caption per scope
            if scope == "paid":
                caption = _paid_caption(p, final_url)
                send_scope = "paid"
            elif scope == "public":
                caption = _public_caption(p, final_url)
                send_scope = "public"
            elif scope == "admin":
                caption = f"🛠️ Admin debug post\n<b>{html.escape(str(p.get('title') or '')[:170])}</b>\n{final_url}"
                send_scope = "admin"
            elif scope == "broadcast":
                # Post public style to public + paid style to paid
                if img:
                    send_photo(img, caption=_public_caption(p, final_url), scope="public", parse_mode="HTML")
                    send_photo(img, caption=_paid_caption(p, final_url), scope="paid", parse_mode="HTML")
                else:
                    send_text(_public_caption(p, final_url), scope="public", parse_mode="HTML", disable_web_page_preview=False)
                    send_text(_paid_caption(p, final_url), scope="paid", parse_mode="HTML", disable_web_page_preview=False)

                posted += 1
                mark_posted_item(
                    url_key=str(p.get("_url_key") or ""),
                    canonical_url=str(p.get("_canonical_url") or ""),
                    keyword=str(p.get("keyword") or ""),
                    title=str(p.get("title") or ""),
                    provider=str(p.get("provider") or ""),
                    source=str(p.get("source") or ""),
                )
                time.sleep(0.55 + random.uniform(0.0, 0.35))
                continue
            else:
                # legacy
                caption = _public_caption(p, final_url)
                send_scope = "legacy"

            # Single-scope send
            if img:
                send_photo(img, caption=caption, scope=send_scope, parse_mode="HTML")
            else:
                send_text(caption, scope=send_scope, parse_mode="HTML", disable_web_page_preview=False)

            posted += 1

            mark_posted_item(
                url_key=str(p.get("_url_key") or ""),
                canonical_url=str(p.get("_canonical_url") or ""),
                keyword=str(p.get("keyword") or ""),
                title=str(p.get("title") or ""),
                provider=str(p.get("provider") or ""),
                source=str(p.get("source") or ""),
            )

            # CTA logic (only for public/broadcast/legacy)
            if allow_cta and (posted % max(2, int(cta_every_n_posts)) == 0) and (not cta_recently_sent):
                send_text(_build_public_cta(), scope="public" if scope != "legacy" else "legacy", parse_mode="HTML", disable_web_page_preview=True)
                mark_posted_item(
                    url_key=_cta_key(scope),
                    canonical_url="cta",
                    keyword="cta",
                    title="telegram_cta",
                    provider="telegram",
                    source="telegram",
                )
                cta_recently_sent = True

            time.sleep(0.55 + random.uniform(0.0, 0.35))
        except Exception:
            continue

    # run summary
    try:
        uniq_topics = set()
        for p in products:
            for t in p.get("tags", []) or []:
                uniq_topics.add(t)
        save_run_summary(topic_count=len(uniq_topics) or 1, item_count=posted)
    except Exception:
        pass
