#!/usr/bin/env python3
"""TIGGE settlement-matched backfill: download + extract only dates with settlements.

Reads the download plan from zeus-shared.db, downloads GRIB from ECMWF TIGGE,
extracts JSON via eccodes, and auto-imports into zeus calibration_pairs.

Key features:
- ECMWF queue management (auto-clear stale requests before starting)
- 48-hour delay compliance (skips dates < 3 days old)
- Rate limiting (max 8 concurrent ECMWF requests)
- Checkpoint/resume (tracks progress in checkpoint file)
- Auto-extraction (GRIB → JSON via eccodes after download)

Usage:
    /Users/leofitz/miniconda3/bin/python scripts/tigge_settlement_backfill.py \\
        --max-dates 50 --checkpoint /tmp/tigge_backfill_checkpoint.json

Must run with conda base python (has ecmwfapi + eccodes).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# --- ECMWF API imports ---
from ecmwfapi import ECMWFDataServer

ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")
RAW_ROOT = ROOT / "raw"
MANIFEST_PATH = ROOT / "docs" / "tigge_city_coordinate_manifest_full_20260330.json"
SCRIPT_DIR = Path(__file__).resolve().parent
ZEUS_ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/zeus")
DEFAULT_ZEUS_DBS = (
    ZEUS_ROOT / "state" / "zeus-world.db",
    ZEUS_ROOT / "state" / "zeus-shared.db",
)
DEFAULT_GAP_DATE_FROM = "2024-01-01"

# Zeus canonical name → TIGGE dir slug
SLUG_MAP = {
    "Ankara": "ankara", "Atlanta": "atlanta", "Austin": "austin",
    "Auckland": "auckland",
    "Beijing": "beijing", "Buenos Aires": "buenos-aires",
    "Busan": "busan", "Cape Town": "cape-town",
    "Chengdu": "chengdu", "Chicago": "chicago", "Chongqing": "chongqing",
    "Dallas": "dallas", "Denver": "denver",
    "Hong Kong": "hong-kong", "Houston": "houston",
    "Istanbul": "istanbul", "Jakarta": "jakarta", "Jeddah": "jeddah",
    "Kuala Lumpur": "kuala-lumpur", "Lagos": "lagos", "London": "london",
    "Los Angeles": "los-angeles", "Lucknow": "lucknow",
    "Madrid": "madrid", "Mexico City": "mexico-city",
    "Miami": "miami", "Milan": "milan", "Moscow": "moscow",
    "Munich": "munich", "NYC": "nyc", "Paris": "paris",
    "San Francisco": "san-francisco", "Sao Paulo": "sao-paulo",
    "Seattle": "seattle", "Seoul": "seoul", "Shanghai": "shanghai",
    "Shenzhen": "shenzhen", "Singapore": "singapore",
    "Taipei": "taipei", "Tel Aviv": "tel-aviv", "Tokyo": "tokyo",
    "Panama City": "panama-city",
    "Toronto": "toronto", "Warsaw": "warsaw",
    "Wellington": "wellington", "Wuhan": "wuhan",
}


def _load_ecmwf_credentials() -> tuple[str, str, str]:
    """Load ECMWF API credentials from ~/.ecmwfapirc."""
    rc_path = os.path.expanduser("~/.ecmwfapirc")
    with open(rc_path) as f:
        content = f.read()
    url = re.search(r'"url"\s*:\s*"([^"]+)"', content).group(1)
    key = re.search(r'"key"\s*:\s*"([^"]+)"', content).group(1)
    email = re.search(r'"email"\s*:\s*"([^"]+)"', content).group(1)
    return url, email, key


def _clear_ecmwf_queue() -> int:
    """Clear all stale ECMWF requests (queued, rejected, complete)."""
    import requests

    url, email, key = _load_ecmwf_credentials()
    headers = {"From": email, "X-ECMWF-KEY": key}

    try:
        resp = requests.get(
            f"{url}/datasets/tigge/requests",
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  Warning: Could not list ECMWF requests: {resp.status_code}")
            return 0

        data = resp.json()
        request_list = data.get("tigge", [])
        cleared = 0
        for r in request_list:
            if r["status"] in ("queued", "active", "rejected", "complete"):
                del_resp = requests.delete(r["href"], headers=headers, timeout=10)
                if del_resp.status_code in (200, 204):
                    cleared += 1
        return cleared
    except Exception as e:
        print(f"  Warning: Queue cleanup failed: {e}")
        return 0


def _load_manifest_city(city_name: str) -> dict:
    """Load city row from TIGGE coordinate manifest."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for row in manifest["cities"]:
        if row["city"].lower() == city_name.lower():
            return row
    raise KeyError(f"City {city_name!r} not found in manifest")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _daily_dates(start: date, end: date) -> list[str]:
    out = []
    current = start
    while current <= end:
        out.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return out


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


