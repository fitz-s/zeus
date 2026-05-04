import httpx
from datetime import date
import json

# Use the key discovered in repro scripts
API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
ICAO = "DNMM"
CC = "NG"
TARGET_DATE = "20260423" # The 3-hour day

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
for o in obs:
    t = o.get("valid_time_gmt")
    temp = o.get("temp")
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(t, timezone.utc).isoformat()
    print(f"TS: {ts} | Temp: {temp}")
