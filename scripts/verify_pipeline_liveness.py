# Created: 2026-06-10
# Last reused or audited: 2026-06-11
# Authority basis: 10h production dead-zone incident 2026-06-10 (operator: "连下载都没接上,一上线卡了整整10小时")
#   + operator 2026-06-11: "这纯粹是我要求的e2e验证下载到下单的不完全验证。甚至没有人probe过是否能下载"
#   — the download legs themselves must be ACTIVELY PROBED (real provider round-trips),
#   not inferred from journal freshness alone.
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

    # DOWNLOAD-LEG PROBES (operator 2026-06-18): real provider round-trips proving the
    # live anchor CAN be downloaded right now, plus published-vs-journaled gap. The leg
    # fails when the provider has a newer cycle than the journal AND the journal copy is
    # older than one cycle interval + slack — that is exactly the silent-starvation shape
    # of 2026-06-10 (provider moved on, we never noticed we couldn't/didn't download).
    out["download_legs"] = {}
    try:
        from src.data.replacement_cycle_availability import (
            probe_anchor_available_any,
            resolve_anchor_cycle_availability,
        )

        availability = resolve_anchor_cycle_availability(
            now,
            probe_anchor=probe_anchor_available_any,
        )
        newest_anchor_pub = next((a.cycle for a in availability if a.anchor_available), None)
        for leg, source_id, newest_pub in (
            ("anchor", "openmeteo_ecmwf_ifs_9km", newest_anchor_pub),
        ):
            have = f.execute(
                "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts WHERE source_id=?",
                (source_id,),
            ).fetchone()[0]
            have_age = _age_hours(now, have)
            gap_h = None
            if newest_pub is not None and have:
                try:
                    have_dt = dt.datetime.fromisoformat(str(have).replace("Z", "+00:00"))
                    if have_dt.tzinfo is None:
                        have_dt = have_dt.replace(tzinfo=dt.timezone.utc)
                    gap_h = (newest_pub - have_dt).total_seconds() / 3600.0
                except ValueError:
                    pass
            # probe_ok: the provider answered our availability round-trip at all
            probe_ok = newest_pub is not None
            # leg_ok: nothing published that we lack by more than one cycle (6h) + slack
            leg_ok = probe_ok and (gap_h is None or gap_h <= 6.0)
            out["download_legs"][leg] = {
                "provider_probe_ok": probe_ok,
                "newest_published_cycle": newest_pub.isoformat() if newest_pub else None,
                "journaled_cycle": have,
                "journal_age_hours": have_age,
                "published_minus_journaled_hours": gap_h,
                "ok": leg_ok,
            }
            if not leg_ok:
                failures.append(
                    f"download leg {leg}: published={newest_pub} journaled={have} gap={gap_h}h"
                )
    except Exception as exc:  # noqa: BLE001 — probe machinery itself failing IS a failure
        out["download_legs"]["error"] = str(exc)[:200]
        failures.append(f"download-leg probes errored: {exc}")

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
