#!/usr/bin/env python3
"""Oracle shadow-snapshot listener.

Captures WU ICAO API responses at the *same time window* that PM's UMA
Oracle fetches its data, storing raw JSON in the shadow storage directory
``raw/oracle_shadow_snapshots/``.  This enables post-hoc comparison
between what the Oracle saw and what our normal ingestion pipeline records
(which may include backfilled METAR data that wasn't available at Oracle
settlement time).

Usage (cron — run once at 10:00 UTC, matching UMA Oracle window):
    0 10 * * * cd /…/zeus && .venv/bin/python scripts/oracle_snapshot_listener.py

Architecture notes:
- Zero coupling to zeus-world.db — reads only ``config/cities.json``
  and writes only to ``raw/oracle_shadow_snapshots/``.
- The bridge script ``scripts/bridge_oracle_to_calibration.py`` is the
  only consumer of these snapshots.
- One file per (city, date) keeps the directory browsable and diffable.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("oracle_snapshot")

# ── paths ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "raw" / "oracle_shadow_snapshots"
CITY_CONFIG = ROOT / "config" / "cities.json"

# ── WU API ────────────────────────────────────────────────────────────
WU_API_KEY = os.environ.get("WU_API_KEY")
WU_ICAO_URL = (
    "https://api.weather.com/v1/location/{icao}:9:{cc}"
    "/observations/historical.json"
)
WU_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# ── HKO API ───────────────────────────────────────────────────────────
HKO_API_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
HKO_STATION = "HKO"

# ── station map (mirrored from daily_obs_append.py CITY_STATIONS) ─────
CITY_STATIONS: dict[str, tuple[str, str]] = {
    # US
    "NYC": ("KLGA", "US"), "Chicago": ("KORD", "US"),
    "Atlanta": ("KATL", "US"), "Austin": ("KAUS", "US"),
    "Dallas": ("KDAL", "US"), "Denver": ("KBKF", "US"),
    "Houston": ("KHOU", "US"), "Los Angeles": ("KLAX", "US"),
    "Miami": ("KMIA", "US"), "San Francisco": ("KSFO", "US"),
    "Seattle": ("KSEA", "US"),
    # Americas
    "Buenos Aires": ("SAEZ", "AR"), "Mexico City": ("MMMX", "MX"),
    "Sao Paulo": ("SBGR", "BR"), "Toronto": ("CYYZ", "CA"),
    # Europe
    "London": ("EGLC", "GB"), "Paris": ("LFPG", "FR"),
    "Munich": ("EDDM", "DE"), "Madrid": ("LEMD", "ES"),
    "Milan": ("LIMC", "IT"), "Warsaw": ("EPWA", "PL"),
    "Amsterdam": ("EHAM", "NL"), "Helsinki": ("EFHK", "FI"),
    "Ankara": ("LTAC", "TR"),
    # Asia
    "Beijing": ("ZBAA", "CN"), "Shanghai": ("ZSPD", "CN"),
    "Shenzhen": ("ZGSZ", "CN"), "Chengdu": ("ZUUU", "CN"),
    "Chongqing": ("ZUCK", "CN"), "Wuhan": ("ZHHH", "CN"),
    "Guangzhou": ("ZGGG", "CN"),
    "Tokyo": ("RJTT", "JP"), "Seoul": ("RKSI", "KR"),
    "Taipei": ("RCSS", "TW"), "Singapore": ("WSSS", "SG"),
    "Lucknow": ("VILK", "IN"), "Karachi": ("OPKC", "PK"),
    "Manila": ("RPLL", "PH"),
    # Oceania
    "Wellington": ("NZWN", "NZ"), "Auckland": ("NZAA", "NZ"),
    # Africa
    "Lagos": ("DNMM", "NG"), "Cape Town": ("FACT", "ZA"),
    # Middle East
    "Jeddah": ("OEJN", "SA"),
    # SE/NE Asia
    "Kuala Lumpur": ("WMKK", "MY"), "Jakarta": ("WIHH", "ID"),
    "Busan": ("RKPK", "KR"), "Panama City": ("MPMG", "PA"),
}


def _fetch_wu_snapshot(icao: str, cc: str, target: date) -> dict | None:
    """Fetch WU ICAO historical data for one (station, date)."""
    if not WU_API_KEY:
        logger.error("WU_API_KEY not set — skipping WU snapshots")
        return None

    url = WU_ICAO_URL.format(icao=icao, cc=cc)
    params = {
        "apiKey": WU_API_KEY,
        "units": "e",  # Fahrenheit (same as PM Oracle)
        "startDate": target.strftime("%Y%m%d"),
        "endDate": target.strftime("%Y%m%d"),
    }
    try:
        resp = httpx.get(url, params=params, headers=WU_HEADERS, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("WU fetch failed %s/%s: %s", icao, target, exc)
        return None


def _fetch_hko_snapshot(target: date) -> dict | None:
    """Fetch HKO CLMMAXT+CLMMINT for the target month."""
    try:
        result = {}
        for dtype in ("CLMMAXT", "CLMMINT"):
            resp = httpx.get(HKO_API_URL, params={
                "dataType": dtype,
                "year": str(target.year),
                "month": f"{target.month:02d}",
                "rformat": "json",
                "lang": "en",
                "station": HKO_STATION,
            }, timeout=30.0)
            resp.raise_for_status()
            result[dtype] = resp.json()
            time.sleep(0.5)
        return result
    except Exception as exc:
        logger.warning("HKO fetch failed %s: %s", target, exc)
        return None


def _extract_wu_daily_high(payload: dict) -> float | None:
    """Extract daily high from WU hourly observations (same as daily_obs_append)."""
    obs = payload.get("observations", [])
    if not obs:
        return None
    temps = [o.get("temp") for o in obs if o.get("temp") is not None]
    return max(temps) if temps else None


def capture_snapshots(target: date | None = None) -> dict:
    """Capture oracle snapshots for all cities for target date.

    Returns stats dict with counts of captured/failed.
    """
    if target is None:
        # Default: yesterday (PM settles next-day)
        from datetime import timedelta
        target = date.today() - timedelta(days=1)

    capture_ts = datetime.now(timezone.utc).isoformat()
    stats = {"wu_captured": 0, "hko_captured": 0, "failed": 0}

    # WU cities
    for city_name, (icao, cc) in CITY_STATIONS.items():
        payload = _fetch_wu_snapshot(icao, cc, target)
        if payload is None:
            stats["failed"] += 1
            continue

        daily_high = _extract_wu_daily_high(payload)

        snapshot = {
            "city": city_name,
            "target_date": target.isoformat(),
            "captured_at_utc": capture_ts,
            "station_id": f"{icao}:9:{cc}",
            "source": "wu_icao_history",
            "daily_high_f": daily_high,
            "observation_count": len(payload.get("observations", [])),
            "data_version": "oracle_shadow_v1",
            "wu_raw_payload": payload,
        }

        city_dir = SNAPSHOT_DIR / city_name.lower().replace(" ", "_")
        city_dir.mkdir(parents=True, exist_ok=True)
        out_file = city_dir / f"{target.isoformat()}.json"
        with open(out_file, "w") as f:
            json.dump(snapshot, f, indent=2)

        logger.info("WU snapshot: %s high=%s (%d obs)", city_name, daily_high,
                     snapshot["observation_count"])
        stats["wu_captured"] += 1
        time.sleep(0.3)  # rate limit courtesy

    # HKO (Hong Kong)
    hko_payload = _fetch_hko_snapshot(target)
    if hko_payload:
        snapshot = {
            "city": "Hong Kong",
            "target_date": target.isoformat(),
            "captured_at_utc": capture_ts,
            "station_id": HKO_STATION,
            "source": "hko_daily_api",
            "data_version": "oracle_shadow_v1",
            "hko_raw_payload": hko_payload,
        }
        city_dir = SNAPSHOT_DIR / "hong_kong"
        city_dir.mkdir(parents=True, exist_ok=True)
        out_file = city_dir / f"{target.isoformat()}.json"
        with open(out_file, "w") as f:
            json.dump(snapshot, f, indent=2)
        logger.info("HKO snapshot: Hong Kong")
        stats["hko_captured"] += 1
    else:
        stats["failed"] += 1

    return stats


if __name__ == "__main__":
    target = None
    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])

    stats = capture_snapshots(target)
    total = stats["wu_captured"] + stats["hko_captured"]
    logger.info(
        "Oracle snapshot complete: %d captured (%d WU + %d HKO), %d failed",
        total, stats["wu_captured"], stats["hko_captured"], stats["failed"],
    )
