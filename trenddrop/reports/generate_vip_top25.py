import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from trenddrop.timezones import NYC_TZ
from trenddrop.reports.generate_reports import (
    _dedupe,
    _signals_sort_value,
    _data_window_label_from_products,
    _record_report_run,
)
from utils.db import load_clean_products_for_providers
from utils.report import generate_table_pdf, write_csv


VIP_TOP_N = 25
VIP_PROVIDER_KEY = "vip_cross_market"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Cross-Marketplace VIP Top 25 report.")
    parser.add_argument(
        "--providers",
        nargs="+",
        default=["ebay", "amazon", "aliexpress"],
        help="Providers to include in the VIP ranking.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    providers = [p.lower() for p in args.providers if p]
    run_started_at = datetime.now(NYC_TZ)

    raw_products = load_clean_products_for_providers(providers, limit=1000)
    print(f"[vip] loaded {len(raw_products)} raw products from {providers}")

    deduped = _dedupe(raw_products, default_provider=VIP_PROVIDER_KEY)
    ranked = sorted(deduped, key=_signals_sort_value, reverse=True)
    curated = ranked[:VIP_TOP_N]

    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_outfile = out_dir / "vip-cross-market.pdf"
    csv_outfile = out_dir / "vip-cross-market.csv"

    data_window_label = _data_window_label_from_products(curated, fallback_ts=None)

    title = "Top 25 Trending Products — Cross-Marketplace VIP Report"
    subtitle_lines = [
        f"Includes providers: {', '.join(providers)}",
        f"Data window: {data_window_label}",
    ]
    columns = [
        {"key": "title", "label": "Title"},
        {"key": "price", "label": "Price"},
        {"key": "currency", "label": "Currency"},
        {"key": "source", "label": "Provider"},
        {"key": "seller_feedback", "label": "Seller FB"},
        {"key": "signals", "label": "Signals"},
    ]

    if not curated:
        print("[vip] no curated products available; aborting")
        _record_report_run(
            run_started_at=run_started_at,
            provider=VIP_PROVIDER_KEY,
            data_window_label=data_window_label,
            products_total=len(ranked),
            curated_count=0,
            success=False,
            error_message="No VIP curated products available",
            pdf_url=None,
            csv_url=None,
        )
        return

    print(f"[vip] generating table PDF ({len(curated)} items) -> {pdf_outfile}")
    generate_table_pdf(curated, str(pdf_outfile), columns, title, subtitle_lines=subtitle_lines)
    write_csv(ranked, str(csv_outfile), columns)

    _record_report_run(
        run_started_at=run_started_at,
        provider=VIP_PROVIDER_KEY,
        data_window_label=data_window_label,
        products_total=len(ranked),
        curated_count=len(curated),
        success=True,
        error_message=None,
        pdf_url=str(pdf_outfile),
        csv_url=str(csv_outfile),
    )

    print(f"[vip] Generated VIP report with {len(curated)} curated and {len(ranked)} total products.")


if __name__ == "__main__":
    main()


