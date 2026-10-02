#!/usr/bin/env python3
# Created: 2026-10-01
# Last audited: 2026-10-01
# Authority basis: operator mandate 2026-10-01 (no station-ground data gaps; Synoptic WRH ground
#   admitted only with an independent NOAA cross-check); config/AGENTS.md station_ground_proof.
"""Capture approved Synoptic WRH station records and their NOAA bridges into config.

Read-only against every provider. Writes the original response bodies verbatim
to config/synoptic_wrh_<icao>_station.json plus each bridge body, then binds a
station_ground_proof row in config/station_precise_coords.json only when the
owning parser derives VERIFIED facts from the two whole bodies. A station whose
bodies disagree keeps no claim (UNPROVEN), never a hand-entered value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import (  # noqa: E402
    CONFIG_DIR, STATION_GROUND_PROOF_REVISION, SYNOPTIC_WRH_SOURCE_KIND, SYNOPTIC_WRH_METADATA_URL,
    _SYNOPTIC_WRH_STATIONS, cities_by_name, station_ground_facts_from_bytes,
    station_ground_identity_bridge, station_ground_source_artifact_ref,
)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
_TOKEN_JS = "https://www.weather.gov/source/wrh/apiKey.js"
_TOKEN_RE = re.compile(r"mesoToken\s*=\s*['\"]([0-9a-fA-F]{8,})['\"]")


def _get(url: str, headers: dict[str, str], *, attempts: int = 6) -> tuple[bytes, datetime]:
    """One GET; 429/5xx back off exponentially, any other non-200 fails."""
    delay = 5.0
    for _ in range(attempts):
        response = httpx.get(url, headers=headers, timeout=60.0)
        received = datetime.now(timezone.utc)
        if response.status_code == 200:
            return response.content, received
        if response.status_code != 429 and response.status_code < 500:
            raise RuntimeError(f"{url.split('?')[0]} HTTP {response.status_code}")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"{url.split('?')[0]} still throttled after {attempts} attempts")


def _synoptic(station: str, token: str) -> tuple[bytes, datetime]:
    body, received = _get(f"{SYNOPTIC_WRH_METADATA_URL}?STID={station}&complete=1&token={token}", {
        "User-Agent": _UA, "Referer": f"https://www.weather.gov/wrh/timeseries?site={station}",
        "Origin": "https://www.weather.gov", "Accept": "application/json, text/javascript, */*; q=0.01"})
    if json.loads(body).get("SUMMARY", {}).get("RESPONSE_CODE") != 1:
        raise RuntimeError(f"{station}: Synoptic refused the metadata request")
    return body, received


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", nargs="*", default=sorted(_SYNOPTIC_WRH_STATIONS))
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse; write nothing")
    args = parser.parse_args(argv)
    unknown = set(args.stations) - _SYNOPTIC_WRH_STATIONS
    if unknown:
        parser.error(f"not approved Synoptic stations: {sorted(unknown)}")
    city_of = {city.wu_station: name for name, city in cities_by_name.items()
               if city.wu_station in _SYNOPTIC_WRH_STATIONS}
    token = _TOKEN_RE.search(_get(_TOKEN_JS, {"User-Agent": _UA})[0].decode()).group(1)
    registry_path = CONFIG_DIR / "station_precise_coords.json"
    rows = json.loads(registry_path.read_text())
    status = 0
    for station in args.stations:
        name = city_of[station]
        bridge_kind, bridge_ref, bridge_url = station_ground_identity_bridge(
            source_kind=SYNOPTIC_WRH_SOURCE_KIND, station_id=station)
        bridge, bridge_at = _get(bridge_url, {"User-Agent": "zeus-station-ground (read-only)"})
        time.sleep(2.0)  # Synoptic per-IP pacing, as the settlement fetcher.
        body, body_at = _synoptic(station, token)
        facts = station_ground_facts_from_bytes(source_kind=SYNOPTIC_WRH_SOURCE_KIND,
            station_id=station, raw_body=body, identity_bridge_bytes=bridge)
        print(f"{name} {station}: {'VERIFIED' if facts else 'UNPROVEN'}"
              + (f" elevation_m={facts['elevation_m']} site=({facts['site_lat']},{facts['site_lon']})" if facts else ""))
        if facts is None:
            status = 1
            continue
        if args.dry_run:
            continue
        ref = station_ground_source_artifact_ref(source_kind=SYNOPTIC_WRH_SOURCE_KIND, station_id=station)
        (ROOT / ref).write_bytes(body)
        (ROOT / bridge_ref).write_bytes(bridge)
        assert facts["revision"] == STATION_GROUND_PROOF_REVISION
        stamp = lambda at: at.isoformat()  # noqa: E731
        rows[name]["station_ground_proof"] = {
            **facts, "artifact_ref": ref, "body_sha256": hashlib.sha256(body).hexdigest(),
            "source_checked_at": stamp(body_at), "checked_at": stamp(max(body_at, bridge_at)),
            "bridge": {"source_kind": bridge_kind, "artifact_ref": bridge_ref,
                       "body_sha256": hashlib.sha256(bridge).hexdigest(),
                       "checked_at": stamp(bridge_at), "source_url": bridge_url},
        }
    if not args.dry_run:
        registry_path.write_text(json.dumps(rows, indent=1) + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
