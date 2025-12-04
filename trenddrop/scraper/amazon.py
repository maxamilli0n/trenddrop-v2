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
    Extract a float like 4.5 from strings such as:
    - "4.5 out of 5 stars"
    - "4.3"
    """
    if not text:
        return 0.0
    match = re.search(r"([\d]+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _extract_reviews(text: str) -> int:
    """
    Extract an int from strings such as:
    - "1,234 ratings"
    - "234 rating"
    """
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def _parse_card(card, keyword: str) -> Tuple[bool, dict]:
    # ---- Title + URL ---------------------------------------------------------
    title_tag = card.find("h2")
    title = " ".join(title_tag.get_text(" ").split()) if title_tag else ""

    link_tag = (
        card.select_one("h2 a[href]")
        or card.select_one("a.a-link-normal[href]")
    )
    if not link_tag:
        return False, {}
    if not title:
        title = " ".join(link_tag.get_text(" ").split())

    href = link_tag.get("href")
    if not href:
        return False, {}
    url = urljoin(AMAZON_BASE, href)

    # ---- Price ---------------------------------------------------------------
    price = None

    # Preferred: a-offscreen has full "$39.99"
    price_tag = card.select_one("span.a-price span.a-offscreen")
    if price_tag:
        price = parse_price(price_tag.get_text().strip())
    else:
        # Fallback: whole + fraction like "39" + "99"
        price_whole = card.select_one("span.a-price-whole")
        price_fraction = card.select_one("span.a-price-fraction")
        if price_whole:
            combined = price_whole.get_text("").strip()
            if price_fraction:
                combined = f"{combined}.{price_fraction.get_text('').strip()}"
            price = parse_price(combined)

    # ---- Image ---------------------------------------------------------------
    image_tag = card.find("img", attrs={"src": True})
    image_url = image_tag["src"] if image_tag else ""

    # ---- Rating + reviews ----------------------------------------------------
    rating_value = 0.0
    review_count = 0

    # Rating text like "4.5 out of 5 stars"
    rating_tag = card.select_one("span.a-icon-alt")
    if rating_tag:
        rating_value = _extract_rating(rating_tag.get_text())
    else:
        # Sometimes stored in aria-label
        alt_rating = card.select_one("span[aria-label$='out of 5 stars']")
        if alt_rating:
            rating_value = _extract_rating(
                alt_rating.get("aria-label") or alt_rating.get_text()
            )

    # Reviews text like "1,234 ratings"
    review_tag = (
        card.select_one("span[aria-label$='ratings']")
        or card.select_one("span[aria-label$='rating']")
        or card.select_one("span.a-size-base.s-underline-text")
    )
    if review_tag:
        label = review_tag.get("aria-label") or review_tag.get_text()
        review_count = _extract_reviews(label)

    # ---- Signals heuristic ---------------------------------------------------
    # Combine rating (0–5) and review_count into a single sortable score.
    # This is just for ranking; absolute numbers don't matter.
    if rating_value <= 0 or review_count <= 0:
        signals = 0.0
    else:
        signals = rating_value * (1.0 + math.log10(1.0 + review_count))

    # ---- Top-rated flag ------------------------------------------------------
    txt = card.get_text(" ")
    top_rated = "Amazon's Choice" in txt or "Best Seller" in txt

    product = {
        "title": title[:200],
        "url": url,
        "image_url": image_url,
        "price": price,
        "currency": "USD",
        "seller_feedback": review_count,
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
    parser = argparse.ArgumentParser(
        description="Scrape Amazon search results into Supabase."
    )
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
