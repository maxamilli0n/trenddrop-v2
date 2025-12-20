import os, json, time, requests, pathlib, html
import hashlib
from urllib.parse import urlparse
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import (
    CLICK_REDIRECT_BASE,
    gumroad_cta_url,
)
from trenddrop.reports.product_quality import (
    dedupe_near_duplicates,
    ensure_rank_fields,
)

ENV_PATH = load_env_once()

from io import BytesIO
from typing import List, Dict, Optional
from datetime import datetime, timezone

from utils.db import save_run_summary, upsert_products, fetch_recent_posted_keys, mark_posted_item
from utils.epn import affiliate_wrap
from utils.ai import marketing_copy_for

from trenddrop.telegram_utils import send_text, send_photo

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
        canonical = f"{scheme}://{netloc}{path}"
        return canonical
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


def _select_with_variety(
    scored: List[Dict],
    limit: int,
    *,
    max_per_keyword: int,
    min_unique_keywords: int,
) -> List[Dict]:
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

    if not picked:
        return []

    def uniq_count(items: List[Dict]) -> int:
        return len({_topic_key_for_product(x) for x in items})

    if uniq_count(picked) >= target_unique:
        return picked

    existing = {_topic_key_for_product(x) for x in picked}
    new_keyword_candidates = []
    for p in scored:
        k = _topic_key_for_product(p)
        if k not in existing:
            new_keyword_candidates.append(p)

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

    if len(picked) < limit:
        existing_counts = {}
        for x in picked:
            k = _topic_key_for_product(x)
            existing_counts[k] = existing_counts.get(k, 0) + 1

        for p in scored:
            if len(picked) >= limit:
                break
            if p in picked:
                continue
            k = _topic_key_for_product(p)
            if existing_counts.get(k, 0) >= max_per_keyword:
                continue
            picked.append(p)
            existing_counts[k] = existing_counts.get(k, 0) + 1

    return picked


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


def _parse_end_time(p: Dict) -> float | None:
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


def _listing_type(p: Dict) -> str:
    lt = str(p.get("listing_type") or p.get("listingType") or p.get("buyingOptions") or "").lower()

    if isinstance(p.get("buyingOptions"), list):
        try:
            bo = [str(x).lower() for x in (p.get("buyingOptions") or [])]
            lt = ",".join(bo)
        except Exception:
            pass

    if "auction" in lt:
        return "Auction"
    if "fixed" in lt or "buy_it_now" in lt or "buynow" in lt or "now" in lt:
        return "Buy It Now"
    return ""


def ensure_dirs():
    pathlib.Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)
    pathlib.Path(DOCS_DATA).mkdir(parents=True, exist_ok=True)


def _generate_og_image(products: List[Dict]) -> None:
    if Image is None:
        return
    try:
        width, height = 1200, 630
        bg_color = (15, 23, 42)
        accent = (99, 102, 241)
        text_primary = (255, 255, 255)
        text_secondary = (226, 232, 240)

        img = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        font_path_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        try:
            f_title = ImageFont.truetype(font_path_bold, 88)
            f_sub = ImageFont.truetype(font_path, 40)
            f_tag = ImageFont.truetype(font_path_bold, 28)
        except Exception:
            f_title = ImageFont.load_default()
            f_sub = ImageFont.load_default()
            f_tag = ImageFont.load_default()

        draw.rectangle([(0, 0), (width, 14)], fill=accent)

        title = "TrendDrop"
        subtitle = "Today’s Trending Finds"
        draw.text((60, 140), title, fill=text_primary, font=f_title)
        draw.text((64, 240), subtitle, fill=text_secondary, font=f_sub)

        x = width - 60
        y = 110
        thumb_w, thumb_h = 260, 260
        spacing = 12
        pasted = 0
        for p in products:
            if pasted >= 3:
                break
            url = p.get("image_url")
            if not url:
                continue
            try:
                r = requests.get(url, timeout=10)
                if r.status_code != 200:
                    continue
                t = Image.open(BytesIO(r.content))  # type: ignore
            except Exception:
                continue
            try:
                t = t.convert("RGB")
                t.thumbnail((thumb_w, thumb_h))
                x_pos = x - t.width
                draw.rectangle([(x_pos - 6, y - 6), (x_pos + t.width + 6, y + t.height + 6)], fill=(30, 41, 59))
                img.paste(t, (x_pos, y))
                y += t.height + spacing
                pasted += 1
            except Exception:
                continue

        ts = time.strftime("Updated %b %d, %Y", time.gmtime())
        draw.text((60, height - 80), ts, fill=(148, 163, 184), font=f_tag)

        img.save(OG_PATH, format="PNG", optimize=True)
    except Exception:
        return


