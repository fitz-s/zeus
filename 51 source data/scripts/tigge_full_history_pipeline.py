#!/usr/bin/env python3
"""TIGGE full-history pipeline for day1..day7 coverage gaps.

This complements tigge_daily_pipeline.py. It uses the same daily lock so the
settlement-matched step24 lane and the full-history multistep lane cannot
compete for the ECMWF queue.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
TMP_DIR = ROOT / "tmp"
LOG_DIR = Path("/Users/leofitz/.openclaw/logs")
RAW_ROOT = ROOT / "raw"
ZEUS_ROOT = Path(__file__).resolve().parents[2]
ZEUS_PYTHON = str(ZEUS_ROOT / ".venv" / "bin" / "python")
DEFAULT_ZEUS_DBS = (
    ZEUS_ROOT / "state" / "zeus-world.db",
    ZEUS_ROOT / "state" / "zeus-shared.db",
)
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"
DEFAULT_COVERAGE_PATH = TMP_DIR / "tigge_coverage_gaps_latest.json"
DEFAULT_DATE_FROM = "2024-01-01"
# step24 is advanced by tigge_daily_pipeline.py's settlement-matched lane.
# This cron lane fills the remaining day2..day7 history by default; pass
# --steps explicitly when a controlled all-step/manual run is needed.
DEFAULT_STEPS = [48, 72, 96, 120, 144, 168]
DEFAULT_MAX_DATE_WINDOWS = 210
DEFAULT_MAX_WORKERS = 4
DEFAULT_MAX_WINDOW_WORKERS = 5
DEFAULT_MAX_BATCH_DATES = 42

# ECMWF Web API public limits, checked from official ECMWF documentation on
# 2026-04-13. The 42-day default uses the larger observed MARS pf request
# cost (~0.95 GB/date) as a conservative proxy, keeping batches below the
# public 50 GB ceiling with margin while still amortizing MARS queue cost.
WEBAPI_FIELD_LIMIT = 600_000
WEBAPI_PUBLIC_TARGET_LIMIT_GB = 50
WEBAPI_QUEUED_REQUEST_LIMIT = 20
WEBAPI_QUEUE_RESERVE = 0
PF_MEMBER_COUNT = 50
ESTIMATED_PF_GB_PER_DATE = 1.05


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_steps(name: str, default: list[int]) -> list[int]:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    parts = raw.replace(",", " ").split()
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return default
    return values or default


def _safe_batch_date_limit(step_count: int) -> int:
    field_limit = WEBAPI_FIELD_LIMIT // max(1, PF_MEMBER_COUNT * step_count)
    size_limit = int((WEBAPI_PUBLIC_TARGET_LIMIT_GB * 0.9) // ESTIMATED_PF_GB_PER_DATE)
    return max(1, min(field_limit, size_limit))


def _clamp_batch_dates(requested: int, steps: list[int]) -> tuple[int, dict]:
    safe_limit = _safe_batch_date_limit(len(steps))
    effective = min(max(1, requested), safe_limit)
    return effective, {
        "requested": requested,
        "effective": effective,
        "safe_limit": safe_limit,
        "field_limit": WEBAPI_FIELD_LIMIT,
        "public_target_limit_gb": WEBAPI_PUBLIC_TARGET_LIMIT_GB,
        "estimated_pf_gb_per_date": ESTIMATED_PF_GB_PER_DATE,
        "pf_fields_per_effective_request": effective * PF_MEMBER_COUNT * len(steps),
    }


def _clamp_parallelism(max_workers: int, max_window_workers: int, region_count: int) -> tuple[int, int, dict]:
    requested_region_workers = max(1, max_workers)
    requested_window_workers = max(1, max_window_workers)
    queued_budget = max(1, WEBAPI_QUEUED_REQUEST_LIMIT - WEBAPI_QUEUE_RESERVE)
    effective_region_workers = min(requested_region_workers, max(1, region_count))
    effective_window_workers = requested_window_workers
    estimated_requests = effective_region_workers * effective_window_workers
    if estimated_requests > queued_budget:
        effective_window_workers = max(1, queued_budget // effective_region_workers)
        estimated_requests = effective_region_workers * effective_window_workers
    return effective_region_workers, effective_window_workers, {
        "requested_region_workers": requested_region_workers,
        "requested_window_workers": requested_window_workers,
        "effective_region_workers": effective_region_workers,
        "effective_window_workers": effective_window_workers,
        "estimated_concurrent_webapi_requests": estimated_requests,
        "official_queued_request_limit": WEBAPI_QUEUED_REQUEST_LIMIT,
        "queue_reserve": WEBAPI_QUEUE_RESERVE,
    }


def _default_date_to() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()


def _daterange(start: date, end: date) -> list[str]:
    from datetime import timedelta

    current = start
    out = []
    while current <= end:
        out.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return out


def _try_lock(path: Path, *, shared: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w")
    lock_type = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        fcntl.flock(fh, lock_type | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def _zeus_db_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("ZEUS_WORLD_DB", "ZEUS_DB"):
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())
    candidates.extend(DEFAULT_ZEUS_DBS)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _has_table(path: Path, table: str) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _resolve_zeus_db() -> Path:
    candidates = _zeus_db_candidates()
    for path in candidates:
        if _has_table(path, "settlements"):
            return path
    checked = ", ".join(str(path) for path in candidates)
    raise RuntimeError(f"No Zeus DB with settlements table found. Checked: {checked}")


def _load_manifest_cities(manifest_path: Path) -> list[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [str(row["city"]) for row in manifest["cities"]]


def _compact_to_iso(date_value: str) -> str:
    return f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:8]}"


def _lane_status_path(lane_index: int, lane_count: int) -> Path:
    if lane_count <= 1:
        return LOG_DIR / "tigge-fullhistory-status-latest.jsonl"
    return LOG_DIR / f"tigge-fullhistory-lane{lane_index}-status-latest.jsonl"


def _load_settlement_city_dates(*, date_from: date, date_to: date) -> set[tuple[str, str]]:
    zeus_db = _resolve_zeus_db()
    conn = sqlite3.connect(f"file:{zeus_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT city, target_date
            FROM settlements
            WHERE settlement_value IS NOT NULL
              AND target_date BETWEEN ? AND ?
            """,
            (date_from.isoformat(), date_to.isoformat()),
        ).fetchall()
        return {(str(city), str(target_date).replace("-", "")) for city, target_date in rows}
    finally:
        conn.close()


