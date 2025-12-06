"""
Generate a FREE 5-item sample report.
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd
from datetime import datetime, timedelta

from utils.db import get_client
from trenddrop.reports.pdf_table import make_pdf

OUT_DIR = Path("out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def build_free_sample():
    supabase = get_client()

    # Get top 5 most recent high-signal eBay products
    since = (datetime.utcnow() - timedelta(days=2)).isoformat()
    query = (
        supabase.table("v_products_consolidated")
        .select("*")
        .eq("provider", "ebay")
        .gte("inserted_at", since)
        .order("signals", desc=True)
        .limit(5)
    )
    res = query.execute()
    rows = res.data or []

    df = pd.DataFrame(rows)
    csv_path = OUT_DIR / "free_sample.csv"
    pdf_path = OUT_DIR / "free_sample.pdf"

    df.to_csv(csv_path, index=False)
    make_pdf(df, title="TrendDrop • FREE 5-Item Sample", out_path=pdf_path)

    return csv_path, pdf_path

if __name__ == "__main__":
    build_free_sample()
