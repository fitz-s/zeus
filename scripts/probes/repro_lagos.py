from datetime import date
from src.data.wu_hourly_client import fetch_wu_hourly
import logging
import json

logging.basicConfig(level=logging.INFO)

# Test Lagos (DNMM) for 2026-04-23 where we have only 3 hours
res = fetch_wu_hourly(
    icao="DNMM",
    cc="NG",
    start_date=date(2026, 4, 23),
    end_date=date(2026, 4, 23),
    unit="C",
    timezone_name="Africa/Lagos",
    city_name="Lagos"
)

print(f"Lagos 2026-04-23: raw_count={res.raw_observation_count}, aggregated={len(res.observations)}")
if res.failed:
    print(f"Failure: {res.failure_reason} - {res.error}")

# Sample one obs to see format
if res.observations:
    print("Sample observation:")
    obs = res.observations[0]
    print(f"UTC: {obs.utc_timestamp}, Local: {obs.local_timestamp}, Temp: {obs.hour_max_temp}")

# Test NYC (KLGA) for 2026-04-23 for comparison
res_nyc = fetch_wu_hourly(
    icao="KLGA",
    cc="US",
    start_date=date(2026, 4, 23),
    end_date=date(2026, 4, 23),
    unit="F",
    timezone_name="America/New_York",
    city_name="NYC"
)
print(f"NYC 2026-04-23: raw_count={res_nyc.raw_observation_count}, aggregated={len(res_nyc.observations)}")