def _summarize_coverage(payload: dict) -> dict:
    coverage = payload.get("coverage", [])
    city_count = int(payload.get("city_count") or 0)
    total_city_slots = len(coverage) * city_count
    present_city_slots = sum(int(row.get("present_count") or 0) for row in coverage)
    complete_slots = sum(1 for row in coverage if int(row.get("missing_count") or 0) == 0)
    return {
        "generated_at": payload.get("generated_at"),
        "city_count": city_count,
        "coverage_slots": len(coverage),
        "complete_slots": complete_slots,
        "slot_completion_pct": round(complete_slots / len(coverage) * 100.0, 2) if coverage else 0.0,
        "city_slot_completion_pct": round(present_city_slots / total_city_slots * 100.0, 2) if total_city_slots else 0.0,
        "gap_count": len(payload.get("gaps", [])),
    }


def _scan_coverage(
    *,
    manifest_path: Path,
    coverage_path: Path,
    date_from: date,
    date_to: date,
    steps: list[int],
    recent_rescan_days: int,
) -> dict:
    scan_mod = _load_module("scan_tigge_coverage_gaps", SCRIPT_DIR / "scan_tigge_coverage_gaps.py")
    payload = scan_mod.scan_gaps(
        manifest_path=manifest_path,
        dates=_daterange(date_from, date_to),
        steps=steps,
        previous_path=coverage_path if coverage_path.exists() else None,
        recent_rescan_days=recent_rescan_days,
    )
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _select_windows(
    gaps: Iterable[dict],
    *,
    allowed_steps: set[int],
    allowed_cities: set[str] | None,
    preferred_city_dates: set[tuple[str, str]] | None,
    max_date_windows: int,
    order: str,
) -> tuple[list[dict], str]:
    preferred_grouped: dict[str, dict] = {}
    fallback_grouped: dict[str, dict] = {}
    for gap in gaps:
        step = int(gap.get("step") or 0)
        if step not in allowed_steps:
            continue
        missing_cities = [str(city) for city in gap.get("missing_cities") or []]
        if allowed_cities is not None:
            missing_cities = [city for city in missing_cities if city in allowed_cities]
        if not missing_cities:
            continue
        date_key = str(gap["date"])
        if preferred_city_dates is None:
            preferred_cities = missing_cities
            fallback_cities: list[str] = []
        else:
            preferred_cities = [
                city for city in missing_cities
                if (city, date_key) in preferred_city_dates
            ]
            fallback_cities = [
                city for city in missing_cities
                if (city, date_key) not in preferred_city_dates
            ]

        for grouped, cities in ((preferred_grouped, preferred_cities), (fallback_grouped, fallback_cities)):
            if not cities:
                continue
            row = grouped.setdefault(date_key, {"date": date_key, "steps": set(), "cities": set()})
            row["steps"].add(step)
            row["cities"].update(cities)

    reverse = order == "newest"
    grouped = preferred_grouped if preferred_grouped else fallback_grouped
    selection_mode = "settlement_backed" if preferred_grouped else "all_gaps_fallback"
    windows = []
    for date_key in sorted(grouped, reverse=reverse):
        row = grouped[date_key]
        windows.append(
            {
                "date": date_key,
                "steps": sorted(row["steps"]),
                "cities": sorted(row["cities"]),
            }
        )
        if len(windows) >= max_date_windows:
            break
    return windows, selection_mode