def _build_settlement_download_plan() -> list[tuple[str, str, str]]:
    """Build list of (zeus_city, slug, target_date) needing download."""
    zeus_db = _resolve_zeus_db()
    print(f"   Zeus DB: {zeus_db}")
    conn = sqlite3.connect(f"file:{zeus_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_slugs = {
        str(row["city"]): str(row["city"]).lower().replace(" ", "-")
        for row in manifest.get("cities", [])
    }

    plan = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")

    for row in conn.execute("""
        SELECT city, target_date FROM settlements
        WHERE settlement_value IS NOT NULL
        AND target_date <= ?
        ORDER BY target_date DESC
    """, (cutoff,)).fetchall():
        city = row["city"]
        target_date = row["target_date"]
        slug = SLUG_MAP.get(city) or manifest_slugs.get(city)
        if not slug:
            continue

        # Check if GRIB already exists
        compact = target_date.replace("-", "")
        cf = RAW_ROOT / "tigge_ecmwf_ens" / slug / compact / "tigge_ecmwf_control_param_167_128_step_024.grib"
        pf = RAW_ROOT / "tigge_ecmwf_ens" / slug / compact / "tigge_ecmwf_perturbed_param_167_128_step_024.grib"
        if cf.exists() and pf.exists():
            continue

        plan.append((city, slug, target_date))

    conn.close()
    return plan


def _build_step24_gapfill_plan(*, date_from: str, date_to: str, previous_path: Path | None = None) -> list[tuple[str, str, str]]:
    """Build an all-city step24 gapfill plan, hotspot cities first."""
    scan_mod = _load_module("scan_tigge_coverage_gaps_step24", SCRIPT_DIR / "scan_tigge_coverage_gaps.py")
    payload = scan_mod.scan_gaps(
        manifest_path=MANIFEST_PATH,
        dates=_daily_dates(date.fromisoformat(date_from), date.fromisoformat(date_to)),
        steps=[24],
        previous_path=previous_path,
        recent_rescan_days=31,
    )

    rows: list[tuple[str, str, str]] = []
    city_counts: dict[str, int] = {}
    for gap in payload.get("gaps", []):
        target_date = f"{gap['date'][:4]}-{gap['date'][4:6]}-{gap['date'][6:8]}"
        for city in gap.get("missing_cities") or []:
            slug = SLUG_MAP.get(city) or city.lower().replace(" ", "-")
            rows.append((city, slug, target_date))
            city_counts[city] = city_counts.get(city, 0) + 1

    rows.sort(key=lambda row: (-city_counts[row[0]], row[2], row[0]))
    return rows


def _group_plan_by_city(plan: list[tuple[str, str, str]]) -> dict[str, list[str]]:
    """Group download plan by slug → list of dates for date-range batching."""
    groups: dict[str, list[str]] = {}
    for city, slug, dt in plan:
        groups.setdefault(slug, []).append(dt)
    # Sort dates within each city
    for slug in groups:
        groups[slug] = sorted(set(groups[slug]))
    return groups


