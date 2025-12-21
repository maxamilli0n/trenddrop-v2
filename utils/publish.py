import os, json, time, pathlib, html
import hashlib
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional
from io import BytesIO

import requests

from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import (
    CLICK_REDIRECT_BASE,
    CHAT_ID,
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
from trenddrop.reports.product_quality import dedupe_near_duplicates, ensure_rank_fields
from utils.db import save_run_summary, upsert_products, fetch_recent_posted_keys, mark_posted_item
from trenddrop.utils.telegram_cta import maybe_send_cta
from utils.epn import affiliate_wrap
from utils.ai import caption_for, marketing_copy_for

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

    if not picked:
        return []

    def uniq_count(items: List[Dict]) -> int:
        return len({_topic_key_for_product(x) for x in items})

    if uniq_count(picked) >= target_unique:
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

    if len(picked) < limit:
        existing_counts: Dict[str, int] = {}
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
        draw.text((60, 140), "TrendDrop", fill=text_primary, font=f_title)
        draw.text((64, 240), "Today’s Trending Finds", fill=text_secondary, font=f_sub)

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

    try:
        _generate_og_image(products)
    except Exception:
        pass


def _build_reseller_cta_text() -> str:
    paid_url = ""
    try:
        paid_url = gumroad_cta_url() or ""
    except Exception:
        paid_url = ""

    lines = [
        "📦 <b>Flip-ready product list</b>",
        "If you resell on eBay / Facebook Marketplace / Amazon / TikTok Shop, grab the free sample pack (PDF + CSV).",
        "",
        f"✅ Free sample (5 items): <a href=\"{FREE_SAMPLE_URL}\">Download here</a>",
    ]

    if paid_url:
        lines += [f"🔥 Full Top 50 pack: <a href=\"{paid_url}\">Get it here</a>"]

    lines += ["", "Tip: post the same-day items fast — speed is the edge."]
    return "\n".join(lines)


def _cta_key(chat_id: str) -> str:
    return f"CTA::{str(chat_id)}"


def _format_product_caption(p: Dict, *, scope: str) -> str:
    """
    Different formatting for public vs paid.
    - public: shorter, cleaner
    - paid: includes "Member Pick" and more trust signals
    """
    title_raw = str(p.get("title") or "")
    title = html.escape(title_raw[:170])

    price = p.get("price")
    currency = p.get("currency", "USD")
    price_text = f"{currency} {price:.2f}" if isinstance(price, (int, float)) else f"{currency} {price}"

    click_url = p.get("click_url") or p.get("url") or ""
    click_url = str(click_url)

    fb = p.get("seller_feedback")
    top_rated = p.get("top_rated")

    trust_line = ""
    if fb:
        trust_line = f"⭐ Seller feedback: {format_feedback_number(fb)}"
        if top_rated:
            trust_line += " · Top Rated"

    hook_lines = []
    lt = _listing_type(p)
    if lt:
        hook_lines.append(f"🛒 {lt}")

    ra = p.get("returns_accepted")
    if ra is True:
        hook_lines.append("✅ Free returns")

    ship = p.get("shipping_cost")
    if ship is not None:
        try:
            ship_f = float(ship)
            if ship_f <= 0.0001:
                hook_lines.append("🚚 Free shipping")
        except Exception:
            pass

    end_ts = _parse_end_time(p)
    if end_ts:
        try:
            hrs_left = max(0.0, (end_ts - datetime.now(timezone.utc).timestamp()) / 3600.0)
            if hrs_left <= 6.0:
                hook_lines.append("⏳ Ends soon")
        except Exception:
            pass

    cond = str(p.get("condition") or "").strip()
    cond_line = f"Condition: {html.escape(cond)}" if cond else ""

    if scope == "paid":
        headline = "💎 TrendDrop+ Member Pick"
        cta_text = "🔗 Open listing"
        body = [headline, f"<b>{title}</b>", f"💰 {price_text}"]
        if trust_line:
            body.append(trust_line)
        if hook_lines:
            body.append("\n".join(hook_lines))
        if cond_line:
            body.append(cond_line)
        body += ["", f"<a href=\"{click_url}\">{cta_text}</a>"]
        return "\n".join(body)

    # public default
    headline = "⚡ TRENDING NOW"
    cta_text = "🛒 View deal"
    body = [headline, f"<b>{title}</b>", f"💰 {price_text}"]
    if hook_lines:
        body.append("\n".join(hook_lines))
    body += ["", f"<a href=\"{click_url}\">{cta_text}</a>"]
    return "\n".join(body)


def post_telegram(products: List[Dict], limit: int = 5, *, scope: str = "broadcast") -> None:
    """
    scope:
      - public
      - paid
      - broadcast (public + paid)
      - admin
      - dm
      - all
    """
    import random
    from trenddrop.conversion.ebay_conversion import conversion_score, passes_hard_filters

    if not products:
        return

    dedupe_hours = int(TELEGRAM_DEDUPE_HOURS)
    max_per_keyword = int(TELEGRAM_MAX_PER_KEYWORD)
    min_unique_keywords = int(TELEGRAM_MIN_UNIQUE_KEYWORDS)
    max_per_seller = max(1, int(TELEGRAM_MAX_PER_SELLER))
    cta_every_n_posts = max(2, int(TELEGRAM_CTA_EVERY_N_POSTS))
    cta_cooldown_minutes = max(15, int(TELEGRAM_CTA_COOLDOWN_MINUTES))
    pin_cta = bool(TELEGRAM_PIN_CTA)

    # We dedupe globally by url_key (shared), so posting to both public+paid doesn't spam repeats run-to-run
    recent_keys = fetch_recent_posted_keys(dedupe_hours) or []
    recent_set = set(recent_keys)

    prepared = [ensure_rank_fields(dict(p)) for p in products]
    collapsed = dedupe_near_duplicates(prepared)

    scored = []
    for p in collapsed:
        raw_url = str(p.get("url") or "")
        canonical = _canonicalize_url(raw_url)
        key = _url_key(canonical)

        if key and key in recent_set:
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

    if len(pick) < int(limit):
        for p in varied:
            if p in pick:
                continue
            sk = _seller_key_for_product(p)
            if sum(1 for x in pick if _seller_key_for_product(x) == sk) >= max_per_seller:
                continue
            pick.append(p)
            if len(pick) >= int(limit):
                break

    try:
        print(f"[telegram] scope={scope} pick keywords: {[_topic_key_for_product(x) for x in pick]}")
        print(f"[telegram] scope={scope} pick sellers:  {[_seller_key_for_product(x) for x in pick]}")
    except Exception:
        pass

    # Persistent CTA cooldown key is per scope target (we store against "CTA::<scope>")
    cta_key = f"CTA::{scope}"
    cta_recent_keys = fetch_recent_posted_keys(max(1, int((cta_cooldown_minutes + 59) // 60))) or []
    cta_recently_sent = (cta_key in set(cta_recent_keys))

    last_cta_ts = 0.0

    def can_send_cta_now() -> bool:
        nonlocal last_cta_ts
        now = time.time()
        if last_cta_ts <= 0.0:
            return True
        mins = (now - last_cta_ts) / 60.0
        return mins >= float(cta_cooldown_minutes)

    def mark_cta_sent():
        nonlocal last_cta_ts
        last_cta_ts = time.time()

    sent_count = 0
    posted_any = False

    for p in pick:
        try:
            # ensure affiliate wrap
            try:
                first_tag = (p.get("tags") or [p.get("keyword") or "trend"])[0]
                p["url"] = affiliate_wrap(p.get("url", ""), custom_id=str(first_tag).replace(" ", "_")[:40])
            except Exception:
                pass

            img = p.get("image_url")
            caption = _format_product_caption(p, scope=scope)

            if img:
                send_photo(str(img), scope=scope, caption=caption, parse_mode="HTML")
            else:
                send_text(caption, scope=scope, parse_mode="HTML", disable_web_page_preview=False)

            posted_any = True
            sent_count += 1

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

            # CTA every N
            if sent_count % int(cta_every_n_posts) == 0:
                if (not cta_recently_sent) and can_send_cta_now():
                    text = _build_reseller_cta_text()
                    send_text(text, scope=scope, parse_mode="HTML", disable_web_page_preview=True)
                    try:
                        mark_posted_item(
                            url_key=cta_key,
                            canonical_url="cta",
                            keyword="cta",
                            title="telegram_cta",
                            provider="telegram",
                            source="telegram",
                        )
                    except Exception:
                        pass
                    cta_recently_sent = True
                    mark_cta_sent()

                    try:
                        maybe_send_cta()
                    except Exception:
                        pass

            time.sleep(0.55 + random.uniform(0.0, 0.35))
        except Exception:
            continue

    # End CTA
    if posted_any and (not cta_recently_sent) and can_send_cta_now():
        try:
            text = _build_reseller_cta_text()
            send_text(text, scope=scope, parse_mode="HTML", disable_web_page_preview=True)
            try:
                mark_posted_item(
                    url_key=cta_key,
                    canonical_url="cta",
                    keyword="cta",
                    title="telegram_cta",
                    provider="telegram",
                    source="telegram",
                )
            except Exception:
                pass
            mark_cta_sent()
        except Exception:
            pass

        try:
            maybe_send_cta()
        except Exception:
            pass

    # run summary
    try:
        uniq_topics = set()
        for p in products:
            for t in p.get("tags", []) or []:
                uniq_topics.add(t)
        save_run_summary(topic_count=len(uniq_topics) or 1, item_count=len(pick))
    except Exception:
        pass
