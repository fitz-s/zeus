#!/usr/bin/env python3
"""Coverage scanner for local-calendar-day TIGGE composite products."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path

from tigge_local_calendar_day_common import (
    DEFAULT_MANIFEST,
    ROOT,
    city_slug,
    load_manifest,
    local_day_bounds_utc,
    load_json_files,
    manifest_rows_by_region,
    now_utc_iso,
    raw_max_step_index,
    required_max_step_for_target_local_date,
    write_json,
)
from tigge_local_calendar_day_extract import TRACKS, TrackConfig


def _load_output_index(root: Path) -> dict[tuple[str, str, str, int], dict]:
    out = {}
    for path in load_json_files(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        key = (
            str(payload.get("city")),
            str(payload.get("issue_time_utc", ""))[:10].replace("-", ""),
            str(payload.get("target_date_local")),
            int(payload.get("lead_day") or 0),
        )
        out[key] = payload
    return out


def _status_for_slot(*, track: TrackConfig, city: dict, issue_date: date, lead_day: int, raw_max_step: int | None, payload: dict | None) -> str:
    target_local_date = issue_date + timedelta(days=lead_day)
    if track.mode == "low":
        issue_utc = datetime.combine(issue_date, dt_time.min, tzinfo=timezone.utc)
        local_day_start_utc, _ = local_day_bounds_utc(target_local_date=target_local_date, timezone_name=str(city["timezone"]))
        if issue_utc > local_day_start_utc:
            return "N/A_CAUSAL_DAY_ALREADY_STARTED"
    if raw_max_step is None:
        return "MISSING_RAW"
    required_step = required_max_step_for_target_local_date(
        timezone_name=str(city["timezone"]),
        issue_date_utc=issue_date,
        target_local_date=target_local_date,
    )
    if raw_max_step < required_step:
        return "N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON"
    if payload is None:
        return "MISSING_EXTRACT"
    if int(payload.get("member_count") or 0) != 51:
        return "REJECTED_MEMBER_COUNT"
    if track.mode == "low":
        if not bool(payload.get("training_allowed")):
            return "REJECTED_BOUNDARY_AMBIGUOUS"
    return "OK"


def scan_track(
    *,
    track: TrackConfig,
    manifest_path: Path,
    raw_root: Path,
    output_root: Path,
    date_from: date,
    date_to: date,
    max_target_lead_day: int,
    output: Path | None,
    cycle: str,
    grid: str,
) -> dict:
    manifest = load_manifest(manifest_path)
    rows_by_region = manifest_rows_by_region(manifest)
    raw_index = raw_max_step_index(
        raw_root=raw_root,
        region_subdir=track.region_subdir,
        param=track.param,
        cycle=cycle,
        grid=grid,
    )
    payload_index = _load_output_index(output_root / track.output_subdir)

    city_stats: dict[str, dict] = {}
    slots = []
    counts = defaultdict(int)
    current = date_from
    while current <= date_to:
        for region, rows in rows_by_region.items():
            raw_max = raw_index.get((region, current.isoformat()))
            for city in rows:
                stats = city_stats.setdefault(
                    str(city["city"]),
                    {
                        "city": city["city"],
                        "forecastable_slots": 0,
                        "boundary_ambiguous_slots": 0,
                        "training_allowed_slots": 0,
                        "ok_slots": 0,
                    },
                )
                for lead_day in range(max_target_lead_day + 1):
                    target_local_date = current + timedelta(days=lead_day)
                    key = (str(city["city"]), current.strftime("%Y%m%d"), target_local_date.isoformat(), lead_day)
                    payload = payload_index.get(key)
                    status = _status_for_slot(
                        track=track,
                        city=city,
                        issue_date=current,
                        lead_day=lead_day,
                        raw_max_step=raw_max,
                        payload=payload,
                    )
                    counts[status] += 1
                    if status not in {"N/A_CAUSAL_DAY_ALREADY_STARTED", "N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON"}:
                        stats["forecastable_slots"] += 1
                    if status == "REJECTED_BOUNDARY_AMBIGUOUS":
                        stats["boundary_ambiguous_slots"] += 1
                    if status == "OK":
                        stats["ok_slots"] += 1
                        stats["training_allowed_slots"] += 1
                    slots.append(
                        {
                            "city": city["city"],
                            "issue_date": current.isoformat(),
                            "target_date_local": target_local_date.isoformat(),
                            "lead_day": lead_day,
                            "status": status,
                            "raw_max_step": raw_max,
                        }
                    )
        current += timedelta(days=1)

    city_rows = []
    for stats in city_stats.values():
        forecastable = stats["forecastable_slots"]
        quarantine = stats["boundary_ambiguous_slots"]
        quarantine_rate = (quarantine / forecastable) if forecastable else 0.0
        row = dict(stats)
        row["quarantine_rate"] = round(quarantine_rate, 6)
        row["warning"] = "WARN_HIGH_QUARANTINE_RATE" if quarantine_rate > 0.20 else ""
        city_rows.append(row)
    city_rows.sort(key=lambda row: row["quarantine_rate"], reverse=True)

    result = {
        "generated_at": now_utc_iso(),
        "track": track.name,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "max_target_lead_day": max_target_lead_day,
        "cycle": cycle,
        "grid": grid,
        "counts": dict(sorted(counts.items())),
        "city_quarantine_table": city_rows,
        "slot_sample": slots[:1000],
    }
    if output is not None:
        write_json(output, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", choices=sorted(TRACKS), required=True)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "raw")
    parser.add_argument("--output-root", type=Path, default=ROOT / "raw")
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--max-target-lead-day", type=int, default=7)
    parser.add_argument("--cycle", choices=["00", "12"], default="00")
    parser.add_argument("--grid", default="0.5/0.5")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    track = TRACKS[args.track]
    result = scan_track(
        track=track,
        manifest_path=args.manifest_path,
        raw_root=args.raw_root,
        output_root=args.output_root,
        date_from=date.fromisoformat(args.date_from),
        date_to=date.fromisoformat(args.date_to),
        max_target_lead_day=args.max_target_lead_day,
        output=args.output,
        cycle=args.cycle,
        grid=args.grid,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