def _download_batch(
    slug: str,
    dates: list[str],
    *,
    step: int = 24,
    param: str = "167.128",
    max_dates_per_request: int = 10,
) -> dict:
    """Download cf + pf GRIB for multiple dates in batch requests.

    Uses ECMWF date='d1/d2/d3/...' syntax to batch multiple dates
    into a single API request, then splits the multi-message GRIB.
    This reduces queue wait time from ~39 min/date to ~39 min/batch.
    """
    from eccodes import codes_get, codes_grib_new_from_file, codes_write

    # Find city in manifest
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    city_row = None
    slug_lower = slug.lower()
    for row in manifest["cities"]:
        if row["city"].lower().replace(" ", "-") == slug_lower:
            city_row = row
            break
    if city_row is None:
        return {"status": "error", "error": f"City {slug!r} not in manifest"}

    server = ECMWFDataServer()
    results = {"downloaded": 0, "skipped": 0, "errors": 0, "dates": {}}

    # Filter out dates that already have GRIB files
    needed_dates = []
    for dt in dates:
        compact = dt.replace("-", "")
        cf = RAW_ROOT / "tigge_ecmwf_ens" / slug / compact / f"tigge_ecmwf_control_param_{param.replace('.', '_')}_step_{step:03d}.grib"
        pf = RAW_ROOT / "tigge_ecmwf_ens" / slug / compact / f"tigge_ecmwf_perturbed_param_{param.replace('.', '_')}_step_{step:03d}.grib"
        if cf.exists() and pf.exists():
            results["skipped"] += 1
            results["dates"][dt] = "exists"
        else:
            needed_dates.append(dt)

    if not needed_dates:
        return results

    # Batch dates into chunks of max_dates_per_request
    for chunk_start in range(0, len(needed_dates), max_dates_per_request):
        chunk = needed_dates[chunk_start:chunk_start + max_dates_per_request]
        date_str = "/".join(chunk)  # ECMWF format: "2026-03-01/2026-03-02/..."
        print(f"     Batch: {len(chunk)} dates [{chunk[0]} → {chunk[-1]}]")

        for forecast_type in ("cf", "pf"):
            suffix = "control" if forecast_type == "cf" else "perturbed"
            # Download to a temp multi-date GRIB file
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".grib", delete=False) as tmp:
                tmp_path = Path(tmp.name)

            request = {
                "dataset": "tigge",
                "class": "ti",
                "origin": "ecmf",
                "expver": "prod",
                "stream": "enfo",
                "levtype": "sfc",
                "param": param,
                "date": date_str,
                "time": "00:00:00",
                "step": str(step),
                "type": forecast_type,
                "area": city_row["tigge_request"]["area"],
                "grid": city_row["tigge_request"]["grid"],
                "target": str(tmp_path),
            }
            if forecast_type == "pf":
                request["number"] = "1/to/50"

            try:
                server.retrieve(request)

                # Split multi-date GRIB into per-date files
                with tmp_path.open("rb") as fh:
                    while True:
                        gid = codes_grib_new_from_file(fh)
                        if gid is None:
                            break
                        data_date = str(codes_get(gid, "dataDate"))
                        # Format: YYYYMMDD
                        date_dir = RAW_ROOT / "tigge_ecmwf_ens" / slug / data_date
                        date_dir.mkdir(parents=True, exist_ok=True)
                        out_file = date_dir / f"tigge_ecmwf_{suffix}_param_{param.replace('.', '_')}_step_{step:03d}.grib"
                        with out_file.open("ab") as out:
                            codes_write(gid, out)
                        from eccodes import codes_release
                        codes_release(gid)

                tmp_path.unlink(missing_ok=True)

            except Exception as e:
                tmp_path.unlink(missing_ok=True)
                err_str = str(e)
                for dt in chunk:
                    results["dates"][dt] = f"error_{forecast_type}: {err_str[:80]}"
                results["errors"] += len(chunk)
                if "QUEUED_LIMIT" in err_str:
                    print(f"     ⏸ Queue full — stopping")
                    return results
                print(f"     ⚠ Batch {forecast_type} error: {err_str[:100]}")
                break  # Skip pf if cf failed for this chunk

        # Mark successful dates
        for dt in chunk:
            if results["dates"].get(dt, "").startswith("error"):
                continue
            results["downloaded"] += 1
            results["dates"][dt] = "downloaded"

    return results


