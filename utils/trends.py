import random
from dotenv import load_dotenv, find_dotenv

# Ensure root .env is loaded (pytrends may not need it, but keep consistent)
load_dotenv(find_dotenv(usecwd=True), override=False)
from typing import List
from pytrends.request import TrendReq

SEED_TOPICS = [
    "desk lamp","pickleball paddle","massage gun","wireless charger",
    "rgb lamp","pet hair trimmer","mechanical keyboard","bike light",
    "cordless vacuum","air fryer","smart light strip","portable monitor"
]

def clean_topic(t: str) -> str:
    t = t.strip()
    bad = ["vs","score","how to","meaning","lyrics","who is","age","net worth"]
    if any(b in t.lower() for b in bad): 
        return ""
    return t

def topic_query_variants(topic: str, max_variants: int = 3) -> List[str]:
    """
    Expand a topic into multiple keyword/search variants to widen the eBay scrape.
    Ensures deterministic ordering and removes duplicates/empties.
    """
    base = clean_topic(topic)
    if not base:
        return []
    max_variants = max(1, max_variants)
    candidates = [
        base,
        f"{base} deals",
        f"best {base}",
        f"{base} sale",
        f"trending {base}",
    ]
    if len(base.split()) == 1:
        candidates.append(f"{base} gadget")
    variants: List[str] = []
    for phrase in candidates:
        cleaned = " ".join(phrase.split()).strip()
        if not cleaned:
            continue
        if cleaned in variants:
            continue
        variants.append(cleaned)
        if len(variants) >= max_variants:
            break
    return variants

def top_topics(limit: int = 8, geo: str = "US") -> List[str]:
    try:
        pytrends = TrendReq(hl='en-US', tz=360)
        df = pytrends.trending_searches(pn='united_states' if geo.upper()=="US" else 'united_kingdom')
        topics = [clean_topic(x) for x in df[0].tolist()]
        topics = [x for x in topics if x]
        if not topics:
            topics = SEED_TOPICS[:]
    except Exception:
        topics = SEED_TOPICS[:]
    random.shuffle(topics)
    return topics[:limit]
