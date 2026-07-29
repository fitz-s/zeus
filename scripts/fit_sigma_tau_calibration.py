#!/usr/bin/env python3
# Created: 2026-07-28
# Last reused/audited: 2026-07-28 (deep-review corrections applied same day)
# Lifecycle: created=2026-07-28; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Walk-forward fitter for the CURRENT-EVIDENCE (Day0) sigma-tau calibration artifact --
#   the ONLY writer of state/sigma_tau_calibration.json, which the materializer reads fail-soft at
#   the site formerly hardcoded to (1.0, 0.0, 0.0).
# Reuse: Re-run to refresh the artifact as the live corpus grows; inspect the OOS gate verdict
#   (`--validate CUTOFF`) and the `_meta.oos_acceptance_gate`/`oos_gate` fields on every group
#   BEFORE placing a new artifact under state/ -- a group with `fitted=False` must stay untouched.
# Authority basis: docs/operations/current/sigma_tau_calibration/PLAN.md. An OOS walk-forward
#   bakeoff (calib_curves/bakeoff.py) selected per-(unit_family, metric) FITTED k(tau) times
#   per-city variance shrinkage as the best legal sigma correction for the CURRENT-EVIDENCE (Day0)
#   shape. Precedent pattern: scripts/fit_sigma_scale.py (the HISTORICAL-path sibling artifact) --
#   this script is the new artifact's ONLY writer; the materializer reads it fail-soft.
#
# 2026-07-28 DEEP-REVIEW CORRECTIONS (applied same day, before any artifact was ever placed under
# state/ -- these supersede the two prior same-day correction passes, not a later revision):
#   FIX 1 LOCAL-DATE ENDPOINT: tau's target-end is now the CITY'S LOCAL midnight (DST-aware, via
#     config/cities.json's IANA timezone + zoneinfo), not target_date+1day 00:00 UTC. Markets
#     settle on the local calendar date; the UTC cut misassigned buckets by the city's UTC offset
#     (Shanghai +8h, Chicago -5h).
#   FIX 2 SETTLEMENT QUANTIZER: the settled integer's preimage is now looked up per city via
#     src.contracts.settlement_semantics.SettlementSemantics.for_city(...).rounding_rule +
#     settlement_preimage_offsets(...) -- NEVER re-derived inline. The universal [v-0.5,v+0.5)
#     assumption is wrong for Hong Kong (oracle_truncate: preimage [v,v+1)).
#   FIX 3 NUMERICAL STABILITY + FAIL-CLOSED FIT: the interval probability is computed in log-space
#     (logcdf/logsf with the stable tail chosen per-row), replacing norm.cdf(hi)-norm.cdf(lo) (which
#     silently underflows to exactly 0.0, hence log(0)=-inf, for intervals ~9+ sigma from center)
#     and removing the old 1e-12 clip that let such rows masquerade as a small but finite penalty.
#     The optimizer's convergence, finiteness, and boundary-pinning are all checked; any failure
#     raises FitFailure, which propagates to an explicit refusal (never a silently wrong k).
#   FIX 4 SHIP TRAIN COEFFICIENTS: a group that passes the OOS gate ships the TRAIN split's fitted
#     coefficients UNCHANGED -- no full-data refit (a refit could activate buckets/cities that were
#     never actually OOS-scored). The holdout is now DATE-BLOCKED (the last 25% of whole target
#     DATES, not individual events) so same-day cross-city correlation cannot leak across the
#     train/holdout boundary. A global-k-only OOS delta is additionally reported (never gated on) so
#     the operator can see what lead-bucket indexing buys over one flat k for the whole group.
#   FIX 5 TRAINING POPULATION FENCE: the query now requires
#     $.bayes_precision_fusion.current_evidence_shape to be present (a proxy for "this posterior
#     actually used the CURRENT-EVIDENCE branch this artifact serves"), computed_at strictly before
#     the FIX-1 local target end (excludes retroactive/late recomputes the current-evidence path
#     would never materialize), and settlement_outcomes.authority='VERIFIED' (NOT the raw
#     `settlements` table -- matches the exact precedent scripts/fit_sigma_scale.py:134 already
#     uses). Both SELECTs run inside one BEGIN read transaction for a consistent snapshot. Every
#     fence predicate is recorded in the artifact's _meta.
#   FIX 8 URI + READ-ONLY HARDENING: _connect_ro now parses ANY file: URI, forces mode=ro (rejecting
#     an explicit non-ro mode rather than silently overriding it), and additionally sets
#     PRAGMA query_only=ON on the connection as defense-in-depth.
#
# 2026-07-28 (earlier same-day) DESIGN-REVIEW CORRECTIONS (superseded in DETAIL by the FIX 1-5/8
# corrections above where they overlap, but the underlying reasoning stays valid and is preserved):
#   TAU CLOCK: tau is hours from the posterior's ISSUE clock (source_cycle_time -- 100% populated,
#     unlike current_evidence_shape.source_cycle_time at ~96.5%) to end of target_date, NOT
#     computed_at (decision time). Live evidence: Hong Kong 2026-07-20 HIGH has 247 distinct
#     computed_at values collapsing to only 4 distinct source_cycle_time values -- a
#     computed_at-anchored tau shrinks on every wall-clock recompute with no new provider issue.
#   EVENT WEIGHTING: multiple posteriors for one (city, target_date, metric) settlement event are
#     correlated. Each row is weighted 1/n_e (n_e = that event's row count within the group), so an
#     event whose lifetime spans two tau buckets contributes a fraction of its unit weight to each.
#     MIN_BUCKET_N/MIN_GROUP_N/MIN_CITY_N are counted in UNIQUE EVENTS, not raw rows.
#   LIKELIHOOD: the PRIMARY estimator is interval-censored MLE over the settled integer's actual
#     bin, maximizing the (event-)weighted sum of log[Phi((U-mu)/(k*sig)) - Phi((L-mu)/(k*sig))] --
#     this fits the traded quantity directly. The earlier closed-form spread-skill ratio
#     (std(z,ddof=1)/sqrt(mean(sig^2)), event-weighted) is retained as a REPORTED cross-check column
#     (k_normal_crosscheck) but is never served.
#   PER-GROUP OOS ACCEPTANCE GATE: no group ships a k that has not proven a positive OOS delta.
"""Walk-forward fit of lead-time-indexed sigma calibration for the CURRENT-EVIDENCE path.

MODEL
  Per (unit_family in {C,F}, metric in {high,low}):
    tau = lead_issue_h = hours from source_cycle_time to the CITY'S LOCAL target-date end (next
      local midnight, DST-aware, converted to UTC -- FIX 1); lead_decision_h = the same measured
      from computed_at is a --validate-only comparison column, never used for fitting.
    Bucketed on tau: [0,6),[6,12),[12,24),[24,36),[36,48),[48,72),[72,inf).
    Per row: mu = bayes_precision_fusion.anchor_value_c (RAW, never refit), sig =
      bayes_precision_fusion.predictive_sigma_c, [L,U) = the settled integer's PER-CITY rounding-
      rule preimage (src.contracts.settlement_semantics; FIX 2), converted to Celsius edges for
      F-unit settlements.
    event_weight = 1 / (row count of this (city, target_date) pair within the group).
    k(tau) = argmax_k sum(event_weight * log[Phi((U-mu)/(k*sig)) - Phi((L-mu)/(k*sig))]), computed
      in LOG SPACE for numerical stability (FIX 3), a 1-D bounded MLE (scipy.optimize.
      minimize_scalar). Optimizer non-convergence, a non-finite result, or a result pinned at a
      search bound raises FitFailure, propagated to an explicit refusal. A bucket with n_events <
      MIN_BUCKET_N is UNFITTED and inherits the group-global pooled k (train-only). A whole group
      with n_events < MIN_GROUP_N is REFUSED: k=1.0 everywhere, fitted=False.
    Per-city variance correction (cities with n_events >= MIN_CITY_N, pooled across tau within the
    group): c_raw = argmax_c of the SAME interval-censored likelihood with sigma = k(tau)*c*sig
    (k(tau) already fixed per-row), then shrunk c_shrunk^2 = (n_e*c_raw^2 + CITY_SHRINKAGE_N0) /
    (n_e + CITY_SHRINKAGE_N0), n_e = the city's unique-event count -- shrinks TOWARD 1.

  OOS ACCEPTANCE GATE (FIX 3/4): a group's fit is evaluated on a HOLDOUT (date-blocked: the
  chronologically last 25% of whole target DATES, or the operator's own --validate cutoff) using
  the event-weighted primary censored likelihood. The group ships ONLY if the censored OOS delta
  (fitted vs k=1) is finite AND exceeds OOS_MARGIN_NATS; otherwise it ships neutral (k=1.0,
  fitted=False) with the failing delta recorded as the refusal reason. A group that PASSES ships
  the TRAIN split's coefficients UNCHANGED (no full-data refit -- FIX 4). A global-k-only OOS delta
  (a single flat k, no bucket/city indexing) is additionally reported, never gated on, so the
  operator can see what lead-bucket indexing buys over one flat correction.

  Served (by the materializer): k_eff = k(tau) * c_shrunk(city). w and floor_steps are NOT part of
  this artifact -- the consumer holds them at exactly 0.0 (k-only calibration for this path).

DATA (FIX 5 population fence)
  forecast_posteriors (mode=ro) joined to settlement_outcomes (NOT the raw `settlements` table --
  matches scripts/fit_sigma_scale.py:134's precedent) on (city, target_date, temperature_metric),
  requiring settlement_outcomes.authority='VERIFIED'. mu/sig read from provenance_json
  '$.bayes_precision_fusion.anchor_value_c' / 'predictive_sigma_c'; the query additionally requires
  '$.bayes_precision_fusion.current_evidence_shape' to be present (a proxy for "this posterior
  actually used the CURRENT-EVIDENCE branch"), and rows are further fenced to
  computed_at < the FIX-1 local target end. Both SELECTs run inside one BEGIN read transaction.
  source_cycle_time from the top-level DB column. Settlement F-unit rows convert to Celsius via
  (v-32)*5/9 after applying the per-city rounding-rule preimage offset (FIX 2). Dedup keeps the
  LAST posterior per (city, target_date, temperature_metric, computed_at floored to the hour) --
  mirrors calib_curves/fit_inputs.py::dedup. Rows with lead_issue_h < 0 (source_cycle_time after
  the local target end already passed -- no trading-lead meaning) are dropped. Rows for a city
  absent from config/cities.json are dropped (fail-closed on unknown cities, never default to
  UTC/wmo_half_up).

READ-ONLY over --fcst (opened ?mode=ro, PRAGMA query_only=ON, FIX 8). Writes ONLY the path given by
--out; there is no default under state/ -- the operator decides if/when to place the artifact where
the materializer reads it.

--validate CUTOFF: fits on target_date < CUTOFF, holds out target_date >= CUTOFF (already
date-blocked by construction), and prints the event-weighted OOS mean log-likelihood ladder (k=1
baseline vs fitted, under BOTH the primary interval-censored likelihood and the Normal-density
cross-check, plus the global-k-only comparison rung) and coverage@68.3, per (unit_family, metric)
group, with the GATE verdict inline. Runs once bucketed on the issue clock (PRIMARY) and once on
the decision clock (COMPARISON ONLY) so the clock choice's effect on OOS numbers is visible.
Read-only; does not write --out unless --out is ALSO given, in which case the operator's own cutoff
governs the shipped gate (see main()).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import sqlite3
import sys
import urllib.parse
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import norm

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import load_cities  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics, settlement_preimage_offsets  # noqa: E402
# B1 (deep-review 2026-07-28) fit/serve parity: score Day0-active rows through the EXACT function
# the live serving path integrates through -- never a second reimplementation of the day0
# absorbing-observed-extreme transform.
from src.data.replacement_forecast_materializer import served_settlement_log_probability  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FCST_DEFAULT = os.path.join(REPO, "state", "zeus-forecasts.db")
SINCE_DEFAULT = "2026-07-11"  # live current-evidence era start (matches calib_curves reference)
COMPONENTS_FENCE_TS = "2026-07-15T22:32:31+00:00"  # current_evidence_shape within/between/delta
                                                     # fields are 100% populated only from here on;
                                                     # NOT applied as a filter -- this fitter never
                                                     # reads those component fields. Recorded for
                                                     # audit only.

SCHEMA_VERSION = 1
ARTIFACT_AUTHORITY = "sigma_tau_calibration_v1_mle"
# Exact machine-checked clock declaration -- the materializer's strict loader (FIX 6) requires this
# STRING to equal its own serving-clock constant before trusting the artifact at all. The prose
# description lives in a separate _meta field for humans.
TAU_CLOCK_ID = "source_cycle_time_local_target_end_v1"

TAU_EDGES = [0.0, 6.0, 12.0, 24.0, 36.0, 48.0, 72.0, math.inf]
TAU_LABELS = ["[0,6)", "[6,12)", "[12,24)", "[24,36)", "[36,48)", "[48,72)", "[72,inf)"]
GROUPS = [("C", "high"), ("C", "low"), ("F", "high"), ("F", "low")]

MIN_BUCKET_N = 60    # in UNIQUE EVENTS (nunique (city,target_date)), not rows
MIN_GROUP_N = 60      # in UNIQUE EVENTS
MIN_CITY_N = 30        # in UNIQUE EVENTS
CITY_SHRINKAGE_N0 = 100
# [0.25, 4.0] matches the materializer's strict acceptance range (FIX 6) exactly -- a served k
# outside this range is nonphysical for a sigma SCALE factor, so the optimizer is bounded to the
# same range the consumer will actually accept.
K_BOUNDS = (0.25, 4.0)
C_BOUNDS = (0.25, 4.0)
_BOUND_PIN_TOL = 1e-4      # a fitted scale within this of a search bound is treated as PINNED (the
                            # true optimum is outside the physically sane range), not accepted.
HOLDOUT_FRACTION = 0.25    # internal OOS-gate holdout: the chronologically LAST 25% of a group's
                            # WHOLE TARGET DATES (FIX 4 -- date-blocked, not per-event), used only
                            # when the operator did not supply --validate.
MIN_HOLDOUT_EVENTS = 10     # below this, the gate cannot be evaluated reliably -> refuse.
OOS_MARGIN_NATS = 0.01      # FIX 3: the gate requires a PREDECLARED margin, not merely delta > 0 --
                             # protects against a razor-thin, noise-driven "pass".

# MODEL SELECTION LAW (B2, deep-review 2026-07-28): the global-k-only comparison rung (previously
# REPORTED only) is now AUTHORITATIVE. Three outcomes, in order:
#   1. NEUTRAL      if global_delta <= OOS_MARGIN_NATS       (even the simplest flat correction
#                    doesn't clear the margin -- ship k=1.0, fitted=False).
#   2. GLOBAL_K_V1   if (full_delta - global_delta) <= OOS_MARGIN_NATS (the bucket+city structure
#                    does not meaningfully beat a single flat k for the whole group -- ship ONE
#                    k applied uniformly to every bucket, no city variation; simpler model wins
#                    on a tie or a non-meaningful edge).
#   3. BUCKET_CITY_K_V1 otherwise (the full lead-bucket + per-city structure earns its complexity
#                    by beating the flat model by more than the margin).
# This prevents shipping bucket/city granularity that is indistinguishable from noise relative to
# the simpler model -- Occam's razor enforced by evidence, not by fiat.
MODEL_TYPE_NEUTRAL = "neutral"
MODEL_TYPE_GLOBAL_K_V1 = "global_k_v1"
MODEL_TYPE_BUCKET_CITY_K_V1 = "bucket_city_k_v1"

_POST_QUERY = """
    SELECT city, target_date, temperature_metric, computed_at, source_cycle_time, posterior_config_hash,
           json_extract(provenance_json,'$.bayes_precision_fusion.anchor_value_c') AS mu,
           json_extract(provenance_json,'$.bayes_precision_fusion.predictive_sigma_c') AS sig,
           json_extract(provenance_json,'$.day0_conditioning.active') AS day0_active,
           json_extract(provenance_json,'$.day0_conditioning.observed_extreme_c') AS day0_observed_extreme_c,
           json_extract(provenance_json,'$.day0_remaining_center_delta_c') AS day0_center_delta_c
    FROM forecast_posteriors
    WHERE computed_at >= ?
      AND json_extract(provenance_json,'$.bayes_precision_fusion.anchor_value_c') IS NOT NULL
      AND json_extract(provenance_json,'$.bayes_precision_fusion.predictive_sigma_c') IS NOT NULL
      AND json_extract(provenance_json,'$.bayes_precision_fusion.current_evidence_shape') IS NOT NULL
