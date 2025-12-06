"""
Generate a FREE 5-item sample report (eBay only).

This script:
- Pulls top 5 eBay products from v_products_clean
- Writes out/free_sample.csv
- Writes out/free_sample.pdf using the same table layout as your other reports
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Dict, List

from utils.db import sb
from utils.report import generate_table_pdf, write_csv
from trenddrop.timezones import NYC_TZ


OUT_DIR = Path("out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

COLUMNS: List[Dict[str, str]] = [
    {"key": "title", "label": "Title"},
    {"key": "price", "label": "Price"},
    {"key": "currency", "label": "Currency"},
    {"key": "seller_feedback", "label": "Seller FB"},
    {"key": "signals", "label": "Signals"},
]


def _fetch_top5_ebay() -> List[Dict]:
    """
    Fetch the top 5 eBay rows from v_products_clean,
    sorted by signals descending, using the same Supabase client
    your other reports rely on.
    """
    client = sb()
    res = (
        client.table("v_products_clean")
        .select(
            "title, price, currency, image_url, url, "
            "seller_feedback, signals, provider, source, inserted_at"
        )
        .eq("provider", "ebay")
        .order("signals", desc=True)
        .limit(5)
        .execute()
    )
    rows = res.data or []
    print(f"[free-sample] fetched {len(rows)} rows for provider=ebay")
    return rows


def build_free_sample():
    rows = _fetch_top5_ebay()
    if not rows:
        raise RuntimeError("[free-sample] No eBay rows available to build sample.")

    csv_path = OUT_DIR / "free_sample.csv"
    pdf_path = OUT_DIR / "free_sample.pdf"

    # Write CSV (same column spec as PDF)
    write_csv(rows, str(csv_path), COLUMNS)

    # Subtitle lines (simple + clear)
    now_local = datetime.now(NYC_TZ)
    subtitle_lines = [
        now_local.strftime("Generated: %Y-%m-%d %I:%M %p %Z"),
        "Free 5-item sampler from TrendDrop’s eBay movers.",
        "PDF shows curated picks; CSV lets you slice/filter in your own tools.",
    ]

    # Build PDF using your nice table layout
    generate_table_pdf(
        rows,
        str(pdf_path),
        columns=COLUMNS,
        title="TrendDrop • Free 5-Item Sample — eBay",
        subtitle_lines=subtitle_lines,
    )

    print(f"[free-sample] wrote CSV  -> {csv_path}")
    print(f"[free-sample] wrote PDF  -> {pdf_path}")
    return csv_path, pdf_path


if __name__ == "__main__":
    build_free_sample()
