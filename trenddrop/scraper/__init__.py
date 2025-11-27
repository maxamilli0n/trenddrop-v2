"""
TrendDrop scraper package.

Usage examples:

    python -m trenddrop.scraper.ebay
    python -m trenddrop.scraper.amazon
    python -m trenddrop.scraper.aliexpress

Each module fetches marketplace data for a handful of seed queries and
pushes cleaned rows into Supabase via utils.db.upsert_products.
"""


