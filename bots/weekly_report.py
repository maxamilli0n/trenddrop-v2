import os, time, json, pathlib
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once

# Load root .env only
ENV_PATH = load_env_once()

from utils.report import generate_weekly_pdf, upload_pdf_to_supabase
from utils.db import sb


def _docs_products_path() -> str:
    root = pathlib.Path(__file__).resolve().parents[1]
    return str(root / "docs" / "data" / "products.json")


def _load_top_products(limit: int = 50):
    try:
        with open(_docs_products_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data.get("products", []) or []
            # products.json already sorted by score when written; otherwise keep order
            return items[:limit]
    except Exception:
        return []


def _load_top_products_last7_from_supabase(limit: int = 50):
    """Prefer ranking by clicks in the last 7 days if Supabase is configured."""
    client = sb()
    if not client:
        return None
    try:
        since = int(time.time()) - 7 * 24 * 3600
        # Get recent products for context
        prod_resp = client.table("products").select("title, price, currency, image_url, url").limit(200).execute()
        products = prod_resp.data or []
        # Count clicks grouped by url in last 7 days
        clicks = client.rpc("exec",
            {"sql": "select product_url as url, count(*) as c from clicks where clicked_at >= now() - interval '7 days' group by product_url order by c desc limit 50"}
        ).execute()
        ranked = clicks.data or []
        by_url = {p.get("url"): p for p in products}
        out = []
        for row in ranked:
            p = by_url.get(row.get("url"))
            if p:
                out.append(p)
            if len(out) >= limit:
                break
        return out if out else None
    except Exception:
        return None


def main(limit: int = 50):
    out_dir = pathlib.Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d", time.gmtime())
    local_path = str(out_dir / f"trenddrop-weekly-{ts}.pdf")

    products = _load_top_products_last7_from_supabase(limit=limit) or _load_top_products(limit=limit)
    if not products:
        print("[weekly] no products to include; exiting")
        return

    print(f"[weekly] generating PDF with {len(products)} products → {local_path}")
    generate_weekly_pdf(products, local_path)

    # Upload to Supabase Storage if configured
    storage_key = f"weekly/trenddrop-weekly-{ts}.pdf"
    url = upload_pdf_to_supabase(local_path, storage_key)
    if url:
        print(f"[weekly] uploaded to: {url}")
    else:
        print("[weekly] upload skipped or failed (no Supabase client)")


if __name__ == "__main__":
    main()


