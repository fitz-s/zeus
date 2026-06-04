#!/usr/bin/env python3
# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: feedback_liveness_first_health_antibody (the immune-system fix
#   for the 2026-06-04 incident — live-trading wedged 12+h with a 487MB world WAL
#   and two stuck APScheduler jobs, undetected because nothing screamed). This
#   probe makes "alive-but-wedged / inert-flag / city-left-behind" a LOUD,
#   non-zero-exit failure instead of a silence that reads as health.
#
# LIVENESS-FIRST: run this as the FIRST step of any resume/loop AND on a cron.
# It checks, in order, the five dimensions silence cannot distinguish from health:
#   1. daemons ps-alive (NOT launchctl STATUS, which is last-prior-exit)
#   2. world-DB WAL byte-size (a long-lived uncommitted txn never checkpointed)
#   3. APScheduler job stalls ("maximum number of running instances reached")
#   4. artifacts advancing (receipts + FSR freshness; reactor regret recency)
#   5. universal city coverage (config roster vs EMOS cells — no city left behind)
#
# Exit 0 = all GREEN. Exit 1 = at least one RED (a hidden error is live).
# Read-only on all DBs (WAL mode permits readers during the write wedge).

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATE = REPO / "state"
WORLD_DB = STATE / "zeus-world.db"
FCST_DB = STATE / "zeus-forecasts.db"
WORLD_WAL = STATE / "zeus-world.db-wal"
CITIES_JSON = REPO / "config" / "cities.json"
EMOS_JSON = STATE / "emos_calibration.json"
LOG_DIR = REPO / "logs"

# --- thresholds (a healthy WAL checkpoints continuously; 487MB = wedged) -------
WAL_WARN_BYTES = 64 * 1024 * 1024       # 64MB
WAL_RED_BYTES = 256 * 1024 * 1024       # 256MB → almost certainly a stuck txn
RECEIPT_STALE_RED_MIN = 30              # no new receipt in 30 min while daemon up = RED
FSR_STALE_RED_MIN = 30                  # no FSR emitted in 30 min = RED
REGRET_STALE_WARN_MIN = 15             # reactor produces regret continuously when alive
STUCK_JOB_WINDOW_MIN = 15              # "max instances reached" seen this recently = RED
DAEMON_LABELS = [
    "com.zeus.live-trading",
    "com.zeus.forecast-live",
    "com.zeus.data-ingest",
    "com.zeus.riskguard-live",
    "com.zeus.venue-heartbeat",
]

reds: list[str] = []
warns: list[str] = []
rows: list[tuple[str, str, str]] = []  # (dimension, status, detail)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_min(ts_iso: str | None) -> float | None:
    if not ts_iso:
        return None
    s = str(ts_iso).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # try a date-only / space-separated form
        try:
            dt = datetime.fromisoformat(s.split(".")[0])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (_now() - dt).total_seconds() / 60.0


def _ro_conn(db: Path) -> sqlite3.Connection | None:
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        conn.execute("PRAGMA query_only=ON")
        return conn
    except sqlite3.Error:
        return None


def _scalar(conn: sqlite3.Connection, sql: str):
    try:
        cur = conn.execute(sql)
        r = cur.fetchone()
        return r[0] if r else None
    except sqlite3.Error as e:
        return f"__ERR__:{e}"


# === 1. DAEMON LIVENESS (ps, not launchctl STATUS) ===========================
def check_daemons() -> None:
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception as e:
        reds.append(f"daemons: launchctl list failed ({e})")
        rows.append(("daemons", "RED", f"launchctl unavailable: {e}"))
        return
    label_pid: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in DAEMON_LABELS:
            label_pid[parts[2]] = parts[0]  # PID col (or '-')
    for label in DAEMON_LABELS:
        pid = label_pid.get(label, "-")
        alive = False
        if pid not in ("-", "", None):
            alive = (
                subprocess.run(
                    ["ps", "-p", str(pid)], capture_output=True, text=True
                ).returncode
                == 0
            )
        if alive:
            rows.append((f"daemon:{label}", "GREEN", f"pid={pid} alive"))
        else:
            # data/forecast/live-trading are load-bearing; the others are aux
            critical = label in (
                "com.zeus.live-trading",
                "com.zeus.forecast-live",
                "com.zeus.data-ingest",
            )
            (reds if critical else warns).append(
                f"daemon {label}: NOT running (pid={pid})"
            )
            rows.append((f"daemon:{label}", "RED" if critical else "WARN", f"pid={pid} DOWN"))


