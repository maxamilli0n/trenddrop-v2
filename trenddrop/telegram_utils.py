import requests
from typing import Iterable, Literal
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import BOT_TOKEN, tg_targets

ENV_PATH = load_env_once()

Target = Literal["admin", "public", "paid", "alerts", "all"]


def _api_base() -> str:
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{BOT_TOKEN}"


def _targets(target: Target) -> list[str]:
    t = tg_targets(target)
    if not t:
        raise RuntimeError(f"No Telegram targets configured for '{target}'.")
    return t


def send_text(
    text: str,
    *,
    target: Target = "admin",
    parse_mode: str | None = None,
    disable_web_page_preview: bool | None = None,
    **kwargs,
) -> None:
    api = _api_base()
    for chat_id in _targets(target):
        try:
            payload = {"chat_id": chat_id, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if disable_web_page_preview is not None:
                payload["disable_web_page_preview"] = bool(disable_web_page_preview)
            payload.update(kwargs)
            requests.post(f"{api}/sendMessage", json=payload, timeout=20).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_text failed for {chat_id} ({target}): {e}")


def send_photo(
    photo: bytes | str,
    caption: str | None = None,
    *,
    target: Target = "admin",
    parse_mode: str | None = None,
    **kwargs,
) -> None:
    api = _api_base()
    for chat_id in _targets(target):
        try:
            data = {"chat_id": chat_id, "caption": caption or ""}
            if parse_mode:
                data["parse_mode"] = parse_mode
            data.update(kwargs)

            # Accept URL or bytes
            if isinstance(photo, (bytes, bytearray)):
                files = {"photo": ("photo.jpg", photo)}
                requests.post(f"{api}/sendPhoto", data=data, files=files, timeout=20).raise_for_status()
            else:
                data["photo"] = str(photo)
                requests.post(f"{api}/sendPhoto", json=data, timeout=20).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_photo failed for {chat_id} ({target}): {e}")


def send_document(
    document: bytes | str,
    filename: str | None = None,
    caption: str | None = None,
    *,
    target: Target = "admin",
    parse_mode: str | None = None,
    **kwargs,
) -> None:
    api = _api_base()
    for chat_id in _targets(target):
        try:
            data = {"chat_id": chat_id, "caption": caption or ""}
            if parse_mode:
                data["parse_mode"] = parse_mode
            data.update(kwargs)

            if isinstance(document, (bytes, bytearray)):
                files = {"document": (filename or "document.bin", document)}
                requests.post(f"{api}/sendDocument", data=data, files=files, timeout=30).raise_for_status()
            else:
                data["document"] = str(document)
                requests.post(f"{api}/sendDocument", json=data, timeout=30).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_document failed for {chat_id} ({target}): {e}")


def send_media_group(media: Iterable[dict], *, target: Target = "admin") -> None:
    api = _api_base()
    for chat_id in _targets(target):
        try:
            payload = {"chat_id": chat_id, "media": list(media)}
            requests.post(f"{api}/sendMediaGroup", json=payload, timeout=30).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_media_group failed for {chat_id} ({target}): {e}")
