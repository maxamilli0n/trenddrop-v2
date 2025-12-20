#!/usr/bin/env python3
import argparse
import os
import time
from typing import Dict, List, Sequence

import requests

from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from utils.trends import top_topics, topic_query_variants
from utils.sources import search_ebay
from utils.epn import affiliate_wrap
from utils.publish import update_storefront, post_telegram
from trenddrop.telegram_utils import send_text
from trenddrop.reports.product_quality import (
    dedupe_near_duplicates,
    rank_key,
    ensure_rank_fields,
)

ENV_PATH = load_env_once()


def log(msg: str) -> None:
    print(f"[cli] {msg}")


def _synthetic_signal(p: Dict) -> float:
    score = 0.0
    if p.get("top_rated"):
        score += 5.0
    try:
        score += min(float(p.get("seller_feedback") or 0) / 1000.0, 5.0)
    except Exception:
        pass
    try:
        price = float(p.get("price") or 0)
        if 15 <= price <= 150:
            score += 4.0
        elif 5 <= price < 15:
            score += 2.0
        elif 150 < price <= 400:
            score += 1.0
    except Exception:
        pass
    return score


def _dedupe(products: Sequence[Dict]) -> List[Dict]:
    seen = set()
    out: List[Dict] = []
    for p in products:
        url = p.get("url")
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(p)
    return out


def cmd_scrape(args: argparse.Namespace) -> int:
    import random

    topics = top_topics(limit=args.topics)
    if not topics:
        log("No topics returned; aborting.")
        return 1

    log(f"Topics: {topics}")
    raw_candidates: List[Dict] = []
    variant_cap = max(1, args.variants_per_topic)

    for topic in topics:
        queries = topic_query_variants(topic, max_variants=variant_cap) or [topic]
        for query in queries:
            try:
                found = search_ebay(query, per_page=args.per_page)
                log(f"Found {len(found)} items for '{query}' (topic '{topic}')")
                for item in found:
                    item["signals"] = _synthetic_signal(item)
                    item["tags"] = [topic]
                    item["url"] = affiliate_wrap(item.get("url", ""), custom_id=topic.replace(" ", "_")[:40])
                    ensure_rank_fields(item)
                raw_candidates.extend(found)
            except Exception as exc:
                log(f"search failed for '{query}' (topic '{topic}'): {exc}")

            if args.sleep_secs > 0:
                sleep = args.sleep_secs
                if args.sleep_jitter > 0:
                    sleep += random.uniform(0, args.sleep_jitter)
                time.sleep(sleep)

    if not raw_candidates:
        log("No products fetched; exiting.")
        return 1

    candidates = _dedupe(raw_candidates)
    prepared = [ensure_rank_fields(p) for p in candidates]
    collapsed = dedupe_near_duplicates(prepared)
    ranked = sorted(collapsed, key=rank_key, reverse=True)
    picks = ranked[: args.picks]

    log(
        "Selected %d products (raw=%d url_deduped=%d title_seller_deduped=%d)."
        % (len(picks), len(raw_candidates), len(candidates), len(collapsed))
    )

    log("Updating storefront + Supabase…")
    update_storefront(picks, raw_products=raw_candidates)
    log("Storefront + Supabase updated.")

    if not args.no_telegram and args.telegram_limit > 0:
        log(f"Posting to Telegram scope='{args.telegram_scope}'…")
        post_telegram(picks, limit=args.telegram_limit, scope=args.telegram_scope)
        log("Telegram broadcast complete.")
    else:
        log("Telegram broadcast skipped.")

    return 0


def _signed_report_url(mode: str, fmt: str) -> str | None:
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    try:
        response = requests.post(
            f"{SUPABASE_URL}/functions/v1/report-links",
            headers={
                "authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "content-type": "application/json",
            },
            json={"mode": mode, "format": fmt},
            timeout=20,
        )
        if not response.ok:
            return None
        data = response.json()
        return data.get("url")
    except Exception:
        return None


def cmd_post_weekly(args: argparse.Namespace) -> int:
    link = args.link or _signed_report_url(args.mode, args.format)
    if not link:
        log("Could not resolve a signed URL; provide --link or ensure SUPABASE env vars are set.")
        return 1

    template = args.message or (
        "📦 TrendDrop Weekly Pack is live!\n"
        f"Download the latest {args.format.upper()}: {{link}}\n"
    )
    message = template.replace("{link}", link)

    send_text(message, scope=args.telegram_scope, disable_web_page_preview=False)
    log("Telegram announcement sent.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TrendDrop automation CLI – scrape, generate packs, and broadcast updates.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape-ebay", help="Fetch trending products and update storefront/Supabase.")
    p_scrape.add_argument("--topics", type=int, default=4)
    p_scrape.add_argument("--per-page", type=int, default=20)
    p_scrape.add_argument("--variants-per-topic", type=int, default=3)
    p_scrape.add_argument("--picks", type=int, default=6)
    p_scrape.add_argument("--sleep-secs", type=float, default=3.0)
    p_scrape.add_argument("--sleep-jitter", type=float, default=2.0)
    p_scrape.add_argument("--telegram-limit", type=int, default=5)
    p_scrape.add_argument("--telegram-scope", default="broadcast", choices=("admin", "public", "paid", "broadcast", "legacy"))
    p_scrape.add_argument("--no-telegram", action="store_true")
    p_scrape.set_defaults(func=cmd_scrape)

    p_post = sub.add_parser("post-weekly-pack-telegram", help="Send latest pack link to Telegram.")
    p_post.add_argument("--mode", default="weekly")
    p_post.add_argument("--format", default="pdf", choices=("pdf", "csv"))
    p_post.add_argument("--link")
    p_post.add_argument("--message")
    p_post.add_argument("--telegram-scope", default="broadcast", choices=("admin", "public", "paid", "broadcast", "legacy"))
    p_post.set_defaults(func=cmd_post_weekly)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
