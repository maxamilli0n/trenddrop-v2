import os

from supabase import Client, create_client
from trenddrop.utils.env_loader import load_env_once

# Ensure .env is loaded when running locally
load_env_once()

url = os.environ["SUPABASE_URL"]
key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(url, key)

payload = {
    "id": "manual_test_1",
    "title": "Manual Test Product",
    "name": "Manual Test Product",
    "provider": "manual",
    "source": "manual",
    "price": 9.99,
    "currency": "USD",
    "image_url": "https://example.com/test.png",
    "url": "https://example.com/manual-test",
    "keyword": "manual-test",
    "seller_feedback": 0,
    "signals": 1.0,
    "inserted_at": "2025-01-01T00:00:00+00:00",
    "top_rated": False,
}

res = supabase.table("products").upsert(payload, on_conflict="url").execute()
print("Upsert result:", res)


