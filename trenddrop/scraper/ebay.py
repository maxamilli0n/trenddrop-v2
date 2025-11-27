from __future__ import annotations

import argparse
from typing import Iterable, List

from utils.db import upsert_products
from utils.sources import search_ebay

from .common import dedupe_by_url, now_iso

SEED_QUERIES = [
    "home fitness",
    "kitchen gadgets",
    "collectible cards",
    "office desk",
    "retro electronics",
]


def _prepare_rows(rows: Iterable[dict], keyword: str) -> List[dict]:
    prepared: List[dict] = []
    for row in rows:
        row = dict(row)
        row["provider"] = "ebay"
        row["source"] = row.get("source") or "ebay"
        row["keyword"] = keyword
        row.setdefault("currency", "USD")
        row.setdefault("signals", 0.0)
        row.setdefault("inserted_at", now_iso())
        prepared.append(row)
    return prepared


def scrape_ebay(queries: List[str], per_page: int) -> List[dict]:
    collected: List[dict] = []
    for keyword in queries:
        results = search_ebay(keyword, per_page=per_page)
        print(f"[scraper-ebay] fetched {len(results)} products for query '{keyword}'")
        prepared = _prepare_rows(results, keyword)
        collected.extend(prepared)
    unique = dedupe_by_url(collected)
    print(f"[scraper-ebay] upserting {len(unique)} unique products into provider=ebay")
    if unique:
        upsert_products(unique)
    return unique


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Scrape trending products from eBay.")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=SEED_QUERIES,
        help="List of keyword queries to search.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=20,
        help="Listings to fetch per query.",
    )
    args = parser.parse_args(argv)
    scrape_ebay(args.queries, per_page=args.per_page)


if __name__ == "__main__":
    main()


