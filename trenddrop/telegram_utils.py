import requests
from typing import Iterable, Optional
from trenddrop.utils.env_loader import load_env_once
from trenddrop.config import BOT_TOKEN, tg_targets

ENV_PATH = load_env_once()


def _api_base() -> str:
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{BOT_TOKEN}"


def _targets(scope: str) -> list[str]:
    t = tg_targets(scope)
    if not t:
        raise RuntimeError(f"No Telegram targets configured for scope='{scope}'.")
    return t


def send_text(
    text: str,
    *,
    scope: str = "broadcast",
    parse_mode: Optional[str] = None,
    disable_web_page_preview: Optional[bool] = None,
    **kwargs,
) -> None:
    api = _api_base()
    for chat_id in _targets(scope):
        try:
            payload = {"chat_id": chat_id, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if disable_web_page_preview is not None:
                payload["disable_web_page_preview"] = disable_web_page_preview
            payload.update(kwargs)
            requests.post(f"{api}/sendMessage", json=payload, timeout=25).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_text failed scope={scope} chat={chat_id}: {e}")


def send_photo(
    photo: bytes | str,
    *,
    scope: str = "broadcast",
    caption: str | None = None,
    parse_mode: Optional[str] = None,
    **kwargs,
) -> None:
    api = _api_base()
    for chat_id in _targets(scope):
        try:
            # Telegram accepts URL via JSON, bytes via multipart
            if isinstance(photo, (bytes, bytearray)):
                data = {"chat_id": chat_id, "caption": caption or ""}
                if parse_mode:
                    data["parse_mode"] = parse_mode
                data.update(kwargs)
                files = {"photo": ("photo.jpg", photo)}
                requests.post(f"{api}/sendPhoto", data=data, files=files, timeout=30).raise_for_status()
            else:
                payload = {"chat_id": chat_id, "photo": str(photo), "caption": caption or ""}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                payload.update(kwargs)
                requests.post(f"{api}/sendPhoto", json=payload, timeout=25).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_photo failed scope={scope} chat={chat_id}: {e}")


def send_document(
    document: bytes | str,
    *,
    scope: str = "broadcast",
    filename: str | None = None,
    caption: str | None = None,
    parse_mode: Optional[str] = None,
    **kwargs,
) -> None:
    api = _api_base()
    for chat_id in _targets(scope):
        try:
            if isinstance(document, (bytes, bytearray)):
                data = {"chat_id": chat_id, "caption": caption or ""}
                if parse_mode:
                    data["parse_mode"] = parse_mode
                data.update(kwargs)
                files = {"document": (filename or "document.bin", document)}
                requests.post(f"{api}/sendDocument", data=data, files=files, timeout=45).raise_for_status()
            else:
                payload = {"chat_id": chat_id, "document": str(document), "caption": caption or ""}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                payload.update(kwargs)
                requests.post(f"{api}/sendDocument", json=payload, timeout=35).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_document failed scope={scope} chat={chat_id}: {e}")


def send_media_group(media: Iterable[dict], *, scope: str = "broadcast") -> None:
    api = _api_base()
    for chat_id in _targets(scope):
        try:
            payload = {"chat_id": chat_id, "media": list(media)}
            requests.post(f"{api}/sendMediaGroup", json=payload, timeout=35).raise_for_status()
        except Exception as e:
            print(f"[telegram] send_media_group failed scope={scope} chat={chat_id}: {e}")
