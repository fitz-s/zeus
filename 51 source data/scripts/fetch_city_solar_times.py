#!/usr/bin/env python3
"""Fetch sunrise/sunset times for manifest cities over a date range."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"
RAW_ROOT = ROOT / "raw" / "solar"


def _city_slug(city: str) -> str:
    return city.lower().replace(" ", "-")


def _fetch_city(city_row: dict, *, start_date: str, end_date: str) -> dict:
    response = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": city_row["lat"],
            "longitude": city_row["lon"],
            "daily": "sunrise,sunset",
            "timezone": city_row["timezone"],
            "start_date": start_date,
            "end_date": end_date,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    payload["_city"] = city_row["city"]
    payload["_timezone"] = city_row["timezone"]
    payload["_lat"] = city_row["lat"]
    payload["_lon"] = city_row["lon"]
    return payload


def _records_for_city(payload: dict) -> list[dict]:
    city = payload["_city"]
    tz_name = payload["_timezone"]
    tz = ZoneInfo(tz_name)
    daily = payload.get("daily", {})
    rows = []
    for target_date, sunrise_local, sunset_local in zip(
        daily.get("time", []),
        daily.get("sunrise", []),
        daily.get("sunset", []),
    ):
        sunrise_dt = datetime.fromisoformat(sunrise_local).replace(tzinfo=tz)
        sunset_dt = datetime.fromisoformat(sunset_local).replace(tzinfo=tz)
        rows.append(
            {
                "city": city,
                "target_date": target_date,
                "timezone": tz_name,
                "lat": payload["_lat"],
                "lon": payload["_lon"],
                "sunrise_local": sunrise_dt.isoformat(timespec="minutes"),
                "sunset_local": sunset_dt.isoformat(timespec="minutes"),
                "sunrise_utc": sunrise_dt.astimezone(timezone.utc).isoformat(timespec="minutes"),
                "sunset_utc": sunset_dt.astimezone(timezone.utc).isoformat(timespec="minutes"),
                "utc_offset_minutes": int(sunrise_dt.utcoffset().total_seconds() // 60),
                "dst_active": bool(sunrise_dt.dst() and sunrise_dt.dst().total_seconds() != 0),
            }
        )
    return rows


def run(*, manifest_path: Path, start_date: str, end_date: str, jsonl_path: Path, csv_path: Path | None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cities = manifest["cities"]
    all_rows: list[dict] = []
    per_city = []

    for city_row in cities:
        payload = _fetch_city(city_row, start_date=start_date, end_date=end_date)
        rows = _records_for_city(payload)
        all_rows.extend(rows)
        per_city.append(
            {
                "city": city_row["city"],
                "row_count": len(rows),
                "timezone": city_row["timezone"],
                "lat": city_row["lat"],
                "lon": city_row["lon"],
            }
        )

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in all_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if csv_path is not None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "city",
                    "target_date",
                    "timezone",
                    "lat",
                    "lon",
                    "sunrise_local",
                    "sunset_local",
                    "sunrise_utc",
                    "sunset_utc",
                    "utc_offset_minutes",
                    "dst_active",
                ],
            )
            writer.writeheader()
            writer.writerows(all_rows)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "city_count": len(cities),
        "start_date": start_date,
        "end_date": end_date,
        "row_count": len(all_rows),
        "jsonl_path": str(jsonl_path),
        "csv_path": str(csv_path) if csv_path is not None else None,
        "cities": per_city,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2026-03-31")
    parser.add_argument("--jsonl-path", type=Path)
    parser.add_argument("--csv-path", type=Path)
    args = parser.parse_args()

    if args.jsonl_path is None:
        args.jsonl_path = RAW_ROOT / f"city_solar_times_{args.start_date.replace('-', '')}_{args.end_date.replace('-', '')}.jsonl"
    if args.csv_path is None:
        args.csv_path = RAW_ROOT / f"city_solar_times_{args.start_date.replace('-', '')}_{args.end_date.replace('-', '')}.csv"

    summary = run(
        manifest_path=args.manifest_path,
        start_date=args.start_date,
        end_date=args.end_date,
        jsonl_path=args.jsonl_path,
        csv_path=args.csv_path,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
