#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Sequence

import requests

from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import (
    BOT_TOKEN,
    CHAT_ID,
    CHANNEL_ID,
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
)
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

# Ensure .env is loaded once for all commands
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
        queries = topic_query_variants(topic, max_variants=variant_cap)
        if not queries:
            queries = [topic]
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
        log("Posting to Telegram…")
        post_telegram(picks, limit=args.telegram_limit)
        log("Telegram broadcast complete.")
    else:
        log("Telegram broadcast skipped.")
    return 0


def cmd_generate_weekly(args: argparse.Namespace) -> int:
    from trenddrop.reports import generate_reports, build_manifest

    log("Generating weekly pack…")
    generate_reports.main()
    build_manifest.main()
    log("Pack + manifest generated.")

    if args.verify:
        log("Running smoke test…")
        from scripts import smoke_test

        smoke_test.main()
        log("Smoke test passed.")
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
            if response.status_code == 404:
                log(
                    "report-links function is not deployed on this Supabase project.\n"
                    "Deploy it via: supabase functions deploy report-links\n"
                    "Or pass --link to the CLI command."
                )
            else:
                log(f"report-links error {response.status_code}: {response.text}")
            return None
        data = response.json()
        return data.get("url")
    except Exception as exc:
        log(f"report-links request failed: {exc}")
        return None


def cmd_post_weekly(args: argparse.Namespace) -> int:
    link = args.link or _signed_report_url(args.mode, args.format)
    if not link:
        log("Could not resolve a signed URL; provide --link or ensure SUPABASE env vars are set.")
        return 1

    template = args.message or (
        "📦 TrendDrop Weekly Pack is live!\n"
        f"Download the latest {args.format.upper()}: {{link}}\n"
        "Check your inbox for the invite + instructions."
    )
    message = template.replace("{link}", link)
    send_text(message, disable_web_page_preview=False)
    log("Telegram announcement sent.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TrendDrop automation CLI – scrape, generate packs, and broadcast updates.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape-ebay", help="Fetch trending products and update storefront/Supabase.")
    p_scrape.add_argument("--topics", type=int, default=4, help="Number of Google Trends topics to explore.")
    p_scrape.add_argument("--per-page", type=int, default=20, help="Listings per keyword query to fetch from eBay.")
    p_scrape.add_argument(
        "--variants-per-topic",
        type=int,
        default=3,
        help="Keyword variants to try per topic to widen the eBay search.",
    )
    p_scrape.add_argument("--picks", type=int, default=6, help="How many final products to publish.")
    p_scrape.add_argument("--sleep-secs", type=float, default=3.0, help="Base delay between eBay API calls.")
    p_scrape.add_argument("--sleep-jitter", type=float, default=2.0, help="Random jitter added to sleep seconds.")
    p_scrape.add_argument("--telegram-limit", type=int, default=5, help="Max Telegram posts per run.")
    p_scrape.add_argument("--no-telegram", action="store_true", help="Skip Telegram posting.")
    p_scrape.set_defaults(func=cmd_scrape)

    p_weekly = sub.add_parser("generate-weekly-pack", help="Generate weekly PDF/CSV and upload to Supabase.")
    p_weekly.add_argument("--verify", action="store_true", help="Run scripts.smoke_test after generation.")
    p_weekly.set_defaults(func=cmd_generate_weekly)

    p_post = sub.add_parser("post-weekly-pack-telegram", help="Send latest pack link to Telegram.")
    p_post.add_argument("--mode", default="weekly", help="Report mode to request via report-links (default: weekly).")
    p_post.add_argument("--format", default="pdf", choices=("pdf", "csv"), help="Report format for signed link.")
    p_post.add_argument("--link", help="Override the link instead of generating via Supabase.")
    p_post.add_argument("--message", help="Custom Telegram message (include {link} if desired).")
    p_post.set_defaults(func=cmd_post_weekly)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())


