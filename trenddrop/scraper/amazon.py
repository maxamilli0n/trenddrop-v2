from __future__ import annotations

import argparse
import math
import re
from typing import List, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from utils.db import upsert_products

from .common import dedupe_by_url, fetch_html, now_iso, parse_price

AMAZON_BASE = "https://www.amazon.com"
AMAZON_SEARCH_URL = "https://www.amazon.com/s"
SEED_QUERIES = [
    "smart home gadgets",
    "desk accessories",
    "travel essentials",
    "phone accessories",
    "gaming gear",
]


def _extract_rating(text: str) -> float:
    """
    Parse something like '4.7 out of 5 stars' -> 4.7
    """
    match = re.search(r"([\d\.]+)", text or "")
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _extract_reviews(text: str) -> int:
    """
    Parse something like '12,345 ratings' -> 12345
    """
    digits = re.sub(r"[^\d]", "", text or "")
    try:
        return int(digits)
    except ValueError:
        return 0


def _compute_signals(rating: float, review_count: int) -> float:
    """
    Composite Amazon signal score:
      - higher rating is better
      - many reviews is *much* better than a handful
      - uses log10 so it doesn't blow up for huge SKUs
    """
    if rating <= 0 or review_count <= 0:
        return 0.0
    return rating * math.log10(review_count + 10)


def _parse_card(card, keyword: str) -> Tuple[bool, dict]:
    title_tag = card.find("h2")
    title = " ".join(title_tag.get_text(" ").split()) if title_tag else ""

    link_tag = card.select_one("h2 a[href]") or card.select_one("a.a-link-normal[href]")
    if not link_tag:
        return False, {}

    if not title:
        title = " ".join(link_tag.get_text(" ").split())

    href = link_tag.get("href")
    if not href:
        return False, {}
    url = urljoin(AMAZON_BASE, href)

    # --- price ---
    price_whole = card.select_one("span.a-price-whole")
    price_fraction = card.select_one("span.a-price-fraction")
    price = None
    if price_whole:
        combined = price_whole.get_text("").strip()
        if price_fraction:
            combined = f"{combined}.{price_fraction.get_text('').strip()}"
        price = parse_price(combined)

    # normalize to a float so downstream code never sees None
    if price is None:
        price = 0.0

    # --- image ---
    image_tag = card.find("img", attrs={"src": True})
    image_url = image_tag["src"] if image_tag else ""

    # --- rating & reviews ---
    rating_tag = card.select_one("span.a-icon-alt")
    rating_value = _extract_rating(rating_tag.get_text() if rating_tag else "")

    review_tag = card.select_one("span.a-size-base.s-underline-text")
    review_count = _extract_reviews(review_tag.get_text() if review_tag else "")

    # For Amazon, treat "seller_feedback" as "number of reviews"
    seller_feedback = review_count

    # Composite signal score
    signals = _compute_signals(rating_value, review_count)

    top_rated = "Amazon's Choice" in card.get_text()

    product = {
        "title": title[:200],
        "url": url,
        "image_url": image_url,
        "price": price,
        "currency": "USD",
        "seller_feedback": seller_feedback,
        "signals": signals,
        "top_rated": top_rated,
        "provider": "amazon",
        "source": "amazon",
        "keyword": keyword,
        "inserted_at": now_iso(),
    }
    return True, product


def fetch_amazon_products(keyword: str, per_page: int) -> List[dict]:
    html = fetch_html(
        AMAZON_SEARCH_URL,
        params={
            "k": keyword,
            "s": "review-rank",
            "ref": "sr_nr_p_76_1",
        },
    )
    soup = BeautifulSoup(html, "html.parser")
    products: List[dict] = []
    for card in soup.select("div[data-component-type='s-search-result']"):
        ok, product = _parse_card(card, keyword)
        if not ok:
            continue
        products.append(product)
        if len(products) >= per_page:
            break
    return products


def scrape_amazon(queries: List[str], per_page: int) -> List[dict]:
    collected: List[dict] = []
    for keyword in queries:
        try:
            rows = fetch_amazon_products(keyword, per_page=per_page)
        except Exception as exc:
            print(f"[scraper-amazon] WARN query '{keyword}' failed: {exc}")
            continue
        print(f"[scraper-amazon] fetched {len(rows)} products for query '{keyword}'")
        collected.extend(rows)
    unique = dedupe_by_url(collected)
    print(f"[scraper-amazon] upserting {len(unique)} unique products into provider=amazon")
    if unique:
        upsert_products(unique)
    return unique


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Scrape Amazon search results into Supabase.")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=SEED_QUERIES,
        help="Keyword queries to fetch.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=20,
        help="Max products per query.",
    )
    args = parser.parse_args(argv)
    scrape_amazon(args.queries, per_page=args.per_page)


if __name__ == "__main__":
    main()
