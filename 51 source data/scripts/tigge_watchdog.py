#!/usr/bin/env python3
"""Restart TIGGE tmux lanes that heartbeat but stop making progress."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = ROOT / "tmp"
LOG_DIR = ROOT / "logs"
DEFAULT_STATE_PATH = TMP_DIR / "tigge_watchdog_state.json"
DEFAULT_LOG_PATH = LOG_DIR / "tigge_watchdog.log"
TRACKS = ("mx2t6", "mn2t6")


@dataclass(frozen=True)
class LaneStatus:
    track: str
    lane: int
    path: Path
    generated_at: datetime | None
    status: str
    date_from: str
    date_to: str
    completed: int
    missing: int
    active_stalled: int
    errors: int

    @property
    def session(self) -> str:
        return f"tigge-{self.track}-a{self.lane}"

    @property
    def state_key(self) -> str:
        return f"{self.track}:a{self.lane}:{self.date_from}:{self.date_to}"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()} {message}"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def _read_status(track: str, lane: int) -> LaneStatus | None:
    path = TMP_DIR / f"tigge_{track}_download_status_a{lane}.json"
    payload = _load_json(path, None)
    if not isinstance(payload, dict):
        return None
    last_pass = payload.get("last_pass") if isinstance(payload.get("last_pass"), dict) else {}
    return LaneStatus(
        track=track,
        lane=lane,
        path=path,
        generated_at=_parse_time(payload.get("generated_at")),
        status=str(payload.get("status") or "unknown"),
        date_from=str(payload.get("date_from") or ""),
        date_to=str(payload.get("date_to") or ""),
        completed=int(payload.get("completed_tasks") or 0),
        missing=int(payload.get("missing_tasks") or 0),
        active_stalled=int(last_pass.get("active_stalled") or 0),
        errors=len(payload.get("last_errors") or []),
    )


def _tmux_has_session(session: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _restart_lane(status: LaneStatus, *, dry_run: bool, log_path: Path) -> None:
    ensure_script = ROOT / "scripts" / f"ensure_tigge_{status.track}_sessions.sh"
    _log(log_path, f"restart lane={status.session} completed={status.completed} missing={status.missing}")
    if dry_run:
        return
    subprocess.run(["tmux", "kill-session", "-t", status.session], check=False)
    subprocess.run(["/bin/bash", str(ensure_script)], cwd=str(ROOT), check=False)


def _check_once(
    *,
    lanes: int,
    stall_minutes: float,
    stale_minutes: float,
    dry_run: bool,
    state_path: Path,
    log_path: Path,
) -> None:
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    changed = False

    for track in TRACKS:
        for lane in range(1, lanes + 1):
            status = _read_status(track, lane)
            if status is None:
                continue
            key = status.state_key
            record = state.get(key)
            if not isinstance(record, dict):
                state[key] = {
                    "completed": status.completed,
                    "last_progress_at": now_ts,
                    "last_seen_at": now_ts,
                    "restart_count": 0,
                }
                changed = True
                continue

            last_completed = int(record.get("completed") or 0)
            if status.completed > last_completed:
                record["completed"] = status.completed
                record["last_progress_at"] = now_ts
                record["last_seen_at"] = now_ts
                changed = True
                continue

            record["last_seen_at"] = now_ts
            if status.status == "complete" or status.missing == 0:
                record["completed"] = status.completed
                record["last_progress_at"] = now_ts
                changed = True
                continue

            heartbeat_age = (
                (now - status.generated_at.astimezone(timezone.utc)).total_seconds()
                if status.generated_at is not None
                else float("inf")
            )
            no_progress_age = now_ts - float(record.get("last_progress_at") or now_ts)
            should_restart = False
            reason = ""
            if heartbeat_age > stale_minutes * 60:
                should_restart = True
                reason = f"stale_status_{heartbeat_age / 60:.1f}m"
            elif status.active_stalled > 0 and no_progress_age > stall_minutes * 60:
                should_restart = True
                reason = f"no_progress_{no_progress_age / 60:.1f}m_active_stalled={status.active_stalled}"

            if should_restart and _tmux_has_session(status.session):
                _log(log_path, f"detected lane={status.session} reason={reason}")
                _restart_lane(status, dry_run=dry_run, log_path=log_path)
                record["restart_count"] = int(record.get("restart_count") or 0) + 1
                record["last_progress_at"] = now_ts
                record["last_restart_at"] = now_ts
                record["last_restart_reason"] = reason
                changed = True

    if changed:
        _write_json(state_path, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lanes", type=int, default=5)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--stall-minutes", type=float, default=120.0)
    parser.add_argument("--stale-minutes", type=float, default=20.0)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    while True:
        _check_once(
            lanes=max(1, args.lanes),
            stall_minutes=max(1.0, args.stall_minutes),
            stale_minutes=max(1.0, args.stale_minutes),
            dry_run=args.dry_run,
            state_path=args.state_path,
            log_path=args.log_path,
        )
        if args.once:
            return 0
        time.sleep(max(10, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
