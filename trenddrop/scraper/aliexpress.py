from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from utils.db import upsert_products

from .common import USER_AGENT, dedupe_by_url, now_iso, parse_price

ALI_BASE = "https://www.aliexpress.com"
ALI_SEARCH_URL = "https://www.aliexpress.com/wholesale"
SEED_QUERIES = [
    "smartwatch",
    "wireless earbuds",
    "usb hub",
    "led strip lights",
    "pet grooming",
]
RUNPARAMS_RE = re.compile(r"window\.runParams\s*=\s*({.*?});", re.S)
PUNISH_TOKEN_RE = re.compile(r'//www\.aliexpress\.com/[^\s"\']+/punish\?x5secdata=([^"\'\\]+)')
ALI_HEADERS = {
    "user-agent": USER_AGENT,
    "accept-language": "en-US,en;q=0.9",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}
_AE_CREDS_LOGGED = False
_HTML_CAPTURED = False


def _env_bool(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _log_credentials_status() -> None:
    global _AE_CREDS_LOGGED
    if _AE_CREDS_LOGGED:
        return
    enabled = _env_bool("AE_ENABLED")
    has_key = bool(os.getenv("AE_APP_KEY"))
    has_secret = bool(os.getenv("AE_APP_SECRET"))
    if enabled and has_key and has_secret:
        print("[scraper-aliexpress] AE credentials detected (key+secret present).")
    elif enabled:
        missing = []
        if not has_key:
            missing.append("AE_APP_KEY")
        if not has_secret:
            missing.append("AE_APP_SECRET")
        missing_text = ", ".join(missing) or "credentials"
        print(f"[scraper-aliexpress] WARN AE_ENABLED but missing {missing_text}.")
    else:
        print("[scraper-aliexpress] AE_ENABLED not set/false; using storefront HTML scrape.")
    _AE_CREDS_LOGGED = True


def _safe_snippet(text: str, limit: int = 500) -> str:
    snippet = re.sub(r"\s+", " ", text or "").strip()
    if len(snippet) > limit:
        return snippet[:limit] + "..."
    return snippet


def _collect_error_fields(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    fields: Dict[str, Any] = {}
    candidates = ("error", "error_code", "errorCode", "resp_code", "respCode", "resp_msg", "respMsg")
    containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        for key in candidates:
            value = container.get(key)
            if value is None or value in ("", 0, "0"):
                continue
            fields[key] = value
    return fields


def _log_empty_payload(keyword: str, payload: Dict[str, Any] | None, product_len: int) -> Dict[str, Any]:
    top_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    print(
        "[scraper-aliexpress] WARN query '%s' runParams produced 0 rows "
        "(product_array_len=%s, top_level_keys=%s)" % (keyword, product_len, top_keys)
    )
    error_fields = _collect_error_fields(payload)
    if error_fields:
        print(f"[aliexpress-error] query '{keyword}' payload errors={error_fields}")
    return error_fields


def _extract_runparams_payload(html: str) -> Dict[str, Any] | None:
    match = RUNPARAMS_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        print(f"[scraper-aliexpress] WARN failed to decode runParams JSON: {exc}")
        return None


def _normalize_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin(ALI_BASE, url)
    return url


def _coerce_int(value) -> int:
    try:
        return int(float(value))
    except Exception:
        text = re.sub(r"[^\d]", "", str(value or ""))
        try:
            return int(text)
        except Exception:
            return 0


def _parse_runparams(payload: Dict[str, Any] | None, keyword: str, limit: int) -> Tuple[List[dict], int]:
    if not isinstance(payload, dict):
        return [], 0
    content = payload.get("mods", {}).get("itemList", {}).get("content", []) or []
    items = content if isinstance(content, list) else []
    rows: List[dict] = []
    for item in items:
        title = item.get("title") or ""
        if not title:
            continue
        url = _normalize_url(item.get("productDetailUrl") or item.get("productUrl") or "")
        price = parse_price(item.get("price"))
        image_url = _normalize_url(item.get("imageUrl") or "")
        seller_feedback = _coerce_int(item.get("sellerPositiveRate") or item.get("productPositiveRate"))
        signals = float(item.get("itemEvalScore") or 0.0)
        currency = "USD"
        if isinstance(item.get("price"), str) and "руб" in item.get("price"):
            currency = "RUB"
        rows.append(
            {
                "title": title[:200],
                "url": url,
                "image_url": image_url,
                "price": price,
                "currency": currency,
                "seller_feedback": seller_feedback,
                "signals": signals,
                "top_rated": bool(item.get("superSupplier") or item.get("isPreferred")),
                "provider": "aliexpress",
                "source": "aliexpress",
                "keyword": keyword,
                "inserted_at": now_iso(),
            }
        )
        if len(rows) >= limit:
            break
    return rows, len(items)


def _maybe_save_html(keyword: str, html: str) -> None:
    global _HTML_CAPTURED
    if _HTML_CAPTURED:
        return
    safe_keyword = re.sub(r"[^a-z0-9_-]+", "_", keyword.lower()).strip("_") or "query"
    path = Path("tmp") / f"aliexpress_{safe_keyword}.html"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        print(f"[scraper-aliexpress] saved HTML for '{keyword}' to {path.as_posix()}")
        _HTML_CAPTURED = True
    except Exception as exc:
        print(f"[scraper-aliexpress] WARN failed to save HTML for '{keyword}': {exc}")


def _request_aliexpress_html(keyword: str) -> str:
    params = {"SearchText": keyword, "catId": 0}
    method = "GET"
    prepared = requests.Request(method, ALI_SEARCH_URL, params=params, headers=ALI_HEADERS).prepare()
    print(f"[scraper-aliexpress] query '{keyword}' -> {method} {prepared.url}")
    try:
        with requests.Session() as session:
            resp = session.send(prepared, timeout=30)
            html = resp.text
            if _contains_punish(html):
                html = _follow_punish_flow(session, html, keyword)
                print(f"[scraper-aliexpress] query '{keyword}' punish flow resolved")
    except requests.RequestException as exc:
        print(f"[aliexpress-error] query '{keyword}' request failed: {exc}")
        raise RuntimeError(f"[aliexpress] request failed for query '{keyword}'") from exc
    print(f"[scraper-aliexpress] query '{keyword}' status={resp.status_code}")
    if resp.status_code != 200:
        snippet = _safe_snippet(resp.text)
        print(f"[aliexpress-error] query '{keyword}' status={resp.status_code} body='{snippet}'")
        raise RuntimeError(f"[aliexpress] API error status {resp.status_code} for query '{keyword}'")
    time.sleep(0.5)
    return html


def _contains_punish(html: str) -> bool:
    if not html:
        return False
    return "x5secdata" in html and "punish" in html


def _follow_punish_flow(session: requests.Session, html: str, keyword: str) -> str:
    match = PUNISH_TOKEN_RE.search(html or "")
    if not match:
        print(f"[scraper-aliexpress] WARN x5 punish detected but token missing for '{keyword}'")
        return html
    token = match.group(1)
    punish_url = f"{ALI_BASE}/wholesale/_____tmd_____/punish?x5secdata={token}"
    print(f"[scraper-aliexpress] query '{keyword}' solving x5 punish via {punish_url}")
    pun_resp = session.get(punish_url, headers=ALI_HEADERS, timeout=30)
    if pun_resp.status_code != 200:
        snippet = _safe_snippet(pun_resp.text)
        print(f"[aliexpress-error] punish flow failed status={pun_resp.status_code} snippet='{snippet}'")
        raise RuntimeError(f"[aliexpress] punish flow failed for query '{keyword}'")
    return pun_resp.text or html


def _parse_cards(html: str, keyword: str, limit: int) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows: List[dict] = []
    for card in soup.select("a") or []:
        href = card.get("href")
        title = card.get("title") or card.get_text().strip()
        if not href or not title:
            continue
        price_tag = card.find(class_=re.compile("price"))
        price = parse_price(price_tag.get_text() if price_tag else None)
        image_tag = card.find("img")
        image_url = image_tag.get("src") if image_tag else ""
        rows.append(
            {
                "title": title[:200],
                "url": _normalize_url(href),
                "image_url": _normalize_url(image_url),
                "price": price,
                "currency": "USD",
                "seller_feedback": 0,
                "signals": 0.0,
                "top_rated": False,
                "provider": "aliexpress",
                "source": "aliexpress",
                "keyword": keyword,
                "inserted_at": now_iso(),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def fetch_aliexpress_products(keyword: str, per_page: int) -> List[dict]:
    html = _request_aliexpress_html(keyword)
    _maybe_save_html(keyword, html)
    payload = _extract_runparams_payload(html)
    rows, product_array_len = _parse_runparams(payload, keyword, per_page)
    if not rows:
        error_fields = _log_empty_payload(keyword, payload, product_array_len)
        if error_fields:
            raise RuntimeError(f"[aliexpress] API error signaled for query '{keyword}'")
        rows = _parse_cards(html, keyword, per_page)
        if rows:
            print(f"[scraper-aliexpress] query '{keyword}' fallback parser yielded {len(rows)} rows")
    return rows


def scrape_aliexpress(queries: List[str], per_page: int) -> List[dict]:
    _log_credentials_status()
    collected: List[dict] = []
    for keyword in queries:
        print(f"[scraper-aliexpress] starting query '{keyword}' (per_page={per_page})")
        try:
            rows = fetch_aliexpress_products(keyword, per_page=per_page)
        except Exception as exc:
            print(f"[scraper-aliexpress] WARN query '{keyword}' failed: {exc}")
            continue
        print(f"[scraper-aliexpress] fetched {len(rows)} raw products for query '{keyword}'")
        collected.extend(rows)
    unique = dedupe_by_url(collected)
    for row in unique:
        row["provider"] = "aliexpress"
        row["source"] = row.get("source") or "aliexpress"
    print(f"[scraper-aliexpress] normalized {len(unique)} products after dedupe")
    print(f"[scraper-aliexpress] upserting {len(unique)} unique products into provider=aliexpress")
    if unique:
        upsert_products(unique)
    return unique


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Scrape AliExpress listings.")
    parser.add_argument(
        "--queries",
        nargs="*",
        default=SEED_QUERIES,
        help="Keyword queries to fetch.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=20,
        help="Listings per query.",
    )
    args = parser.parse_args(argv)
    scrape_aliexpress(args.queries, per_page=args.per_page)


if __name__ == "__main__":
    main()


