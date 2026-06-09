#!/usr/bin/env python3
"""Download one regional TIGGE control/perturbed file for multiple dates/steps.

Backend selection: ZEUS_TIGGE_API_BACKEND env var (default: "ecds").
  ecds  — uses cdsapi.Client + https://ecds.ecmwf.int/api + ~/.cdsapirc
  ecmwf — uses ecmwfapi.ECMWFDataServer (legacy WEB-API) + ~/.ecmwfapirc
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from eccodes import codes_grib_new_from_file, codes_release

from tigge_regions import REGIONS, TiggeRegion

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "raw"
QUEUE_RETRY_LIMIT = int(os.environ.get("TIGGE_QUEUE_RETRY_LIMIT", "60"))
QUEUE_RETRY_SLEEP_SECONDS = int(os.environ.get("TIGGE_QUEUE_RETRY_SLEEP_SECONDS", "120"))

# Backend: "ecds" (default) or "ecmwf" (legacy rollback).
_BACKEND: str = os.environ.get("ZEUS_TIGGE_API_BACKEND", "ecds")
_ECDS_URL = "https://ecds.ecmwf.int/api"


def _ok_marker_path(target: Path) -> Path:
    return Path(str(target) + ".ok")


def _expected_field_count(*, forecast_type: str, steps: list[int], dates: list[date]) -> int:
    members = 1 if forecast_type == "cf" else 50
    return len(steps) * len(dates) * members


def _validate_downloaded_grib(target: Path, *, forecast_type: str, steps: list[int], dates: list[date]) -> None:
    if not target.exists() or target.stat().st_size <= 0:
        raise ValueError(f"missing_or_empty_output: {target}")

    expected = _expected_field_count(forecast_type=forecast_type, steps=steps, dates=dates)
    count = 0
    with target.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            count += 1
            codes_release(gid)
    if count != expected:
        raise ValueError(f"field_count_mismatch target={target} expected={expected} actual={count}")


def _region_by_name(name: str) -> TiggeRegion:
    for region in REGIONS:
        if region.name == name:
            return region
    raise KeyError(f"Unknown TIGGE region {name!r}")


def _steps_arg(steps: list[int]) -> str:
    return "/".join(str(step) for step in steps)


def _dates_arg(dates: list[date]) -> str:
    return "/".join(day.isoformat() for day in dates)


def _grid_suffix(grid: str) -> str:
    if grid == "0.5/0.5":
        return ""
    return "_grid" + grid.replace(".", "p").replace("/", "x")


def _request(
    region: TiggeRegion,
    dates: list[date],
    steps: list[int],
    param: str,
    target: Path,
    forecast_type: str,
    time_utc: str = "00:00:00",
    grid: str = "0.5/0.5",
) -> dict:
    request = {
        "dataset": "tigge",
        "class": "ti",
        "origin": "ecmf",
        "expver": "prod",
        "stream": "enfo",
        "levtype": "sfc",
        "param": param,
        "date": _dates_arg(dates),
        "time": time_utc,
        "step": _steps_arg(steps),
        "type": forecast_type,
        "area": region.area,
        "grid": grid,
        "target": str(target),
    }
    if forecast_type == "pf":
        request["number"] = "1/to/50"
    return request


def _make_retriever():
    """Return a callable ``retrieve(request, target)`` for the active backend.

    ECDS backend:  ``cdsapi.Client.retrieve("tigge-forecasts", request, target)``
    WEB-API backend: ``ECMWFDataServer().retrieve(request)``  (target is in request dict)

    The returned callable signature is always ``(request: dict, target: Path) -> None``.
    """
    if _BACKEND == "ecmwf":
        from ecmwfapi import ECMWFDataServer
        server = ECMWFDataServer()

        def _ecmwf_retrieve(request: dict, target: Path) -> None:
            # WEB-API expects target inside the request dict (already set by _request()).
            server.retrieve(request)

        return _ecmwf_retrieve
    else:
        import cdsapi
        client = cdsapi.Client(url=_ECDS_URL)

        def _ecds_retrieve(request: dict, target: Path) -> None:
            # CDS-API: dataset name is the first positional arg; target is a separate path.
            # Remove "target" from request dict (WEB-API field not used by CDS-API).
            cds_request = {k: v for k, v in request.items() if k not in ("dataset", "target")}
            client.retrieve("tigge-forecasts", cds_request, str(target))

        return _ecds_retrieve


def download_region_multistep(
    region_name: str,
    *,
    dates: list[date],
    steps: list[int],
    param: str,
    raw_root: Path,
    region_subdir: str = "tigge_ecmwf_ens_regions",
    overwrite: bool = False,
    dry_run: bool = False,
    cycle: str = "00",
    grid: str = "0.5/0.5",
) -> dict:
    if cycle not in ("00", "12"):
        raise ValueError(f"cycle must be '00' or '12', got {cycle!r}")
    time_utc = f"{cycle}:00:00"
    region = _region_by_name(region_name)
    retrieve = _make_retriever()
    if not dates:
        raise ValueError("dates must not be empty")
    date_str = _dates_arg(dates)
    start_compact = dates[0].strftime("%Y%m%d")
    end_compact = dates[-1].strftime("%Y%m%d")
    date_compact = start_compact if start_compact == end_compact else f"{start_compact}_{end_compact}"
    step_slug = "-".join(f"{step:03d}" for step in steps)
    # Cycle-namespace the output directory so 00z and 12z files don't collide.
    # Default (cycle="00") uses the legacy path for backward compatibility.
    cycle_suffix = f"_cycle{cycle}z" if cycle != "00" else ""
    date_compact_cycle = date_compact + cycle_suffix + _grid_suffix(grid)
    target_dir = raw_root / region_subdir / region.name / date_compact_cycle
    target_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for forecast_type in ("cf", "pf"):
        suffix = "control" if forecast_type == "cf" else "perturbed"
        target = target_dir / f"tigge_ecmwf_{suffix}_param_{param.replace('.', '_')}_steps_{step_slug}.grib"
        ok_marker = _ok_marker_path(target)
        request = _request(region, dates, steps, param, target, forecast_type, time_utc=time_utc, grid=grid)
        if target.exists() and target.stat().st_size > 0 and not overwrite:
            if ok_marker.exists():
                results.append(
                    {
                        "region": region.name,
                        "date": date_str,
                        "steps": steps,
                        "type": forecast_type,
                        "target": str(target),
                        "status": "skipped_exists",
                    }
                )
                continue
            try:
                _validate_downloaded_grib(target, forecast_type=forecast_type, steps=steps, dates=dates)
                ok_marker.touch()
                results.append(
                    {
                        "region": region.name,
                        "date": date_str,
                        "steps": steps,
                        "type": forecast_type,
                        "target": str(target),
                        "status": "skipped_validated_exists",
                    }
                )
                continue
            except Exception:
                target.unlink(missing_ok=True)
                ok_marker.unlink(missing_ok=True)
        if dry_run:
            results.append(
                {
                    "region": region.name,
                    "date": date_str,
                    "steps": steps,
                    "type": forecast_type,
                    "target": str(target),
                    "status": "dry_run",
                    "request": request,
                }
            )
            continue
        print(
            json.dumps(
                {
                    "event": "request_start",
                    "region": region.name,
                    "type": forecast_type,
                    "date": date_str,
                    "steps": steps,
                    "target": str(target),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        attempt = 0
        while True:
            try:
                retrieve(request, target)
                _validate_downloaded_grib(target, forecast_type=forecast_type, steps=steps, dates=dates)
                ok_marker.touch()
                break
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if "USER_QUEUED_LIMIT_EXCEEDED" in message and attempt < QUEUE_RETRY_LIMIT:
                    attempt += 1
                    print(
                        json.dumps(
                            {
                                "event": "queue_retry",
                                "region": region.name,
                                "type": forecast_type,
                                "date": date_str,
                                "attempt": attempt,
                                "sleep_seconds": QUEUE_RETRY_SLEEP_SECONDS,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    time.sleep(QUEUE_RETRY_SLEEP_SECONDS)
                    continue
                # Clean up partial file before retry or re-raise.
                if target.exists():
                    target.unlink()
                if ok_marker.exists():
                    ok_marker.unlink()
                if attempt < QUEUE_RETRY_LIMIT:
                    attempt += 1
                    print(
                        json.dumps(
                            {
                                "event": "file_retry",
                                "region": region.name,
                                "type": forecast_type,
                                "date": date_str,
                                "attempt": attempt,
                                "sleep_seconds": QUEUE_RETRY_SLEEP_SECONDS,
                                "reason": repr(exc),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    time.sleep(QUEUE_RETRY_SLEEP_SECONDS)
                    continue
                raise
        print(
            json.dumps(
                {
                    "event": "request_done",
                    "region": region.name,
                    "type": forecast_type,
                    "date": date_str,
                    "bytes": target.stat().st_size if target.exists() else 0,
                    "attempts": attempt + 1,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        results.append(
            {
                "region": region.name,
                "date": date_str,
                "steps": steps,
                "type": forecast_type,
                "target": str(target),
                "bytes": target.stat().st_size if target.exists() else 0,
                "status": "downloaded",
                "attempts": attempt + 1,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "region": region.name,
        "area": region.area,
        "date": date_str,
        "steps": steps,
        "param": param,
        "grid": grid,
        "raw_root": str(raw_root),
        "region_subdir": region_subdir,
        "dry_run": dry_run,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("region")
    parser.add_argument("--date")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--steps", nargs="+", type=int, required=True)
    parser.add_argument("--param", default="167.128")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--region-subdir", default="tigge_ecmwf_ens_regions")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cycle", choices=["00", "12"], default="00",
                        help="TIGGE model cycle: '00' (00Z, default) or '12' (12Z)")
    parser.add_argument("--grid", default="0.5/0.5",
                        help="MARS output grid, e.g. 0.5/0.5 legacy or 0.25/0.25 to match OpenData.")
    args = parser.parse_args()

    if args.date:
        dates = [date.fromisoformat(args.date)]
    else:
        if not args.date_from or not args.date_to:
            raise SystemExit("Provide either --date or both --date-from and --date-to")
        current = date.fromisoformat(args.date_from)
        end = date.fromisoformat(args.date_to)
        dates = []
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)

    summary = download_region_multistep(
        args.region,
        dates=dates,
        steps=args.steps,
        param=args.param,
        raw_root=args.raw_root,
        region_subdir=args.region_subdir,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        cycle=args.cycle,
        grid=args.grid,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
