# trenddrop/config.py
import os
from pathlib import Path
from datetime import datetime
from trenddrop.utils.env_loader import load_env_once

ENV_PATH = load_env_once()


def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name, default)
    if v is None:
        return None
    v = v.strip()
    # treat empty string as "missing"
    return v if v != "" else default


def env_int(name: str, default: int) -> int:
    """
    Safe int env reader:
    - missing -> default
    - empty string -> default
    - invalid -> default
    """
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    s = str(raw).strip()
    if s == "":
        return int(default)
    try:
        return int(s)
    except Exception:
        return int(default)


def env_bool(name: str, default: bool = False) -> bool:
    """
    Safe bool env reader.
    True values: 1, true, yes, y, on
    False values: 0, false, no, n, off, "" (falls back to default if missing)
    """
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    s = str(raw).strip().lower()
    if s == "":
        return bool(default)
    return s in ("1", "true", "yes", "y", "on")


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
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY") or (
    "" if IS_LIVE else require("SUPABASE_SERVICE_ROLE_KEY")
)
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY")
SUPABASE_BUCKET = env("SUPABASE_BUCKET")
REPORTS_BUCKET = env("REPORTS_BUCKET") or SUPABASE_BUCKET or "trenddrop-reports"

# Stripe
STRIPE_SECRET_KEY_LIVE = env("STRIPE_SECRET_KEY_LIVE")
STRIPE_SECRET_KEY_TEST = env("STRIPE_SECRET_KEY_TEST")
STRIPE_SECRET_KEY = (STRIPE_SECRET_KEY_LIVE if IS_LIVE else STRIPE_SECRET_KEY_TEST) or env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET_LIVE = env("STRIPE_WEBHOOK_SECRET_LIVE")
STRIPE_WEBHOOK_SECRET_TEST = env("STRIPE_WEBHOOK_SECRET_TEST")
STRIPE_WEBHOOK_SECRET = (STRIPE_WEBHOOK_SECRET_LIVE if IS_LIVE else STRIPE_WEBHOOK_SECRET_TEST) or env(
    "STRIPE_WEBHOOK_SECRET"
)

# Brevo
BREVO_API_KEY = env("BREVO_API_KEY")
EMAIL_FROM = env("EMAIL_FROM")

# Telegram base
BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")

# Legacy variables (kept for backwards compatibility)
CHAT_ID = env("TELEGRAM_CHAT_ID")  # DM / legacy fallback
CHANNEL_ID = env("TELEGRAM_CHANNEL_ID")  # legacy channel id (single)

# New routing variables
PUBLIC_CHANNEL_ID = env("TELEGRAM_PUBLIC_CHANNEL_ID") or CHANNEL_ID
PAID_CHANNEL_ID = env("TELEGRAM_PAID_CHANNEL_ID")
ADMIN_CHAT_ID = env("TELEGRAM_ADMIN_CHAT_ID") or CHAT_ID

COMMUNITY_CHAT_ID = env("TELEGRAM_COMMUNITY_CHAT_ID")
ALERT_CHAT_ID = env("TELEGRAM_ALERT_CHAT_ID") or ADMIN_CHAT_ID or CHAT_ID
INVITE_URL = env("TELEGRAM_INVITE_URL")

# Default posting behavior (used by CLI)
DEFAULT_POST_SCOPE = (env("TELEGRAM_POST_SCOPE", "broadcast") or "broadcast").lower()

# Telegram tuning knobs (these fix your ImportError issue and prevent empty env crashes)
TELEGRAM_DEDUPE_HOURS = env_int("TELEGRAM_DEDUPE_HOURS", 48)
TELEGRAM_MAX_PER_KEYWORD = env_int("TELEGRAM_MAX_PER_KEYWORD", 2)
TELEGRAM_MIN_UNIQUE_KEYWORDS = env_int("TELEGRAM_MIN_UNIQUE_KEYWORDS", 4)
TELEGRAM_MAX_PER_SELLER = env_int("TELEGRAM_MAX_PER_SELLER", 1)
TELEGRAM_CTA_EVERY_N_POSTS = env_int("TELEGRAM_CTA_EVERY_N_POSTS", 6)
TELEGRAM_CTA_COOLDOWN_MINUTES = env_int("TELEGRAM_CTA_COOLDOWN_MINUTES", 180)
TELEGRAM_PIN_CTA = env_bool("TELEGRAM_PIN_CTA", False)

# Misc / storefront
GUMROAD_CTA_URL = env("GUMROAD_CTA_URL", "")
CLICK_REDIRECT_BASE = env("CLICK_REDIRECT_BASE", "")


def gumroad_cta_url() -> str:
    raw = GUMROAD_CTA_URL or ""
    if not raw:
        return ""
    return raw.replace("{date}", datetime.utcnow().strftime("%Y-%m-%d"))


def tg_targets(scope: str = "broadcast") -> list[str]:
    """
    Returns a list of chat/channel targets based on scope.
    Supported scopes:
      - broadcast: public + paid (if configured)
      - public: public only
      - paid: paid only
      - admin: admin only
      - dm: TELEGRAM_CHAT_ID only
      - all: admin + dm + public + paid (rare; mostly for debugging)
    """
    s = (scope or "broadcast").lower().strip()
    targets: list[str] = []

    def add(x: str | None):
        if x and str(x).strip() != "":
            targets.append(str(x).strip())

    if s == "public":
        add(PUBLIC_CHANNEL_ID)
    elif s == "paid":
        add(PAID_CHANNEL_ID)
    elif s == "admin":
        add(ADMIN_CHAT_ID)
    elif s == "dm":
        add(CHAT_ID)
    elif s == "all":
        add(ADMIN_CHAT_ID)
        add(CHAT_ID)
        add(PUBLIC_CHANNEL_ID)
        add(PAID_CHANNEL_ID)
    else:
        # broadcast default
        add(PUBLIC_CHANNEL_ID)
        add(PAID_CHANNEL_ID)

    # de-dupe
    return list(dict.fromkeys(targets))