def update_storefront(products: List[Dict], raw_products: Optional[List[Dict]] = None):
    raw_for_upsert = raw_products if raw_products else products
    print(f"[scraper] fetched {len(raw_for_upsert)} raw eBay products before filtering/dedup")
    upsert_products(raw_for_upsert)

    for p in products:
        try:
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
            p["headline"] = None
            p["blurb"] = None
            p["emojis"] = ""

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

    try:
        _generate_og_image(products)
    except Exception:
        pass


def _build_cta_text(*, premium: bool) -> str:
    paid_url = ""
    try:
        paid_url = gumroad_cta_url() or ""
    except Exception:
        paid_url = ""

    if premium:
        lines = [
            "💎 <b>Premium: weekly drops + higher-signal picks</b>",
            "You’re in the paid channel — here’s the latest free sample + full pack link.",
            "",
            f"✅ Free sample (5 items): <a href=\"{FREE_SAMPLE_URL}\">Download here</a>",
        ]
        if paid_url:
            lines += [f"🔥 Full Top 50 pack: <a href=\"{paid_url}\">Get it here</a>"]
        lines += ["", "Tip: move fast — same-day listings convert best."]
        return "\n".join(lines)

    lines = [
        "📦 <b>Flip-ready product list</b>",
        "Want the full weekly pack (PDF + CSV) with the best picks?",
        "",
        f"✅ Free sample (5 items): <a href=\"{FREE_SAMPLE_URL}\">Download here</a>",
    ]
    if paid_url:
        lines += [f"🔥 Full Top 50 pack: <a href=\"{paid_url}\">Get it here</a>"]
    return "\n".join(lines)


def _cta_key(chat_id: str) -> str:
    return f"CTA::{str(chat_id)}"


def _price_text(currency: str, price) -> str:
    try:
        if isinstance(price, (int, float)):
            return f"{currency} {price:.2f}"
        return f"{currency} {price}"
    except Exception:
        return f"{currency} {price}"


def _build_post(
    p: Dict,
    *,
    premium: bool,
) -> str:
    title_raw = str(p.get("title") or "").strip()
    title = html.escape(title_raw[:170])

    currency = str(p.get("currency") or "USD")
    price = p.get("price")
    price_text = _price_text(currency, price)

    click_url = p.get("click_url")
    url = p.get("url") or ""
    final_url = click_url or url

    fb = p.get("seller_feedback")
    top_rated = p.get("top_rated")
    trust = ""
    if fb:
        trust = f"⭐ {format_feedback_number(fb)} feedback"
        if top_rated:
            trust += " · Top Rated"

    # hooks
    hooks = []
    lt = _listing_type(p)
    if lt:
        hooks.append(f"🛒 {lt}")

    ship = p.get("shipping_cost")
    if ship is not None:
        try:
            ship_f = float(ship)
            hooks.append("🚚 Free shipping" if ship_f <= 0.0001 else f"🚚 Shipping: {currency} {ship_f:.2f}")
        except Exception:
            pass

    ra = p.get("returns_accepted")
    if ra is True and premium:
        hooks.append("✅ Returns accepted")

    end_ts = _parse_end_time(p)
    if end_ts:
        try:
            hrs_left = max(0.0, (end_ts - datetime.now(timezone.utc).timestamp()) / 3600.0)
            if hrs_left <= 6.0:
                hooks.append("⏳ Ends soon (≤6h)")
        except Exception:
            pass

    # copy
    headline = str(p.get("headline") or "").strip()
    blurb = str(p.get("blurb") or "").strip()

    # if ai copy missing, use a clean default
    if not headline:
        headline = "🔥 Trending pick"
    if not blurb:
        blurb = "Worth a quick look — listings like this can move fast."

    lines = []
    lines.append(f"{'💎' if premium else '⚡'} <b>{html.escape(headline[:80])}</b>")
    lines.append(f"<b>{title}</b>")
    lines.append(f"💰 {price_text}")
    if trust:
        lines.append(trust)

    if hooks:
        lines.append("\n".join(hooks))

    if premium:
        # premium value: keyword + quick flip note
        kw = str(p.get("keyword") or "")
        tags = p.get("tags") or []
        topic = kw or (tags[0] if isinstance(tags, list) and tags else "")
        if topic:
            lines.append(f"🏷️ Topic: <i>{html.escape(str(topic)[:60])}</i>")

        # simple flip suggestion (safe heuristic)
        if isinstance(price, (int, float)) and 15 <= float(price) <= 120:
            lines.append("💡 Flip idea: list on FB Marketplace / TikTok Shop w/ fast shipping + clear photos.")

        lines.append(f"🧭 {html.escape(blurb[:220])}")
    else:
        # public: shorter
        lines.append(html.escape(blurb[:160]))

    lines += ["", f"<a href=\"{final_url}\">{'View listing' if premium else 'Tap to view'}</a>"]
    return "\n".join([x for x in lines if x])