# === 2. WORLD WAL SIZE (the lock wedge) ======================================
def check_wal() -> None:
    if not WORLD_WAL.exists():
        rows.append(("world-wal", "GREEN", "no -wal (checkpointed)"))
        return
    n = WORLD_WAL.stat().st_size
    mb = n / (1024 * 1024)
    if n >= WAL_RED_BYTES:
        reds.append(
            f"world WAL {mb:.0f}MB ≥ {WAL_RED_BYTES//1024//1024}MB — stuck uncommitted "
            f"txn under world_write_mutex (lock-starvation; restart to checkpoint)"
        )
        rows.append(("world-wal", "RED", f"{mb:.0f}MB"))
    elif n >= WAL_WARN_BYTES:
        warns.append(f"world WAL {mb:.0f}MB elevated")
        rows.append(("world-wal", "WARN", f"{mb:.0f}MB"))
    else:
        rows.append(("world-wal", "GREEN", f"{mb:.1f}MB"))


# === 3. STUCK APSCHEDULER JOBS ("maximum number of running instances reached")
def _age_min_chicago(ts: str) -> float | None:
    """Age of a naive 'YYYY-MM-DD HH:MM:SS' stamp logged in America/Chicago.

    The Zeus daemons log in Chicago local time (CLAUDE.md: Chicago primary),
    naive (no offset). Interpreting these as UTC would mis-age by the CDT/CST
    offset and hide a genuinely-stuck job. Parse them in their real tz.
    """
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(ts.replace("T", " ").split(",")[0])
        dt = dt.replace(tzinfo=ZoneInfo("America/Chicago"))
        return (_now() - dt).total_seconds() / 60.0
    except Exception:
        return None


