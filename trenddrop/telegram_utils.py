import requests
from typing import Iterable

from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import BOT_TOKEN, tg_targets

ENV_PATH = load_env_once()


def _api_base() -> str:
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{BOT_TOKEN}"


def _resolve_targets(scope: str | None) -> list[str]:
    scope = scope or "broadcast"
    targets = tg_targets(scope)
    if not targets:
        raise RuntimeError(
            f"No Telegram targets configured for scope='{scope}'. "
            f"Set TELEGRAM_ADMIN_CHAT_ID / TELEGRAM_PUBLIC_CHANNEL_ID / TELEGRAM_PAID_CHANNEL_ID (or legacy TELEGRAM_CHAT_ID / TELEGRAM_CHANNEL_ID)."
        )
    return targets


def send_text(text: str, *, scope: str | None = None, **kwargs) -> None:
    api = _api_base()
    targets = _resolve_targets(scope)
    for chat_id in targets:
        try:
            payload = {"chat_id": chat_id, "text": text}
            payload.update(kwargs)
            requests.post(f"{api}/sendMessage", json=payload, timeout=20).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_text failed for {chat_id}: {e}")


def send_photo(photo: bytes | str, caption: str | None = None, *, scope: str | None = None, **kwargs) -> None:
    api = _api_base()
    targets = _resolve_targets(scope)
    for chat_id in targets:
        try:
            data = {"chat_id": chat_id, "caption": caption or ""}
            data.update(kwargs)

            if isinstance(photo, (bytes, bytearray)):
                files = {"photo": ("photo.jpg", photo)}
                requests.post(f"{api}/sendPhoto", data=data, files=files, timeout=20).raise_for_status()
            else:
                data["photo"] = str(photo)
                requests.post(f"{api}/sendPhoto", json=data, timeout=20).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_photo failed for {chat_id}: {e}")


def send_document(document: bytes | str, filename: str | None = None, caption: str | None = None, *, scope: str | None = None, **kwargs) -> None:
    api = _api_base()
    targets = _resolve_targets(scope)
    for chat_id in targets:
        try:
            data = {"chat_id": chat_id, "caption": caption or ""}
            data.update(kwargs)

            if isinstance(document, (bytes, bytearray)):
                files = {"document": (filename or "document.bin", document)}
                requests.post(f"{api}/sendDocument", data=data, files=files, timeout=30).raise_for_status()
            else:
                data["document"] = str(document)
                requests.post(f"{api}/sendDocument", json=data, timeout=30).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_document failed for {chat_id}: {e}")


def send_media_group(media: Iterable[dict], *, scope: str | None = None) -> None:
    api = _api_base()
    targets = _resolve_targets(scope)
    for chat_id in targets:
        try:
            payload = {"chat_id": chat_id, "media": list(media)}
            requests.post(f"{api}/sendMediaGroup", json=payload, timeout=30).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_media_group failed for {chat_id}: {e}")
