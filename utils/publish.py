import os, json, time, requests, pathlib, html
import hashlib
from urllib.parse import urlparse
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import CLICK_REDIRECT_BASE, BOT_TOKEN, CHAT_ID, gumroad_cta_url
from trenddrop.reports.product_quality import (
    dedupe_near_duplicates,
    ensure_rank_fields,
)

# Ensure root .env is loaded (safe even if you don't use .env; env vars can come from GitHub Secrets)
ENV_PATH = load_env_once()

from io import BytesIO
from typing import List, Dict, Optional
from datetime import datetime, timezone
from utils.db import save_run_summary, upsert_products, fetch_recent_posted_keys, mark_posted_item
from trenddrop.utils.telegram_cta import maybe_send_cta
from utils.epn import affiliate_wrap
from utils.ai import caption_for, marketing_copy_for

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")
DOCS_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "data")
PRODUCTS_PATH = os.path.join(DOCS_DATA, "products.json")
OG_PATH = os.path.join(DOCS_DIR, "og.png")

# Your real free sample link (hard-coded as requested)
FREE_SAMPLE_URL = "https://trenddropstudio.gumroad.com/l/free-sample"

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


def _canonicalize_url(raw_url: str) -> str:
    """
    Normalize URLs so the same item always maps to the same canonical URL,
    even if affiliate params change.
    We keep scheme + host + path only. (Drop all query params.)
    """
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
    """
    Stable key stored in Supabase. Short + consistent.
    """
    try:
        return hashlib.md5((canonical_url or "").encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _topic_key_for_product(p: Dict) -> str:
    """
    Variety grouping key.
    Prefer keyword (your scraper tags items with keyword like 'wireless_charger'),
    fall back to first tag, else 'other'.
    """
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
    """
    Enforce variety:
      - At most `max_per_keyword` items for the same keyword per run.
      - Try to reach at least `min_unique_keywords` unique keywords (when possible).

    We do this in a deterministic greedy way over the already score-sorted list.
    """
    if limit <= 0:
        return []

    max_per_keyword = max(1, int(max_per_keyword))
    min_unique_keywords = max(1, int(min_unique_keywords))
    target_unique = min(min_unique_keywords, limit)

    picked: List[Dict] = []
    counts: Dict[str, int] = {}

    # Pass 1: strict cap per keyword
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

    # Pass 2: attempt swaps to increase unique keywords
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

    # Fill again if short
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
    """
    Stable seller identity for diversity control.
    Priority:
      1) seller_username (best)
      2) domain of listing URL (fallback)
      3) 'unknown'
    """
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


def _enforce_seller_diversity(
    items: List[Dict],
    *,
    max_per_seller: int,
) -> List[Dict]:
    """
    Enforce max items per seller while preserving order (already score-sorted).
    """
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
    """
    Best-effort: supports:
      - p["end_time_ts"] epoch seconds
      - p["end_time"] ISO string
      - p["itemEndDate"] ISO string
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


def _listing_type(p: Dict) -> str:
    """
    Returns: "Auction" | "Buy It Now" | ""
    Tries a few common fields; safe if missing.
    """
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
        bg_color = (15, 23, 42)  # slate-900
        accent = (99, 102, 241)  # indigo-500
        text_primary = (255, 255, 255)
        text_secondary = (226, 232, 240)  # slate-200

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
    """
    CTA aimed at "anyone flipping products online" (your chosen framing).
    Includes your real free sample URL always.
    Includes paid pack URL if GUMROAD_CTA_URL is set in env/config.
    """
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
        lines += [
            f"🔥 Full Top 50 pack: <a href=\"{paid_url}\">Get it here</a>",
        ]

    lines += [
        "",
        "Tip: post the same-day items fast — speed is the edge.",
    ]

    return "\n".join(lines)


def _send_reseller_cta(api_base: str, chat_id: str) -> None:
    """
    Posts the CTA message (HTML) to the Telegram target.
    """
    try:
        text = _build_reseller_cta_text()
        requests.post(
            f"{api_base}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
    except Exception:
        return


def _pin_last_message(api_base: str, chat_id: str) -> None:
    """
    Optional: pin the most recent message in the chat/channel.
    Requires the bot to have pin permissions in that chat/channel.
    """
    try:
        # Get last update and pin it (best effort).
        # NOTE: Telegram doesn't provide a direct "pin last message" without a message_id.
        # We try by fetching getUpdates; if not possible (common in channels), we just skip.
        r = requests.get(f"{api_base}/getUpdates", timeout=20)
        if r.status_code != 200:
            return
        data = r.json() if r.content else {}
        res = data.get("result") or []
        if not res:
            return

        last = res[-1]
        msg = last.get("message") or last.get("channel_post") or {}
        mid = msg.get("message_id")
        cid = msg.get("chat", {}).get("id")

        # Ensure we pin only in the intended chat
        if not mid or not cid:
            return
        if str(cid) != str(chat_id) and str(chat_id) not in (str(cid),):
            # For channels, ids can be numeric; if mismatch, bail safely
            pass

        requests.post(
            f"{api_base}/pinChatMessage",
            data={
                "chat_id": chat_id,
                "message_id": mid,
                "disable_notification": True,
            },
            timeout=20,
        )
    except Exception:
        return


def post_telegram(products: List[Dict], limit=5):
    import random
    from trenddrop.conversion.ebay_conversion import conversion_score, passes_hard_filters

    token = BOT_TOKEN
    chat_id = CHAT_ID
    if not token or not chat_id or not products:
        return

    api = f"https://api.telegram.org/bot{token}"

    # Dedupe window (hours)
    dedupe_hours = 48
    try:
        dedupe_hours = int(str(os.environ.get("TELEGRAM_DEDUPE_HOURS", "48")).strip())
    except Exception:
        dedupe_hours = 48

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

    # CTA Controls (Combination D)
    # - After every N product posts
    # - And at end of the batch
    # - And optional pin (best effort)
    cta_every_n_posts = 6
    cta_cooldown_minutes = 180  # 3 hours by default; avoids hourly spam
    try:
        cta_every_n_posts = int(str(os.environ.get("TELEGRAM_CTA_EVERY_N_POSTS", "6")).strip())
    except Exception:
        cta_every_n_posts = 6
    if cta_every_n_posts < 2:
        cta_every_n_posts = 2

    try:
        cta_cooldown_minutes = int(str(os.environ.get("TELEGRAM_CTA_COOLDOWN_MINUTES", "180")).strip())
    except Exception:
        cta_cooldown_minutes = 180
    if cta_cooldown_minutes < 15:
        cta_cooldown_minutes = 15

    pin_cta = False
    try:
        pin_cta = str(os.environ.get("TELEGRAM_PIN_CTA", "0")).strip().lower() in ("1", "true", "yes", "y")
    except Exception:
        pin_cta = False

    recent_keys = fetch_recent_posted_keys(dedupe_hours)
    if recent_keys:
        print(f"[telegram] dedupe active: {len(recent_keys)} items posted in last {dedupe_hours}h")

    prepared = [ensure_rank_fields(dict(p)) for p in products]
    collapsed = dedupe_near_duplicates(prepared)

    scored = []
    for p in collapsed:
        raw_url = str(p.get("url") or "")
        canonical = _canonicalize_url(raw_url)
        key = _url_key(canonical)

        if key and key in recent_keys:
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

    # >>> FORCE VARIETY (KEYWORDS) <<<
    varied = _select_with_variety(
        scored,
        max(1, int(limit)),
        max_per_keyword=max_per_keyword,
        min_unique_keywords=min_unique_keywords,
    )

    # >>> FORCE DIVERSITY (SELLERS) <<<
    pick = _enforce_seller_diversity(
        varied,
        max_per_seller=max_per_seller,
    )

    # If seller diversity trimmed too aggressively, refill
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

    # Debug log so you can confirm variety in Actions logs
    try:
        kset = [_topic_key_for_product(x) for x in pick]
        sset = [_seller_key_for_product(x) for x in pick]
        print(f"[telegram] pick keywords: {kset}")
        print(f"[telegram] pick sellers:  {sset}")
    except Exception:
        pass

    # CTA cooldown memory (per-run; avoids multiple CTAs inside the same job run)
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
            title_raw = str(p.get("title") or "")
            title = html.escape(title_raw[:170])

            price = p.get("price")
            currency = p.get("currency", "USD")

            try:
                first_tag = (p.get("tags") or [p.get("keyword") or "trend"])[0]
                url = affiliate_wrap(p.get("url", ""), custom_id=str(first_tag).replace(" ", "_")[:40])
            except Exception:
                url = p.get("url", "")

            click_url = p.get("click_url")
            final_url = click_url or url

            img = p.get("image_url")

            fb = p.get("seller_feedback")
            top_rated = p.get("top_rated")
            trust_line = ""
            if fb:
                trust_line = f"⭐ Seller feedback: {fb}"
                if top_rated:
                    trust_line += " · Top Rated"

            # ==========================
            # IMPROVED HOOK LINES
            # ✅ Free returns
            # 🚚 Free shipping / Shipping: $X.XX
            # ⏳ Ends soon (within 6h)
            # 🛒 Listing type
            # ==========================
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
                    else:
                        hook_lines.append(f"🚚 Shipping: {currency} {ship_f:.2f}")
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

            price_text = f"{currency} {price:.2f}" if isinstance(price, (int, float)) else f"{currency} {price}"
            variant = (hash(title_raw) % 2)

            if variant == 0:
                headline = "🔥 DEAL WATCH"
                cta = "👉 Tap to view"
                body_lines = [
                    headline,
                    f"<b>{title}</b>",
                    f"💰 {price_text}",
                ]
            else:
                headline = "⚡ TRENDING + BUYER-READY"
                cta = "🛒 Check it out"
                body_lines = [
                    headline,
                    f"<b>{title}</b>",
                    f"Price: {price_text}",
                ]

            if trust_line:
                body_lines.append(trust_line)

            if hook_lines:
                body_lines.append("\n".join(hook_lines))

            if cond_line:
                body_lines.append(cond_line)

            body_lines += ["", f"<a href=\"{final_url}\">{cta}</a>"]
            caption = "\n".join(body_lines)

            if img:
                resp = requests.post(
                    f"{api}/sendPhoto",
                    data={
                        "chat_id": chat_id,
                        "photo": img,
                        "caption": caption,
                        "parse_mode": "HTML",
                    },
                    timeout=20,
                )
            else:
                resp = requests.post(
                    f"{api}/sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": caption,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                    },
                    timeout=20,
                )

            try:
                if getattr(resp, "status_code", 0) >= 400:
                    continue
            except Exception:
                pass

            posted_any = True
            sent_count += 1

            try:
                mark_posted_item(
                    url_key=str(p.get("_url_key") or ""),
                    canonical_url=str(p.get("_canonical_url") or ""),
                    keyword=str(p.get("keyword") or ""),
                    title=title_raw,
                    provider=str(p.get("provider") or ""),
                    source=str(p.get("source") or ""),
                )
            except Exception:
                pass

            # --- CTA behavior (Combination D) ---
            # 1) keep your existing CTA logic available (but don't spam it every product)
            # 2) send our reseller CTA every N posts (cooldown-protected)
            # 3) still allow maybe_send_cta() to run, but only at the same cadence
            if sent_count % int(cta_every_n_posts) == 0:
                if can_send_cta_now():
                    try:
                        _send_reseller_cta(api, chat_id)
                        mark_cta_sent()
                    except Exception:
                        pass

                    # Keep your existing CTA helper too (best-effort)
                    try:
                        maybe_send_cta()
                    except Exception:
                        pass

                    # Optional pin (best effort; usually works in groups, may not in channels)
                    if pin_cta:
                        try:
                            _pin_last_message(api, chat_id)
                        except Exception:
                            pass

            time.sleep(0.55 + random.uniform(0.0, 0.35))
        except Exception:
            continue

    # End-of-batch CTA (cooldown protected)
    if posted_any and can_send_cta_now():
        try:
            _send_reseller_cta(api, chat_id)
            mark_cta_sent()
        except Exception:
            pass

        # Keep your existing CTA helper too (best-effort)
        try:
            maybe_send_cta()
        except Exception:
            pass

        if pin_cta:
            try:
                _pin_last_message(api, chat_id)
            except Exception:
                pass

    # after posting, log a run summary
    try:
        uniq_topics = set()
        for p in products:
            for t in p.get("tags", []) or []:
                uniq_topics.add(t)
        save_run_summary(topic_count=len(uniq_topics) or 1, item_count=len(pick))
    except Exception:
        pass
