import os, json, re
from trenddrop.utils.env_loader import load_env_once
from typing import Dict

ENV_PATH = load_env_once()

try:
    import openai  # type: ignore
except Exception:
    openai = None  # type: ignore

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

CAPTION_PROMPT = """You write short, punchy affiliate deal copy.
Rules:
- Keep it truthful.
- 1 line headline max.
- No hashtags.
- No quotes.
Product:
Title: {title}
Price: {currency} {price}
"""


def _safe_title(p: Dict) -> str:
    return str(p.get("title", "")).strip()[:120]


def _fallback_marketing_copy(p: Dict) -> Dict:
    raw_title = str(p.get("title", "")).strip()
    currency = p.get("currency", "USD")
    price = p.get("price")
    price_text = f"{currency} {price:.2f}" if isinstance(price, (int, float)) else (f"{currency} {price}" if price else "")

    headline = raw_title
    headline = re.sub(r"\b(New|Brand\s*New|Hot|Sale|4IN1|3IN1|2PCS|Lot|Bundle|Free\s*Ship)\b", "", headline, flags=re.I)
    headline = re.sub(r"\s{2,}", " ", headline).strip()
    headline = headline[:85] if headline else "Trending pick"

    text = f"{raw_title} {p.get('keyword','')}".lower()
    if any(k in text for k in ["game", "gaming", "xbox", "ps5", "keyboard", "mouse", "headset"]):
        emojis = "🕹️🎮"
    elif any(k in text for k in ["dress", "jacket", "sneaker", "fashion", "shirt", "jean"]):
        emojis = "👟✨"
    elif any(k in text for k in ["sofa", "lamp", "home", "kitchen", "cook", "vacuum", "air fryer"]):
        emojis = "🏠✨"
    elif any(k in text for k in ["charger", "wireless", "magsafe", "usb"]):
        emojis = "⚡🔌"
    else:
        emojis = "🔥✨"

    blurb = "Worth a quick look — listings like this can move fast."
    if price_text:
        blurb = f"{price_text} — {blurb}"

    return {"headline": f"{emojis} {headline}".strip(), "blurb": blurb, "emojis": emojis}


def marketing_copy_for(p: Dict) -> Dict:
    if not OPENAI_API_KEY or not openai:
        return _fallback_marketing_copy(p)

    raw_title = str(p.get("title", "")).strip()
    currency = p.get("currency", "USD")
    price = p.get("price", "")
    topic = ", ".join(p.get("tags", []) or ([p.get("keyword")] if p.get("keyword") else []))

    sys_prompt = (
        "You are a conversion-focused copywriter for an affiliate deals channel. "
        "Write exciting but truthful copy. Avoid exaggeration."
    )

    user_prompt = (
        "Return ONLY compact JSON.\n"
        "Keys: headline, blurb, emojis\n"
        "Rules:\n"
        "- headline: <= 85 chars; may start with 1-2 emojis\n"
        "- blurb: <= 180 chars; simple, punchy, not spammy\n"
        "- emojis: 2-3 emojis max\n"
        "Product:\n"
        f"- title: {raw_title}\n"
        f"- price: {currency} {price}\n"
        f"- topic: {topic}\n"
    )

    try:
        if hasattr(openai, "api_key"):
            openai.api_key = OPENAI_API_KEY

        resp = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=220,
        )

        content = resp.choices[0].message.content.strip()

        match = re.search(r"\{[\s\S]*\}$", content)
        json_text = match.group(0) if match else content
        data = json.loads(json_text)

        headline = str(data.get("headline", "")).strip()
        blurb = str(data.get("blurb", "")).strip()
        emojis = str(data.get("emojis", "")).strip()

        if not headline or not blurb:
            return _fallback_marketing_copy(p)

        return {
            "headline": headline[:85],
            "blurb": blurb[:180],
            "emojis": emojis[:16],
        }
    except Exception:
        return _fallback_marketing_copy(p)
