import os, json, time, requests, pathlib, html
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import CLICK_REDIRECT_BASE, BOT_TOKEN, CHAT_ID
from trenddrop.reports.product_quality import (
    dedupe_near_duplicates,
    rank_key,
    ensure_rank_fields,
)

# Ensure root .env is loaded
ENV_PATH = load_env_once()
from io import BytesIO
from typing import List, Dict, Optional
from utils.db import save_run_summary, upsert_products
from trenddrop.utils.telegram_cta import maybe_send_cta
from utils.epn import affiliate_wrap
from utils.ai import caption_for, marketing_copy_for

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs")
DOCS_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "data")
PRODUCTS_PATH = os.path.join(DOCS_DATA, "products.json")
OG_PATH = os.path.join(DOCS_DIR, "og.png")

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore

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

        # Try to load a clean sans font
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

        # Accent bar
        draw.rectangle([(0, 0), (width, 14)], fill=accent)

        # Title and subtitle
        title = "TrendDrop"
        subtitle = "Today’s Trending Finds"
        draw.text((60, 140), title, fill=text_primary, font=f_title)
        draw.text((64, 240), subtitle, fill=text_secondary, font=f_sub)

        # Optional: paste up to 3 product thumbnails on the right
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

        # Tagline footer
        ts = time.strftime("Updated %b %d, %Y", time.gmtime())
        draw.text((60, height - 80), ts, fill=(148, 163, 184), font=f_tag)

        # Save
        img.save(OG_PATH, format="PNG", optimize=True)
    except Exception:
        # soft-fail; skip OG generation
        return

def update_storefront(products: List[Dict], raw_products: Optional[List[Dict]] = None):
    raw_for_upsert = raw_products if raw_products else products
    print(f"[scraper] fetched {len(raw_for_upsert)} raw eBay products before filtering/dedup")
    upsert_products(raw_for_upsert)
    # enrich captions for site/telegram
    for p in products:
        try:
            p["caption"] = caption_for(p)
            # add structured marketing copy
            mc = marketing_copy_for(p)
            p["headline"] = mc.get("headline")
            p["blurb"] = mc.get("blurb")
            p["emojis"] = mc.get("emojis")
            # ensure affiliate params present
            try:
                first_tag = (p.get("tags") or [p.get("keyword") or "trend"]) [0]
                p["url"] = affiliate_wrap(p.get("url", ""), custom_id=str(first_tag).replace(" ", "_")[:40])
            except Exception:
                pass
        except Exception:
            p["caption"] = p.get("title", "")
    ensure_dirs()
    # compute click redirect URL if configured
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
    # Generate or refresh OG image banner (best-effort)
    try:
        _generate_og_image(products)
    except Exception:
        pass

def post_telegram(products: List[Dict], limit=5):
    token = BOT_TOKEN
    chat_id = CHAT_ID
    if not token or not chat_id or not products:
        return

    api = f"https://api.telegram.org/bot{token}"
    prepared = [ensure_rank_fields(dict(p)) for p in products]
    collapsed = dedupe_near_duplicates(prepared)
    ranked = sorted(collapsed, key=rank_key, reverse=True)
    pick = ranked[:limit]
    for p in pick:
        try:
            # prefer AI headline; fallback to title
            title_raw = str(p.get("headline") or p.get("title") or "")
            title = html.escape(title_raw)
            price = p.get("price")
            currency = p.get("currency", "USD")
            # ensure affiliate params present again for safety
            try:
                first_tag = (p.get("tags") or [p.get("keyword") or "trend"]) [0]
                url = affiliate_wrap(p.get("url", ""), custom_id=str(first_tag).replace(" ", "_")[:40])
            except Exception:
                url = p.get("url", "")
            img = p.get("image_url")
            # combine AI blurb + old caption for redundancy
            blurb = str(p.get("blurb") or "").strip()
            caption_extra = blurb if blurb else (p.get("caption") or "")
            price_text = f"{currency} {price:.2f}" if isinstance(price, (int, float)) else f"{currency} {price}"
            emoji_pack = p.get("emojis") or ""
            cap_body = html.escape(caption_extra)
            if emoji_pack:
                cap_body = f"{emoji_pack} {cap_body}"
            caption = f"✅ <b>{title}</b> — {price_text}\n{cap_body}\n<a href=\"{url}\">View</a>"

            if img:
                requests.post(
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
                requests.post(
                    f"{api}/sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": caption,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=20,
                )
            # After each product message, maybe trigger CTA based on batch + cooldown
            try:
                maybe_send_cta()
            except Exception:
                pass
            time.sleep(0.4)
        except Exception:
            # best-effort; continue with next product
            continue

    # after posting, log a run summary
    try:
        uniq_topics = set()
        for p in products:
            for t in p.get("tags", []) or []:
                uniq_topics.add(t)
        save_run_summary(topic_count=len(uniq_topics) or 1, item_count=len(pick))
    except Exception:
        pass
