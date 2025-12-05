import argparse
from dataclasses import dataclass
import os
import pathlib
import shutil
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple
import re
import sys

from supabase import create_client
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore

from utils.report import (
    generate_weekly_pdf,
    generate_table_pdf,
    write_csv,
    get_provider_filter,  # still imported, but V1 is eBay-only
)
from trenddrop.utils.supabase_upload import upload_file
from trenddrop.utils.env_loader import load_env_once
from utils.db import sb
from trenddrop.reports.product_quality import (
    dedupe_near_duplicates,
    rank_key,
    canonical_title_key,
)
from trenddrop.reports.zip_packs import create_zip_pack, create_master_zip
from trenddrop.reports.master_pack import build_master_top25
from trenddrop.timezones import NYC_TZ
from trenddrop.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

try:  # Ensure Unicode-friendly stdout for rich labels
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ENV_PATH = load_env_once()
ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "out"
ARTIFACTS_PATH = OUT_DIR / "artifacts.json"

# ==========================
# V1: E B A Y   O N L Y
# ==========================
DEFAULT_PROVIDER = "ebay"
# In V1 we only ever run eBay. Amazon / AliExpress are reserved for V2.
DEFAULT_PROVIDERS = ["ebay"]
SUPPORTED_PROVIDERS = ["ebay"]

PDF_TOP_N = 50
MAX_PULL = 150
EST_TZ = ZoneInfo("America/New_York") if ZoneInfo else None
EASTERN = ZoneInfo("US/Eastern") if ZoneInfo else None


@dataclass(frozen=True)
class ReportStoragePaths:
    latest_pdf_key: str
    dated_pdf_key: str
    latest_csv_key: str
    dated_csv_key: str
    latest_zip_key: str
    dated_zip_key: str


def _build_storage_paths(provider: str, run_started_at: datetime) -> ReportStoragePaths:
    date_str = run_started_at.date().isoformat()
    prefix = f"weekly/{provider}"
    return ReportStoragePaths(
        latest_pdf_key=f"{prefix}/latest.pdf",
        dated_pdf_key=f"{prefix}/{date_str}/report.pdf",
        latest_csv_key=f"{prefix}/latest.csv",
        dated_csv_key=f"{prefix}/{date_str}/report.csv",
        latest_zip_key=f"{prefix}/latest.zip",
        dated_zip_key=f"{prefix}/{date_str}/pack.zip",
    )


def _signals_sort_value(row: Dict) -> float:
    try:
        sig = row.get("signals")
        if sig is not None:
            return float(sig)
    except Exception:
        pass
    try:
        fb = row.get("seller_feedback") or row.get("seller_fb") or 0
        return float(fb)
    except Exception:
        return 0.0


CONDITION_WORDS = {
    "good",
    "very good",
    "acceptable",
    "like new",
    "brand new",
    "paperback",
    "hardcover",
    "vg",
    "v.g.",
}