def post_telegram(products: List[Dict], limit=5):
    """
    Posts:
      - PUBLIC channel: clean shorter posts
      - PAID channel: richer “reseller notes” version
      - ADMIN: optional summary (no product spam)
    """
    import random
    from trenddrop.conversion.ebay_conversion import conversion_score, passes_hard_filters

    if not products:
        return

    # Dedupe window (hours)
    dedupe_hours = 48
    try:
        dedupe_hours = int(str(os.environ.get("TELEGRAM_DEDUPE_HOURS", "48")).strip())
    except Exception:
        dedupe_hours = 48

    # per-channel limits (defaults)
    public_limit = int(str(os.environ.get("TELEGRAM_PUBLIC_LIMIT", str(max(1, int(limit))))).strip())
    paid_limit = int(str(os.environ.get("TELEGRAM_PAID_LIMIT", str(max(2, int(limit) + 2)))).strip())
    admin_summary = str(os.environ.get("TELEGRAM_ADMIN_SUMMARY", "1")).strip().lower() in ("1", "true", "yes", "y")

    # Variety controls
    max_per_keyword = 2
    min_unique_keywords = 4
    try:
        max_per_keyword = int(str(os.environ.get("TELEGRAM_MAX_PER_KEYWORD", "2")).strip())
    except Exception:
        max_per_keyword = 2
    try:
        min_unique_keywords = int(str(os.environ.get("TELEGRAM_MIN_UNIQUE_KEYWORDS", "4")).strip())
    except Exception:
        min_unique_keywords = 4

    # Seller diversity controls
    max_per_seller = 1
    try:
        max_per_seller = int(str(os.environ.get("TELEGRAM_MAX_PER_SELLER", "1")).strip())
    except Exception:
        max_per_seller = 1
    if max_per_seller < 1:
        max_per_seller = 1

    # CTA controls (separate)
    public_cta_every = int(str(os.environ.get("TELEGRAM_PUBLIC_CTA_EVERY_N_POSTS", "12")).strip())
    paid_cta_every = int(str(os.environ.get("TELEGRAM_PAID_CTA_EVERY_N_POSTS", "8")).strip())
    cta_cooldown_minutes = int(str(os.environ.get("TELEGRAM_CTA_COOLDOWN_MINUTES", "360")).strip())  # 6h default

    recent_keys = fetch_recent_posted_keys(dedupe_hours) or []
    if recent_keys:
        print(f"[telegram] dedupe active: {len(recent_keys)} items posted in last {dedupe_hours}h")

    prepared = [ensure_rank_fields(dict(p)) for p in products]
    collapsed = dedupe_near_duplicates(prepared)

    scored: List[Dict] = []
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

        p["_conv_score"] = float(s)
        scored.append(p)

    scored.sort(key=lambda x: float(x.get("_conv_score", 0.0)), reverse=True)

    varied = _select_with_variety(
        scored,
        max(public_limit, paid_limit, 1),
        max_per_keyword=max_per_keyword,
        min_unique_keywords=min_unique_keywords,
    )

    pick = _enforce_seller_diversity(varied, max_per_seller=max_per_seller)

    # refill if trimmed
    if len(pick) < max(public_limit, paid_limit):
        for p in varied:
            if p in pick:
                continue
            sk = _seller_key_for_product(p)
            if sum(1 for x in pick if _seller_key_for_product(x) == sk) >= max_per_seller:
                continue
            pick.append(p)
            if len(pick) >= max(public_limit, paid_limit):
                break

    # split: public gets top N, paid gets top M (usually more)
    public_pick = pick[: max(0, public_limit)]
    paid_pick = pick[: max(0, paid_limit)]

    # CTA persistent cooldown keys
    cta_cooldown_hours = max(1, int((int(cta_cooldown_minutes) + 59) // 60))
    cta_recent_keys = set(fetch_recent_posted_keys(cta_cooldown_hours) or [])

    def should_send_cta(chat_key: str) -> bool:
        return _cta_key(chat_key) not in cta_recent_keys

    def mark_cta(chat_key: str):
        try:
            mark_posted_item(
                url_key=_cta_key(chat_key),
                canonical_url="cta",
                keyword="cta",
                title="telegram_cta",
                provider="telegram",
                source="telegram",
            )
        except Exception:
            pass

    posted_public = 0
    posted_paid = 0

    # PUBLIC
    for p in public_pick:
        try:
            msg = _build_post(p, premium=False)
            img = p.get("image_url")
            if img:
                send_photo(str(img), msg, target="public", parse_mode="HTML")
            else:
                send_text(msg, target="public", parse_mode="HTML", disable_web_page_preview=False)
            posted_public += 1

            try:
                mark_posted_item(
                    url_key=str(p.get("_url_key") or ""),
                    canonical_url=str(p.get("_canonical_url") or ""),
                    keyword=str(p.get("keyword") or ""),
                    title=str(p.get("title") or ""),
                    provider=str(p.get("provider") or ""),
                    source=str(p.get("source") or ""),
                )
            except Exception:
                pass

            # CTA
            if posted_public > 0 and public_cta_every > 0 and posted_public % public_cta_every == 0:
                # use "public" as CTA key since channel IDs can be @handle
                if should_send_cta("public"):
                    send_text(_build_cta_text(premium=False), target="public", parse_mode="HTML", disable_web_page_preview=True)
                    mark_cta("public")

            time.sleep(0.55 + random.uniform(0.0, 0.35))
        except Exception:
            continue

    # PAID
    for p in paid_pick:
        try:
            msg = _build_post(p, premium=True)
            img = p.get("image_url")
            if img:
                send_photo(str(img), msg, target="paid", parse_mode="HTML")
            else:
                send_text(msg, target="paid", parse_mode="HTML", disable_web_page_preview=False)
            posted_paid += 1

            # CTA
            if posted_paid > 0 and paid_cta_every > 0 and posted_paid % paid_cta_every == 0:
                if should_send_cta("paid"):
                    send_text(_build_cta_text(premium=True), target="paid", parse_mode="HTML", disable_web_page_preview=True)
                    mark_cta("paid")

            time.sleep(0.55 + random.uniform(0.0, 0.35))
        except Exception:
            continue

    # ADMIN summary (no spam)
    if admin_summary:
        try:
            topics = sorted({_topic_key_for_product(x) for x in pick})
            send_text(
                "🧠 TrendDrop run summary\n"
                f"- Picked: {len(pick)}\n"
                f"- Posted public: {posted_public}\n"
                f"- Posted paid: {posted_paid}\n"
                f"- Topics: {', '.join(topics[:12])}{'…' if len(topics) > 12 else ''}",
                target="admin",
                disable_web_page_preview=True,
            )
        except Exception:
            pass

    try:
        uniq_topics = set()
        for p in products:
            for t in p.get("tags", []) or []:
                uniq_topics.add(t)
        save_run_summary(topic_count=len(uniq_topics) or 1, item_count=len(pick))
    except Exception:
        pass
