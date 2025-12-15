from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import os

def affiliate_wrap(url: str, custom_id: str = "trenddrop") -> str:
    """
    Add EPN tracking params directly to the item URL to avoid rover 1x1.
    Uses env var EPN_CAMPAIGN_ID (set via GitHub Secrets / runtime env).
    """
    campid = (os.environ.get("EPN_CAMPAIGN_ID") or "").strip()
    if not campid:
        return url

    u = urlparse(url)
    query = dict(parse_qsl(u.query, keep_blank_values=True))

    query.update({
        "mkcid": "1",
        "mkrid": "711-53200-19255-0",
        "mkevt": "1",
        "campid": campid,
        "customid": (custom_id or "trenddrop")[:64],
        "toolid": "10001",
    })

    new_query = urlencode(query, doseq=True)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