def _write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _summarize_backfill(summary: dict) -> dict:
    download_statuses: dict[str, int] = {}
    extract_statuses: dict[str, int] = {}
    downloaded_files = 0
    extracted_vectors = 0
    region_count = 0

    for date_summary in summary.get("date_summaries", []):
        for region in date_summary.get("regions", []):
            region_count += 1
            download_summary = region.get("download_summary") or {}
            for item in download_summary.get("results", []):
                status = str(item.get("status") or "unknown")
                download_statuses[status] = download_statuses.get(status, 0) + 1
                if status == "downloaded":
                    downloaded_files += 1

            extract_summary = region.get("extract_summary") or {}
            for item in extract_summary.get("results", []):
                status = str(item.get("status") or "unknown")
                extract_statuses[status] = extract_statuses.get(status, 0) + 1
                if status == "extracted":
                    extracted_vectors += 1

    return {
        "date_from": summary.get("date_from"),
        "date_to": summary.get("date_to"),
        "steps": summary.get("steps"),
        "city_count": len(summary.get("cities") or []),
        "region_count": region_count,
        "downloaded_files": downloaded_files,
        "extracted_vectors": extracted_vectors,
        "download_statuses": download_statuses,
        "extract_statuses": extract_statuses,
    }


def _group_windows_into_batches(windows: list[dict], max_batch_size: int) -> list[dict]:
    """Group windows by shared steps into batches for fewer MARS requests.

    Windows with identical step sets are merged so that multiple dates are
    requested in a single MARS call. Each batch contains at most
    max_batch_size dates (ECMWF request size safety boundary).

    Returns a list of batch dicts:
      {"dates": [compact_date_str, ...], "cities": [city, ...], "steps": [int, ...]}
    """
    from collections import defaultdict
    step_groups: dict[tuple[int, ...], list[dict]] = defaultdict(list)
    for w in windows:
        key = tuple(sorted(w["steps"]))
        step_groups[key].append(w)

    batches = []
    for steps_key, group in step_groups.items():
        group = sorted(group, key=lambda row: row["date"])
        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        previous_date: date | None = None
        for row in group:
            current_date = date.fromisoformat(_compact_to_iso(row["date"]))
            starts_new_chunk = (
                not current_chunk
                or previous_date is None
                or (current_date - previous_date).days != 1
                or len(current_chunk) >= max(1, max_batch_size)
            )
            if starts_new_chunk:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = [row]
            else:
                current_chunk.append(row)
            previous_date = current_date
        if current_chunk:
            chunks.append(current_chunk)

        for chunk in chunks:
            all_cities: set[str] = set()
            dates = []
            for w in chunk:
                all_cities.update(w["cities"])
                dates.append(w["date"])
            batches.append({
                "dates": sorted(dates),
                "cities": sorted(all_cities),
                "steps": list(steps_key),
            })
    return batches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date-from", default=os.environ.get("TIGGE_FULL_HISTORY_DATE_FROM", DEFAULT_DATE_FROM))
    parser.add_argument("--date-to", default=os.environ.get("TIGGE_FULL_HISTORY_DATE_TO", _default_date_to()))
    parser.add_argument("--steps", nargs="+", type=int, default=_env_steps("TIGGE_FULL_HISTORY_STEPS", DEFAULT_STEPS))
    parser.add_argument("--cities", nargs="+", help="Optional city subset from the TIGGE manifest")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--coverage-path", type=Path, default=DEFAULT_COVERAGE_PATH)
    parser.add_argument("--status-path", type=Path)
    parser.add_argument("--max-date-windows", type=int, default=_env_int("TIGGE_FULL_HISTORY_MAX_DATE_WINDOWS", DEFAULT_MAX_DATE_WINDOWS))
    parser.add_argument("--max-workers", type=int, default=_env_int("TIGGE_FULL_HISTORY_MAX_WORKERS", DEFAULT_MAX_WORKERS))
    parser.add_argument("--max-window-workers", type=int, default=_env_int("TIGGE_FULL_HISTORY_MAX_WINDOW_WORKERS", DEFAULT_MAX_WINDOW_WORKERS),
                        help="Number of date windows to process in parallel. "
                             "Each window fires up to max-workers region downloads. The script clamps "
                             "estimated concurrent Web API requests below ECMWF's 20 queued-request/user limit.")
    parser.add_argument("--max-batch-dates", type=int,
                        default=_env_int("TIGGE_FULL_HISTORY_MAX_BATCH_DATES", DEFAULT_MAX_BATCH_DATES),
                        help="Max dates per batched MARS request. "
                             "The script clamps this under the ECMWF public request-size limits.")
    parser.add_argument("--recent-rescan-days", type=int, default=_env_int("TIGGE_FULL_HISTORY_RECENT_RESCAN_DAYS", 31))
    parser.add_argument("--order", choices=("oldest", "newest"), default=os.environ.get("TIGGE_FULL_HISTORY_ORDER", "oldest"))
    parser.add_argument(
        "--prefer-settlement-backed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prefer gaps for city/dates that already have settlements; fall back to all gaps only when none exist",
    )
    parser.add_argument("--lane-index", type=int, default=_env_int("TIGGE_FULL_HISTORY_LANE_INDEX", 0))
    parser.add_argument("--lane-count", type=int, default=_env_int("TIGGE_FULL_HISTORY_LANE_COUNT", 1))
    parser.add_argument(
        "--ignore-legacy-fullhistory-lock",
        action="store_true",
        help="Allow a partitioned lane to run while a legacy single-lane process holds tigge-fullhistory.lock. Use only with a non-overlapping date range.",
    )
    parser.add_argument(
        "--ignore-daily-lock",
        action="store_true",
        help="Allow a manual partitioned lane to run without taking tigge-daily.lock. Use only when another full-history process is already holding that lock and the date range is non-overlapping.",
    )
    parser.add_argument("--skip-etl", action="store_true", help="Skip the downstream Zeus multistep calibration ETL")
    args = parser.parse_args()
    args.lane_count = max(1, args.lane_count)
    if args.lane_index < 0 or args.lane_index >= args.lane_count:
        raise SystemExit(f"--lane-index must be between 0 and lane_count-1, got {args.lane_index}/{args.lane_count}")
    if args.status_path is None:
        args.status_path = _lane_status_path(args.lane_index, args.lane_count)

    started_at = datetime.now(timezone.utc)
    status = {
        "generated_at": started_at.isoformat(),
        "dry_run": args.dry_run,
        "args": {
            "date_from": args.date_from,
            "date_to": args.date_to,
            "steps": args.steps,
            "cities": args.cities,
            "manifest_path": str(args.manifest_path),
            "coverage_path": str(args.coverage_path),
            "max_date_windows": args.max_date_windows,
            "max_workers": args.max_workers,
            "max_window_workers": args.max_window_workers,
            "max_batch_dates": args.max_batch_dates,
            "lane_index": args.lane_index,
            "lane_count": args.lane_count,
            "ignore_legacy_fullhistory_lock": args.ignore_legacy_fullhistory_lock,
            "ignore_daily_lock": args.ignore_daily_lock,
            "recent_rescan_days": args.recent_rescan_days,
            "order": args.order,
            "prefer_settlement_backed": args.prefer_settlement_backed,
            "skip_etl": args.skip_etl,
            "zeus_db": str(_resolve_zeus_db()),
        },
        "steps": {},
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    compat_lock = None
    if args.lane_count > 1 and not args.ignore_legacy_fullhistory_lock:
        compat_lock = _try_lock(LOG_DIR / "tigge-fullhistory.lock", shared=True)
        if compat_lock is None:
            status["status"] = "skipped_legacy_fullhistory_running"
            status["lock"] = str(LOG_DIR / "tigge-fullhistory.lock")
            _write_status(args.status_path, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0

    full_lock_path = (
        LOG_DIR / "tigge-fullhistory.lock"
        if args.lane_count <= 1
        else LOG_DIR / f"tigge-fullhistory-lane{args.lane_index}.lock"
    )
    full_lock = _try_lock(full_lock_path)
    if full_lock is None:
        if compat_lock is not None:
            compat_lock.close()
        status["status"] = "skipped_lock_held"
        status["lock"] = str(full_lock_path)
        _write_status(args.status_path, status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    daily_lock = None
    if not args.ignore_daily_lock:
        daily_lock = _try_lock(LOG_DIR / "tigge-daily.lock", shared=True)
        if daily_lock is None:
            full_lock.close()
            if compat_lock is not None:
                compat_lock.close()
            status["status"] = "skipped_daily_pipeline_running"
            status["lock"] = str(LOG_DIR / "tigge-daily.lock")
            _write_status(args.status_path, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0

    try:
        all_cities = set(_load_manifest_cities(args.manifest_path))
        requested_cities = set(args.cities) if args.cities else None
        if requested_cities:
            unknown = sorted(requested_cities - all_cities)
            if unknown:
                raise SystemExit(f"Unknown TIGGE cities: {', '.join(unknown)}")

        regions_for_limits = _load_module("tigge_regions_for_limits", SCRIPT_DIR / "tigge_regions.py")
        args.max_batch_dates, batch_limit_status = _clamp_batch_dates(args.max_batch_dates, args.steps)
        args.max_workers, args.max_window_workers, parallelism_status = _clamp_parallelism(
            args.max_workers,
            args.max_window_workers,
            region_count=len(regions_for_limits.REGIONS),
        )
        status["safety_limits"] = {
            "batch_dates": batch_limit_status,
            "parallelism": parallelism_status,
            "tigge_delay_hours": 48,
        }
        status["args"]["effective_max_batch_dates"] = args.max_batch_dates
        status["args"]["effective_max_workers"] = args.max_workers
        status["args"]["effective_max_window_workers"] = args.max_window_workers

        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)
        scan_payload = _scan_coverage(
            manifest_path=args.manifest_path,
            coverage_path=args.coverage_path,
            date_from=date_from,
            date_to=date_to,
            steps=args.steps,
            recent_rescan_days=args.recent_rescan_days,
        )
        status["steps"]["pre_scan"] = _summarize_coverage(scan_payload)

        preferred_city_dates = (
            _load_settlement_city_dates(date_from=date_from, date_to=date_to)
            if args.prefer_settlement_backed else None
        )
        windows, selection_mode = _select_windows(
            scan_payload.get("gaps", []),
            allowed_steps=set(args.steps),
            allowed_cities=requested_cities,
            preferred_city_dates=preferred_city_dates,
            max_date_windows=max(1, args.max_date_windows * args.lane_count),
            order=args.order,
        )
        if args.lane_count > 1:
            candidate_windows = windows
            lane_size = max(1, (len(candidate_windows) + args.lane_count - 1) // args.lane_count)
            lane_start = args.lane_index * lane_size
            lane_end = lane_start + lane_size
            windows = candidate_windows[lane_start:lane_end][: max(1, args.max_date_windows)]
            status["lane_candidate_window_count"] = len(candidate_windows)
        status["selection_mode"] = selection_mode
        status["selected_windows"] = windows
        if not windows:
            status["status"] = "complete_no_gaps"
            _write_status(args.status_path, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0

        if args.dry_run:
            status["status"] = "dry_run"
            _write_status(args.status_path, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0

        city_batch_mod = _load_module("backfill_tigge_city_batch", SCRIPT_DIR / "backfill_tigge_city_batch.py")
        run_results: list[dict] = []
        queue_full = False

        batches = _group_windows_into_batches(windows, args.max_batch_dates)

        def _run_batch(batch: dict) -> dict:
            dates = [date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:8]}") for d in batch["dates"]]
            date_from_val = min(dates)
            date_to_val = max(dates)
            try:
                summary = city_batch_mod.backfill_batch_multistep(
                    batch["cities"],
                    date_from=date_from_val,
                    date_to=date_to_val,
                    steps=batch["steps"],
                    param="167.128",
                    manifest_path=args.manifest_path,
                    dry_run=False,
                    overwrite=False,
                    max_workers=args.max_workers,
                )
                return {"batch": batch, "status": "ok", "summary": _summarize_backfill(summary)}
            except Exception as exc:
                return {"batch": batch, "status": "error", "error": str(exc)[:1000]}

        max_ww = max(1, args.max_window_workers)
        if max_ww == 1:
            for batch in batches:
                result = _run_batch(batch)
                run_results.append(result)
                if "QUEUED_LIMIT" in result.get("error", ""):
                    queue_full = True
                    break
        else:
            with ThreadPoolExecutor(max_workers=max_ww) as wex:
                future_to_batch = {wex.submit(_run_batch, b): b for b in batches}
                for future in as_completed(future_to_batch):
                    result = future.result()
                    run_results.append(result)
                    if "QUEUED_LIMIT" in result.get("error", ""):
                        queue_full = True

        if queue_full:
            status["status"] = "queue_full"
        if any(result.get("status") != "ok" for result in run_results) and "status" not in status:
            status["status"] = "backfill_error"
        status["steps"]["backfill"] = run_results

        etl_script = ZEUS_ROOT / "scripts" / "etl_tigge_calibration.py"
        if args.skip_etl:
            status["steps"]["multistep_calibration_etl"] = {"status": "skipped_by_flag"}
        elif etl_script.exists():
            r = subprocess.run(
                [ZEUS_PYTHON, str(etl_script)],
                capture_output=True,
                text=True,
                timeout=900,
                cwd=str(ZEUS_ROOT),
            )
            status["steps"]["multistep_calibration_etl"] = {
                "returncode": r.returncode,
                "stdout_tail": r.stdout[-2000:] if r.stdout else "",
                "stderr_tail": r.stderr[-1000:] if r.stderr else "",
            }
            print(r.stdout[-500:] if r.stdout else "(no output)")
            if r.returncode != 0:
                status["status"] = "etl_error"
        else:
            status["steps"]["multistep_calibration_etl"] = {"status": "missing_script", "path": str(etl_script)}

        post_scan_payload = _scan_coverage(
            manifest_path=args.manifest_path,
            coverage_path=args.coverage_path,
            date_from=date_from,
            date_to=date_to,
            steps=args.steps,
            recent_rescan_days=args.recent_rescan_days,
        )
        status["steps"]["post_scan"] = _summarize_coverage(post_scan_payload)
        status.setdefault("status", "ok")
        status["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_status(args.status_path, status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    finally:
        full_lock.close()
        if daily_lock is not None:
            daily_lock.close()
        if compat_lock is not None:
            compat_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