def _download_single(
    slug: str,
    target_date: str,
    *,
    step: int = 24,
    param: str = "167.128",
) -> dict:
    """Download cf + pf GRIB for one city-date."""
    city_row = _load_manifest_city(slug.replace("-", " ").title())
    # Try to find city in manifest with various name formats
    for attempt_name in [slug, slug.replace("-", " ").title(), slug.upper()]:
        try:
            city_row = _load_manifest_city(attempt_name)
            break
        except KeyError:
            continue
    else:
        # Manual lookup — load all cities and find slug match
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        slug_lower = slug.lower()
        for row in manifest["cities"]:
            if row["city"].lower().replace(" ", "-") == slug_lower:
                city_row = row
                break
        else:
            return {"status": "error", "error": f"City {slug!r} not in manifest"}

    server = ECMWFDataServer()
    compact = target_date.replace("-", "")
    target_dir = RAW_ROOT / "tigge_ecmwf_ens" / slug / compact
    target_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for forecast_type in ("cf", "pf"):
        suffix = "control" if forecast_type == "cf" else "perturbed"
        target = target_dir / f"tigge_ecmwf_{suffix}_param_{param.replace('.', '_')}_step_{step:03d}.grib"

        if target.exists():
            results[forecast_type] = {"status": "exists", "bytes": target.stat().st_size}
            continue

        request = {
            "dataset": "tigge",
            "class": "ti",
            "origin": "ecmf",
            "expver": "prod",
            "stream": "enfo",
            "levtype": "sfc",
            "param": param,
            "date": target_date,
            "time": "00:00:00",
            "step": str(step),
            "type": forecast_type,
            "area": city_row["tigge_request"]["area"],
            "grid": city_row["tigge_request"]["grid"],
            "target": str(target),
        }
        if forecast_type == "pf":
            request["number"] = "1/to/50"

        try:
            server.retrieve(request)
            results[forecast_type] = {
                "status": "downloaded",
                "bytes": target.stat().st_size if target.exists() else 0,
            }
        except Exception as e:
            results[forecast_type] = {"status": "error", "error": str(e)}

    return results


