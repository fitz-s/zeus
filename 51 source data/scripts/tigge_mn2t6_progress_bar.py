#!/usr/bin/env python3
"""Terminal progress monitor for mn2t6 regional GRIB download."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TRACK_RE = re.compile(r"tigge_ecmwf_ens_regions_(?P<track>mx2t6|mn2t6)$")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _infer_started_at(root: Path) -> datetime | None:
    match = TRACK_RE.search(root.name)
    if not match:
        return None

    status_dir = root.parent.parent / "tmp"
    candidates: list[datetime] = []
    for path in sorted(status_dir.glob(f"tigge_{match.group('track')}_download_status_a*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue

        generated_at = _parse_timestamp(payload.get("generated_at"))
        if generated_at is not None:
            candidates.append(generated_at)

        last_pass = payload.get("last_pass")
        if isinstance(last_pass, dict):
            last_pass_at = _parse_timestamp(last_pass.get("timestamp"))
            if last_pass_at is not None:
                candidates.append(last_pass_at)

    return min(candidates) if candidates else None


def _collect_complete_timestamps(root: Path) -> list[float]:
    if not root.exists():
        return []
    timestamps: list[float] = []
    for path in root.rglob("*.grib"):
        ok_marker = Path(str(path) + ".ok")
        if ok_marker.exists() and path.stat().st_size > 0:
            timestamps.append(ok_marker.stat().st_mtime)
    return timestamps


def _collect_lane_statuses(root: Path) -> list[dict[str, object]]:
    match = TRACK_RE.search(root.name)
    if not match:
        return []

    status_dir = root.parent.parent / "tmp"
    rows: list[dict[str, object]] = []
    for path in sorted(status_dir.glob(f"tigge_{match.group('track')}_download_status_a*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        rows.append(
            {
                "lane": path.stem.rsplit("_", 1)[-1],
                "status": payload.get("status") or "?",
                "completed": payload.get("completed_tasks"),
                "total": payload.get("total_tasks"),
                "missing": payload.get("missing_tasks"),
                "stall": payload.get("stall_passes") or 0,
                "errors": len(payload.get("last_errors") or []),
                "eta": payload.get("eta_seconds"),
                "date_from": payload.get("date_from") or "?",
                "date_to": payload.get("date_to") or "?",
            }
        )
    return rows


def _format_lane_summary(lanes: list[dict[str, object]]) -> str:
    if not lanes:
        return "lanes=none"
    parts: list[str] = []
    for row in lanes:
        completed = row.get("completed")
        total = row.get("total")
        if isinstance(completed, int) and isinstance(total, int):
            progress = str(completed)
        else:
            progress = "?"
        parts.append(f"{row['lane']}:{progress}")
    errors = sum(int(row.get("errors") or 0) for row in lanes)
    stalls = sum(int(row.get("stall") or 0) for row in lanes)
    active = sum(1 for row in lanes if row.get("status") == "running")
    lane_etas = [
        float(row["eta"])
        for row in lanes
        if isinstance(row.get("eta"), (int, float)) and math.isfinite(float(row["eta"]))
    ]
    return f"L{active} " + " ".join(parts) + f" e{errors}s{stalls}"


def _format_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--:--:--"
    total = int(seconds + 0.5)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_duration_compact(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--"
    total_minutes = int((seconds / 60.0) + 0.5)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h{minutes:02d}"


def _color(enabled: bool, code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "raw" / "tigge_ecmwf_ens_regions_mn2t6",
    )
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--bar-width", type=int, default=40)
    parser.add_argument("--decimals", type=int, default=4)
    parser.add_argument("--single-line", action="store_true")
    args = parser.parse_args()

    expected = max(1, int(args.expected))
    track = args.root.name.rsplit("_", 1)[-1]
    use_color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    bar_width = max(12, int(args.bar_width))
    if args.single_line:
        bar_width = min(bar_width, max(12, shutil.get_terminal_size(fallback=(120, 24)).columns - 85))
    spinner = "|/-\\"
    tick = 0
    while True:
        timestamps = _collect_complete_timestamps(args.root)
        lanes = _collect_lane_statuses(args.root)
        count = len(timestamps)
        started_at = (
            datetime.fromtimestamp(min(timestamps), tz=timezone.utc)
            if timestamps
            else _infer_started_at(args.root) or datetime.now(timezone.utc)
        )
        ratio = min(1.0, count / float(expected))
        fill = max(0, min(bar_width, int(round(bar_width * ratio))))
        bar = "#" * fill + "-" * (bar_width - fill)
        now_ts = time.time()
        elapsed_seconds = max(0.0, now_ts - started_at.timestamp())
        rate_overall = (count / elapsed_seconds) * 3600.0 if elapsed_seconds > 0 and count > 0 else None
        one_hour_ago = now_ts - 3600.0
        recent_1h = sum(1 for t in timestamps if t >= one_hour_ago)
        rate_last_hour = float(recent_1h) if recent_1h > 0 else None
        six_hours_ago = now_ts - 21600.0
        recent_6h = sum(1 for t in timestamps if t >= six_hours_ago)
        rate_last_6h = (recent_6h / 6.0) if recent_6h > 0 else None
        eta_seconds = (
            max(0.0, (expected - count) / (rate_overall / 3600.0))
            if rate_overall and rate_overall > 0
            else None
        )
        eta_seconds_1h = (
            max(0.0, (expected - count) / (rate_last_hour / 3600.0))
            if rate_last_hour and rate_last_hour > 0
            else None
        )
        eta_seconds_6h = (
            max(0.0, (expected - count) / (rate_last_6h / 3600.0))
            if rate_last_6h and rate_last_6h > 0
            else None
        )
        pct = f"{ratio * 100:.2f}"
        rate_text = f"{rate_overall:.1f}/h" if rate_overall is not None else "--/h"
        eta_text = _format_duration_compact(eta_seconds)
        lane_text = _format_lane_summary(lanes)
        line = (
            f"{_color(use_color, '1;36', track)} "
            f"{count}/{expected} "
            f"{_color(use_color, '1;33', pct + '%')} "
            f"eta{eta_text} "
            f"r{rate_text} "
            f"{lane_text}"
        )
        if args.single_line:
            sys.stdout.write("\r" + line + "\x1b[K")
            sys.stdout.flush()
        else:
            print(line, flush=True)
        if count >= expected:
            if args.single_line:
                sys.stdout.write("\n")
            return 0
        tick += 1
        time.sleep(max(1, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
