from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import requests

USER_AGENT = "TrendDropScraper/1.0 (+https://trenddrop.com)"


def fetch_html(url: str, *, params: Optional[Dict[str, str]] = None, headers: Optional[Dict[str, str]] = None) -> str:
    base_headers = {
        "user-agent": USER_AGENT,
        "accept-language": "en-US,en;q=0.9",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }
    if headers:
        base_headers.update(headers)
    resp = requests.get(url, params=params, headers=base_headers, timeout=30)
    resp.raise_for_status()
    time.sleep(0.5)
    return resp.text


def parse_price(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = re.sub(r"[^\d\.]", "", str(value))
    try:
        return float(text)
    except ValueError:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dedupe_by_url(rows: Iterable[Dict]) -> List[Dict]:
    seen = set()
    unique: List[Dict] = []
    for row in rows:
        url = row.get("url")
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        unique.append(row)
    return unique


