"""
Build per-provider product packs and a VIP Top 25 pack from weekly exports.

Usage:
    python -m trenddrop.reports.build_packs

This script expects the latest weekly PDFs/CSVs to exist locally under
out/<provider>_weekly.(pdf|csv) (or legacy weekly-<provider>.*) or to be retrievable from Supabase Storage
at weekly/<provider>/latest.(pdf|csv). It zips each provider's assets and
creates a VIP pack that ranks the top 25 listings across all providers.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from utils.report import generate_table_pdf
from trenddrop.reports.master_pack import build_master_top25
from trenddrop.reports.zip_packs import create_master_zip

VIP_TOP_N = 25
PROVIDERS = ["ebay", "amazon", "aliexpress"]

OUT_DIR = Path("out")
PACKS_DIR = OUT_DIR / "packs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PACKS_DIR.mkdir(parents=True, exist_ok=True)


def _storage_bucket() -> str:
    return os.environ.get("SUPABASE_STORAGE_BUCKET") or "trenddrop-reports"


def _supabase_url() -> Optional[str]:
    url = os.environ.get("SUPABASE_URL")
    if url:
        return url.rstrip("/")
    return None


def _storage_public_url(path: str) -> Optional[str]:
    base = _supabase_url()
    if not base:
        return None
    bucket = _storage_bucket()
    return f"{base}/storage/v1/object/public/{bucket}/{path}"


def _download(url: str, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[packs] downloading {url} -> {dest}")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return True
    except Exception as exc:
        print(f"[packs] failed to download {url}: {exc}")
        return False


def _candidate_files(provider: str, extension: str) -> List[Path]:
    return [
        OUT_DIR / f"{provider}_weekly.{extension}",
        OUT_DIR / f"weekly-{provider}.{extension}",
    ]


def _ensure_provider_file(provider: str, extension: str) -> Optional[Path]:
    for candidate in _candidate_files(provider, extension):
        if candidate.exists():
            return candidate
    local_path = _candidate_files(provider, extension)[0]
    storage_path = f"weekly/{provider}/latest.{extension}"
    public_url = _storage_public_url(storage_path)
    if public_url and _download(public_url, local_path):
        return local_path
    print(f"[packs] missing {provider}_weekly.{extension}; unable to download from storage")
    return None


def _ensure_provider_assets(provider: str) -> Tuple[Optional[Path], Optional[Path]]:
    pdf_path = _ensure_provider_file(provider, "pdf")
    csv_path = _ensure_provider_file(provider, "csv")
    return pdf_path, csv_path


def load_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(dict(row))
    return rows


def _safe_float(value: Optional[str]) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    normalized = re.sub(r"[^0-9.]", "", str(value))
    try:
        return float(normalized)
    except ValueError:
        return 0.0


def _star_value(value: Optional[str]) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    if text.strip().isdigit():
        return float(text.strip())
    return text.count("★")


def compute_score(signals: float, seller_fb: float, price: float) -> float:
    price_term = max(price, 0.0)
    return signals * math.log(seller_fb + 1.0) / math.sqrt(price_term + 1.0)


def build_provider_pack(provider: str, date_str: str) -> Optional[Path]:
    pdf_path, csv_path = _ensure_provider_assets(provider)
    if not pdf_path or not csv_path:
        print(f"[packs] skipping provider pack for {provider} (missing assets)")
        return None

    zip_path = PACKS_DIR / f"{provider}-top50-{date_str}.zip"
    arc_pdf = f"{provider}-weekly-report.pdf"
    arc_csv = f"{provider}-weekly-report.csv"
    print(f"[packs] building provider pack -> {zip_path}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(pdf_path, arcname=arc_pdf)
        zf.write(csv_path, arcname=arc_csv)

    return zip_path


def _normalize_vip_row(provider: str, row: Dict[str, str]) -> Dict[str, object]:
    title = row.get("Title") or row.get("title") or row.get("headline") or ""
    price_str = row.get("Price") or row.get("price") or ""
    currency = row.get("Currency") or row.get("currency") or ""
    seller_fb_str = row.get("Seller FB") or row.get("seller_feedback") or row.get("seller_fb") or ""
    signals_str = row.get("Signals") or row.get("signals") or ""
    url = row.get("URL") or row.get("Url") or row.get("url") or ""

    price_val = _safe_float(price_str)
    seller_fb_val = _safe_float(seller_fb_str)
    signals_val = _star_value(signals_str)
    score = compute_score(signals_val, seller_fb_val, price_val)

    return {
        "provider": provider,
        "provider_label": provider.capitalize(),
        "title": str(title),
        "price": str(price_str),
        "currency": str(currency or "USD"),
        "seller_feedback": str(seller_fb_str),
        "signals": str(signals_str),
        "url": str(url),
        "score": score,
        "score_label": f"{score:.3f}",
    }


def _write_vip_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    fieldnames = ["Provider", "Title", "Price", "Currency", "Seller FB", "Signals", "Score"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "Provider": row["provider_label"],
                    "Title": row["title"],
                    "Price": row["price"],
                    "Currency": row["currency"],
                    "Seller FB": row["seller_feedback"],
                    "Signals": row["signals"],
                    "Score": f'{row["score"]:.3f}',
                }
            )


def build_vip_pack(date_str: str) -> Optional[Path]:
    vip_rows: List[Dict[str, object]] = []
    for provider in PROVIDERS:
        csv_path = _ensure_provider_file(provider, "csv")
        if not csv_path or not csv_path.exists():
            print(f"[packs] missing CSV for VIP aggregation: {provider}")
            continue
        for row in load_csv(csv_path):
            vip_rows.append(_normalize_vip_row(provider, row))

    if not vip_rows:
        print("[packs] no data available for VIP pack")
        return None

    vip_rows.sort(key=lambda r: r["score"], reverse=True)
    top_rows = vip_rows[:VIP_TOP_N]

    vip_csv = PACKS_DIR / f"vip-top25-all-{date_str}.csv"
    _write_vip_csv(vip_csv, top_rows)

    vip_pdf = PACKS_DIR / f"vip-top25-all-{date_str}.pdf"
    columns = [
        {"key": "title", "label": "Title"},
        {"key": "provider_label", "label": "Provider"},
        {"key": "price", "label": "Price"},
        {"key": "currency", "label": "Currency"},
        {"key": "seller_feedback", "label": "Seller FB"},
        {"key": "signals", "label": "Signals"},
        {"key": "score_label", "label": "Score"},
    ]
    subtitle_lines = [
        f"Generated: {dt.datetime.now(dt.timezone.utc).strftime('%b %d %Y %H:%M UTC')}",
        "Top 25 listings ranked across all providers by signals, seller reputation, and price.",
    ]
    generate_table_pdf(top_rows, str(vip_pdf), columns, title="VIP Top 25 — All Platforms", subtitle_lines=subtitle_lines)

    zip_path = PACKS_DIR / f"vip-top25-all-{date_str}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(vip_pdf, arcname="vip-top25-all-platforms.pdf")
        zf.write(vip_csv, arcname="vip-top25-all-platforms.csv")

    print(f"[packs] built VIP pack -> {zip_path}")
    return zip_path


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Build weekly provider packs and VIP pack.")
    parser.add_argument(
        "--date",
        type=str,
        default=dt.date.today().isoformat(),
        help="Date string (YYYY-MM-DD) used in output filenames. Defaults to today.",
    )
    args = parser.parse_args(argv)
    date_str = args.date

    for provider in PROVIDERS:
        build_provider_pack(provider, date_str=date_str)

    build_vip_pack(date_str=date_str)
    try:
        master_csv, master_pdf = build_master_top25()
        master_zip = create_master_zip(master_csv, master_pdf)
        print(f"[packs] master pack built at {master_zip}")
    except RuntimeError as exc:
        print(f"[packs] master pack skipped: {exc}")


if __name__ == "__main__":
    main()


