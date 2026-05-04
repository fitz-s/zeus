import httpx
from datetime import date, datetime, timezone
import json

API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
ICAO = "DNMM"
CC = "NG"
TARGET_DATE = "20260426"

url = f"https://api.weather.com/v1/location/{ICAO}:9:{CC}/observations/historical.json"
params = {
    "apiKey": API_KEY,
    "units": "m",
    "startDate": TARGET_DATE,
    "endDate": TARGET_DATE,
}

resp = httpx.get(url, params=params)
data = resp.json()

obs = data.get("observations", [])
print(f"Total observations for {TARGET_DATE}: {len(obs)}")
