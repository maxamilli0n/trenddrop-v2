"""
Master Top 25 pack builder.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

import pandas as pd

from .pdf_table import make_pdf

OUT_DIR = Path("out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
PROVIDERS = ["ebay", "amazon", "aliexpress"]


def load_csv(provider: str) -> pd.DataFrame:
    path = OUT_DIR / f"{provider}_weekly.csv"
    if not path.exists():
        legacy = OUT_DIR / f"weekly-{provider}.csv"
        path = legacy if legacy.exists() else path
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["provider"] = provider
    if "signals" not in df.columns and "Signals" in df.columns:
        df["signals"] = df["Signals"]
    if "seller_feedback" not in df.columns and "Seller FB" in df.columns:
        df["seller_feedback"] = df["Seller FB"]
    return df


def build_master_top25():
    frames: List[pd.DataFrame] = [df for df in (load_csv(p) for p in PROVIDERS) if not df.empty]
    if not frames:
        raise RuntimeError("No provider data available")
    df = pd.concat(frames, ignore_index=True)

    df["signals"] = pd.to_numeric(df["signals"], errors="coerce").fillna(0)
    df["seller_feedback"] = pd.to_numeric(df["seller_feedback"], errors="coerce").fillna(0)

    # Ranking formula
    df["rank"] = (df["signals"] * 100000) + df["seller_feedback"]

    top25 = df.sort_values("rank", ascending=False).head(25)

    csv_out = Path("out/master_top25.csv")
    top25.to_csv(csv_out, index=False)

    # Build a PDF version
    pdf_out = Path("out/master_top25.pdf")
    make_pdf(top25, title="TrendDrop Master Top 25 — Cross Marketplace", out_path=pdf_out)

    return csv_out, pdf_out


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build Master Top 25 pack.")
    parser.parse_args(argv)
    build_master_top25()


if __name__ == "__main__":
    main()

