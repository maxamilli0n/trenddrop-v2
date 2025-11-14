import os
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once

ENV_PATH = load_env_once()
from datetime import datetime


def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name, default)
    if v is None:
        return None
    v = v.strip()
    return v or default


# Mode
MODE = (env("MODE", "live") or "live").lower()
IS_LIVE = MODE == "live"

def require(name: str) -> str:
    v = env(name)
    if v:
        return v
    msg = f"[config.py] missing required env {name}"
    if IS_LIVE:
        # Production: warn but do not crash long-running jobs.
        print(msg)
        return ""
    raise RuntimeError(msg)

# Supabase
SUPABASE_URL = env("SUPABASE_URL") or ("" if IS_LIVE else require("SUPABASE_URL"))
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY") or ("" if IS_LIVE else require("SUPABASE_SERVICE_ROLE_KEY"))
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY")
SUPABASE_BUCKET = env("SUPABASE_BUCKET")
REPORTS_BUCKET = env("REPORTS_BUCKET") or SUPABASE_BUCKET or "trenddrop-reports"

# Stripe
STRIPE_SECRET_KEY_LIVE = env("STRIPE_SECRET_KEY_LIVE")
STRIPE_SECRET_KEY_TEST = env("STRIPE_SECRET_KEY_TEST")
STRIPE_SECRET_KEY = (STRIPE_SECRET_KEY_LIVE if IS_LIVE else STRIPE_SECRET_KEY_TEST) or env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET_LIVE = env("STRIPE_WEBHOOK_SECRET_LIVE")
STRIPE_WEBHOOK_SECRET_TEST = env("STRIPE_WEBHOOK_SECRET_TEST")
STRIPE_WEBHOOK_SECRET = (STRIPE_WEBHOOK_SECRET_LIVE if IS_LIVE else STRIPE_WEBHOOK_SECRET_TEST) or env("STRIPE_WEBHOOK_SECRET")

# Brevo
BREVO_API_KEY = env("BREVO_API_KEY")
EMAIL_FROM = env("EMAIL_FROM")

# Telegram
BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
CHAT_ID = env("TELEGRAM_CHAT_ID")                # DM / test chat
CHANNEL_ID = env("TELEGRAM_CHANNEL_ID")          # @TrendDropStudio or numeric
COMMUNITY_CHAT_ID = env("TELEGRAM_COMMUNITY_CHAT_ID")
ALERT_CHAT_ID = env("TELEGRAM_ALERT_CHAT_ID") or CHAT_ID
INVITE_URL = env("TELEGRAM_INVITE_URL")

def tg_targets() -> list[str]:
    targets: list[str] = []
    if CHAT_ID:
        targets.append(CHAT_ID)
    if CHANNEL_ID:
        targets.append(CHANNEL_ID)
    # de-dupe just in case
    return list(dict.fromkeys(targets))

# Misc / storefront
GUMROAD_CTA_URL = env("GUMROAD_CTA_URL", "")
CLICK_REDIRECT_BASE = env("CLICK_REDIRECT_BASE", "")

def gumroad_cta_url() -> str:
    raw = GUMROAD_CTA_URL or ""
    if not raw:
        return ""
    # allow {date} token
    return raw.replace("{date}", datetime.utcnow().strftime("%Y-%m-%d"))

