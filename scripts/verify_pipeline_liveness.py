# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: 10h production dead-zone incident 2026-06-10 (operator: "连下载都没接上,一上线卡了整整10小时")
"""Standing e2e DATA-SUPPLY liveness check for the replacement chain.

The order-side e2e verifier (verify_fill_e2e.py) proved fills are correct but
said nothing about whether the engine HAS data to trade on. This verifier
asserts every upstream stage is alive, so a stall anywhere in
  download -> raw_forecast_artifacts -> posteriors -> readiness -> candidate receipts
is alarmed within minutes instead of being discovered 10 hours later.

Stage freshness bars (steady-state, all four model cycles scheduled):
  raw artifact   <= 8h   (cycle cadence 6h + publication lag slack)
  posterior      <= 8h
  readiness READY (unexpired) count >= 10 scopes
  candidate-bearing receipt (q_live not null) <= 6h  [engine actually ranking]

Exit 0 = all alive. Exit 2 = at least one stage stale (prints which).
Read-only (uri mode=ro). Intended for the operator watch loop and a future
daemon scheduler slot.

Usage: PYTHONSAFEPATH=1 PYTHONPATH=. .venv/bin/python scripts/verify_pipeline_liveness.py [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys

FORECASTS = "file:state/zeus-forecasts.db?mode=ro"
WORLD = "file:state/zeus-world.db?mode=ro"

BARS_HOURS = {
    "raw_artifact": 8.0,
    "posterior": 8.0,
    "candidate_receipt": 6.0,
}
MIN_READY_SCOPES = 10


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _age_hours(now: dt.datetime, iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return (now - t).total_seconds() / 3600.0
    except ValueError:
        return None


def check() -> tuple[int, dict]:
    now = dt.datetime.now(dt.timezone.utc)
    f = _ro(FORECASTS)
    w = _ro(WORLD)
    out: dict = {"checked_at": now.isoformat(), "stages": {}}

    raw_latest = f.execute(
        "SELECT MAX(recorded_at) FROM raw_forecast_artifacts"
    ).fetchone()[0]
    post_latest = f.execute(
        "SELECT MAX(computed_at) FROM forecast_posteriors"
    ).fetchone()[0]
    ready_n = f.execute(
        "SELECT COUNT(*) FROM readiness_state WHERE status='READY' AND expires_at > ?",
        (now.isoformat(),),
    ).fetchone()[0]
    cand_latest = w.execute(
        "SELECT MAX(created_at) FROM no_trade_regret_events WHERE q_live IS NOT NULL"
    ).fetchone()[0]

    failures = []
    for name, latest in (
        ("raw_artifact", raw_latest),
        ("posterior", post_latest),
        ("candidate_receipt", cand_latest),
    ):
        age = _age_hours(now, latest)
        ok = age is not None and age <= BARS_HOURS[name]
        out["stages"][name] = {"latest": latest, "age_hours": age, "ok": ok}
        if not ok:
            failures.append(f"{name} stale (age={age}, bar={BARS_HOURS[name]}h)")

    ready_ok = ready_n >= MIN_READY_SCOPES
    out["stages"]["readiness_ready_scopes"] = {"count": ready_n, "ok": ready_ok}
    if not ready_ok:
        failures.append(f"readiness READY scopes {ready_n} < {MIN_READY_SCOPES}")

    out["failures"] = failures
    return (2 if failures else 0), out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    code, out = check()
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        for name, st in out["stages"].items():
            print(f"{name}: {st}")
        print("PIPELINE_ALIVE" if code == 0 else "PIPELINE_STALL: " + "; ".join(out["failures"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
