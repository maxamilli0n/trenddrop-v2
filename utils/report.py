import os, time, requests, csv
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once

# Ensure root .env is loaded
ENV_PATH = load_env_once()
from typing import List, Dict, Optional

# ------------------------------------------------------------
# 🔥 NEW MULTI-PROVIDER SUPPORT
# ------------------------------------------------------------
PRODUCT_SOURCE = os.getenv("PRODUCT_SOURCE", "ebay").lower()

def get_provider_filter() -> Optional[List[str]]:
    """
    Returns a list of providers to filter by (['ebay'], ['amazon'], ['ebay','amazon']),
    or None if PRODUCT_SOURCE=multi/all/* which means use ALL providers.
    """
    raw = PRODUCT_SOURCE.strip()
    if not raw:
        return ["ebay"]

    # ANY of these means "include all providers"
    if raw in {"multi", "all", "*"}:
        return None

    # Allow comma separated list like: "ebay,amazon"
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parts or ["ebay"]
# ------------------------------------------------------------
# END PROVIDER FILTER ADDITION
# ------------------------------------------------------------


try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
except Exception:
    letter = None  # type: ignore
    ImageReader = None  # type: ignore
    canvas = None  # type: ignore
    colors = None  # type: ignore

from utils.db import sb


def _safe_text(val: Optional[str]) -> str:
    return (val or "").strip()

BULLET_PREFIXES = ("■", "▪", "•", "●", "◼", "◾", "▫", "◻", "●", "●")

def _strip_leading_bullet(text: str) -> str:
    if not text:
        return text
    for bullet in BULLET_PREFIXES:
        if text.startswith(bullet):
            return text[len(bullet):].lstrip()
    return text

# … — ALL your existing code continues unchanged below —
# I am not touching anything else because it all works.
# ------------------------------------------------------------

# (Everything below remains EXACTLY as in your file)
# seller_fb_to_stars()
# _fetch_image_bytes()
# generate_weekly_pdf()
# _value_for_column()
# generate_table_pdf()
# write_csv()
# upload_pdf_to_supabase()
# upload_csv_to_supabase()
# (Your whole file continues exactly as-is)
