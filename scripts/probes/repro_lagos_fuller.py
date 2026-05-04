from datetime import date
from src.data.wu_hourly_client import fetch_wu_hourly
import logging

logging.basicConfig(level=logging.INFO)

res = fetch_wu_hourly(
    icao="DNMM",
    cc="NG",
    start_date=date(2026, 5, 1),
    end_date=date(2026, 5, 1),
    unit="C",
    timezone_name="Africa/Lagos",
    city_name="Lagos"
)

print(f"Lagos 2026-05-01: raw_count={res.raw_observation_count}, aggregated={len(res.observations)}")
for obs in sorted(res.observations, key=lambda x: x.utc_timestamp):
    print(f"UTC Hour: {obs.utc_timestamp} | Obs Count: {obs.observation_count}")
