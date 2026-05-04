import httpx
from datetime import date, datetime, timezone, timedelta
import json

API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
ICAO = "DNMM"
CC = "NG"
YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")

url = f"https://api.weather.com/v1/location/{ICAO}:9:{CC}/observations/historical.json"
params = {
    "apiKey": API_KEY,
    "units": "m",
    "startDate": YESTERDAY,
    "endDate": YESTERDAY,
}

resp = httpx.get(url, params=params)
data = resp.json()

obs = data.get("observations", [])
print(f"Total observations for {YESTERDAY}: {len(obs)}")
for o in obs:
    t = o.get("valid_time_gmt")
    temp = o.get("temp")
    ts = datetime.fromtimestamp(t, timezone.utc).isoformat()
    print(f"TS: {ts} | Temp: {temp}")
