import httpx
from datetime import date, datetime, timezone
import json

API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
ICAO = "DNMM"
CC = "NG"
TARGET_DATE = "20260423"

# Try the v2/v3 endpoints if possible, or a different location type
# But the standard is v1. Let's see if we can find a different station ID for Lagos.
# Actually, let's just check if there's any other station nearby.
url = "https://api.weather.com/v3/location/search"
params = {
    "query": "Lagos",
    "locationType": "airport",
    "language": "en-US",
    "format": "json",
    "apiKey": API_KEY
}
resp = httpx.get(url, params=params)
print("Lagos Search:")
print(json.dumps(resp.json(), indent=2))
