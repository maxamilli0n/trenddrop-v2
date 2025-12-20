import os
from datetime import datetime
from pathlib import Path
from trenddrop.utils.env_loader import load_env_once

ENV_PATH = load_env_once()


def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name, default)
    if v is None:
        return None
    v = v.strip()
    return v if v != "" else default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, None)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, None)
    if raw is None:
        return default
    raw = str(raw).strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "y", "on")


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

# Telegram (backward compatible)
BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")

# Legacy single target (DM/test)
CHAT_ID = env("TELEGRAM_CHAT_ID")

# Legacy single channel target (public)
CHANNEL_ID = env("TELEGRAM_CHANNEL_ID")

# New: explicit routing IDs
ADMIN_CHAT_ID = env("TELEGRAM_ADMIN_CHAT_ID") or CHAT_ID
PUBLIC_CHANNEL_ID = env("TELEGRAM_PUBLIC_CHANNEL_ID") or CHANNEL_ID
PAID_CHANNEL_ID = env("TELEGRAM_PAID_CHANNEL_ID") or env("TELEGRAM_PREMIUM_CHANNEL_ID")  # alias support

COMMUNITY_CHAT_ID = env("TELEGRAM_COMMUNITY_CHAT_ID")
ALERT_CHAT_ID = env("TELEGRAM_ALERT_CHAT_ID") or ADMIN_CHAT_ID
INVITE_URL = env("TELEGRAM_INVITE_URL")

# CTA / misc
GUMROAD_CTA_URL = env("GUMROAD_CTA_URL", "")
CLICK_REDIRECT_BASE = env("CLICK_REDIRECT_BASE", "")

# Telegram tuning (safe even if blank in Actions)
TELEGRAM_DEDUPE_HOURS = env_int("TELEGRAM_DEDUPE_HOURS", 48)
TELEGRAM_MAX_PER_KEYWORD = env_int("TELEGRAM_MAX_PER_KEYWORD", 2)
TELEGRAM_MIN_UNIQUE_KEYWORDS = env_int("TELEGRAM_MIN_UNIQUE_KEYWORDS", 4)
TELEGRAM_MAX_PER_SELLER = env_int("TELEGRAM_MAX_PER_SELLER", 1)

TELEGRAM_CTA_EVERY_N_POSTS = env_int("TELEGRAM_CTA_EVERY_N_POSTS", 6)
TELEGRAM_CTA_COOLDOWN_MINUTES = env_int("TELEGRAM_CTA_COOLDOWN_MINUTES", 180)
TELEGRAM_PIN_CTA = env_bool("TELEGRAM_PIN_CTA", False)


def gumroad_cta_url() -> str:
    raw = GUMROAD_CTA_URL or ""
    if not raw:
        return ""
    return raw.replace("{date}", datetime.utcnow().strftime("%Y-%m-%d"))


def tg_targets(scope: str = "broadcast") -> list[str]:
    """
    scope:
      - 'admin'     -> ADMIN_CHAT_ID only
      - 'public'    -> PUBLIC channel only
      - 'paid'      -> PAID channel only
      - 'broadcast' -> public + paid (if configured)
      - 'legacy'    -> (CHAT_ID + CHANNEL_ID) old behavior
    """
    targets: list[str] = []

    if scope == "admin":
        if ADMIN_CHAT_ID:
            targets.append(ADMIN_CHAT_ID)

    elif scope == "public":
        if PUBLIC_CHANNEL_ID:
            targets.append(PUBLIC_CHANNEL_ID)

    elif scope == "paid":
        if PAID_CHANNEL_ID:
            targets.append(PAID_CHANNEL_ID)

    elif scope == "broadcast":
        if PUBLIC_CHANNEL_ID:
            targets.append(PUBLIC_CHANNEL_ID)
        if PAID_CHANNEL_ID:
            targets.append(PAID_CHANNEL_ID)

    elif scope == "legacy":
        if CHAT_ID:
            targets.append(CHAT_ID)
        if CHANNEL_ID:
            targets.append(CHANNEL_ID)

    # de-dupe
    return list(dict.fromkeys([t for t in targets if t]))