def _normalize_title_for_dedupe(title: str) -> str:
    """Strip condition words / punctuation and keep the core of the title."""
    if not title:
        return ""
    t = title.lower()
    for word in CONDITION_WORDS:
        t = t.replace(word, " ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    parts = t.split(" ")
    return " ".join(parts[:10])


def _score_for_dedupe(product: Dict) -> Tuple[float, float, float]:
    try:
        signals = float(product.get("signals") or 0.0)
    except Exception:
        signals = 0.0
    try:
        seller_fb = float(product.get("seller_feedback") or 0.0)
    except Exception:
        seller_fb = 0.0
    try:
        price = float(product.get("price") or 0.0)
    except Exception:
        price = 0.0
    return (signals, seller_fb, price)


def _dedupe(items: List[Dict], default_provider: Optional[str] = None) -> List[Dict]:
    """
    De-duplicate products by (source, seller_feedback, normalized_title).
    Keep the best listing by signals, then seller_feedback, then price.
    """
    buckets: Dict[Tuple[str, float, str], Dict] = {}
    for product in items:
        source = product.get("source") or default_provider or "unknown"
        try:
            seller_fb = float(product.get("seller_feedback") or 0.0)
        except Exception:
            seller_fb = 0.0
        title_key = _normalize_title_for_dedupe(product.get("title") or "")
        key = (source, seller_fb, title_key)
        current = buckets.get(key)
        if current is None or _score_for_dedupe(product) > _score_for_dedupe(current):
            buckets[key] = product
    return list(buckets.values())


def _get_supabase_admin():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _record_report_run(
    *,
    client,
    provider: str,
    run_started_at: datetime,
    data_window_label: str,
    products_total: int,
    curated_count: int,
    success: bool,
    pdf_url: Optional[str],
    csv_url: Optional[str],
    error_message: Optional[str] = None,
) -> None:
    if client is None:
        print("[reports] skipped report_runs insert (no Supabase client)")
        return
    payload = {
        "provider": provider,
        "run_started_at": run_started_at.astimezone(timezone.utc).isoformat(),
        "data_window_label": data_window_label,
        "products_total": products_total,
        "curated_count": curated_count,
        "success": success,
        "pdf_url": pdf_url,
        "csv_url": csv_url,
        "error_message": error_message,
    }
    try:
        client.table("report_runs").insert(payload).execute()
        print("[reports] recorded run in report_runs")
    except Exception as exc:
        print("[reports] failed to record report_runs row:", exc)


def _ensure_dir(path: str) -> None:
    p = pathlib.Path(path)
    p.mkdir(parents=True, exist_ok=True)


def _copy_file(src: pathlib.Path, dest: pathlib.Path) -> None:
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _get_env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    try:
        v = os.environ.get(name)
        return int(v) if v not in (None, "") else default
    except Exception:
        return default


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        val = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(val)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _format_est(dt: Optional[datetime]) -> str:
    if dt is None:
        return "Unknown"
    target = dt
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    if EST_TZ:
        target = target.astimezone(EST_TZ)
    else:
        target = target.astimezone(timezone.utc)
    return target.strftime("%B %d, %Y %I:%M %p %Z")


def _to_eastern(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize any datetime to US/Eastern for display."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    target_tz = EASTERN or timezone.utc
    return dt.astimezone(target_tz)


def _format_data_window_label(start: Optional[datetime], end: Optional[datetime]) -> str:
    """
    Build the human-readable 'Data window: ...' label in EST.
    """
    if not start or not end:
        return "Unknown"
    start_est = _to_eastern(start)
    end_est = _to_eastern(end)
    if not start_est or not end_est:
        return "Unknown"
    return (
        f"{start_est:%b %d %Y} → "
        f"{end_est:%b %d %Y %I:%M %p %Z}"
    )


def _should_exclude_manual(row: Dict) -> bool:
    source = str(row.get("source") or "").strip().lower()
    if source == "manual":
        return True
    title = str(row.get("title") or "").strip().lower()
    if title.startswith("manual test"):
        return True
    return False


def _latest_timestamp(rows: List[Dict]) -> Optional[datetime]:
    timestamps = [_parse_timestamp(row.get("inserted_at")) for row in rows]
    timestamps = [t for t in timestamps if t is not None]
    if not timestamps:
        return None
    return max(timestamps)


def _get_latest_successful_run() -> Tuple[Optional[str], Optional[datetime]]:
    try:
        client = sb()
    except RuntimeError:
        return None, None
    try:
        res = (
            client.table("runs")
            .select("id, started_at")
            .eq("status", "success")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        row = (res.data or [None])[0]
        if not row:
            return None, None
        return row.get("id"), _parse_timestamp(row.get("started_at"))
    except Exception:
        return None, None


def _normalize_products(rows: List[Dict]) -> List[Dict]:
    normalized: List[Dict] = []
    for row in rows:
        item = dict(row)
        inserted_at = item.get("inserted_at")
        parsed: Optional[datetime] = None
        if hasattr(inserted_at, "isoformat"):
            parsed = inserted_at
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        elif isinstance(inserted_at, str):
            parsed = _parse_timestamp(inserted_at)
        if parsed is not None:
            item["inserted_at"] = parsed
        normalized.append(item)
    return normalized


def _compute_data_window_label_from_products(products: List[Dict]) -> str:
    if not products:
        return "Unknown"
    tz_est = ZoneInfo("America/New_York") if ZoneInfo else timezone.utc
    inserted_times: List[datetime] = []
    for product in products:
        inserted_at = product.get("inserted_at")
        if inserted_at is None:
            continue
        if hasattr(inserted_at, "astimezone"):
            dt_obj = inserted_at
        elif isinstance(inserted_at, str):
            dt_obj = _parse_timestamp(inserted_at)
        else:
            dt_obj = None
        if not dt_obj:
            continue
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=timezone.utc)
        inserted_times.append(dt_obj.astimezone(tz_est))
    if not inserted_times:
        return "Unknown"
    start = min(inserted_times)
    end = max(inserted_times)
    return (
        f"{start.strftime('%b %d %Y')} \N{RIGHTWARDS ARROW} "
        f"{end.strftime('%b %d %Y %I:%M %p EST')}"
    )


def _load_clean_products_from_supabase(
    client,
    provider: str,
    limit: int = MAX_PULL,
) -> Tuple[List[Dict], str]:
    if not client:
        return [], "Unknown"
    print(f"[reports] fetching up to {limit} cleaned products from Supabase for {provider}")
    try:
        query = (
            client.table("v_products_clean")
            .select("*")
            .eq("provider", provider)
            .order("inserted_at", desc=True)
            .limit(max(1, limit))
        )
        res = query.execute()
        rows = [row for row in (res.data or []) if not _should_exclude_manual(row)]
        print(f"[reports] Supabase returned {len(rows)} rows from v_products_clean for {provider}")
        products = _normalize_products(rows)
        data_window_label = _compute_data_window_label_from_products(products)
        return products, data_window_label
    except Exception as exc:
        print(f"[reports] error fetching v_products_clean: {exc}")
        return [], "Unknown"


def _load_top_products_view(limit: int) -> Tuple[List[Dict], str]:
    source_name = "top_products_view"
    try:
        client = sb()
    except RuntimeError:
        return [], source_name
    try:
        res = client.table("v_products_top_by_feedback").select(
            "title, price, currency, image_url, url, seller_feedback, top_rated, source, inserted_at, keyword, created_at",
        ).limit(max(1, limit)).execute()
        rows = []
        for row in (res.data or []):
            if not row:
                continue
            if not row.get("inserted_at") and row.get("created_at"):
                row["inserted_at"] = row.get("created_at")
            rows.append(row)
        return rows, source_name
    except Exception:
        return [], source_name


def _load_products_from_docs(limit: int) -> Tuple[List[Dict], str]:
    source_name = "docs_products_json"
    import json
    try:
        root = pathlib.Path(__file__).resolve().parents[2]
        with open(root / "docs" / "data" / "products.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data.get("products", []) or []
            return items[:limit], source_name
    except Exception:
        return [], source_name


def generate_weekly_report(provider: str) -> None:
    provider = provider.strip().lower() or DEFAULT_PROVIDER
    run_started_at = datetime.now(NYC_TZ)
    data_window_label: str = "Unknown"
    pdf_storage_target: Optional[str] = None
    csv_storage_target: Optional[str] = None
    try:
        supabase_client = _get_supabase_admin()
    except Exception as exc:
        print(f"[reports] warning: cannot init Supabase admin client: {exc}")
        supabase_client = None
    read_client = supabase_client
    if read_client is None:
        try:
            read_client = sb()
        except RuntimeError:
            read_client = None
    try:
        # Mode and options
        mode = (_get_env("REPORT_MODE", "weekly_paid") or "").lower()
        max_items = PDF_TOP_N

        # Human-friendly provider label
        provider_label_map = {
            "ebay": "eBay",
            "amazon": "Amazon",
            "aliexpress": "AliExpress",
        }
        provider_label = provider_label_map.get(provider, provider.title())

        # Default titles depend on mode + provider
        if "weekly" in mode:
            default_title = f"Top 50 Trending {provider_label} Products — Weekly Report"
        elif "daily" in mode:
            default_title = f"Top 10 {provider_label} Movers — Daily Report"
        elif "nightly_multi" in mode:
            default_title = f"Nightly {provider_label} Movers — {provider_label} Marketplace"
        else:
            default_title = f"TrendDrop Report — {provider_label}"

        # Allow override from env if you *really* want a custom title
        title = _get_env("REPORT_TITLE", default_title)

        out_dir = pathlib.Path("out")
        _ensure_dir(str(out_dir))

        is_weekly = "weekly" in (mode or "")
        legacy_pdf_path: Optional[pathlib.Path] = None
        legacy_csv_path: Optional[pathlib.Path] = None
        if is_weekly:
            pdf_outfile = out_dir / f"{provider}_weekly.pdf"
            csv_outfile = out_dir / f"{provider}_weekly.csv"
            legacy_pdf_path = out_dir / "weekly-report.pdf"
            legacy_csv_path = out_dir / "weekly-report.csv"
        else:
            pdf_outfile = out_dir / "daily-report.pdf"
            csv_outfile = out_dir / "daily-report.csv"

        latest_run_id, latest_run_started_at = _get_latest_successful_run()
        if latest_run_started_at:
            print(f"[reports] latest successful run {latest_run_id} @ {latest_run_started_at.isoformat()}")

        # Source products (over-fetch, then dedupe)
        products, data_window_label = _load_clean_products_from_supabase(
            read_client,
            provider,
            limit=MAX_PULL,
        )
        print(f"[reports] loaded {len(products)} products from Supabase (initial)")
        if len(products) < max_items:
            top_view_rows, top_view_source = _load_top_products_view(limit=max_items * 3)
            print(f"[reports] fallback from {top_view_source}: {len(top_view_rows)} products")
            if top_view_rows:
                merged: Dict[str, Dict] = {}
                for row in top_view_rows + products:
                    key = row.get("url") or row.get("title") or ""
                    if not key:
                        continue
                    existing = merged.get(key)
                    if not existing:
                        merged[key] = row
                        continue
                    curr_score = _signals_sort_value(existing)
                    new_score = _signals_sort_value(row)
                    if new_score > curr_score:
                        merged[key] = row
                products = list(merged.values())
        if not products:
            products, docs_source = _load_products_from_docs(limit=max_items * 3)
            print(f"[reports] fallback from {docs_source}: {len(products)} products")

        products = _dedupe(products, default_provider=provider)
        print(f"[reports] after url dedupe: {len(products)} products")
        products = dedupe_near_duplicates(products)
        print(f"[reports] after title+seller dedupe: {len(products)} products")
        products = sorted(products, key=rank_key, reverse=True)
        print(f"[reports] after rank sort: {len(products)} products")
        if not products:
            print("[reports] no products found; exiting")
            _record_report_run(
                client=supabase_client,
                run_started_at=run_started_at,
                provider=provider,
                data_window_label=data_window_label,
                products_total=0,
                curated_count=0,
                success=False,
                error_message="No products found after dedupe",
                pdf_url=None,
                csv_url=None,
            )
            return

        curated_products = products[:PDF_TOP_N]
        print(f"[reports] curated_products for PDF: {len(curated_products)} of {len(products)} total")
        if not curated_products:
            print("[reports] no curated products found; exiting")
            _record_report_run(
                client=supabase_client,
                run_started_at=run_started_at,
                provider=provider,
                data_window_label=data_window_label,
                products_total=len(products),
                curated_count=0,
                success=False,
                error_message="No curated products available",
                pdf_url=None,
                csv_url=None,
            )
            return

        generated_at = datetime.now(timezone.utc)
        if data_window_label == "Unknown":
            window_end = generated_at
            window_start = window_end - timedelta(days=7)
            data_window_label = _format_data_window_label(window_start, window_end)
        generated_label = _format_est(generated_at)
        subtitle_lines = [
            f"Generated: {generated_label}",
            "Source: live marketplace data · PDF shows curated picks · full dataset in CSV",
            f"Data window: {data_window_label}",
        ]

        layout = _get_env("REPORT_LAYOUT", "table")
        if layout == "table":
            import json
            default_cols = [
                {"key": "title", "label": "Title"},
                {"key": "price", "label": "Price"},
                {"key": "currency", "label": "Currency"},
                {"key": "seller_feedback", "label": "Seller FB"},
                {"key": "signals", "label": "Signals"},
            ]
            cols_json = _get_env("REPORT_COLUMNS")
            try:
                columns = json.loads(cols_json) if cols_json else default_cols
            except Exception:
                columns = default_cols
            print(f"[reports] generating table PDF ({len(curated_products)} items) -> {pdf_outfile}")
            generate_table_pdf(curated_products, str(pdf_outfile), columns, title, subtitle_lines=subtitle_lines)
            write_csv(products, str(csv_outfile), columns)
        else:
            print(f"[reports] generating PDF ({len(products)} items) -> {pdf_outfile}")
            generate_weekly_pdf(products, str(pdf_outfile))

        if legacy_pdf_path:
            _copy_file(pdf_outfile, legacy_pdf_path)
        if legacy_csv_path and layout == "table":
            _copy_file(csv_outfile, legacy_csv_path)

        zip_pack_path = create_zip_pack(
            provider,
            pdf_outfile,
            csv_outfile if layout == "table" else None,
        )

        bucket = _get_env("REPORTS_BUCKET", None) or _get_env("SUPABASE_BUCKET", "trenddrop-reports")
        storage_paths = _build_storage_paths(provider, run_started_at)

        provider_artifacts: Dict[str, str] = {}
        if bucket:
            has_url = bool(os.environ.get("SUPABASE_URL"))
            has_key = bool(os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))
            print(f"[reports] supabase configured url={has_url} key={has_key} bucket={bucket}")

            pdf_url_latest = upload_file(bucket, str(pdf_outfile), storage_paths.latest_pdf_key, "application/pdf")
            if pdf_url_latest:
                provider_artifacts["pdf_url"] = pdf_url_latest
                print(f"[reports] uploaded latest PDF: {pdf_url_latest}")
            pdf_url_dated = upload_file(bucket, str(pdf_outfile), storage_paths.dated_pdf_key, "application/pdf")
            if pdf_url_dated:
                provider_artifacts["pdf_url_dated"] = pdf_url_dated
                print(f"[reports] uploaded dated PDF: {pdf_url_dated}")

            if layout == "table":
                csv_url_latest = upload_file(bucket, str(csv_outfile), storage_paths.latest_csv_key, "text/csv")
                if csv_url_latest:
                    provider_artifacts["csv_url"] = csv_url_latest
                    print(f"[reports] uploaded latest CSV: {csv_url_latest}")
                csv_url_dated = upload_file(bucket, str(csv_outfile), storage_paths.dated_csv_key, "text/csv")
                if csv_url_dated:
                    provider_artifacts["csv_url_dated"] = csv_url_dated
                    print(f"[reports] uploaded dated CSV: {csv_url_dated}")

            zip_url_latest = upload_file(bucket, str(zip_pack_path), storage_paths.latest_zip_key, "application/zip")
            if zip_url_latest:
                provider_artifacts["zip_url"] = zip_url_latest
                print(f"[reports] uploaded latest ZIP: {zip_url_latest}")
            zip_url_dated = upload_file(bucket, str(zip_pack_path), storage_paths.dated_zip_key, "application/zip")
            if zip_url_dated:
                provider_artifacts["zip_url_dated"] = zip_url_dated
                print(f"[reports] uploaded dated ZIP: {zip_url_dated}")

        try:
            import json
            manifest = {}
            if ARTIFACTS_PATH.exists():
                try:
                    with ARTIFACTS_PATH.open("r", encoding="utf-8") as f:
                        manifest = json.load(f)
                except Exception:
                    manifest = {}
            manifest[provider] = {
                "pdf_url": provider_artifacts.get("pdf_url"),
                "csv_url": provider_artifacts.get("csv_url"),
                "pdf_url_dated": provider_artifacts.get("pdf_url_dated"),
                "csv_url_dated": provider_artifacts.get("csv_url_dated"),
                "zip_url": provider_artifacts.get("zip_url"),
                "zip_url_dated": provider_artifacts.get("zip_url_dated"),
                "data_window_label": data_window_label,
                "run_started_at": run_started_at.isoformat(),
            }
            with ARTIFACTS_PATH.open("w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            print("[reports] wrote provider artifacts to artifacts.json")
        except Exception as exc:
            print(f"[reports] warning: could not write artifacts.json: {exc}")

        pdf_storage_target = (
            f"{bucket}/{storage_paths.latest_pdf_key}" if bucket else str(pdf_outfile)
        )
        csv_storage_target = (
            f"{bucket}/{storage_paths.latest_csv_key}" if bucket else str(csv_outfile)
        )
        zip_storage_target = (
            f"{bucket}/{storage_paths.latest_zip_key}" if bucket else str(zip_pack_path)
        )
        if layout != "table":
            csv_storage_target = "n/a"
        print(
            f"Generated weekly report: {len(curated_products)} rows (from {data_window_label}), "
            f"uploaded to {pdf_storage_target}, {csv_storage_target}, and {zip_storage_target}"
        )
        _record_report_run(
            client=supabase_client,
            run_started_at=run_started_at,
            provider=provider,
            data_window_label=data_window_label,
            products_total=len(products),
            curated_count=len(curated_products),
            success=True,
            error_message=None,
            pdf_url=provider_artifacts.get("pdf_url") or str(pdf_storage_target),
            csv_url=provider_artifacts.get("csv_url") or str(csv_storage_target),
        )
        return {
            "provider": provider,
            "pdf_path": str(pdf_outfile),
            "csv_path": str(csv_outfile) if layout == "table" else None,
            "zip_path": str(zip_pack_path),
            "pdf_url": provider_artifacts.get("pdf_url"),
            "csv_url": provider_artifacts.get("csv_url"),
            "zip_url": provider_artifacts.get("zip_url"),
            "run_started_at": run_started_at,
            "bucket": bucket,
            "curated_count": len(curated_products),
            "products_total": len(products),
        }
    except Exception as exc:
        _record_report_run(
            client=supabase_client,
            run_started_at=run_started_at,
            provider=provider,
            data_window_label=data_window_label,
            products_total=0,
            curated_count=0,
            success=False,
            error_message=str(exc),
            pdf_url=None,
            csv_url=None,
        )
        raise


def _run_master_pack(run_started_at: Optional[datetime] = None, bucket_hint: Optional[str] = None) -> None:
    """
    V1 NOTE: master pack is disabled (we only run eBay).
    This stub is left here for easy re-enable in V2.
    """
    print("[reports-master] master pack generation is disabled in V1 (eBay-only).")
    return


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider",
        type=str,
        help=f"Provider to generate report for (supported: {SUPPORTED_PROVIDERS})",
    )
    parser.add_argument(
        "--master",
        action="store_true",
        help="(V1 disabled) Generate only the Master Top 25 pack.",
    )
    args = parser.parse_args(argv)

    provider_arg = args.provider.lower().strip() if args.provider else None

    # V1: master pack is disabled
    if args.master:
        print("[reports] --master requested but master pack is disabled in V1 (eBay-only).")
        return

    # Decide which providers to run (V1: always eBay)
    if provider_arg:
        if provider_arg != "ebay":
            raise SystemExit("In V1, only provider 'ebay' is enabled.")
        providers_to_run = ["ebay"]
    else:
        # Ignore PRODUCT_SOURCE / get_provider_filter in V1.
        providers_to_run = ["ebay"]

    last_result: Optional[Dict[str, object]] = None
    for provider in providers_to_run:
        if provider not in SUPPORTED_PROVIDERS:
            raise SystemExit(f"Unsupported provider: {provider}")
        print(f"[reports] === provider={provider} start ===")
        result = generate_weekly_report(provider=provider)
        curated_count = 0
        if isinstance(result, dict):
            try:
                curated_count = int(result.get("curated_count") or 0)
            except Exception:
                curated_count = 0
        print(f"[reports] === provider={provider} done ({curated_count} rows) ===")
        last_result = result


if __name__ == "__main__":
    main()
