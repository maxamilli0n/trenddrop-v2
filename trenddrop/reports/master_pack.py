"""
Master Top 25 pack builder (cross-market, eBay + Amazon only).
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

from utils.db import sb
from utils.report import generate_table_pdf, write_csv
from trenddrop.reports.product_quality import dedupe_near_duplicates, rank_key
from trenddrop.timezones import NYC_TZ

# Where to write master CSV/PDF
OUT_DIR = Path("out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

MASTER_CSV = OUT_DIR / "master_top25.csv"
MASTER_PDF = OUT_DIR / "master_top25.pdf"

# Only providers we care about right now
SUPPORTED_PROVIDERS = ["ebay", "amazon"]


def _fetch_clean_products() -> List[Dict]:
    """
    Pull cleaned products from v_products_clean for the supported providers.

    We read directly from Supabase so we have full fields available
    (title, url, provider, signals, seller_feedback, etc.).
    """
    client = sb()
    rows: List[Dict] = []

    for provider in SUPPORTED_PROVIDERS:
        try:
            print(f"[master] fetching rows for provider={provider}")
            res = (
                client.table("v_products_clean")
                .select(
                    "title, price, currency, image_url, url, "
                    "seller_feedback, signals, provider, source, inserted_at"
                )
                .eq("provider", provider)
                .order("inserted_at", desc=True)
                .limit(200)
                .execute()
            )

            for row in res.data or []:
                # Make sure provider is always present
                row.setdefault("provider", provider)
                rows.append(row)
        except Exception as exc:
            print(f"[master] warning: failed for provider={provider}: {exc}")

    print(f"[master] total rows fetched across providers: {len(rows)}")
    return rows


def build_master_top25() -> Tuple[str, str]:
    """
    Build a cross-market Top-25 CSV + PDF and return their paths.

    Returns:
        (csv_path, pdf_path)
    """
    products = _fetch_clean_products()

    if not products:
        print("[master] no products fetched; writing empty master files")
        # still create empty CSV/PDF so the workflow doesn't explode
        write_csv([], str(MASTER_CSV), [
            {"key": "title", "label": "Title"},
            {"key": "provider", "label": "Provider"},
            {"key": "price", "label": "Price"},
            {"key": "currency", "label": "Currency"},
            {"key": "seller_feedback", "label": "Seller FB"},
            {"key": "signals", "label": "Signals"},
        ])
        generate_table_pdf(
            [],
            str(MASTER_PDF),
            columns=[
                {"key": "title", "label": "Title"},
                {"key": "provider", "label": "Provider"},
                {"key": "price", "label": "Price"},
                {"key": "currency", "label": "Currency"},
                {"key": "seller_feedback", "label": "Seller FB"},
                {"key": "signals", "label": "Signals"},
            ],
            title="TrendDrop Master Top 25 — Cross Marketplace",
            subtitle_lines=["No data available"],
        )
        return str(MASTER_CSV), str(MASTER_PDF)

    # Use same dedupe + ranking logic as other reports
    products = dedupe_near_duplicates(products)
    products = sorted(products, key=rank_key, reverse=True)
    top25 = products[:25]

    # Columns for CSV + PDF (Provider column included)
    columns = [
        {"key": "title", "label": "Title"},
        {"key": "provider", "label": "Provider"},
        {"key": "price", "label": "Price"},
        {"key": "currency", "label": "Currency"},
        {"key": "seller_feedback", "label": "Seller FB"},
        {"key": "signals", "label": "Signals"},
    ]

    # Subtitle lines (timestamp + explanation)
    now_local = datetime.now(NYC_TZ)
    subtitle_lines = [
        now_local.strftime("Generated: %Y-%m-%d %I:%M %p %Z"),
        "Cross-market Top 25 curated across eBay and Amazon.",
        "PDF shows curated picks; full per-provider data lives in the individual packs.",
    ]

    # CSV (same columns as PDF)
    write_csv(top25, str(MASTER_CSV), columns)

    # PDF using the same nice table layout as the weekly reports
    generate_table_pdf(
        top25,
        str(MASTER_PDF),
        columns=columns,
        title="TrendDrop Master Top 25 — Cross Marketplace",
        subtitle_lines=subtitle_lines,
    )

    print(f"[master] wrote CSV -> {MASTER_CSV}")
    print(f"[master] wrote PDF -> {MASTER_PDF}")

    # Return as strings (works fine with create_master_zip etc.)
    return str(MASTER_CSV), str(MASTER_PDF)


def main(argv: List[str] | None = None) -> None:
    # Simple CLI hook for manual runs: `python -m trenddrop.reports.master_pack`
    _ = argv  # unused for now
    build_master_top25()


if __name__ == "__main__":
    main()
