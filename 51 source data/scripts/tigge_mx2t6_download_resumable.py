#!/usr/bin/env python3
"""Resumable TIGGE mx2t6 regional downloader with checkpoint/status output."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from tigge_download_ecmwf_ens_region_multistep import download_region_multistep
from tigge_local_calendar_day_common import required_max_step_for_lead_horizon
from tigge_regions import region_for_city

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
DEFAULT_STATUS = ROOT / "tmp" / "tigge_mx2t6_download_status.json"
DEFAULT_RAW_ROOT = ROOT / "raw"
DEFAULT_REGION_SUBDIR = "tigge_ecmwf_ens_regions_mx2t6"
DEFAULT_MAX_TARGET_LEAD_DAY = 7
STEP_HOURS = 6


@dataclass(frozen=True)
class RegionWindowTask:
    region: str
    date_from: date
    date_to: date

    @property
    def key(self) -> str:
        return f"{self.region}:{self.date_from.isoformat()}:{self.date_to.isoformat()}"

    @property
    def dates(self) -> list[date]:
        out: list[date] = []
        current = self.date_from
        while current <= self.date_to:
            out.append(current)
            current += timedelta(days=1)
        return out

    @property
    def compact(self) -> str:
        start_compact = self.date_from.strftime("%Y%m%d")
        end_compact = self.date_to.strftime("%Y%m%d")
        return start_compact if start_compact == end_compact else f"{start_compact}_{end_compact}"


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_region_city_map(manifest: dict, selected_cities: set[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for row in manifest["cities"]:
        city = str(row["city"])
        if city not in selected_cities:
            continue
        region = region_for_city(lat=float(row["lat"]), lon=float(row["lon"])).name
        groups.setdefault(region, []).append(city)
    return groups


def _selected_manifest_rows(manifest: dict, selected_cities: set[str]) -> list[dict]:
    return [row for row in manifest["cities"] if str(row["city"]) in selected_cities]


def _windowed_dates(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    current = start
    step = timedelta(days=max_days - 1)
    while current <= end:
        window_end = min(end, current + step)
        windows.append((current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def _steps_for_task(*, task: RegionWindowTask, manifest_rows: list[dict], max_target_lead_day: int) -> list[int]:
    max_step = STEP_HOURS
    for row in manifest_rows:
        timezone_name = str(row["timezone"])
        for issue_date_utc in task.dates:
            max_step = max(
                max_step,
                required_max_step_for_lead_horizon(
                    timezone_name=timezone_name,
                    issue_date_utc=issue_date_utc,
                    max_target_lead_day=max_target_lead_day,
                ),
            )
    return list(range(STEP_HOURS, max_step + 1, STEP_HOURS))


def _grid_suffix(grid: str) -> str:
    if grid == "0.5/0.5":
        return ""
    return "_grid" + grid.replace(".", "p").replace("/", "x")


def _target_paths(
    task: RegionWindowTask,
    *,
    raw_root: Path,
    region_subdir: str,
    param: str,
    steps: list[int],
    cycle: str = "00",
    grid: str = "0.5/0.5",
) -> list[Path]:
    step_slug = "-".join(f"{step:03d}" for step in steps)
    cycle_suffix = f"_cycle{cycle}z" if cycle != "00" else ""
    date_compact_cycle = task.compact + cycle_suffix + _grid_suffix(grid)
    base_dir = raw_root / region_subdir / task.region / date_compact_cycle
    param_slug = param.replace(".", "_")
    return [
        base_dir / f"tigge_ecmwf_control_param_{param_slug}_steps_{step_slug}.grib",
        base_dir / f"tigge_ecmwf_perturbed_param_{param_slug}_steps_{step_slug}.grib",
    ]


def _is_complete(
    task: RegionWindowTask,
    *,
    raw_root: Path,
    region_subdir: str,
    param: str,
    steps: list[int],
    cycle: str = "00",
    grid: str = "0.5/0.5",
) -> bool:
    for path in _target_paths(task, raw_root=raw_root, region_subdir=region_subdir, param=param, steps=steps, cycle=cycle, grid=grid):
        if not path.exists():
            return False
        if path.stat().st_size <= 0:
            return False
        ok_marker = Path(str(path) + ".ok")
        if not ok_marker.exists():
            return False
    return True


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eta_seconds(start_time: float, completed: int, remaining: int) -> float | None:
    if completed <= 0 or remaining <= 0:
        return None
    elapsed = max(1.0, time.time() - start_time)
    per_task = elapsed / float(completed)
    return per_task * float(remaining)


def _write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_downloads(
    tasks: Iterable[RegionWindowTask],
    *,
    task_steps: dict[str, list[int]],
    param: str,
    raw_root: Path,
    region_subdir: str,
    max_workers: int,
    cycle: str = "00",
    grid: str = "0.5/0.5",
    progress_hook: Callable[[dict], None] | None = None,
    heartbeat_seconds: int = 300,
    active_stall_seconds: int = 21600,
) -> tuple[list[dict], list[dict]]:
    task_list = list(tasks)
    if not task_list:
        return [], []

    successes: list[dict] = []
    errors: list[dict] = []
    futures: dict[Future, RegionWindowTask] = {}
    future_started_at: dict[Future, float] = {}
    last_heartbeat_at = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for task in task_list:
            steps = task_steps[task.key]
            future = executor.submit(
                download_region_multistep,
                task.region,
                dates=task.dates,
                steps=steps,
                param=param,
                raw_root=raw_root,
                region_subdir=region_subdir,
                overwrite=False,
                dry_run=False,
                cycle=cycle,
                grid=grid,
            )
            futures[future] = task
            future_started_at[future] = time.time()

        while futures:
            done, _ = wait(list(futures.keys()), timeout=max(1, heartbeat_seconds), return_when=FIRST_COMPLETED)
            if not done:
                now = time.time()
                active_rows = []
                queued_count = 0
                for future, task in futures.items():
                    if not future.running():
                        queued_count += 1
                        continue
                    age = now - future_started_at.get(future, now)
                    active_rows.append(
                        {
                            "task": task.key,
                            "region": task.region,
                            "date_from": task.date_from.isoformat(),
                            "date_to": task.date_to.isoformat(),
                            "age_seconds": age,
                            "age_exceeds_stall_threshold": age >= active_stall_seconds,
                        }
                    )
                if progress_hook is not None:
                    progress_hook(
                        {
                            "result": "heartbeat",
                            "success_count": len(successes),
                            "error_count": len(errors),
                            "futures_remaining": len(futures),
                            "active_tasks": active_rows,
                            "queued_futures": queued_count,
                            "active_stall_seconds": active_stall_seconds,
                            "heartbeat_seconds": heartbeat_seconds,
                        }
                    )
                last_heartbeat_at = now
                continue
            for future in done:
                task = futures.pop(future)
                future_started_at.pop(future, None)
                steps = task_steps[task.key]
                try:
                    summary = future.result()
                    result_row = {
                        "task": task.key,
                        "region": task.region,
                        "date_from": task.date_from.isoformat(),
                        "date_to": task.date_to.isoformat(),
                        "steps": steps,
                        "summary": summary,
                    }
                    successes.append(result_row)
                    event = {
                        "task": task.key,
                        "region": task.region,
                        "date_from": task.date_from.isoformat(),
                        "date_to": task.date_to.isoformat(),
                        "steps": steps,
                        "result": "success",
                        "task_complete": _is_complete(
                            task,
                            raw_root=raw_root,
                            region_subdir=region_subdir,
                            param=param,
                            steps=steps,
                            cycle=cycle,
                            grid=grid,
                        ),
                        "success_count": len(successes),
                        "error_count": len(errors),
                        "futures_remaining": len(futures),
                    }
                except Exception as exc:  # noqa: BLE001
                    result_row = {
                        "task": task.key,
                        "region": task.region,
                        "date_from": task.date_from.isoformat(),
                        "date_to": task.date_to.isoformat(),
                        "steps": steps,
                        "error": repr(exc),
                    }
                    errors.append(result_row)
                    event = {
                        "task": task.key,
                        "region": task.region,
                        "date_from": task.date_from.isoformat(),
                        "date_to": task.date_to.isoformat(),
                        "steps": steps,
                        "result": "error",
                        "error": repr(exc),
                        "task_complete": _is_complete(
                            task,
                            raw_root=raw_root,
                            region_subdir=region_subdir,
                            param=param,
                            steps=steps,
                            cycle=cycle,
                            grid=grid,
                        ),
                        "success_count": len(successes),
                        "error_count": len(errors),
                        "futures_remaining": len(futures),
                    }
                if progress_hook is not None:
                    progress_hook(event)
    return successes, errors


def run(
    *,
    manifest_path: Path,
    status_path: Path,
    date_from: date,
    date_to: date,
    steps: list[int] | None,
    param: str,
    raw_root: Path,
    region_subdir: str,
    max_batch_days: int,
    max_workers: int,
    max_passes: int,
    retry_stall_limit: int,
    sleep_seconds: int,
    max_target_lead_day: int,
    cities: list[str] | None,
    dry_run: bool,
    cycle: str = "00",
    grid: str = "0.5/0.5",
) -> int:
    if date_to < date_from:
        raise ValueError("date_to must be >= date_from")
    if max_batch_days < 1:
        raise ValueError("max_batch_days must be >= 1")
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if cycle not in ("00", "12"):
        raise ValueError(f"cycle must be '00' or '12', got {cycle!r}")
    if "/" not in grid:
        raise ValueError(f"grid must be a MARS grid string like '0.25/0.25', got {grid!r}")

    # Namespace status_path by cycle/grid so 00z/12z and 0.5/0.25 runs don't clobber each other.
    status_suffix = ""
    if cycle != "00":
        status_suffix += f"_cycle{cycle}z"
    if grid != "0.5/0.5":
        status_suffix += _grid_suffix(grid)
    if status_suffix:
        status_path = status_path.with_name(
            status_path.stem + status_suffix + status_path.suffix
        )

    manifest = _load_manifest(manifest_path)
    manifest_cities = [str(row["city"]) for row in manifest["cities"]]
    selected_cities = set(cities or manifest_cities)
    manifest_rows = _selected_manifest_rows(manifest, selected_cities)
    unknown = sorted(selected_cities - set(manifest_cities))
    if unknown:
        raise SystemExit(f"Unknown cities in manifest: {', '.join(unknown)}")

    region_city_map = _build_region_city_map(manifest, selected_cities)
    if not region_city_map:
        raise SystemExit("No cities selected for download.")

    windows = _windowed_dates(date_from, date_to, max_batch_days)
    all_tasks = [
        RegionWindowTask(region=region, date_from=window_start, date_to=window_end)
        for window_start, window_end in windows
        for region in sorted(region_city_map)
    ]
    task_steps = {
        task.key: list(steps) if steps is not None else _steps_for_task(
            task=task,
            manifest_rows=manifest_rows,
            max_target_lead_day=max_target_lead_day,
        )
        for task in all_tasks
    }
    all_step_values = sorted({step for task_step_list in task_steps.values() for step in task_step_list})
    computed_global_max_step = max(all_step_values) if all_step_values else None
    steps_mode = "fixed" if steps is not None else "dynamic_by_task"
    start_time = time.time()
    pass_count = 0
    total_tasks = len(all_tasks)
    stall_passes = 0
    pass_history: list[dict] = []
    last_errors: list[dict] = []

    if dry_run:
        payload = {
            "generated_at": _iso_now(),
            "status": "dry_run",
            "manifest_path": str(manifest_path),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "steps": steps,
            "steps_mode": steps_mode,
            "max_target_lead_day": max_target_lead_day,
            "computed_global_steps": all_step_values,
            "computed_global_max_step": computed_global_max_step,
            "param": param,
            "grid": grid,
            "raw_root": str(raw_root),
            "region_subdir": region_subdir,
            "max_batch_days": max_batch_days,
            "max_workers": max_workers,
            "total_tasks": total_tasks,
            "regions": region_city_map,
        }
        _write_status(status_path, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    while pass_count < max_passes:
        pass_count += 1
        missing = [
            task
            for task in all_tasks
            if not _is_complete(
                task,
                raw_root=raw_root,
                region_subdir=region_subdir,
                param=param,
                steps=task_steps[task.key],
                cycle=cycle,
                grid=grid,
            )
        ]
        completed = total_tasks - len(missing)
        eta = _eta_seconds(start_time, completed, len(missing))
        if not missing:
            payload = {
                "generated_at": _iso_now(),
                "status": "complete",
                "pass_count": pass_count - 1,
                "manifest_path": str(manifest_path),
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "steps": steps,
                "steps_mode": steps_mode,
                "max_target_lead_day": max_target_lead_day,
                "computed_global_steps": all_step_values,
                "computed_global_max_step": computed_global_max_step,
                "param": param,
                "grid": grid,
                "raw_root": str(raw_root),
                "region_subdir": region_subdir,
                "max_batch_days": max_batch_days,
                "max_workers": max_workers,
                "total_tasks": total_tasks,
                "completed_tasks": completed,
                "missing_tasks": 0,
                "eta_seconds": 0,
                "pass_history": pass_history,
                "last_errors": last_errors[-20:],
                "regions": region_city_map,
            }
            _write_status(status_path, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        prepass_payload = {
            "generated_at": _iso_now(),
            "status": "running",
            "pass_count": pass_count,
            "manifest_path": str(manifest_path),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "steps": steps,
            "steps_mode": steps_mode,
            "max_target_lead_day": max_target_lead_day,
            "computed_global_steps": all_step_values,
            "computed_global_max_step": computed_global_max_step,
            "param": param,
            "grid": grid,
            "raw_root": str(raw_root),
            "region_subdir": region_subdir,
            "max_batch_days": max_batch_days,
            "max_workers": max_workers,
            "total_tasks": total_tasks,
            "completed_tasks": completed,
            "missing_tasks": len(missing),
            "stall_passes": stall_passes,
            "eta_seconds": eta,
            "last_pass": {
                "pass": pass_count,
                "timestamp": _iso_now(),
                "phase": "downloading",
                "missing_before": len(missing),
                "completed_before": completed,
            },
            "pass_history": pass_history[-40:],
            "last_errors": last_errors[-20:],
            "regions": region_city_map,
        }
        _write_status(status_path, prepass_payload)
        print(
            json.dumps(
                {
                    "event": "pass_start",
                    "pass": pass_count,
                    "missing_before": len(missing),
                    "completed_before": completed,
                    "eta_seconds": eta,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        live_completed = completed

        def _progress_update(event: dict) -> None:
            nonlocal live_completed
            if event.get("task_complete"):
                live_completed += 1
            live_missing = max(0, total_tasks - live_completed)
            progress_errors = last_errors[-20:]
            if event.get("result") == "error":
                progress_errors = (progress_errors + [{"task": event["task"], "error": event["error"]}])[-20:]
            active_tasks = event.get("active_tasks") if isinstance(event.get("active_tasks"), list) else []
            active_stalled = sum(1 for row in active_tasks if row.get("age_exceeds_stall_threshold"))
            progress_payload = {
                "generated_at": _iso_now(),
                "status": "running",
                "pass_count": pass_count,
                "manifest_path": str(manifest_path),
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "steps": steps,
                "steps_mode": steps_mode,
                "max_target_lead_day": max_target_lead_day,
                "computed_global_steps": all_step_values,
                "computed_global_max_step": computed_global_max_step,
                "param": param,
                "grid": grid,
                "raw_root": str(raw_root),
                "region_subdir": region_subdir,
                "max_batch_days": max_batch_days,
                "max_workers": max_workers,
                "total_tasks": total_tasks,
                "completed_tasks": live_completed,
                "missing_tasks": live_missing,
                "stall_passes": stall_passes,
                "eta_seconds": _eta_seconds(start_time, live_completed, live_missing),
                "last_pass": {
                    "pass": pass_count,
                    "timestamp": _iso_now(),
                    "phase": "downloading",
                    "missing_before": len(missing),
                    "completed_before": completed,
                    "completed_live": live_completed,
                    "missing_live": live_missing,
                    "success_count": event["success_count"],
                    "error_count": event["error_count"],
                    "futures_remaining": event["futures_remaining"],
                    "current_task": event.get("task"),
                    "current_result": event["result"],
                    "current_task_complete": event.get("task_complete"),
                    "active_tasks": active_tasks,
                    "active_stalled": active_stalled,
                    "queued_futures": event.get("queued_futures"),
                    "active_stall_seconds": event.get("active_stall_seconds"),
                    "heartbeat_seconds": event.get("heartbeat_seconds"),
                },
                "pass_history": pass_history[-40:],
                "last_errors": progress_errors,
                "regions": region_city_map,
            }
            _write_status(status_path, progress_payload)

        successes, errors = _run_downloads(
            missing,
            task_steps=task_steps,
            param=param,
            raw_root=raw_root,
            region_subdir=region_subdir,
            max_workers=max_workers,
            cycle=cycle,
            grid=grid,
            progress_hook=_progress_update,
            heartbeat_seconds=max(60, sleep_seconds),
            active_stall_seconds=10800,
        )

        post_missing = [
            task
            for task in all_tasks
            if not _is_complete(
                task,
                raw_root=raw_root,
                region_subdir=region_subdir,
                param=param,
                steps=task_steps[task.key],
                cycle=cycle,
                grid=grid,
            )
        ]
        post_completed = total_tasks - len(post_missing)
        progress = post_completed - completed
        if progress > 0:
            stall_passes = 0
        else:
            stall_passes += 1
        last_errors.extend(errors)

        pass_row = {
            "pass": pass_count,
            "timestamp": _iso_now(),
            "missing_before": len(missing),
            "completed_before": completed,
            "completed_after": post_completed,
            "progress_tasks": progress,
            "success_count": len(successes),
            "error_count": len(errors),
            "stall_passes": stall_passes,
            "eta_seconds": _eta_seconds(start_time, post_completed, len(post_missing)),
        }
        pass_history.append(pass_row)
        payload = {
            "generated_at": _iso_now(),
            "status": "running",
            "pass_count": pass_count,
            "manifest_path": str(manifest_path),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "steps": steps,
            "steps_mode": steps_mode,
            "max_target_lead_day": max_target_lead_day,
            "computed_global_steps": all_step_values,
            "computed_global_max_step": computed_global_max_step,
            "param": param,
            "grid": grid,
            "raw_root": str(raw_root),
            "region_subdir": region_subdir,
            "max_batch_days": max_batch_days,
            "max_workers": max_workers,
            "total_tasks": total_tasks,
            "completed_tasks": post_completed,
            "missing_tasks": len(post_missing),
            "stall_passes": stall_passes,
            "eta_seconds": pass_row["eta_seconds"],
            "last_pass": pass_row,
            "pass_history": pass_history[-40:],
            "last_errors": last_errors[-20:],
            "regions": region_city_map,
        }
        _write_status(status_path, payload)
        print(json.dumps(payload["last_pass"], ensure_ascii=False), flush=True)

        if stall_passes >= retry_stall_limit:
            payload["status"] = "failed_stalled"
            _write_status(status_path, payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 3
        if len(post_missing) == 0:
            continue
        time.sleep(max(1, sleep_seconds))

    payload = {
        "generated_at": _iso_now(),
        "status": "failed_max_passes",
        "pass_count": pass_count,
        "manifest_path": str(manifest_path),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "steps": steps,
        "steps_mode": steps_mode,
        "max_target_lead_day": max_target_lead_day,
        "computed_global_steps": all_step_values,
        "computed_global_max_step": computed_global_max_step,
        "param": param,
        "grid": grid,
        "raw_root": str(raw_root),
        "region_subdir": region_subdir,
        "max_batch_days": max_batch_days,
        "max_workers": max_workers,
        "total_tasks": total_tasks,
        "last_errors": last_errors[-20:],
        "pass_history": pass_history[-40:],
        "regions": region_city_map,
    }
    _write_status(status_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--date-from", default="2024-01-01")
    parser.add_argument("--date-to", default=(date.today() - timedelta(days=2)).isoformat())
    parser.add_argument("--steps", nargs="+", type=int, default=None)
    parser.add_argument("--param", default="121.128")
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--region-subdir", default=DEFAULT_REGION_SUBDIR)
    parser.add_argument("--max-batch-days", type=int, default=3)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--max-passes", type=int, default=2000)
    parser.add_argument("--retry-stall-limit", type=int, default=60)
    parser.add_argument("--sleep-seconds", type=int, default=180)
    parser.add_argument("--max-target-lead-day", type=int, default=DEFAULT_MAX_TARGET_LEAD_DAY)
    parser.add_argument("--cities", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cycle", choices=["00", "12"], default="00",
                        help="TIGGE model cycle: '00' (00Z, default) or '12' (12Z)")
    parser.add_argument("--grid", default="0.5/0.5",
                        help="MARS output grid, e.g. 0.5/0.5 legacy or 0.25/0.25 to match OpenData.")
    args = parser.parse_args()

    return run(
        manifest_path=args.manifest_path,
        status_path=args.status_path,
        date_from=date.fromisoformat(args.date_from),
        date_to=date.fromisoformat(args.date_to),
        steps=list(args.steps) if args.steps is not None else None,
        param=args.param,
        raw_root=args.raw_root,
        region_subdir=args.region_subdir,
        max_batch_days=args.max_batch_days,
        max_workers=args.max_workers,
        max_passes=args.max_passes,
        retry_stall_limit=args.retry_stall_limit,
        sleep_seconds=args.sleep_seconds,
        max_target_lead_day=args.max_target_lead_day,
        cities=args.cities,
        dry_run=args.dry_run,
        cycle=args.cycle,
        grid=args.grid,
    )


if __name__ == "__main__":
    raise SystemExit(main())
