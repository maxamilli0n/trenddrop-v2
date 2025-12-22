import os, json, re
from trenddrop.utils.env_loader import load_env_once
from typing import Dict

ENV_PATH = load_env_once()

try:
    import openai  # type: ignore
except Exception:
    openai = None  # type: ignore

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or ""

PROMPT = """You write short hypey product captions (<180 chars) with an emoji and a CTA.
Return just the sentence, no extra quotes.
Title: {title}
Price: {currency} {price}
"""


def caption_for(p: Dict) -> str:
    title = str(p.get("title", ""))[:120]
    currency = p.get("currency", "USD")
    price = p.get("price", "")
    if not OPENAI_API_KEY or not openai:
        return f"{title} • {currency} {price}"

    try:
        if hasattr(openai, "api_key"):
            openai.api_key = OPENAI_API_KEY
        text = PROMPT.format(title=p.get("title", ""), currency=currency, price=price)
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": text}],
            temperature=0.7,
            max_tokens=80,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or f"{title} • {currency} {price}"
    except Exception:
        return f"{title} • {currency} {price}"


def _fallback_marketing_copy(p: Dict) -> Dict:
    raw_title = str(p.get("title", "")).strip()
    currency = p.get("currency", "USD")
    price = p.get("price")
    price_text = f"{currency} {price:.2f}" if isinstance(price, (int, float)) else (f"{currency} {price}" if price else "")

    headline = raw_title
    headline = re.sub(r"\b(New|Brand\s*New|Hot|Sale|4IN1|3IN1|2PCS|Lot|Bundle)\b", "", headline, flags=re.I)
    headline = re.sub(r"\s{2,}", " ", headline).strip()
    headline = headline[:90]

    text = f"{raw_title} {p.get('keyword','')}".lower()
    if any(k in text for k in ["game", "gaming", "xbox", "ps5", "keyboard", "mouse"]):
        emojis = "🕹️🎮✨"
    elif any(k in text for k in ["dress", "jacket", "sneaker", "fashion", "shirt", "jean"]):
        emojis = "👗👟✨"
    elif any(k in text for k in ["sofa", "lamp", "home", "kitchen", "cook", "vacuum"]):
        emojis = "🏠🛋️✨"
    else:
        emojis = "🔥✨"

    blurb_bits = ["Limited stock—grab it now!"]
    if price_text:
        blurb_bits.insert(0, f"{price_text} steal.")
    blurb = " ".join(blurb_bits)

    lead = emojis[:2]
    if lead:
        headline = f"{lead} {headline}"

    return {"headline": headline, "blurb": blurb, "emojis": emojis}


def marketing_copy_for(p: Dict) -> Dict:
    if not OPENAI_API_KEY or not openai:
        return _fallback_marketing_copy(p)

    raw_title = str(p.get("title", ""))
    currency = p.get("currency", "USD")
    price = p.get("price", "")
    topic = ", ".join(p.get("tags", []) or ([p.get("keyword")] if p.get("keyword") else []))

    sys_prompt = "You are a conversion-focused copywriter for an affiliate deals site. Write exciting but truthful copy."
    user_prompt = (
        "Create concise marketing copy with this structure and return ONLY compact JSON.\n"
        "Rules:\n"
        "- headline: short, punchy, <= 90 chars; can include a leading emoji.\n"
        "- blurb: 1–2 sentences, urgency (limited time/stock), clear benefit + CTA.\n"
        "- emojis: optional 2–3 emojis relevant to category.\n"
        "- Keep it clean, no quotes or markdown.\n"
        "Product:\n"
        f"- title: {raw_title}\n"
        f"- price: {currency} {price}\n"
        f"- topic: {topic}\n\n"
        "Respond as JSON with keys exactly: headline, blurb, emojis."
    )

    try:
        if hasattr(openai, "api_key"):
            openai.api_key = OPENAI_API_KEY
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.8,
            max_tokens=200,
        )
        content = (resp.choices[0].message.content or "").strip()
        match = re.search(r"\{[\s\S]*\}$", content)
        json_text = match.group(0) if match else content
        data = json.loads(json_text)

        headline = str(data.get("headline", "")).strip()
        blurb = str(data.get("blurb", "")).strip()
        emojis = str(data.get("emojis", "")).strip()

        if not headline or not blurb:
            return _fallback_marketing_copy(p)

        return {"headline": headline[:90], "blurb": blurb[:240], "emojis": emojis[:16]}
    except Exception:
        return _fallback_marketing_copy(p)