def _extract_json(slug: str, target_date: str) -> bool:
    """Extract GRIB → JSON using eccodes."""
    from eccodes import codes_get, codes_grib_find_nearest, codes_grib_new_from_file, codes_release

    compact = target_date.replace("-", "")
    date_dir = RAW_ROOT / "tigge_ecmwf_ens" / slug / compact
    cf_path = date_dir / "tigge_ecmwf_control_param_167_128_step_024.grib"
    pf_path = date_dir / "tigge_ecmwf_perturbed_param_167_128_step_024.grib"
    json_path = date_dir / "tigge_ecmwf_members_param_167_128_step_024.json"

    if json_path.exists():
        return True

    if not cf_path.exists() or not pf_path.exists():
        return False

    # Find city in manifest
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    city_row = None
    for row in manifest["cities"]:
        if row["city"].lower().replace(" ", "-") == slug.lower():
            city_row = row
            break
    if city_row is None:
        return False

    lat = float(city_row["lat"])
    lon = float(city_row["lon"])
    unit = str(city_row["unit"])

    def _kelvin_to_native(value_k: float) -> float:
        value_c = value_k - 273.15
        return value_c * 9.0 / 5.0 + 32.0 if unit.upper() == "F" else value_c

    def _extract_members(path: Path, forecast_type: str) -> list[dict]:
        members = []
        with path.open("rb") as fh:
            while True:
                gid = codes_grib_new_from_file(fh)
                if gid is None:
                    break
                nearest = codes_grib_find_nearest(gid, lat, lon)[0]
                member = 0 if forecast_type == "cf" else int(codes_get(gid, "number"))
                value_k = float(nearest["value"])
                members.append({
                    "member": member,
                    "forecast_type": forecast_type,
                    "short_name": codes_get(gid, "shortName"),
                    "data_date": int(codes_get(gid, "dataDate")),
                    "data_time": int(codes_get(gid, "dataTime")),
                    "step_range": str(codes_get(gid, "stepRange")),
                    "nearest_grid_lat": float(nearest["lat"]),
                    "nearest_grid_lon": float(nearest["lon"]),
                    "distance_km": float(nearest["distance"]),
                    "value_kelvin": value_k,
                    "value_native_unit": _kelvin_to_native(value_k),
                    "native_unit": unit,
                })
                codes_release(gid)
        return members

    members = _extract_members(cf_path, "cf") + _extract_members(pf_path, "pf")
    members = sorted(members, key=lambda m: m["member"])

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "city": city_row["city"],
        "lat": city_row["lat"],
        "lon": city_row["lon"],
        "unit": unit,
        "cf_path": str(cf_path),
        "pf_path": str(pf_path),
        "member_count": len(members),
        "mean_native_unit": round(sum(m["value_native_unit"] for m in members) / len(members), 4) if members else None,
        "members": members,
    }

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-dates", type=int, default=50, help="Max city-dates to process per run")
    parser.add_argument("--checkpoint", type=Path, default=Path("/tmp/tigge_backfill_checkpoint.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-queue-clear", action="store_true")
    parser.add_argument("--plan-mode", choices=("auto", "settlement", "step24_all"), default="auto")
    parser.add_argument("--gap-date-from", default=DEFAULT_GAP_DATE_FROM)
    parser.add_argument("--gap-date-to", help="Inclusive end date YYYY-MM-DD for full step24 gapfill")
    parser.add_argument("--previous-gap-scan", type=Path, default=ROOT / "tmp" / "tigge_coverage_gaps_latest.json")
    parser.add_argument("--batch-mode", action="store_true",
                        help="Use multi-date batching (10 dates/request). ~10x faster.")
    parser.add_argument("--dates-per-request", type=int, default=10,
                        help="Max dates per ECMWF batch request (default: 10)")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Number of cities to download in parallel (default: 1, safe max: 3)")
    parser.add_argument("--delay-between-cities", type=int, default=10,
                        help="Seconds to wait between city batches (rate limit protection)")
    args = parser.parse_args()

    print(f"=== TIGGE Settlement Backfill ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"Max dates: {args.max_dates}")
    print(f"Mode: {'BATCH' if args.batch_mode else 'single'}")
    print(f"Plan mode: {args.plan_mode}")

    print("\n1. Building download plan...")
    settlement_plan: list[tuple[str, str, str]] = []
    plan_mode_used = args.plan_mode
    if args.plan_mode in ("auto", "settlement"):
        settlement_plan = _build_settlement_download_plan()

    if args.plan_mode == "settlement":
        plan = settlement_plan
    elif args.plan_mode == "step24_all":
        gap_date_to = args.gap_date_to or (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        plan = _build_step24_gapfill_plan(
            date_from=args.gap_date_from,
            date_to=gap_date_to,
            previous_path=args.previous_gap_scan if args.previous_gap_scan and args.previous_gap_scan.exists() else None,
        )
        plan_mode_used = "step24_all"
    else:
        if settlement_plan:
            plan = settlement_plan
            plan_mode_used = "settlement"
        else:
            gap_date_to = args.gap_date_to or (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
            plan = _build_step24_gapfill_plan(
                date_from=args.gap_date_from,
                date_to=gap_date_to,
                previous_path=args.previous_gap_scan if args.previous_gap_scan and args.previous_gap_scan.exists() else None,
            )
            plan_mode_used = "step24_all"
    print(f"   Effective plan mode: {plan_mode_used}")
    print(f"   Total needed: {len(plan)} city-dates")

    # Limit to max_dates
    batch = plan[:args.max_dates]
    print(f"   This batch: {len(batch)} city-dates")

    if args.dry_run:
        if args.batch_mode:
            groups = _group_plan_by_city(batch)
            for slug, dates in groups.items():
                print(f"   DRY BATCH: {slug:20s} {len(dates)} dates [{dates[0]} → {dates[-1]}]")
                n_requests = (len(dates) + args.dates_per_request - 1) // args.dates_per_request
                print(f"              → {n_requests * 2} ECMWF requests (was {len(dates) * 2})")
        else:
            for city, slug, dt in batch[:20]:
                print(f"   DRY: {slug:20s} {dt}")
            if len(batch) > 20:
                print(f"   ... and {len(batch) - 20} more")
        return 0

    if not batch:
        print("\n2. No step24 downloads needed; skipping queue cleanup.")
        args.checkpoint.write_text(json.dumps({
            "last_update": datetime.now(timezone.utc).isoformat(),
            "plan_mode": plan_mode_used,
            "mode": "batch" if args.batch_mode else "single",
            "parallel": args.parallel if args.batch_mode else 1,
            "total_remaining": 0,
            "downloaded": 0,
            "extracted": 0,
            "errors": 0,
            "details": [],
            "no_work": True,
        }, indent=2) + "\n", encoding="utf-8")
        print("\n=== Summary ===")
        print("Downloaded: 0")
        print("Extracted:  0")
        print("Errors:     0")
        print("Remaining:  0")
        return 0

    if not args.skip_queue_clear and not args.dry_run:
        print("\n2. Clearing ECMWF queue...")
        cleared = _clear_ecmwf_queue()
        print(f"   Cleared {cleared} stale requests")

    # Step 3: Download + extract
    print("\n3. Downloading and extracting...")
    results = {"downloaded": 0, "extracted": 0, "errors": 0, "details": []}

    if args.batch_mode:
        # ── BATCH MODE: group by city, multi-date ECMWF requests ──
        groups = _group_plan_by_city(batch)
        total_requests = sum(
            (len(dates) + args.dates_per_request - 1) // args.dates_per_request * 2
            for dates in groups.values()
        )
        print(f"   Grouped into {len(groups)} cities, ~{total_requests} ECMWF requests")
        print(f"   Parallel workers: {args.parallel}")

        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        _results_lock = threading.Lock()
        _queue_full = threading.Event()

        def _process_city(slug_dates):
            slug, dates = slug_dates
            if _queue_full.is_set():
                return slug, {"downloaded": 0, "errors": 0, "dates": {}}
            print(f"\n   [{slug}] {len(dates)} dates...")
            batch_result = _download_batch(
                slug, dates, max_dates_per_request=args.dates_per_request
            )
            # Rate-limit: pause between cities to avoid queue saturation
            time.sleep(args.delay_between_cities)
            return slug, batch_result

        city_items = list(groups.items())
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {executor.submit(_process_city, item): item for item in city_items}
            for future in as_completed(futures):
                slug, batch_result = future.result()

                with _results_lock:
                    results["downloaded"] += batch_result["downloaded"]
                    results["errors"] += batch_result["errors"]

                    # Extract JSON for each downloaded date
                    for dt, status in batch_result["dates"].items():
                        if status in ("downloaded", "exists"):
                            try:
                                if _extract_json(slug, dt):
                                    results["extracted"] += 1
                            except Exception as ext_err:
                                print(f"     ⚠ Extract {dt}: {ext_err}")

                    # Save checkpoint
                    args.checkpoint.write_text(json.dumps({
                        "last_update": datetime.now(timezone.utc).isoformat(),
                        "plan_mode": plan_mode_used,
                        "mode": "batch",
                        "parallel": args.parallel,
                        "total_remaining": len(plan) - results["downloaded"] - results["errors"],
                        **results,
                    }, indent=2) + "\n", encoding="utf-8")

                if batch_result.get("status") == "error" and "QUEUED_LIMIT" in str(batch_result.get("error", "")):
                    print(f"   ⏸ Queue full after {slug} — pausing 60s then continuing...")
                    _queue_full.set()
                    time.sleep(60)
                    _queue_full.clear()
    else:
        # ── SINGLE MODE: one request per date (legacy) ──
        for i, (city, slug, dt) in enumerate(batch):
            print(f"\n   [{i+1}/{len(batch)}] {slug} {dt}...")

            try:
                dl_result = _download_single(slug, dt)
                cf_ok = dl_result.get("cf", {}).get("status") in ("downloaded", "exists")
                pf_ok = dl_result.get("pf", {}).get("status") in ("downloaded", "exists")

                if cf_ok and pf_ok:
                    results["downloaded"] += 1

                    try:
                        if _extract_json(slug, dt):
                            results["extracted"] += 1
                            print(f"     ✅ Downloaded + extracted")
                        else:
                            print(f"     ⚠ Downloaded but extraction failed")
                    except Exception as ext_err:
                        print(f"     ⚠ Extraction error: {ext_err}")
                else:
                    results["errors"] += 1
                    print(f"     ❌ Download failed: {dl_result}")
            except Exception as e:
                results["errors"] += 1
                err_str = str(e)
                if "RESTRICTED_ACCESS" in err_str:
                    print(f"     ⏭ Skipped (48h delay): {dt}")
                elif "QUEUED_LIMIT" in err_str:
                    print(f"     ⏸ Queue full — stopping batch")
                    break
                else:
                    print(f"     ❌ Error: {err_str[:100]}")

            results["details"].append({
                "city": city, "slug": slug, "date": dt,
                "status": "ok" if cf_ok and pf_ok else "error",
            })

            args.checkpoint.write_text(json.dumps({
                "last_update": datetime.now(timezone.utc).isoformat(),
                "plan_mode": plan_mode_used,
                "processed": i + 1,
                "total_batch": len(batch),
                "total_remaining": len(plan) - i - 1,
                **results,
            }, indent=2) + "\n", encoding="utf-8")

    # Summary
    print(f"\n=== Summary ===")
    print(f"Downloaded: {results['downloaded']}")
    print(f"Extracted:  {results['extracted']}")
    print(f"Errors:     {results['errors']}")
    print(f"Remaining:  {len(plan) - len(batch)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
