#!/usr/bin/env python3
# Created: 2026-05-19
# Last reused or audited: 2026-06-09
# Authority basis: Addendum 2 §2 (majority threshold + strict-< boundary rule);
#   rule-drift parity fix 2026-06-09 — mirrors extract_open_ens_localday.py
#   fix commit 1b77ca94db (same two bugs patched in OpenData extractor tonight).
"""Generic extractor for TIGGE local-calendar-day max/min products."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from eccodes import codes_get, codes_grib_find_nearest, codes_grib_new_from_file, codes_release

from tigge_local_calendar_day_common import (
    DEFAULT_MANIFEST,
    ROOT,
    city_slug,
    find_region_pairs,
    grid_suffix,
    issue_utc_from_grib_fields,
    iter_overlap_target_local_dates,
    kelvin_to_native,
    load_manifest,
    local_day_bounds_utc,
    manifest_rows_by_region,
    manifest_sha256,
    now_utc_iso,
    overlap_seconds,
    write_json,
)


@dataclass(frozen=True)
class TrackConfig:
    name: str
    mode: str  # high | low
    param: str
    param_id: int
    short_name: str
    step_type: str
    data_version: str
    physical_quantity: str
    region_subdir: str
    output_subdir: str


TRACKS: dict[str, TrackConfig] = {
    "mx2t6_high": TrackConfig(
        name="mx2t6_high",
        mode="high",
        param="121.128",
        param_id=121,
        short_name="mx2t6",
        step_type="max",
        data_version="tigge_mx2t6_local_calendar_day_max_v1",
        physical_quantity="mx2t6_local_calendar_day_max",
        region_subdir="tigge_ecmwf_ens_regions_mx2t6",
        output_subdir="tigge_ecmwf_ens_mx2t6_localday_max",
    ),
    "mn2t6_low": TrackConfig(
        name="mn2t6_low",
        mode="low",
        param="122.128",
        param_id=122,
        short_name="mn2t6",
        step_type="min",
        data_version="tigge_mn2t6_local_calendar_day_min_v1",
        physical_quantity="mn2t6_local_calendar_day_min",
        region_subdir="tigge_ecmwf_ens_regions_mn2t6",
        output_subdir="tigge_ecmwf_ens_mn2t6_localday_min",
    ),
}


def _init_member_bucket(mode: str) -> dict[str, Any]:
    if mode == "high":
        return {"values": []}
    return {"inner_values": [], "boundary_values": []}


def _init_record(*, city: dict, issue_utc: datetime, target_local_date: date, lead_day: int, track: TrackConfig, manifest_sha: str) -> dict[str, Any]:
    return {
        "city": city["city"],
        "issue_time_utc": issue_utc.isoformat(),
        "issue_date": issue_utc.date().strftime("%Y%m%d"),
        "target_date_local": target_local_date.isoformat(),
        "lead_day": int(lead_day),
        "timezone": city["timezone"],
        "lat": city["lat"],
        "lon": city["lon"],
        "unit": city["unit"],
        "manifest_sha256": manifest_sha,
        "nearest_grid_lat": None,
        "nearest_grid_lon": None,
        "nearest_grid_distance_km": None,
        "selected_step_ranges": set(),
        "selected_step_ranges_inner": set(),
        "selected_step_ranges_boundary": set(),
        "members": {member: _init_member_bucket(track.mode) for member in range(51)},
    }


def _record_path(
    *,
    output_root: Path,
    output_subdir: str,
    city_name: str,
    issue_date_compact: str,
    target_local_date: str,
    lead_day: int,
    cycle: str = "00",
    grid: str = "0.5/0.5",
) -> Path:
    # Namespace the output directory by cycle/grid so caches don't collide.
    cycle_suffix = f"_cycle{cycle}z" if cycle != "00" else ""
    issue_dir = issue_date_compact + cycle_suffix + grid_suffix(grid)
    return (
        output_root
        / output_subdir
        / city_slug(city_name)
        / issue_dir
        / f"{output_subdir}_target_{target_local_date}_lead_{lead_day}.json"
    )


def _finalize_high_record(record: dict[str, Any], track: TrackConfig) -> dict[str, Any]:
    members_out = []
    missing_members = []
    for member in range(51):
        values = record["members"][member]["values"]
        if not values:
            missing_members.append(member)
            value_native = None
        else:
            value_native = max(values)
        members_out.append({"member": member, "value_native_unit": value_native})
    member_count = len(members_out)
    payload = {
        "generated_at": now_utc_iso(),
        "data_version": track.data_version,
        "physical_quantity": track.physical_quantity,
        "param": track.param,
        "paramId": track.param_id,
        "short_name": track.short_name,
        "step_type": track.step_type,
        "aggregation_window_hours": 6,
        "city": record["city"],
        "lat": record["lat"],
        "lon": record["lon"],
        "unit": record["unit"],
        "manifest_sha256": record["manifest_sha256"],
        "issue_time_utc": record["issue_time_utc"],
        "target_date_local": record["target_date_local"],
        "lead_day": record["lead_day"],
        "lead_day_anchor": "issue_utc.date()",
        "timezone": record["timezone"],
        "local_day_window": {
            "start": local_day_bounds_utc(
                target_local_date=date.fromisoformat(record["target_date_local"]),
                timezone_name=record["timezone"],
            )[0].isoformat(),
            "end": local_day_bounds_utc(
                target_local_date=date.fromisoformat(record["target_date_local"]),
                timezone_name=record["timezone"],
            )[1].isoformat(),
        },
        "nearest_grid_lat": record["nearest_grid_lat"],
        "nearest_grid_lon": record["nearest_grid_lon"],
        "nearest_grid_distance_km": record["nearest_grid_distance_km"],
        "selected_step_ranges": sorted(record["selected_step_ranges"]),
        "member_count": member_count,
        "missing_members": missing_members,
        "training_allowed": len(missing_members) == 0,
        "causality": {"pure_forecast_valid": True, "status": "OK"},
        "members": members_out,
    }
    return payload


def _finalize_low_record(record: dict[str, Any], track: TrackConfig) -> dict[str, Any]:
    members_out = []
    missing_members = []
    boundary_ambiguous_members = []
    for member in range(51):
        inner_values = record["members"][member]["inner_values"]
        boundary_values = record["members"][member]["boundary_values"]
        inner_min = min(inner_values) if inner_values else None
        boundary_min = min(boundary_values) if boundary_values else None
        # Fix 2026-06-09 (Bug A): strict < — ties (boundary_min == inner_min) are NOT
        # ambiguous. Pre-fix used <=, quarantining members where temperature is stable
        # at midnight. Mirrors the same fix applied to extract_open_ens_localday.py
        # (commit 1b77ca94db, Addendum 2 §2 parity).
        boundary_ambiguous = boundary_min is not None and (inner_min is None or boundary_min < inner_min)
        if boundary_ambiguous:
            boundary_ambiguous_members.append(member)
        value_native = inner_min if (inner_min is not None and not boundary_ambiguous) else None
        if inner_min is None and boundary_min is None:
            missing_members.append(member)
        members_out.append(
            {
                "member": member,
                "value_native_unit": value_native,
                "inner_min_native_unit": inner_min,
                "boundary_min_native_unit": boundary_min,
                "boundary_ambiguous": boundary_ambiguous,
            }
        )
    member_count = len(members_out)
    # Fix 2026-06-09 (Bug B): majority threshold — a minority of ambiguous members
    # must NOT quarantine the whole snapshot. Pre-fix used len > 0 (any()), which let
    # a single tie-flagged member quarantine all 51. Threshold = ceil(51/2) = 26.
    # Mirrors extract_open_ens_localday.py fix (commit 1b77ca94db, Addendum 2 §2).
    _majority_threshold = max(1, len(members_out) // 2 + 1)
    any_boundary_ambiguous = len(boundary_ambiguous_members) >= _majority_threshold
    training_allowed = len(missing_members) == 0 and not any_boundary_ambiguous
    payload = {
        "generated_at": now_utc_iso(),
        "data_version": track.data_version,
        "physical_quantity": track.physical_quantity,
        "param": track.param,
        "paramId": track.param_id,
        "short_name": track.short_name,
        "step_type": track.step_type,
        "aggregation_window_hours": 6,
        "city": record["city"],
        "lat": record["lat"],
        "lon": record["lon"],
        "unit": record["unit"],
        "manifest_sha256": record["manifest_sha256"],
        "issue_time_utc": record["issue_time_utc"],
        "target_date_local": record["target_date_local"],
        "lead_day": record["lead_day"],
        "lead_day_anchor": "issue_utc.date()",
        "timezone": record["timezone"],
        "local_day_window": {
            "start": local_day_bounds_utc(
                target_local_date=date.fromisoformat(record["target_date_local"]),
                timezone_name=record["timezone"],
            )[0].isoformat(),
            "end": local_day_bounds_utc(
                target_local_date=date.fromisoformat(record["target_date_local"]),
                timezone_name=record["timezone"],
            )[1].isoformat(),
        },
        "causality": {"pure_forecast_valid": True, "status": "OK"},
        "boundary_policy": {
            "training_rule": "use_inner_only_and_exclude_if_boundary_can_win",
            "boundary_ambiguous": any_boundary_ambiguous,
            "ambiguous_member_count": len(boundary_ambiguous_members),
            "boundary_ambiguous_members": boundary_ambiguous_members,
        },
        "nearest_grid_lat": record["nearest_grid_lat"],
        "nearest_grid_lon": record["nearest_grid_lon"],
        "nearest_grid_distance_km": record["nearest_grid_distance_km"],
        "selected_step_ranges_inner": sorted(record["selected_step_ranges_inner"]),
        "selected_step_ranges_boundary": sorted(record["selected_step_ranges_boundary"]),
        "member_count": member_count,
        "missing_members": missing_members,
        "training_allowed": training_allowed,
        "members": members_out,
    }
    return payload


def _finalize_record(record: dict[str, Any], track: TrackConfig) -> dict[str, Any]:
    if track.mode == "high":
        return _finalize_high_record(record, track)
    return _finalize_low_record(record, track)


def extract_track(
    *,
    track: TrackConfig,
    manifest_path: Path,
    raw_root: Path,
    output_root: Path,
    date_from: date | None,
    date_to: date | None,
    cities: set[str] | None,
    max_target_lead_day: int,
    max_pairs: int | None,
    overwrite: bool,
    summary_path: Path | None,
    cycle: str = "00",
    grid: str = "0.5/0.5",
) -> dict:
    if cycle not in ("00", "12"):
        raise ValueError(f"cycle must be '00' or '12', got {cycle!r}")
    manifest = load_manifest(manifest_path)
    manifest_sha = manifest_sha256(manifest_path)
    rows_by_region = manifest_rows_by_region(manifest, cities)
    pairs = find_region_pairs(
        raw_root=raw_root,
        region_subdir=track.region_subdir,
        param=track.param,
        cycle=cycle,
        grid=grid,
    )
    if date_from is not None or date_to is not None:
        filtered_pairs = []
        for pair in pairs:
            issue_dates = pair.dates
            if date_from is not None and max(issue_dates) < date_from:
                continue
            if date_to is not None and min(issue_dates) > date_to:
                continue
            filtered_pairs.append(pair)
        pairs = filtered_pairs
    if max_pairs is not None:
        pairs = pairs[: max(0, int(max_pairs))]

    records: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    pair_summaries = []
    metadata_errors: list[dict[str, Any]] = []

    def collect_file(path: Path, *, forecast_type: str, city_rows: list[dict]) -> None:
        with path.open("rb") as fh:
            while True:
                gid = codes_grib_new_from_file(fh)
                if gid is None:
                    break
                try:
                    param_id = int(codes_get(gid, "paramId"))
                    short_name = str(codes_get(gid, "shortName"))
                    step_type = str(codes_get(gid, "stepType"))
                    if param_id != track.param_id or short_name != track.short_name or step_type != track.step_type:
                        metadata_errors.append(
                            {
                                "file": str(path),
                                "paramId": param_id,
                                "shortName": short_name,
                                "stepType": step_type,
                                "expected_paramId": track.param_id,
                                "expected_shortName": track.short_name,
                                "expected_stepType": track.step_type,
                            }
                        )
                        continue
                    member = 0 if forecast_type == "cf" else int(codes_get(gid, "number"))
                    data_date = int(codes_get(gid, "dataDate"))
                    data_time = int(codes_get(gid, "dataTime"))
                    start_step = int(codes_get(gid, "startStep"))
                    end_step = int(codes_get(gid, "endStep"))
                    step_range = str(codes_get(gid, "stepRange"))
                    issue_utc = issue_utc_from_grib_fields(data_date=data_date, data_time=data_time)
                    window_start_utc = issue_utc + timedelta(hours=start_step)
                    window_end_utc = issue_utc + timedelta(hours=end_step)
                    for city in city_rows:
                        nearest = codes_grib_find_nearest(gid, float(city["lat"]), float(city["lon"]))[0]
                        value_native = kelvin_to_native(float(nearest["value"]), str(city["unit"]))
                        for target_local_date in iter_overlap_target_local_dates(
                            window_start_utc=window_start_utc,
                            window_end_utc=window_end_utc,
                            timezone_name=str(city["timezone"]),
                        ):
                            day_start_utc, day_end_utc = local_day_bounds_utc(
                                target_local_date=target_local_date,
                                timezone_name=str(city["timezone"]),
                            )
                            overlap = overlap_seconds(
                                window_start=window_start_utc,
                                window_end=window_end_utc,
                                target_start=day_start_utc,
                                target_end=day_end_utc,
                            )
                            if overlap <= 0:
                                continue
                            lead_day = (target_local_date - issue_utc.date()).days
                            if lead_day < 0 or lead_day > max_target_lead_day:
                                continue
                            key = (str(city["city"]), issue_utc.date().strftime("%Y%m%d"), target_local_date.isoformat(), lead_day)
                            record = records.setdefault(
                                key,
                                _init_record(
                                    city=city,
                                    issue_utc=issue_utc,
                                    target_local_date=target_local_date,
                                    lead_day=lead_day,
                                    track=track,
                                    manifest_sha=manifest_sha,
                                ),
                            )
                            record["nearest_grid_lat"] = float(nearest["lat"])
                            record["nearest_grid_lon"] = float(nearest["lon"])
                            record["nearest_grid_distance_km"] = float(nearest["distance"])
                            member_bucket = record["members"][member]
                            if track.mode == "high":
                                member_bucket["values"].append(value_native)
                                record["selected_step_ranges"].add(step_range)
                            else:
                                fully_inside = window_start_utc >= day_start_utc and window_end_utc <= day_end_utc
                                if fully_inside:
                                    member_bucket["inner_values"].append(value_native)
                                    record["selected_step_ranges_inner"].add(step_range)
                                else:
                                    member_bucket["boundary_values"].append(value_native)
                                    record["selected_step_ranges_boundary"].add(step_range)
                finally:
                    codes_release(gid)

    for pair in pairs:
        city_rows = rows_by_region.get(pair.region, [])
        if not city_rows:
            continue
        collect_file(pair.cf_path, forecast_type="cf", city_rows=city_rows)
        collect_file(pair.pf_path, forecast_type="pf", city_rows=city_rows)
        pair_summaries.append(
            {
                "region": pair.region,
                "date_compact": pair.date_compact,
                "cf_path": str(pair.cf_path),
                "pf_path": str(pair.pf_path),
                "steps": pair.steps,
            }
        )

    written = 0
    skipped = 0
    results = []
    for key in sorted(records):
        record = records[key]
        payload = _finalize_record(record, track)
        output_path = _record_path(
            output_root=output_root,
            output_subdir=track.output_subdir,
            city_name=record["city"],
            issue_date_compact=record["issue_date"],
            target_local_date=record["target_date_local"],
            lead_day=record["lead_day"],
            cycle=cycle,
            grid=grid,
        )
        if output_path.exists() and not overwrite:
            skipped += 1
            results.append({"output_path": str(output_path), "status": "skipped_exists"})
            continue
        write_json(output_path, payload)
        written += 1
        results.append(
            {
                "output_path": str(output_path),
                "status": "written",
                "training_allowed": payload.get("training_allowed"),
                "member_count": payload.get("member_count"),
            }
        )

    summary = {
        "generated_at": now_utc_iso(),
        "track": track.name,
        "data_version": track.data_version,
        "physical_quantity": track.physical_quantity,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "raw_root": str(raw_root),
        "region_subdir": track.region_subdir,
        "output_root": str(output_root),
        "output_subdir": track.output_subdir,
        "cycle": cycle,
        "grid": grid,
        "pair_count": len(pair_summaries),
        "metadata_error_count": len(metadata_errors),
        "metadata_errors": metadata_errors[:200],
        "written_outputs": written,
        "skipped_outputs": skipped,
        "results": results[:500],
        "pairs": pair_summaries[:200],
    }
    if summary_path is not None:
        write_json(summary_path, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", choices=sorted(TRACKS), required=True)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "raw")
    parser.add_argument("--output-root", type=Path, default=ROOT / "raw")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--cities", nargs="*")
    parser.add_argument("--max-target-lead-day", type=int, default=7)
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--summary-path", type=Path, default=None)
    parser.add_argument("--cycle", choices=["00", "12"], default="00",
                        help="TIGGE model cycle: '00' (00Z, default) or '12' (12Z)")
    parser.add_argument("--grid", default="0.5/0.5",
                        help="MARS output grid to scan, e.g. 0.5/0.5 or 0.25/0.25")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    track = TRACKS[args.track]
    summary = extract_track(
        track=track,
        manifest_path=args.manifest_path,
        raw_root=args.raw_root,
        output_root=args.output_root,
        date_from=date.fromisoformat(args.date_from) if args.date_from else None,
        date_to=date.fromisoformat(args.date_to) if args.date_to else None,
        cities=set(args.cities or []),
        max_target_lead_day=args.max_target_lead_day,
        max_pairs=args.max_pairs,
        overwrite=args.overwrite,
        summary_path=args.summary_path,
        cycle=args.cycle,
        grid=args.grid,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["metadata_error_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
