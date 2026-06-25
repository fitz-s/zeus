#!/usr/bin/env python3
# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: docs/the_path/P1_BRIEF.md §1 (q_d0 leg: "Day0 Platt never fit";
#   day0_horizon_platt_fits = 0 rows) + §4 Step 0 (proper temporal-holdout fit
#   protocol) + iron rules (settlement=only truth; NEVER fabricate skill).
#   ThePath P1 ITEM "activate the Day0 nowcast lane / start the obs-timing clock".
"""Persist a CONSERVATIVE / IDENTITY Day0 horizon Platt fit (OFFLINE).

WHY IDENTITY, NOT A HOLDOUT FIT
-------------------------------
The Day0 horizon Platt model (src/calibration/day0_horizon_calibration.py)
needs, per training row: p_now_raw (running-extreme-conditioned climatology),
hours_remaining (0..6 intra-day), daypart (morning/afternoon/post_peak), a
temperature_metric_indicator, and a binary settlement outcome.

As of 2026-06-07 NO populated table carries those features jointly:
  - day0_metric_fact         = 0 rows (the only table carrying local_hour /
                               running_extreme / temp_current).
  - probability_trace_fact   = 0 rows in zeus-forecasts (Day0 same-day q_d0 not
                               persisted; the 33k zeus_trades rows end 2026-05-18
                               and carry NO Day0 same-day q_d0 — P1_BRIEF §1).
  - calibration_pairs        = 6.5M rows but carries NEITHER daypart NOR
                               hours_remaining NOR running-extreme-conditioned
                               p_now_raw. Its lead_days is in DAYS (full horizon),
                               and p_raw is full-horizon climatology — wrong
                               covariate semantics for the intra-day Day0 model.

A proper temporal-holdout fit therefore requires the full OFFLINE reconstruction
harness described in P1_BRIEF §4 Step 0 (obs-lock from observation_instants in
zeus-world.db + running-max + fixed-publish-lag obs-clock + daypart from local
solar time + VERIFIED settlement join) — a separate, cross-DB, PR-scale build,
NOT this task's scope. Synthesizing coefficients from calibration_pairs would be
a fictitious skillful fit (wrong covariates), which the iron rules forbid.

Per the iron rules, when a clean holdout fit is not feasible we persist a
CONSERVATIVE / IDENTITY fit (documented, no fabricated skill) to START THE DATA
CLOCK and flag refinement, rather than inventing skill.

IDENTITY SEMANTICS (zero claimed skill)
---------------------------------------
  alpha = 1.0   -> pass logit(p_now_raw) through UNCHANGED
  beta  = 0.0   -> no horizon adjustment
  gamma_* = 0.0 -> no daypart adjustment
  delta = 0.0   -> no metric adjustment
  epsilon = 0.0 -> no intercept shift
=> predict_proba(p_now_raw, *) == p_now_raw exactly. The model asserts the raw
input is its own best estimate; it claims NOTHING beyond it. n_obs = 0 makes the
absence of training data explicit and queryable.

EFFECT
------
read_latest_platt_fit() returns non-None -> _maybe_write_day0_nowcast
(monitor_refresh.py:1767) stops short-circuiting -> the Day0 nowcast lane writes
day0_nowcast_runs rows carrying observation_available_at. This is audit logging
(nowcast_runs is logged, NOT traded); the mainline executor / trading decision
path is untouched. Starting the clock lets the obs-timing dataset accumulate so a
real holdout fit (and the G-DAY0 ROI verdict) become measurable later.

USAGE
-----
  python3 scripts/persist_day0_horizon_identity_fit.py --dry-run   # temp DB copy
  python3 scripts/persist_day0_horizon_identity_fit.py --verify    # read-back only
  python3 scripts/persist_day0_horizon_identity_fit.py             # write to LIVE

Idempotent: write_platt_fit uses INSERT OR IGNORE on fit_run_id PK, and we use a
deterministic fit_run_id so re-runs do not stack duplicate identity rows.
Fail-soft: any error is reported and exits non-zero WITHOUT raising into a caller.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

# Deterministic id so repeated runs are idempotent (INSERT OR IGNORE on PK).
_IDENTITY_FIT_RUN_ID = "hpf_v1_identity_conservative_v1"
_FIT_ARTIFACT_ID = "hpf_v1"


def build_identity_fit():
    """Return a documented CONSERVATIVE / IDENTITY HorizonPlattFit (zero skill)."""
    from src.calibration.day0_horizon_calibration import HorizonPlattFit

    return HorizonPlattFit(
        alpha=1.0,          # pass logit(p_now_raw) through unchanged
        beta=0.0,           # no horizon adjustment
        gamma_morning=0.0,  # no daypart adjustment
        gamma_afternoon=0.0,
        gamma_post_peak=0.0,
        delta=0.0,          # no metric adjustment
        epsilon=0.0,        # no intercept shift
        fit_artifact_id=_FIT_ARTIFACT_ID,
        fit_run_id=_IDENTITY_FIT_RUN_ID,
        fit_date=date.today().isoformat(),
        n_obs=0,            # explicit: NO training data (conservative/identity)
        sample_period_start="",
        sample_period_end="",
    )


def _read_back(conn) -> object | None:
    from src.state.day0_nowcast_store import read_latest_platt_fit

    return read_latest_platt_fit(fit_artifact_id=_FIT_ARTIFACT_ID, conn=conn)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Copy the LIVE forecasts DB to a temp file and write there; LIVE untouched.",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Read-only: report whether an identity fit is already present; no write.",
    )
    args = ap.parse_args(argv)

    # Ensure repo root on sys.path so `src.*` imports resolve when run as a script.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from src.state.day0_nowcast_store import write_platt_fit
        from src.state.db import ZEUS_FORECASTS_DB_PATH
    except Exception as exc:  # noqa: BLE001 — fail-soft import guard
        print(f"[persist_day0_horizon_identity_fit] import FAILED (non-fatal): {exc}")
        return 2

    fit = build_identity_fit()

    # --verify: read-only against LIVE, no write.
    if args.verify:
        try:
            conn = sqlite3.connect(f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            got = _read_back(conn)
            conn.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[verify] read FAILED (non-fatal): {exc}")
            return 2
        if got is None:
            print("[verify] no Day0 horizon Platt fit present yet (lane would short-circuit).")
            return 1
        print(
            "[verify] fit present -> "
            f"fit_run_id={got.fit_run_id} alpha={got.alpha} beta={got.beta} "
            f"n_obs={got.n_obs} fit_artifact_id={got.fit_artifact_id}"
        )
        return 0

    # Choose target DB: temp copy (dry-run) or LIVE.
    if args.dry_run:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        target = Path(tmp.name)
        if Path(ZEUS_FORECASTS_DB_PATH).exists():
            shutil.copy2(ZEUS_FORECASTS_DB_PATH, target)
        else:
            # No live DB to copy: build the schema fresh so the dry-run is meaningful.
            from src.state.db import _create_day0_horizon_platt_fits

            c = sqlite3.connect(target)
            _create_day0_horizon_platt_fits(c)
            c.commit()
            c.close()
        print(f"[dry-run] writing identity fit to TEMP copy: {target}")
        try:
            conn = sqlite3.connect(target)
            write_platt_fit(fit, conn=conn)
            conn.row_factory = sqlite3.Row
            got = _read_back(conn)
            conn.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[dry-run] write/read FAILED (non-fatal): {exc}")
            return 2
        ok = got is not None and got.fit_run_id == _IDENTITY_FIT_RUN_ID
        print(f"[dry-run] read-back -> {'OK' if ok else 'MISSING'}: "
              f"{None if got is None else got.fit_run_id}")
        return 0 if ok else 2

    # LIVE write: write_platt_fit opens its own LIVE-class connection under
    # db_writer_lock(LIVE). INSERT OR IGNORE on the deterministic PK -> idempotent.
    print(f"[live] writing CONSERVATIVE/IDENTITY fit to {ZEUS_FORECASTS_DB_PATH}")
    try:
        write_platt_fit(fit)  # conn=None -> LIVE-class connection + writer lock
    except Exception as exc:  # noqa: BLE001 — fail-soft; never raise into a caller
        print(f"[live] write FAILED (non-fatal): {exc}")
        return 2

    # Read back to confirm the lane will now see a fit.
    try:
        conn = sqlite3.connect(f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        got = _read_back(conn)
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[live] read-back FAILED (non-fatal): {exc}")
        return 2

    if got is None:
        print("[live] read-back returned None -> lane would still short-circuit (FAILED).")
        return 2
    print(
        "[live] OK -> Day0 horizon Platt fit persisted; nowcast lane will fire (audit). "
        f"fit_run_id={got.fit_run_id} alpha={got.alpha} n_obs={got.n_obs}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
