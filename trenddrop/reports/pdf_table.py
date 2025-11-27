"""
Helper wrapper to generate table PDFs from pandas DataFrames.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from utils.report import generate_table_pdf


def _columns_from_df(df: pd.DataFrame) -> list[dict]:
    cols: list[dict] = []
    for col in df.columns:
        label = str(col).strip() or str(col)
        cols.append({"key": col, "label": label})
    return cols


def make_pdf(df: pd.DataFrame, title: str, out_path: Path) -> None:
    """
    Render a DataFrame as a PDF table, reusing the weekly report generator.
    """
    if df.empty:
        raise ValueError("Cannot render PDF for empty DataFrame.")
    columns = _columns_from_df(df)
    rows = df.to_dict(orient="records")
    generate_table_pdf(rows, str(out_path), columns, title=title)


