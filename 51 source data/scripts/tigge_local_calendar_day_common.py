#!/usr/bin/env python3
"""Shared local-calendar-day helpers for TIGGE mx2t6/mn2t6 pipelines."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from tigge_regions import region_for_city


ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
STEP_HOURS = 6
CEIL_EPSILON_HOURS = 1e-9
DEFAULT_GRID = "0.5/0.5"
_GRID_SUFFIX_RE = re.compile(r"_grid([0-9p]+x[0-9p]+)$")
_CYCLE_SUFFIX_RE = re.compile(r"_cycle(\d{2})z(?:_grid[0-9p]+x[0-9p]+)?$")


@dataclass(frozen=True)
class RegionPair:
    region: str
    date_compact: str
    dates: list[date]
    cf_path: Path
    pf_path: Path
    steps: list[int]
    param: str


def load_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def manifest_sha256(manifest_path: Path) -> str:
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def city_slug(city_name: str) -> str:
    return str(city_name).strip().lower().replace(" ", "-")


def kelvin_to_native(value_k: float, unit: str) -> float:
    value_c = value_k - 273.15
    if str(unit).upper() == "F":
        return value_c * 9.0 / 5.0 + 32.0
    return value_c


def parse_steps_from_filename(path: Path) -> list[int]:
    match = re.search(r"_steps_([0-9-]+)\.grib$", path.name)
    if not match:
        raise ValueError(f"Could not parse step slug from {path}")
    return [int(part) for part in match.group(1).split("-")]


def parse_dates_from_dirname(dirname: str) -> list[date]:
    # Strip optional cycle/grid suffixes before parsing date portion.
    date_part = _GRID_SUFFIX_RE.sub("", dirname)
    date_part = re.sub(r"_cycle\d{2}z$", "", date_part)
    if "_" not in date_part:
        return [datetime.strptime(date_part, "%Y%m%d").date()]
    start_s, end_s = date_part.split("_", 1)
    start = datetime.strptime(start_s, "%Y%m%d").date()
    end = datetime.strptime(end_s, "%Y%m%d").date()
    out: list[date] = []
    current = start
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def grid_suffix(grid: str) -> str:
    if grid == DEFAULT_GRID:
        return ""
    return "_grid" + grid.replace(".", "p").replace("/", "x")


def grid_from_dirname(dirname: str) -> str:
    match = _GRID_SUFFIX_RE.search(dirname)
    if not match:
        return DEFAULT_GRID
    return match.group(1).replace("p", ".").replace("x", "/")


def cycle_from_dirname(dirname: str) -> str:
    match = _CYCLE_SUFFIX_RE.search(dirname)
    if not match:
        return "00"
    return match.group(1)


def issue_utc_from_grib_fields(*, data_date: int, data_time: int) -> datetime:
    day = datetime.strptime(str(data_date), "%Y%m%d").date()
    hh = int(data_time) // 100
    mm = int(data_time) % 100
    return datetime.combine(day, dt_time(hour=hh, minute=mm), tzinfo=timezone.utc)


def local_day_bounds_utc(*, target_local_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(str(timezone_name))
    start_local = datetime.combine(target_local_date, dt_time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def overlap_seconds(
    *,
    window_start: datetime,
    window_end: datetime,
    target_start: datetime,
    target_end: datetime,
) -> int:
    latest_start = max(window_start, target_start)
    earliest_end = min(window_end, target_end)
    seconds = (earliest_end - latest_start).total_seconds()
    return max(0, int(seconds))


def iter_overlap_target_local_dates(*, window_start_utc: datetime, window_end_utc: datetime, timezone_name: str) -> list[date]:
    tz = ZoneInfo(str(timezone_name))
    local_start = window_start_utc.astimezone(tz)
    local_end_exclusive = (window_end_utc - timedelta(microseconds=1)).astimezone(tz)
    dates = {local_start.date(), local_end_exclusive.date()}
    return sorted(dates)


def ceil_to_next_6h(hours: float) -> int:
    adjusted_hours = float(hours) - CEIL_EPSILON_HOURS
    return max(STEP_HOURS, int(math.ceil(adjusted_hours / STEP_HOURS) * STEP_HOURS))


def required_max_step_for_target_local_date(
    *,
    timezone_name: str,
    issue_date_utc: date,
    target_local_date: date,
) -> int:
    issue_utc = datetime.combine(issue_date_utc, dt_time.min, tzinfo=timezone.utc)
    _, local_day_end_utc = local_day_bounds_utc(target_local_date=target_local_date, timezone_name=timezone_name)
    delta_hours = (local_day_end_utc - issue_utc).total_seconds() / 3600.0
    return ceil_to_next_6h(delta_hours)


def required_max_step_for_lead_horizon(
    *,
    timezone_name: str,
    issue_date_utc: date,
    max_target_lead_day: int,
) -> int:
    target_local_date = issue_date_utc + timedelta(days=max_target_lead_day)
    return required_max_step_for_target_local_date(
        timezone_name=timezone_name,
        issue_date_utc=issue_date_utc,
        target_local_date=target_local_date,
    )


def selected_manifest_rows(manifest: dict, selected_cities: set[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    wanted = set(selected_cities or [])
    for row in manifest["cities"]:
        city_name = str(row["city"])
        if wanted and city_name not in wanted:
            continue
        entry = dict(row)
        entry.setdefault(
            "region",
            region_for_city(lat=float(entry["lat"]), lon=float(entry["lon"])).name,
        )
        rows.append(entry)
    return rows


def manifest_rows_by_region(manifest: dict, selected_cities: set[str] | None = None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in selected_manifest_rows(manifest, selected_cities):
        out.setdefault(str(row["region"]), []).append(row)
    return out


def find_region_pairs(
    *,
    raw_root: Path,
    region_subdir: str,
    param: str,
    cycle: str = "00",
    grid: str = DEFAULT_GRID,
) -> list[RegionPair]:
    """Find control/perturbed GRIB file pairs for the given model cycle.

    cycle='00' (default): scans directories named YYYYMMDD or
    YYYYMMDD_YYYYMMDD, optionally suffixed with a non-default grid.
    cycle='12': scans directories whose date portion is suffixed with
    '_cycle12z', optionally followed by a grid suffix.
    This matches the output directory convention of tigge_mx2t6_download_resumable.py.
    """
    param_slug = param.replace(".", "_")
    control_pattern = f"tigge_ecmwf_control_param_{param_slug}_steps_*.grib"
    pairs: list[RegionPair] = []
    for cf_path in sorted((raw_root / region_subdir).rglob(control_pattern)):
        dir_name = cf_path.parent.name
        if cycle_from_dirname(dir_name) != cycle:
            continue
        if grid_from_dirname(dir_name) != grid:
            continue
        pf_path = cf_path.with_name(cf_path.name.replace("control", "perturbed", 1))
        if not pf_path.exists():
            continue
        steps = parse_steps_from_filename(cf_path)
        pairs.append(
            RegionPair(
                region=cf_path.parent.parent.name,
                date_compact=dir_name,
                dates=parse_dates_from_dirname(dir_name),
                cf_path=cf_path,
                pf_path=pf_path,
                steps=steps,
                param=param,
            )
        )
    return pairs


def raw_max_step_index(
    *,
    raw_root: Path,
    region_subdir: str,
    param: str,
    cycle: str = "00",
    grid: str = DEFAULT_GRID,
) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for pair in find_region_pairs(
        raw_root=raw_root,
        region_subdir=region_subdir,
        param=param,
        cycle=cycle,
        grid=grid,
    ):
        pair_max = max(pair.steps)
        for issue_date in pair.dates:
            key = (pair.region, issue_date.isoformat())
            out[key] = max(pair_max, out.get(key, 0))
    return out


def load_json_files(root: Path, *, max_files: int | None = None) -> list[Path]:
    files = sorted(root.rglob("*.json")) if root.exists() else []
    if max_files is not None:
        return files[: max(0, int(max_files))]
    return files


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ROOT",
    "DEFAULT_MANIFEST",
    "STEP_HOURS",
    "CEIL_EPSILON_HOURS",
    "DEFAULT_GRID",
    "RegionPair",
    "ceil_to_next_6h",
    "city_slug",
    "cycle_from_dirname",
    "find_region_pairs",
    "grid_from_dirname",
    "grid_suffix",
    "issue_utc_from_grib_fields",
    "iter_overlap_target_local_dates",
    "kelvin_to_native",
    "load_json_files",
    "load_manifest",
    "local_day_bounds_utc",
    "manifest_rows_by_region",
    "manifest_sha256",
    "now_utc_iso",
    "overlap_seconds",
    "parse_steps_from_filename",
    "raw_max_step_index",
    "required_max_step_for_lead_horizon",
    "required_max_step_for_target_local_date",
    "selected_manifest_rows",
    "write_json",
]