def check_stuck_jobs() -> None:
    pat = re.compile(r"maximum number of running instances reached", re.I)
    ts_pat = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
    if not LOG_DIR.exists():
        rows.append(("stuck-jobs", "WARN", "no logs/ dir"))
        return
    hits: list[tuple[str, float]] = []  # (job-ish line, age_min)
    # APScheduler "max instances reached" lands in the daemons' *.err streams,
    # not *.log — scan both, newest-modified first.
    log_files = sorted(
        list(LOG_DIR.glob("*.err")) + list(LOG_DIR.glob("*.log")),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for log in log_files:
        try:
            # tail ~400 lines cheaply
            tail = subprocess.run(
                ["tail", "-n", "400", str(log)], capture_output=True, text=True, timeout=10
            ).stdout
        except Exception:
            continue
        for line in tail.splitlines():
            if pat.search(line):
                m = ts_pat.search(line)
                age = _age_min_chicago(m.group(1)) if m else None
                hits.append((log.name, age if age is not None else 1e9))
    recent = [(n, a) for n, a in hits if a <= STUCK_JOB_WINDOW_MIN]
    if recent:
        worst = min(recent, key=lambda x: x[1])
        reds.append(
            f"APScheduler job STALLED ('max instances reached') in {worst[0]} "
            f"{worst[1]:.0f}min ago — a hung job silently freezes its emission "
            f"(restart the owning daemon; no self-heal)"
        )
        rows.append(("stuck-jobs", "RED", f"{worst[0]} {worst[1]:.0f}m ago"))
    elif hits:
        rows.append(("stuck-jobs", "WARN", "old occurrences only"))
    else:
        rows.append(("stuck-jobs", "GREEN", "none in tail"))


# === 4. ARTIFACTS ADVANCING (receipts / FSR / regret) ========================
def check_artifacts() -> None:
    conn = _ro_conn(WORLD_DB)
    if conn is None:
        reds.append("world-db: cannot open read-only (locked hard or missing)")
        rows.append(("artifacts", "RED", "world-db unopenable"))
        return
    # receipts
    rid = _scalar(conn, "SELECT MAX(rowid) FROM edli_no_submit_receipts")
    rts = _scalar(conn, "SELECT MAX(created_at) FROM edli_no_submit_receipts")
    age = _age_min(rts if isinstance(rts, str) and not str(rts).startswith("__ERR__") else None)
    detail = f"max_rowid={rid} age={age:.0f}m" if age is not None else f"max_rowid={rid} age=?"
    if age is not None and age > RECEIPT_STALE_RED_MIN:
        reds.append(f"receipts STALE: newest {age:.0f}min old (>{RECEIPT_STALE_RED_MIN}m) — reactor not producing")
        rows.append(("receipts", "RED", detail))
    else:
        rows.append(("receipts", "GREEN" if age is not None else "WARN", detail))
    # FSR freshness
    fsr = _scalar(
        conn,
        "SELECT MAX(available_at) FROM opportunity_events "
        "WHERE event_type='FORECAST_SNAPSHOT_READY'",
    )
    fage = _age_min(fsr if isinstance(fsr, str) and not str(fsr).startswith("__ERR__") else None)
    if fage is not None and fage > FSR_STALE_RED_MIN:
        reds.append(f"FSR STALE: newest {fage:.0f}min old (>{FSR_STALE_RED_MIN}m) — no fresh candidates emitted")
        rows.append(("fsr", "RED", f"age={fage:.0f}m"))
    else:
        rows.append(("fsr", "GREEN" if fage is not None else "WARN", f"age={fage:.0f}m" if fage is not None else "no FSR rows"))
    # regret recency (reactor-alive proxy)
    reg = _scalar(conn, "SELECT MAX(created_at) FROM no_trade_regret_events")
    gage = _age_min(reg if isinstance(reg, str) and not str(reg).startswith("__ERR__") else None)
    if gage is not None and gage > REGRET_STALE_WARN_MIN:
        warns.append(f"no_trade_regret idle {gage:.0f}min — reactor may be wedged")
        rows.append(("regret", "WARN", f"age={gage:.0f}m"))
    else:
        rows.append(("regret", "GREEN" if gage is not None else "WARN", f"age={gage:.0f}m" if gage is not None else "?"))
    conn.close()


# === 5. UNIVERSAL CITY COVERAGE (no city left behind) ========================
def check_city_coverage() -> None:
    if not CITIES_JSON.exists():
        rows.append(("city-coverage", "WARN", "cities.json missing"))
        return
    try:
        cfg = json.loads(CITIES_JSON.read_text())
    except Exception as e:
        rows.append(("city-coverage", "WARN", f"cities.json parse: {e}"))
        return
    # cities.json may be a list or {"cities":[...]} or {name:{...}}
    if isinstance(cfg, dict) and "cities" in cfg:
        cfg = cfg["cities"]
    if isinstance(cfg, dict):
        roster = set(cfg.keys())
    else:
        roster = {(c.get("name") or c.get("city")) if isinstance(c, dict) else c for c in cfg}
    roster = {r for r in roster if r}
    emos_cities: set[str] = set()
    if EMOS_JSON.exists():
        try:
            cells = json.loads(EMOS_JSON.read_text()).get("cells", {})
            for k in cells:
                emos_cities.add(str(k).split("|")[0])
        except Exception:
            pass
    missing = sorted(roster - emos_cities) if emos_cities else []
    if missing:
        # left-behind cities fall back to raw (under-dispersed) q — non-uniform, not fatal
        warns.append(
            f"{len(missing)} cities absent from EMOS cells → raw-q fallback (non-uniform): "
            f"{', '.join(missing[:12])}{'…' if len(missing) > 12 else ''}"
        )
        rows.append(("city-coverage", "WARN", f"{len(roster)} roster / {len(missing)} no-EMOS"))
    else:
        rows.append(("city-coverage", "GREEN", f"{len(roster)} roster, all EMOS-covered"))


def main() -> int:
    check_daemons()
    check_wal()
    check_stuck_jobs()
    check_artifacts()
    check_city_coverage()

    width = max((len(d) for d, _, _ in rows), default=12)
    print(f"=== zeus health probe @ {_now().isoformat(timespec='seconds')} ===")
    for dim, status, detail in rows:
        mark = {"GREEN": "✓", "WARN": "~", "RED": "✗"}.get(status, "?")
        print(f"  {mark} {dim.ljust(width)}  {status:<5} {detail}")
    print("---")
    if reds:
        print(f"RED ({len(reds)}) — a hidden error is LIVE:")
        for r in reds:
            print(f"  ✗ {r}")
    if warns:
        print(f"WARN ({len(warns)}):")
        for w in warns:
            print(f"  ~ {w}")
    if not reds and not warns:
        print("ALL GREEN — alive, advancing, universal.")
    return 1 if reds else 0


if __name__ == "__main__":
    sys.exit(main())