"""
# FIX 5: settlement_outcomes (NOT the raw `settlements` table), authority='VERIFIED' -- the exact
# precedent shape scripts/fit_sigma_scale.py:134 already uses for calibration fitting.
_SETT_QUERY = """
    SELECT city, target_date, temperature_metric, settlement_value, settlement_unit AS unit
    FROM settlement_outcomes
    WHERE authority = 'VERIFIED' AND settlement_value IS NOT NULL AND settlement_unit IS NOT NULL
"""


class FitFailure(Exception):
    """Raised when the interval-censored optimizer cannot produce a trustworthy scale: a
    non-finite objective anywhere sampled, optimizer non-convergence, or a result pinned exactly at
    a search bound (pinning means the true optimum is outside the physically sane range, not that
    the bound IS the answer). Callers convert this into an explicit refusal -- never a silently
    wrong k."""


def tau_bucket(lead_h: "pd.Series") -> "pd.Series":
    return pd.cut(lead_h, bins=TAU_EDGES, labels=TAU_LABELS, right=False)


def _connect_ro(fcst_path: str) -> sqlite3.Connection:
    """Accept a plain filesystem path OR a ``file:`` URI. Force ``mode=ro`` either way -- an
    explicit OTHER mode in a supplied URI is REJECTED (not silently overridden), so a caller cannot
    accidentally open this read-only fitter against a writable connection. ``PRAGMA query_only=ON``
    is set on the resulting connection as defense-in-depth regardless of path (FIX 8)."""
    if fcst_path.startswith("file:"):
        parsed = urllib.parse.urlsplit(fcst_path)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        modes = params.get("mode")
        if modes is not None and modes != ["ro"]:
            raise ValueError(f"--fcst URI must use mode=ro (or omit mode), got mode={modes!r}: {fcst_path}")
        params["mode"] = ["ro"]
        new_query = urllib.parse.urlencode(params, doseq=True)
        uri = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(f"file:{fcst_path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _city_metadata() -> dict[str, dict]:
    """city name -> {"timezone": IANA str, "rounding_rule": RoundingRule str}, via the canonical
    config/cities.json loader (src.config.load_cities) and the canonical settlement-rounding
    dispatch (src.contracts.settlement_semantics.SettlementSemantics.for_city). NEVER re-derive
    either mapping inline (FIX 1 / FIX 2)."""
    out: dict[str, dict] = {}
    for c in load_cities():
        out[c.name] = {
            "timezone": c.timezone,
            "rounding_rule": SettlementSemantics.for_city(c).rounding_rule,
        }
    return out


_ZONEINFO_CACHE: dict[str, ZoneInfo] = {}


def _zoneinfo(tz_name: str) -> ZoneInfo:
    zi = _ZONEINFO_CACHE.get(tz_name)
    if zi is None:
        zi = ZoneInfo(tz_name)
        _ZONEINFO_CACHE[tz_name] = zi
    return zi


def _local_target_end_utc(target_date: str, tz_name: str) -> _dt.datetime:
    """The local END of target_date (next local midnight) in tz_name, DST-aware, as UTC (FIX 1).

    Markets settle on the CITY'S LOCAL date, not UTC. The prior cut used
    target_date+1day 00:00 UTC universally, misassigning tau buckets by up to the city's UTC
    offset (Shanghai +8h, Chicago -5h)."""
    next_local_date = _dt.date.fromisoformat(target_date) + _dt.timedelta(days=1)
    local_midnight = _dt.datetime(
        next_local_date.year, next_local_date.month, next_local_date.day, tzinfo=_zoneinfo(tz_name)
    )
    return local_midnight.astimezone(_dt.timezone.utc)


def load(fcst_path: str, since: str) -> tuple["pd.DataFrame", "pd.DataFrame"]:
    """FIX 5: both SELECTs run inside one BEGIN read transaction for a consistent snapshot."""
    conn = _connect_ro(fcst_path)
    try:
        conn.execute("BEGIN")
        post = pd.read_sql_query(_POST_QUERY, conn, params=(since,))
        sett = pd.read_sql_query(_SETT_QUERY, conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return post, sett


def build_frame(post: "pd.DataFrame", sett: "pd.DataFrame", city_meta: dict[str, dict]) -> tuple["pd.DataFrame", dict]:
    """Join, attach city timezone/rounding-rule metadata (FIX 1/2), compute both tau clocks using
    the LOCAL target end, compute interval-censored bin edges using the per-city rounding-rule
    preimage, and apply the computed_at-before-local-end population fence (FIX 5). Returns
    (frame, drop_stats)."""
    post = post.copy()
    post["mu"] = post["mu"].astype(float)
    post["sig"] = post["sig"].astype(float)
    post["computed_at_dt"] = pd.to_datetime(post["computed_at"], utc=True)
    post["source_cycle_time_dt"] = pd.to_datetime(post["source_cycle_time"], utc=True)
    # MEDIUM (deep-review 2026-07-28): dedup identity is (source_cycle_time, posterior_config_hash),
    # not a wall-clock hour floor -- config_hash already changes whenever the anchor/sigma/current-
    # evidence-shape inputs that PRODUCED the row change, so it collapses only truly-redundant
    # re-materializations (idempotent retries), never a genuine recompute. A row with no config_hash
    # (older data) falls back to its exact computed_at -- never silently collapsed with a neighbor.
    post["dedup_key"] = post["posterior_config_hash"].where(
        post["posterior_config_hash"].notna(), post["computed_at"]
    )

    df = post.merge(sett, on=["city", "target_date", "temperature_metric"], how="inner")
    n_joined_raw = len(df)

    # FIX 1/2: attach per-city timezone + rounding rule; DROP rows for a city absent from the
    # canonical config (fail-closed -- never default to UTC or wmo_half_up for an unknown city).
    df["tz"] = df["city"].map(lambda c: (city_meta.get(c) or {}).get("timezone"))
    df["rounding_rule"] = df["city"].map(lambda c: (city_meta.get(c) or {}).get("rounding_rule"))
    unknown_cities = sorted(df.loc[df["tz"].isna() | df["rounding_rule"].isna(), "city"].unique().tolist())
    df = df.dropna(subset=["tz", "rounding_rule"]).copy()
    n_dropped_unknown_city = n_joined_raw - len(df)

    # FIX 1: LOCAL target end, computed once per unique (city,target_date) pair (far fewer than
    # rows), then merged back -- DST-aware via zoneinfo. `.apply(axis=1)` on an EMPTY frame returns
    # an empty DataFrame (not a Series), which pandas then refuses to assign into a single column
    # -- guard the empty case explicitly rather than let every all-rows-dropped case crash.
    unique_pairs = df[["city", "target_date", "tz"]].drop_duplicates().copy()
    if len(unique_pairs) == 0:
        unique_pairs["target_end_utc"] = pd.Series([], dtype="datetime64[ns, UTC]")
    else:
        unique_pairs["target_end_utc"] = unique_pairs.apply(
            lambda r: _local_target_end_utc(r["target_date"], r["tz"]), axis=1
        )
    df = df.merge(unique_pairs[["city", "target_date", "target_end_utc"]], on=["city", "target_date"], how="left")

    df["lead_issue_h"] = (df["target_end_utc"] - df["source_cycle_time_dt"]).dt.total_seconds() / 3600.0
    df["lead_decision_h"] = (df["target_end_utc"] - df["computed_at_dt"]).dt.total_seconds() / 3600.0

    # FIX 2: settlement quantizer -- per-city rounding-rule preimage
    # (src.contracts.settlement_semantics.settlement_preimage_offsets), NOT a universal
    # [v-0.5, v+0.5). Hong Kong (oracle_truncate) has preimage [v, v+1).
    offsets = df["rounding_rule"].map(lambda r: settlement_preimage_offsets(r, half_step=0.5))
    low_offset = offsets.map(lambda o: o[0]).astype(float)
    high_offset = offsets.map(lambda o: o[1]).astype(float)
    is_f = df["unit"] == "F"
    df["settled_c"] = np.where(is_f, (df["settlement_value"] - 32.0) * 5.0 / 9.0, df["settlement_value"])
    df["z"] = df["settled_c"] - df["mu"]
    lower_native = df["settlement_value"] + low_offset
    upper_native = df["settlement_value"] + high_offset
    df["bin_lower_c"] = np.where(is_f, (lower_native - 32.0) * 5.0 / 9.0, lower_native)
    df["bin_upper_c"] = np.where(is_f, (upper_native - 32.0) * 5.0 / 9.0, upper_native)
    df["l"] = df["bin_lower_c"] - df["mu"]
    df["u"] = df["bin_upper_c"] - df["mu"]
    df["unit_family"] = df["unit"]
    df["event_key"] = df["city"].astype(str) + "|" + df["target_date"].astype(str)

    # B1 (deep-review 2026-07-28) fit/serve parity: join the causal Day0 state stamped on the
    # posterior itself (never re-derived) -- a row where Day0 was active must be scored under the
    # max/min absorbing-observed-extreme transform, not raw Normal (served_settlement_log_probability
    # below dispatches on these exact columns, matching _compute_posterior_payload's own dispatch).
    df["day0_active"] = df["day0_active"].eq(1)
    df["day0_observed_extreme_c"] = pd.to_numeric(df["day0_observed_extreme_c"], errors="coerce")
    df["day0_center_delta_c"] = pd.to_numeric(df["day0_center_delta_c"], errors="coerce").fillna(0.0)

    # FIX 5 (second half): the CURRENT-EVIDENCE serving population never materializes a posterior
    # after the local target day already ended -- fence training rows to the same population.
    n_before_fence = len(df)
    df = df[df["computed_at_dt"] < df["target_end_utc"]].copy()
    n_dropped_computed_at_after_local_end = n_before_fence - len(df)

    drop_stats = dict(
        n_joined_raw=n_joined_raw,
        n_dropped_unknown_city=n_dropped_unknown_city,
        unknown_cities=unknown_cities,
        n_dropped_computed_at_after_local_target_end=n_dropped_computed_at_after_local_end,
    )
    return df, drop_stats


def dedup(df: "pd.DataFrame") -> "pd.DataFrame":
    """Keep only the LAST posterior per INFORMATION IDENTITY -- (city, target_date,
    temperature_metric, source_cycle_time, dedup_key), not a wall-clock hour floor (MEDIUM,
    deep-review 2026-07-28). ``dedup_key`` is ``posterior_config_hash`` (falls back to the exact
    ``computed_at`` when absent) -- see ``build_frame``. An hour-floor key both under-counts (two
    genuinely different source_cycle_time issues whose computed_at happens to land in the same wall
    hour get wrongly merged -- source_cycle_time was never even part of the old key) and over-counts
    (an idempotent re-materialization retry that straddles an hour boundary wrongly survives as two
    rows)."""
    return df.sort_values("computed_at_dt").drop_duplicates(
        subset=["city", "target_date", "temperature_metric", "source_cycle_time", "dedup_key"], keep="last"
    )


def prep(fcst_path: str, since: str) -> tuple["pd.DataFrame", dict]:
    city_meta = _city_metadata()
    post, sett = load(fcst_path, since)
    df, drop_stats = build_frame(post, sett, city_meta)
    d = dedup(df)
    n_before_negative_lead = len(d)
    d = d[d["lead_issue_h"] >= 0.0].copy()
    n_dropped_negative_lead = n_before_negative_lead - len(d)
    d["taut"] = tau_bucket(d["lead_issue_h"])
    d["taut_decision"] = tau_bucket(d["lead_decision_h"])
    stats = dict(
        n_posteriors_read=len(post),
        n_settlements_read=len(sett),
        n_joined_dedup=n_before_negative_lead,
        n_dropped_negative_lead=n_dropped_negative_lead,
        n_final=len(d),
        **drop_stats,
    )
    return d, stats


def _add_event_weights(sub: "pd.DataFrame") -> "pd.DataFrame":
    """event_weight = 1/(row count of this event WITHIN sub) -- each unique (city,target_date)
    event's rows sum to weight exactly 1 across the population passed in (a group, not re-derived
    per bucket -- an event spanning two buckets contributes a fraction of its unit weight to each,
    matching a single fixed design weight per event)."""
    sub = sub.copy()
    counts = sub.groupby("event_key")["event_key"].transform("count")
    sub["event_weight"] = 1.0 / counts
    return sub


def _weighted_mean(x: "np.ndarray", w: "np.ndarray") -> float:
    return float(np.sum(w * x) / np.sum(w))


def _weighted_var(x: "np.ndarray", w: "np.ndarray") -> float:
    """Reliability-weights unbiased variance (reduces to the standard ddof=1 formula for equal
    weights); NaN if the effective sample size is <=1."""
    mean = _weighted_mean(x, w)
    sw = float(np.sum(w))
    sw2 = float(np.sum(w ** 2))
    denom = sw - sw2 / sw if sw > 0 else 0.0
    if denom <= 0:
        return float("nan")
    return float(np.sum(w * (x - mean) ** 2) / denom)


def weighted_spread_skill_k(z: "np.ndarray", sig: "np.ndarray", w: "np.ndarray") -> float:
    """Event-weighted std(z)/rms(sig) -- the CROSS-CHECK estimator (was the primary before the
    2026-07-28 design-review correction; kept as k_normal_crosscheck)."""
    var_z = _weighted_var(z, w)
    mean_sig2 = _weighted_mean(sig ** 2, w)
    if not (math.isfinite(var_z) and var_z >= 0.0 and math.isfinite(mean_sig2) and mean_sig2 > 0.0):
        return float("nan")
    return float(math.sqrt(var_z / mean_sig2))


def _log_interval_prob(lo: "np.ndarray", hi: "np.ndarray", sigma: "np.ndarray") -> "np.ndarray":
    """log(Phi(hi/sigma) - Phi(lo/sigma)), numerically stable (FIX 3).

    Replaces the naive ``norm.cdf(hi/sigma) - norm.cdf(lo/sigma)`` (which silently underflows to
    exactly 0.0, hence log(0)=-inf, for any interval more than ~9 sigma from center) with a
    logcdf/logsf branch chosen per-row to keep both terms away from catastrophic cancellation:
    the CDF branch when the interval sits mostly left of center, the SURVIVAL-FUNCTION branch
    (Phi(hi)-Phi(lo) = SF(lo)-SF(hi)) when it sits mostly right of center."""
    a = np.asarray(hi, dtype=float) / sigma
    b = np.asarray(lo, dtype=float) / sigma
    use_sf = (a + b) > 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        log_cdf_a = norm.logcdf(a)
        log_cdf_b = norm.logcdf(b)
        log_sf_a = norm.logsf(a)
        log_sf_b = norm.logsf(b)
        cdf_branch = log_cdf_a + np.log1p(-np.exp(np.clip(log_cdf_b - log_cdf_a, -700.0, 0.0)))
        sf_branch = log_sf_b + np.log1p(-np.exp(np.clip(log_sf_a - log_sf_b, -700.0, 0.0)))
    return np.where(use_sf, sf_branch, cdf_branch)


def _censored_log_prob(g: "pd.DataFrame", sigma: "np.ndarray") -> "np.ndarray":
    """Per-row log P(settlement in bin) at the given FINAL predictive sigma (``sigma_base * scale``,
    already scale-applied by the caller) -- B1 (deep-review 2026-07-28) fit/serve parity.

    ``g`` must carry ``l``/``u`` (mu-centered bin offsets), plus the joined Day0 causal state
    (``day0_active``, ``day0_observed_extreme_c``, ``day0_center_delta_c``) and the absolute fields
    needed to score those rows (``mu``, ``temperature_metric``, ``bin_lower_c``, ``bin_upper_c``,
    ``rounding_rule``) -- see ``build_frame``. Non-Day0 rows score through the fast vectorized
    ``_log_interval_prob``; Day0-active rows score through ``served_settlement_log_probability``
    (the SAME max/min absorbing-observed-extreme transform serving applies), one row at a time --
    Day0-active rows are a narrow lead-time slice, so the scalar path costs nothing at corpus scale."""
    log_p = _log_interval_prob(g["l"].values, g["u"].values, sigma)
    day0_mask = g["day0_active"].values
    if day0_mask.any():
        log_p = log_p.copy()
        sigma_arr = np.asarray(sigma, dtype=float)
        if sigma_arr.ndim == 0:
            sigma_arr = np.full(len(g), float(sigma_arr))
        for pos in np.flatnonzero(day0_mask):
            row = g.iloc[pos]
            day0_obs = row["day0_observed_extreme_c"]
            log_p[pos] = served_settlement_log_probability(
                anchor_value_c=float(row["mu"]),
                predictive_sigma_c=float(sigma_arr[pos]),
                k=1.0,
                metric=str(row["temperature_metric"]),
                bin_low_c=float(row["bin_lower_c"]),
                bin_high_c=float(row["bin_upper_c"]),
                half_step=0.5,
                rounding_rule=str(row["rounding_rule"]),
                day0_observed_extreme_c=None if pd.isna(day0_obs) else float(day0_obs),
                day0_center_delta_c=float(row["day0_center_delta_c"]),
            )
    return log_p


def _interval_censored_negloglik(
    scale: float, g: "pd.DataFrame", sigma_base: "np.ndarray", w: "np.ndarray"
) -> float:
    sigma = scale * sigma_base
    if not np.all(np.isfinite(sigma)) or np.any(sigma <= 0.0):
        return math.inf
    log_p = _censored_log_prob(g, sigma)
    if not np.all(np.isfinite(log_p)):
        # FIX 3: no clip -- a row whose log-probability is non-finite makes the objective explicitly
        # undefined for this scale, rather than silently masquerading as a small finite penalty.
        return math.inf
    return float(-np.sum(w * log_p))


def fit_interval_censored_scale(
    g: "pd.DataFrame", sigma_base: "np.ndarray", w: "np.ndarray", bounds: tuple[float, float]
) -> float:
    """argmax_scale of the (weighted) interval-censored bin likelihood -- the PRIMARY estimator.
    A 1-D bounded MLE (no closed form); cheap even at corpus scale (single scalar optimization).
    Raises FitFailure (FIX 3) on optimizer non-convergence, a non-finite result, or a result pinned
    at a search bound (the true optimum would be outside the physically sane range)."""
    res = minimize_scalar(
        _interval_censored_negloglik, bounds=bounds, method="bounded", args=(g, sigma_base, w)
    )
    if not res.success:
        raise FitFailure(f"optimizer did not converge: {res.message}")
    if not (math.isfinite(res.x) and math.isfinite(res.fun)):
        raise FitFailure(f"optimizer returned a non-finite result: x={res.x} fun={res.fun}")
    lo_bound, hi_bound = bounds
    if abs(res.x - lo_bound) < _BOUND_PIN_TOL or abs(res.x - hi_bound) < _BOUND_PIN_TOL:
        raise FitFailure(f"optimizer pinned at a search bound: x={res.x} bounds={bounds}")
    return float(res.x)


def fit_k_by_tau(sub: "pd.DataFrame") -> dict:
    """PRIMARY k(tau) = interval-censored MLE scale per tau bucket (event-weighted); a bucket with
    n_events < MIN_BUCKET_N is UNFITTED and inherits the group-global pooled k. A bucket whose own
    fit raises FitFailure is ALSO treated as unfitted (inherits global), flagged distinctly. If the
    GLOBAL fit itself fails, the whole result is marked not-ok (the caller refuses the group).
    Returns {"ok": bool, "reason": str|None, "global_k", "global_k_normal_crosscheck",
    "n_events_group", "buckets"}."""
    w = sub["event_weight"].values
    try:
        global_k = fit_interval_censored_scale(sub, sub["sig"].values, w, K_BOUNDS)
    except FitFailure as exc:
        return {"ok": False, "reason": f"GLOBAL_FIT_FAILED:{exc}"}
    global_k_normal = weighted_spread_skill_k(sub["z"].values, sub["sig"].values, w)
    n_events_group = sub["event_key"].nunique()
    buckets: dict = {}
    for label in TAU_LABELS:
        g = sub[sub["taut"] == label]
        n_rows = len(g)
        n_events = g["event_key"].nunique()
        if n_events >= MIN_BUCKET_N:
            try:
                k = fit_interval_censored_scale(g, g["sig"].values, g["event_weight"].values, K_BOUNDS)
                k_normal = weighted_spread_skill_k(g["z"].values, g["sig"].values, g["event_weight"].values)
                buckets[label] = {"k": k, "k_normal_crosscheck": k_normal, "n": int(n_rows), "n_events": int(n_events), "fitted": True}
            except FitFailure as exc:
                buckets[label] = {
                    "k": global_k, "k_normal_crosscheck": global_k_normal, "n": int(n_rows),
                    "n_events": int(n_events), "fitted": False, "bucket_fit_failed_reason": str(exc),
                }
        else:
            buckets[label] = {"k": global_k, "k_normal_crosscheck": global_k_normal, "n": int(n_rows), "n_events": int(n_events), "fitted": False}
    return {
        "ok": True, "reason": None, "global_k": global_k, "global_k_normal_crosscheck": global_k_normal,
        "n_events_group": n_events_group, "buckets": buckets,
    }


def fit_city_shrinkage(sub: "pd.DataFrame", k_by_bucket: dict) -> dict:
    """c_raw = interval-censored MLE scale on top of the (already-fitted) k(tau)*sig, per city
    (n_events >= MIN_CITY_N, pooled over tau), shrunk toward 1 via
    c_shrunk^2 = (n_e*c_raw^2 + N0) / (n_e + N0). A city whose fit raises FitFailure is SKIPPED
    (not included in the returned map -- same fail-soft posture as insufficient events).

    COMPOSED BOUND (deep-review, 2026-07-28): the SERVED k_eff = k(tau) * c_shrunk must itself lie
    in [0.25, 4.0] -- each factor individually passing its own [0.25, 4.0] range does NOT guarantee
    the PRODUCT does (e.g. 4.0 * 4.0 = 16.0). A city is REJECTED (omitted, same as insufficient
    events) if its c_shrunk, composed with ANY of the group's bucket k values (all 7, including
    inherited-global ones), would escape [K_BOUNDS[0], K_BOUNDS[1]] -- the fitter must never emit a
    city correction the strict loader (which enforces the SAME composed bound) would reject anyway."""
    k_row = sub["taut"].map(lambda lab: (k_by_bucket.get(lab) or {}).get("k")).astype(float)
    valid = k_row.notna() & (k_row > 0.0)
    tmp = sub[valid].copy()
    tmp["k_row"] = k_row[valid]
    tmp["sigma_base"] = tmp["k_row"] * tmp["sig"]
    all_bucket_ks = [b.get("k") for b in k_by_bucket.values() if isinstance(b.get("k"), (int, float))]
    cities: dict = {}
    for city, g in tmp.groupby("city"):
        n_events = g["event_key"].nunique()
        if n_events < MIN_CITY_N:
            continue
        w = g["event_weight"].values
        try:
            c_raw = fit_interval_censored_scale(g, g["sigma_base"].values, w, C_BOUNDS)
        except FitFailure:
            continue
        c_raw_normal = weighted_spread_skill_k(g["z"].values, g["sigma_base"].values, w)
        c_raw2 = c_raw ** 2
        c_shrunk2 = (n_events * c_raw2 + CITY_SHRINKAGE_N0) / (n_events + CITY_SHRINKAGE_N0)
        c_shrunk = math.sqrt(c_shrunk2)
        composed = [bk * c_shrunk for bk in all_bucket_ks]
        if any(not (K_BOUNDS[0] <= comp <= K_BOUNDS[1]) for comp in composed):
            continue  # a composed k_eff would escape the served range -- reject this city
        cities[str(city)] = {
            "c_raw": round(c_raw, 6),
            "c_raw_normal_crosscheck": round(c_raw_normal, 6) if math.isfinite(c_raw_normal) else None,
            "c_shrunk": round(c_shrunk, 6),
            "n": int(len(g)),
            "n_events": int(n_events),
        }
    return cities


def fit_group(sub_raw: "pd.DataFrame") -> dict:
    sub = _add_event_weights(sub_raw)
    n_events_group = sub["event_key"].nunique()
    n_rows_group = len(sub)
    if n_events_group < MIN_GROUP_N:
        return {
            "fitted": False,
            "model_type": MODEL_TYPE_NEUTRAL,
            "global_k": 1.0,
            "global_k_normal_crosscheck": None,
            "n": int(n_rows_group),
            "n_events": int(n_events_group),
            "refusal_reason": f"INSUFFICIENT_EVENTS:{n_events_group}<{MIN_GROUP_N}",
            "buckets": {},
            "cities": {},
        }
    fit_result = fit_k_by_tau(sub)
    if not fit_result["ok"]:
        return {
            "fitted": False,
            "model_type": MODEL_TYPE_NEUTRAL,
            "global_k": 1.0,
            "global_k_normal_crosscheck": None,
            "n": int(n_rows_group),
            "n_events": int(n_events_group),
            "refusal_reason": fit_result["reason"],
            "buckets": {},
            "cities": {},
        }
    buckets = fit_result["buckets"]
    global_k = fit_result["global_k"]
    global_k_normal = fit_result["global_k_normal_crosscheck"]
    cities = fit_city_shrinkage(sub, buckets)
    return {
        "fitted": True,
        "global_k": round(float(global_k), 6),
        "global_k_normal_crosscheck": round(float(global_k_normal), 6) if math.isfinite(global_k_normal) else None,
        "n": int(n_rows_group),
        "n_events": int(n_events_group),
        "buckets": {
            lab: {
                **b,
                "k": round(float(b["k"]), 6) if b["k"] is not None else None,
                "k_normal_crosscheck": round(float(b["k_normal_crosscheck"]), 6) if b["k_normal_crosscheck"] is not None and math.isfinite(b["k_normal_crosscheck"]) else None,
            }
            for lab, b in buckets.items()
        },
        "cities": cities,
    }


def _k_eff_row(group: dict, taut: "pd.Series", city: "pd.Series") -> "pd.Series":
    bucket_k = taut.map(lambda lab: (group["buckets"].get(lab) or {}).get("k") if group.get("buckets") else None)
    bucket_k = bucket_k.fillna(group.get("global_k", 1.0)).astype(float)
    cities = group.get("cities") or {}
    c_shrunk = city.map(lambda c: (cities.get(str(c)) or {}).get("c_shrunk", 1.0)).astype(float)
    return bucket_k * c_shrunk


def normal_logpdf(z, sigma):
    return -0.5 * np.log(2 * np.pi) - np.log(sigma) - (z ** 2) / (2 * sigma ** 2)


def _censored_oos_delta(group: dict, holdout_sub: "pd.DataFrame") -> tuple[float, float, float, float, int]:
    """Event-weighted OOS mean log-lik delta (fitted vs k=1) of an ALREADY-FITTED group against
    holdout_sub, under BOTH the primary interval-censored likelihood and the Normal-density
    cross-check. Returns (censored_delta, normal_delta, coverage_base, coverage_fit,
    n_holdout_events); any of the first four are NaN if k_eff or the log-likelihoods are non-finite
    anywhere (FIX 3 -- the gate's caller must reject NaN explicitly, since NaN <= threshold is
    always False in Python and would otherwise fail OPEN). Shared by the CLI --validate report and
    the OOS acceptance gate so the two never compute the number two different ways."""
    val = _add_event_weights(holdout_sub)
    w = val["event_weight"].values
    k_eff = _k_eff_row(group, val["taut"], val["city"]).values
    n_holdout_events = int(val["event_key"].nunique())
    if not np.all(np.isfinite(k_eff)) or np.any(k_eff <= 0.0):
        return float("nan"), float("nan"), float("nan"), float("nan"), n_holdout_events

    ll_censored_base = _censored_log_prob(val, val["sig"].values)
    ll_censored_fit = _censored_log_prob(val, k_eff * val["sig"].values)
    ll_normal_base = normal_logpdf(val["z"].values, val["sig"].values)
    ll_normal_fit = normal_logpdf(val["z"].values, k_eff * val["sig"].values)
    if not (
        np.all(np.isfinite(ll_censored_base)) and np.all(np.isfinite(ll_censored_fit))
        and np.all(np.isfinite(ll_normal_base)) and np.all(np.isfinite(ll_normal_fit))
    ):
        return float("nan"), float("nan"), float("nan"), float("nan"), n_holdout_events

    def wmean(x):
        return float(np.sum(w * x) / np.sum(w))

    cov_base = float(np.sum(w * (val["z"].abs() <= val["sig"]).values) / np.sum(w))
    cov_fit = float(np.sum(w * (val["z"].abs() <= (k_eff * val["sig"])).values) / np.sum(w))
    censored_delta = wmean(ll_censored_fit) - wmean(ll_censored_base)
    normal_delta = wmean(ll_normal_fit) - wmean(ll_normal_base)
    return censored_delta, normal_delta, cov_base, cov_fit, n_holdout_events


def _censored_oos_delta_flat_k(flat_k: float, holdout_sub: "pd.DataFrame") -> float:
    """Event-weighted OOS censored delta (ONE flat k applied to every row, no bucket/city
    indexing, vs k=1) -- the global-k-only comparison rung (FIX 4). REPORTED ONLY, never gates:
    shows whether lead-bucket indexing earns its complexity over a single flat correction."""
    val = _add_event_weights(holdout_sub)
    w = val["event_weight"].values
    if not (math.isfinite(flat_k) and flat_k > 0.0):
        return float("nan")
    ll_base = _censored_log_prob(val, val["sig"].values)
    ll_flat = _censored_log_prob(val, flat_k * val["sig"].values)
    if not (np.all(np.isfinite(ll_base)) and np.all(np.isfinite(ll_flat))):
        return float("nan")

    def wmean(x):
        return float(np.sum(w * x) / np.sum(w))

    return wmean(ll_flat) - wmean(ll_base)


def _split_holdout_by_target_date(sub: "pd.DataFrame", frac: float = HOLDOUT_FRACTION) -> tuple["pd.DataFrame", "pd.DataFrame"]:
    """Split sub into (train, holdout): holdout = the chronologically LAST `frac` fraction of this
    group's WHOLE TARGET DATES (FIX 4 -- date-blocked, not per-event: a same-date split would let
    correlated same-day cross-city outcomes leak across the train/holdout boundary). This is the
    INTERNAL OOS-gate split used by the default (no --validate) production fit -- see main()."""
    dates = sorted(sub["target_date"].unique())
    n_dates = len(dates)
    n_holdout = max(1, math.ceil(n_dates * frac))
    holdout_dates = set(dates[-n_holdout:])
    is_holdout = sub["target_date"].isin(holdout_dates)
    return sub[~is_holdout].copy(), sub[is_holdout].copy()


def _flatten_to_global_k_v1(train_group: dict) -> dict:
    """Rebuild a fitted group as the GLOBAL_K_V1 shape: every bucket carries the SAME global_k
    (fitted=True uniformly), cities is empty. Achieves "one flat k for the whole group" using the
    EXISTING bucket-lookup mechanism (no special-case logic needed at the serving site) -- a
    lookup for ANY bucket or ANY city on this group resolves to exactly global_k."""
    global_k = train_group["global_k"]
    global_k_normal = train_group.get("global_k_normal_crosscheck")
    flat_buckets = {
        lab: {
            "k": global_k,
            "k_normal_crosscheck": global_k_normal,
            "n": (b.get("n", 0) if isinstance(b, dict) else 0),
            "n_events": (b.get("n_events", 0) if isinstance(b, dict) else 0),
            "fitted": True,
        }
        for lab, b in train_group["buckets"].items()
    }
    result = dict(train_group)
    result["buckets"] = flat_buckets
    result["cities"] = {}
    return result


def gate_group(train_sub: "pd.DataFrame", holdout_sub: "pd.DataFrame", *, gate_method: str) -> dict:
    """Fit on train_sub, then apply the THREE-WAY model selection law (B2, deep-review 2026-07-28):
      1. NEUTRAL if the flat global-k-only OOS delta doesn't itself clear OOS_MARGIN_NATS -- even
         the simplest correction has no evidence.
      2. GLOBAL_K_V1 if the full bucket+city model does not beat the flat global-k model by more
         than OOS_MARGIN_NATS -- ships ONE k applied uniformly (see _flatten_to_global_k_v1).
      3. BUCKET_CITY_K_V1 otherwise -- the full lead-bucket + per-city structure earns its
         complexity by evidence, not by default.
    NaN deltas are REJECTED explicitly (FIX 3 -- `NaN <= margin` is always False in Python, so a
    naive `<=` check would fail OPEN for NaN). A group that already refused for insufficient TRAIN
    events, a fit failure, or too few HOLDOUT events to gate reliably, is returned fitted=False
    with the exact reason recorded. A group that ships (either model type) uses the TRAIN split's
    coefficients UNCHANGED (FIX 4 -- no full-data refit, which could activate buckets/cities never
    actually OOS-scored)."""
    train_group = fit_group(train_sub)
    if not train_group["fitted"]:
        return train_group  # insufficient TRAIN events or a fit failure -- reason already set
    n_holdout_events = holdout_sub["event_key"].nunique()
    if n_holdout_events < MIN_HOLDOUT_EVENTS:
        return {
            "fitted": False,
            "model_type": MODEL_TYPE_NEUTRAL,
            "global_k": 1.0,
            "global_k_normal_crosscheck": None,
            "n": int(len(train_sub)),
            "n_events": int(train_group["n_events"]),
            "refusal_reason": f"INSUFFICIENT_HOLDOUT_EVENTS:{n_holdout_events}<{MIN_HOLDOUT_EVENTS}",
            "buckets": {},
            "cities": {},
            "oos_gate": {"passed": False, "method": gate_method, "n_holdout_events": int(n_holdout_events)},
        }
    full_delta, normal_delta, cov_base, cov_fit, n_ho = _censored_oos_delta(train_group, holdout_sub)
    global_delta = _censored_oos_delta_flat_k(train_group["global_k"], holdout_sub)

    global_ok = math.isfinite(global_delta) and global_delta > OOS_MARGIN_NATS
    if not global_ok:
        reason = (
            f"NEUTRAL:global_delta={global_delta:.5f}<=margin({OOS_MARGIN_NATS})"
            if math.isfinite(global_delta)
            else "NEUTRAL:non_finite_global_delta"
        )
        return {
            "fitted": False,
            "model_type": MODEL_TYPE_NEUTRAL,
            "global_k": 1.0,
            "global_k_normal_crosscheck": None,
            "n": int(len(train_sub)),
            "n_events": int(train_group["n_events"]),
            "refusal_reason": reason,
            "buckets": {},
            "cities": {},
            "oos_gate": {
                "passed": False, "method": gate_method,
                "censored_delta": round(full_delta, 6) if math.isfinite(full_delta) else None,
                "normal_delta_crosscheck": round(normal_delta, 6) if math.isfinite(normal_delta) else None,
                "global_k_only_censored_delta": round(global_delta, 6) if math.isfinite(global_delta) else None,
                "n_holdout_events": int(n_ho), "margin_required_nats": OOS_MARGIN_NATS,
            },
        }

    beats_global_by = (full_delta - global_delta) if math.isfinite(full_delta) else -math.inf
    bucket_city_earns_it = math.isfinite(beats_global_by) and beats_global_by > OOS_MARGIN_NATS

    oos_gate = {
        "passed": True, "method": gate_method,
        "censored_delta": round(full_delta, 6) if math.isfinite(full_delta) else None,
        "normal_delta_crosscheck": round(normal_delta, 6) if math.isfinite(normal_delta) else None,
        "global_k_only_censored_delta": round(global_delta, 6),
        "full_beats_global_by": round(beats_global_by, 6) if math.isfinite(beats_global_by) else None,
        "n_holdout_events": int(n_ho), "margin_required_nats": OOS_MARGIN_NATS,
        "coverage_68_3_base": round(cov_base, 4), "coverage_68_3_fitted": round(cov_fit, 4),
    }
    if bucket_city_earns_it:
        # FIX 4: ship the TRAIN split's full bucket+city coefficients UNCHANGED -- no full-data refit.
        final_group = dict(train_group)
        final_group["model_type"] = MODEL_TYPE_BUCKET_CITY_K_V1
    else:
        final_group = _flatten_to_global_k_v1(train_group)
        final_group["model_type"] = MODEL_TYPE_GLOBAL_K_V1
    final_group["oos_gate"] = oos_gate
    return final_group


def validate(d: "pd.DataFrame", cutoff: str, *, tau_col: str, label: str) -> None:
    train_mask = d["target_date"] < cutoff
    val_mask = d["target_date"] >= cutoff
    print(f"[sigma-tau] validate cutoff={cutoff} clock={label} n_train_rows={int(train_mask.sum())}  n_val_rows={int(val_mask.sum())}")
    for unit, metric in GROUPS:
        train = d[train_mask & (d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        val = d[val_mask & (d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        # Overwrite (not rename-with-duplicate) "taut" with the requested clock's bucket labels --
        # both "taut" and "taut_decision" already exist on every row from prep(), so a rename would
        # create two columns named "taut" and break every downstream ["taut"] lookup.
        train["taut"] = train[tau_col]
        val["taut"] = val[tau_col]
        n_events_train = train["event_key"].nunique()
        n_events_val = val["event_key"].nunique()
        if n_events_train < MIN_GROUP_N or n_events_val < 10:
            print(f"  {unit}/{metric}: SKIP (n_events_train={n_events_train}, n_events_val={n_events_val})")
            continue
        group = fit_group(train)
        if not group["fitted"]:
            print(f"  {unit}/{metric}: TRAIN REFUSED ({group.get('refusal_reason')})")
            continue
        censored_delta, normal_delta, cov_base, cov_fit, _n_ho = _censored_oos_delta(group, val)
        flat_delta = _censored_oos_delta_flat_k(group["global_k"], val)
        gate_verdict = "PASS" if (math.isfinite(censored_delta) and censored_delta > OOS_MARGIN_NATS) else "REJECT"
        print(
            f"  {unit}/{metric}: n_events_train={n_events_train} n_events_val={n_events_val} "
            f"global_k={group.get('global_k')} (normal_crosscheck={group.get('global_k_normal_crosscheck')})\n"
            f"    censored  oos_mean_loglik delta:{censored_delta:+.5f}  GATE:{gate_verdict} (margin required:{OOS_MARGIN_NATS})\n"
            f"    normal_xc oos_mean_loglik delta:{normal_delta:+.5f}\n"
            f"    global_k_only (no bucket/city indexing) oos_mean_loglik delta:{flat_delta:+.5f}\n"
            f"    coverage@68.3 (event-weighted) k=1:{cov_base:.4f} fitted:{cov_fit:.4f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fcst", default=FCST_DEFAULT, help="zeus-forecasts.db (forecast_posteriors + settlement_outcomes), opened ?mode=ro.")
    ap.add_argument("--since", default=SINCE_DEFAULT, help="ISO date/datetime floor on computed_at (live current-evidence era start).")
    ap.add_argument("--out", default=None, help="output sigma_tau_calibration.json path (REQUIRED unless --validate).")
    ap.add_argument("--validate", default=None, metavar="CUTOFF", help="read-only OOS diagnostic: fit on target_date<CUTOFF, validate on target_date>=CUTOFF. When combined with --out, this cutoff ALSO governs the shipped gate.")
    args = ap.parse_args()

    if args.out is None and args.validate is None:
        ap.error("must supply --out (to write the artifact) or --validate CUTOFF (to report OOS numbers only)")

    d, stats = prep(args.fcst, args.since)
    print(f"[sigma-tau] {stats}")

    if args.validate is not None:
        validate(d, args.validate, tau_col="taut", label="PRIMARY(issue-clock)")
        validate(d, args.validate, tau_col="taut_decision", label="COMPARISON-ONLY(decision-clock)")
        if args.out is None:
            return 0

    # OOS ACCEPTANCE GATE: no group ships a k that has not proven a positive, margined censored OOS
    # delta -- the fail-closed principle applied to the fit itself. When --validate CUTOFF was ALSO
    # supplied, the operator's own external cutoff governs the gate (already date-blocked by
    # construction: every event with target_date<cutoff is train, every event with
    # target_date>=cutoff is holdout). Otherwise the gate uses an INTERNAL, DATE-BLOCKED holdout
    # (FIX 4): the chronologically last HOLDOUT_FRACTION of each group's own WHOLE TARGET DATES.
    if args.validate is not None:
        gate_method = f"external_validate_cutoff:{args.validate}"
    else:
        gate_method = f"internal_holdout_last_{int(HOLDOUT_FRACTION * 100)}pct_dates_by_target_date"
    families: dict = {}
    for unit, metric in GROUPS:
        sub = d[(d["unit_family"] == unit) & (d["temperature_metric"] == metric)].copy()
        if args.validate is not None:
            train_sub = sub[sub["target_date"] < args.validate]
            holdout_sub = sub[sub["target_date"] >= args.validate]
        else:
            train_sub, holdout_sub = _split_holdout_by_target_date(sub)
        families.setdefault(unit, {})[metric] = gate_group(train_sub, holdout_sub, gate_method=gate_method)

    source_query_hash = hashlib.sha256((_POST_QUERY + _SETT_QUERY).encode("utf-8")).hexdigest()[:16]
    data_window = f"since={args.since}"
    if len(d):
        data_window = f"{d['target_date'].min()}..{d['target_date'].max()} (since={args.since})"
    artifact = {
        "_meta": {
            "authority": ARTIFACT_AUTHORITY,
            "schema_version": SCHEMA_VERSION,
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "method": "tau_bucketed_interval_censored_mle_plus_city_variance_shrinkage_event_weighted_oos_gated",
            "oos_acceptance_gate": gate_method,
            "oos_margin_nats": OOS_MARGIN_NATS,
            "tau_clock": TAU_CLOCK_ID,
            "tau_clock_description": (
                "source_cycle_time (forecast issue; NOT computed_at/decision time) to the CITY'S "
                "LOCAL target-date end (next local midnight, DST-aware via config/cities.json "
                "IANA timezone, converted to UTC) -- 2026-07-28 correction"
            ),
            "tau_buckets": {lab: [lo if math.isfinite(lo) else None, hi if math.isfinite(hi) else None]
                            for lab, lo, hi in zip(TAU_LABELS, TAU_EDGES, TAU_EDGES[1:])},
            "min_bucket_n_events": MIN_BUCKET_N,
            "min_group_n_events": MIN_GROUP_N,
            "min_city_n_events": MIN_CITY_N,
            "city_shrinkage_n0": CITY_SHRINKAGE_N0,
            "k_bounds": list(K_BOUNDS),
            "event_weighting": "1/n_e per (city,target_date) event, group-wide (not per-bucket-renormalized)",
            "settlement_quantizer": "src.contracts.settlement_semantics.settlement_preimage_offsets per city rounding_rule (SettlementSemantics.for_city)",
            "population_fence": {
                "current_evidence_shape_required": True,
                "computed_at_before_local_target_end_required": True,
                "settlement_authority_filter": "settlement_outcomes.authority='VERIFIED'",
            },
            "components_fence_applied": False,
            "components_fence_reference_ts": COMPONENTS_FENCE_TS,
            "components_fence_note": (
                "This fitter reads only anchor_value_c/predictive_sigma_c (always populated, not a "
                "component field); current_evidence_shape's within/between/delta components are NOT "
                "read here, so no fence was needed for correctness. Recorded so a future editor who "
                "adds a component-field read is forced to confront the 2026-07-15T22:32:31Z rollout "
                "boundary rather than silently training across it."
            ),
            "data_window": data_window,
            "row_counts": stats,
            "source_query_hash": source_query_hash,
        },
        "families": families,
    }

    # MEDIUM (deep-review 2026-07-28): a unique tmp filename (pid+uuid) so two concurrent fitter
    # runs against the same --out never clobber each other's in-progress write; allow_nan=False so a
    # NaN/Infinity that slipped through the gates fails the write loudly instead of shipping invalid
    # JSON; fsync so the artifact is durable before the atomic rename, not just buffered.
    tmp_path = f"{args.out}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True, default=str, allow_nan=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, args.out)
    print(f"[sigma-tau] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
