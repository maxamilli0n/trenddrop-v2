import re
import requests

headers = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119 Safari/537.36",
    "accept-language": "en-US,en;q=0.9",
}

resp = requests.get(
    "https://www.aliexpress.com/wholesale",
    params={"SearchText": "smartwatch"},
    headers=headers,
    timeout=30,
)
print("initial", resp.status_code, len(resp.text))
match = re.search(r'//www\.aliexpress\.com/[^\'"]+/punish\?x5secdata=([^"\'\\]+)', resp.text)
print("match", bool(match))
if match:
    punish_url = f"https://www.aliexpress.com/wholesale/_____tmd_____/punish?x5secdata={match.group(1)}"
    pun = requests.get(punish_url, headers=headers, timeout=30)
    print("punish", pun.status_code, pun.text[:200])

