#!/usr/bin/env python3
"""Combined terminal monitor for mx2t6 and mn2t6 regional GRIB downloads."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TRACK_RE = re.compile(r"tigge_ecmwf_ens_regions_(?P<track>mx2t6|mn2t6)$")


@dataclass(frozen=True)
class TrackStat:
    track: str
    expected: int
    count: int
    elapsed_seconds: float
    rate_overall: float | None
    rate_last_hour: float | None
    rate_last_6h: float | None

    @property
    def ratio(self) -> float:
        return min(1.0, self.count / float(self.expected))

    @property
    def pct(self) -> float:
        return self.ratio * 100.0

    @property
    def eta_all(self) -> float | None:
        return (
            max(0.0, (self.expected - self.count) / (self.rate_overall / 3600.0))
            if self.rate_overall and self.rate_overall > 0
            else None
        )

    @property
    def eta_last_hour(self) -> float | None:
        return (
            max(0.0, (self.expected - self.count) / (self.rate_last_hour / 3600.0))
            if self.rate_last_hour and self.rate_last_hour > 0
            else None
        )

    @property
    def eta_last_6h(self) -> float | None:
        return (
            max(0.0, (self.expected - self.count) / (self.rate_last_6h / 3600.0))
            if self.rate_last_6h and self.rate_last_6h > 0
            else None
        )


@dataclass(frozen=True)
class LaneSummary:
    active: int
    total: int
    completed: tuple[int | None, ...]
    errors: int
    stalls: int


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


def _collect_lane_summary(root: Path, lanes: int | None = None) -> LaneSummary:
    match = TRACK_RE.search(root.name)
    if not match:
        return LaneSummary(active=0, total=0, completed=(), errors=0, stalls=0)

    status_dir = root.parent.parent / "tmp"
    active = 0
    completed: list[int | None] = []
    errors = 0
    stalls = 0
    paths = []
    if lanes is None:
        paths = sorted(status_dir.glob(f"tigge_{match.group('track')}_download_status_a*.json"))
    else:
        paths = [status_dir / f"tigge_{match.group('track')}_download_status_a{idx}.json" for idx in range(1, lanes + 1)]
    for path in paths:
        if not path.exists():
            completed.append(None)
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            completed.append(None)
            errors += 1
            continue
        if payload.get("status") == "running":
            active += 1
        value = payload.get("completed_tasks")
        completed.append(value if isinstance(value, int) else None)
        errors += len(payload.get("last_errors") or [])
        stalls += int(payload.get("stall_passes") or 0)
    return LaneSummary(
        active=active,
        total=len(completed),
        completed=tuple(completed),
        errors=errors,
        stalls=stalls,
    )


def _format_lane_completed(summary: LaneSummary) -> str:
    if not summary.completed:
        return "?"
    return ",".join(str(value) if value is not None else "?" for value in summary.completed)


def _format_one_line(
    mx_stat: TrackStat,
    mn_stat: TrackStat,
    mx_lanes: LaneSummary,
    mn_lanes: LaneSummary,
    bar_width: int,
    use_color: bool,
) -> str:
    total_count = mx_stat.count + mn_stat.count
    total_expected = mx_stat.expected + mn_stat.expected
    ratio = min(1.0, total_count / float(total_expected))
    fill = max(0, min(bar_width, int(round(bar_width * ratio))))
    bar = "#" * fill + "-" * (bar_width - fill)
    total_rate = (mx_stat.rate_overall or 0.0) + (mn_stat.rate_overall or 0.0)
    remaining = max(0, total_expected - total_count)
    eta = (remaining / (total_rate / 3600.0)) if total_rate > 0 else None
    errors = mx_lanes.errors + mn_lanes.errors
    stalls = mx_lanes.stalls + mn_lanes.stalls
    line = (
        f"TIGGE {_color(use_color, '32', '[' + bar + ']')} "
        f"{total_count}/{total_expected} "
        f"{_color(use_color, '1;33', f'{ratio * 100:.2f}%')} "
        f"eta{_format_duration_compact(eta)} "
        f"r{total_rate:.1f}/h "
        f"mx{mx_stat.count}({_format_lane_completed(mx_lanes)}) "
        f"mn{mn_stat.count}({_format_lane_completed(mn_lanes)}) "
        f"L{mx_lanes.active + mn_lanes.active}/{mx_lanes.total + mn_lanes.total} "
        f"e{errors}s{stalls}"
    )
    return line


def _format_track_line(stat: TrackStat, bar_width: int, decimals: int, use_color: bool) -> str:
    fill = max(0, min(bar_width, int(round(bar_width * stat.ratio))))
    bar = "#" * fill + "-" * (bar_width - fill)
    rate_text = (
        f"{stat.rate_overall:.2f} overall, {stat.rate_last_hour:.2f} last1h, "
        f"{stat.rate_last_6h:.2f} last6h files/h"
        if (stat.rate_overall is not None or stat.rate_last_hour is not None or stat.rate_last_6h is not None)
        else "-- files/h"
    )
    eta_text = (
        f"{_format_duration(stat.eta_all)}(all) "
        f"{_format_duration(stat.eta_last_hour)}(last1h) "
        f"{_format_duration(stat.eta_last_6h)}(last6h)"
    )
    pct = f"{stat.pct:.{decimals}f}%"
    return (
        f"{_color(use_color, '1;36', stat.track)} "
        f"{_color(use_color, '32', '[' + bar + ']')} "
        f"{stat.count}/{stat.expected} "
        f"{_color(use_color, '1;33', pct)} "
        f"| rate={rate_text} "
        f"| elapsed={_format_duration(stat.elapsed_seconds)} "
        f"| eta={eta_text}"
    )


def _color(enabled: bool, code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def _build_track_stat(root: Path, expected: int, now_ts: float) -> TrackStat:
    timestamps = _collect_complete_timestamps(root)
    count = len(timestamps)
    started_at = (
        datetime.fromtimestamp(min(timestamps), tz=timezone.utc)
        if timestamps
        else _infer_started_at(root) or datetime.now(timezone.utc)
    )
    elapsed_seconds = max(0.0, now_ts - started_at.timestamp())

    rate_overall = (count / elapsed_seconds) * 3600.0 if elapsed_seconds > 0 and count > 0 else None

    one_hour_ago = now_ts - 3600.0
    recent_1h = sum(1 for t in timestamps if t >= one_hour_ago)
    rate_last_hour = float(recent_1h) if recent_1h > 0 else None

    six_hours_ago = now_ts - 21600.0
    recent_6h = sum(1 for t in timestamps if t >= six_hours_ago)
    rate_last_6h = (recent_6h / 6.0) if recent_6h > 0 else None

    track = root.name.rsplit("_", 1)[-1]
    return TrackStat(
        track=track,
        expected=expected,
        count=count,
        elapsed_seconds=elapsed_seconds,
        rate_overall=rate_overall,
        rate_last_hour=rate_last_hour,
        rate_last_6h=rate_last_6h,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mx-root",
        type=Path,
        default=Path("/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens_regions_mx2t6"),
    )
    parser.add_argument(
        "--mn-root",
        type=Path,
        default=Path("/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens_regions_mn2t6"),
    )
    parser.add_argument("--expected", type=int, default=2232)
    parser.add_argument("--expected-mx2t6", type=int, default=None)
    parser.add_argument("--expected-mn2t6", type=int, default=None)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--bar-width", type=int, default=28)
    parser.add_argument("--decimals", type=int, default=4)
    parser.add_argument("--lanes", type=int, default=None)
    parser.add_argument("--single-line", action="store_true")
    parser.add_argument("--one-line", action="store_true")
    args = parser.parse_args()

    expected_mx = max(1, int(args.expected_mx2t6 or args.expected))
    expected_mn = max(1, int(args.expected_mn2t6 or args.expected))
    bar_width = max(12, int(args.bar_width))
    if args.single_line:
        max_bar_by_terminal = max(
            12,
            (shutil.get_terminal_size(fallback=(140, 24)).columns // 2) - 40,
        )
        bar_width = min(bar_width, max_bar_by_terminal)
    use_color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    spinner = "|/-\\"
    tick = 0
    while True:
        now_ts = time.time()
        mx_stat = _build_track_stat(args.mx_root, expected_mx, now_ts)
        mn_stat = _build_track_stat(args.mn_root, expected_mn, now_ts)
        lane_limit = max(1, args.lanes) if args.lanes is not None else None
        mx_lanes = _collect_lane_summary(args.mx_root, lane_limit)
        mn_lanes = _collect_lane_summary(args.mn_root, lane_limit)

        decimals = max(1, min(8, int(args.decimals)))
        if args.one_line:
            line = _format_one_line(mx_stat, mn_stat, mx_lanes, mn_lanes, bar_width, use_color)
            if args.single_line:
                sys.stdout.write("\r" + line + "\x1b[K")
                sys.stdout.flush()
            else:
                print(line, flush=True)
            if mx_stat.count >= mx_stat.expected and mn_stat.count >= mn_stat.expected:
                if args.single_line:
                    sys.stdout.write("\n")
                return 0
            tick += 1
            time.sleep(max(1, args.interval_seconds))
            continue

        line_mx = (
            f"{_color(use_color, '36', spinner[tick % len(spinner)])} "
            f"{_format_track_line(mx_stat, bar_width, decimals, use_color)}"
        )
        line_mn = (
            f"{_color(use_color, '36', ' ')} "
            f"{_format_track_line(mn_stat, bar_width, decimals, use_color)}"
        )

        if args.single_line:
            sys.stdout.write(
                "\r" + line_mx + "\x1b[K\n" + line_mn + "\x1b[K\x1b[1A"
            )
            sys.stdout.flush()
        else:
            print(line_mx, flush=True)
            print(line_mn, flush=True)
            print("", flush=True)

        if mx_stat.count >= mx_stat.expected and mn_stat.count >= mn_stat.expected:
            if args.single_line:
                sys.stdout.write("\n")
            return 0
        tick += 1
        time.sleep(max(1, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
