# Created: 2026-09-04
# Last reused or audited: 2026-09-04
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   items 26-32 (RAW-law reversal) — operator instruction 2026-09-04 to correct the
#   SERVED forecast center. Row extraction and the EB math mirror the read-only
#   study scripts step1_select_rows.py / step3_percity_table.py / step4_crps_walkforward.py
#   (per-city empirical-Bayes shrunk residual mean, N_MIN=8, walk-forward gated).
"""In-process fit provider for the per-city served-center de-bias.

The served HIGH center is systematically low: pooled settlement minus served
center is +0.34 degC over 4204 settled pre-day0 rows, and the per-city spread is
two-signed and far larger than the pool (Chicago -0.80, Guangzhou +1.65). That
is a per-city location error, so the correction is a per-city location shift,
shrunk toward the pooled mean by empirical Bayes so a thin city cannot buy its
own noise.

FITTED ON THE UNCORRECTED CENTER — the load-bearing invariant. The residual
basis is ``provenance_json.anchor_value_c``, and the materializer keeps writing
the UNCORRECTED fused center into that field even once this shift is live. Were
the corrected center stored there instead, the next fit would measure the
already-corrected residual, drive b toward zero, and silently unwind itself
inside two windows. The provenance field ``center_debias_c`` records the shift
separately for exactly this reason.

There is deliberately no artifact FILE: a written artifact plus a separate
refitter is a known failure class here — the refitter stops, the file goes
stale, and the live path keeps acting on frozen parameters while every freshness
check it has still passes. An in-process cache cannot outlive the process that
fitted it.

DETERMINISTIC ACROSS PROCESSES: ``training_cutoff`` is ``now`` floored to the
UTC 6h boundary (00/06/12/18Z), not a wall-clock TTL measured from first use.
Two daemons that boot hours apart therefore fit over the IDENTICAL row set
within one window and serve the IDENTICAL b, where a TTL would have given them
two different cutoffs and two different shifts for the same instant. Six hours
matches the forecast cycle interval: settled rows arrive in bursts tied to
market resolution.

Walk-forward law: only rows that SETTLED strictly before the cutoff train the
artifact serving that window, so a live decision is never informed by an outcome
that had not yet resolved when the decision was made.

Fail-open is the whole contract. Too few rows, an unreadable database, a metric
that has not earned activation — every one of these returns None, and the caller
keeps the center it already had. This module never raises into the decision
path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
from datetime import datetime, timezone

_LOG = logging.getLogger("zeus.center_debias_live_fit")

# Metrics licensed to receive the shift. HIGH only: its pooled residual is
# +0.34 degC with a walk-forward CRPS gain whose 95% CI excludes zero, while LOW
# pools to +0.04 degC — indistinguishable from no bias — and its walk-forward CI
# crosses zero, so LOW stays the negative control at shift=0 until settled
# evidence earns it.
ENABLED_METRICS: tuple[str, ...] = ("high",)

# Pooled rows below this and the fit is not attempted at all. The pooled mean
# is the fallback every thin city leans on, so it must itself be well measured.
MIN_ROWS = 200

# A city needs this many settled rows before its OWN mean enters its estimate.
# Below it the city takes the pooled mean unchanged — the same activation
# threshold the walk-forward CRPS gate was run under.
N_MIN_CITY = 8

# Sole sanity bound on the served shift, in degC. The largest per-city value in
# the whole settled sample is 1.65, so 2.0 cannot bind on any legitimate city;
# it exists to stop a data pathology (a settlement source or unit flipping under
# one city) from moving a live center by an absurd amount. A clamp that fires is
# a defect signal, hence the WARNING.
MAX_ABS_SHIFT_C = 2.0

# Window length in hours for the deterministic cutoff floor.
WINDOW_HOURS = 6

# Rows whose settlement is unusable as truth are excluded in SQL rather than
# filtered later, so an unsettled or unpriced cell is never materialized.
#
# DEDUP BEFORE SHAPE FILTER — matches the study scripts and is the semantically
# right decision proxy. The winner per (city, target_date, lead) is the LAST
# posterior of that lead day whatever its shape; only then is it kept if it was
# pre-day0 (``fused_normal_direct``). A lead day whose final posterior was
# day0-conditioned is DROPPED, not backfilled with an earlier pre-day0 row,
# because that day's served belief was the day0 one and its residual does not
# describe the pre-day0 center this shift corrects.
#
# ``settled_at`` is NOT stored UTC: live rows carry real non-UTC offsets
# (+08:00, +02:00, -07:00, -05:00, ...) and a handful use a space separator
# instead of 'T'. A string-prefix compare treats a negative-offset row's
# LOCAL wall clock as its instant, which can admit an outcome before it has
# actually settled in UTC, and 'space' < 'T' lexicographically, so a
# space-separated row can sort before a same-day cutoff it actually settles
# after. ``julianday()`` parses both the offset and the space form, so the
# comparison is on the real instant regardless of how it is written. A stray
# two-digit offset with no colon (``+00`` rather than ``+00:00``) is the one
# shape SQLite's parser rejects outright (returns NULL, which the WHERE
# clause then drops as fail-closed exclusion rather than a leak); the
# trailing-colon normalization below covers it so those rows are compared
# like every other row instead of silently dropped.
_RESIDUAL_SQL = """
WITH candidates AS (
    SELECT city,
           target_date,
           computed_at,
           posterior_id,
           CAST(julianday(target_date) - julianday(date(computed_at)) AS INTEGER) AS lead
    FROM forecast_posteriors
    WHERE temperature_metric = :metric
),
winners AS (
    SELECT city,
           target_date,
           lead,
           posterior_id,
           MAX(computed_at) AS computed_at
    FROM candidates
    WHERE lead IN (1, 2)
    GROUP BY city, target_date, lead
)
SELECT w.city AS city,
       p.anchor_value_c AS center_c,
       CASE WHEN s.settlement_unit = 'F'
            THEN (s.settlement_value - 32.0) * 5.0 / 9.0
            ELSE s.settlement_value
       END AS settled_c
FROM winners AS w
JOIN forecast_posteriors AS p
  ON p.posterior_id = w.posterior_id
JOIN settlement_outcomes AS s
  ON s.city = w.city
 AND s.target_date = w.target_date
 AND s.temperature_metric = :metric
WHERE p.q_shape = 'fused_normal_direct'
  AND p.anchor_value_c IS NOT NULL
  AND s.authority = 'VERIFIED'
  AND s.settlement_value IS NOT NULL
  AND s.settlement_unit IN ('F', 'C')
  AND s.settled_at IS NOT NULL
  AND julianday(
        CASE WHEN substr(s.settled_at, -3, 1) IN ('+', '-')
             THEN s.settled_at || ':00'
             ELSE s.settled_at
        END
      ) < julianday(:cutoff)
"""


class CenterDebiasArtifact:
    """One fitted per-city shift table. Frozen: rebuilt, never mutated."""

    __slots__ = (
        "metric",
        "by_city",
        "global_mean",
        "tau2",
        "n_rows",
        "n_cities_activated",
        "training_cutoff",
        "param_hash",
    )

    def __init__(
        self,
        *,
        metric: str,
        by_city: dict[str, float],
        global_mean: float,
        tau2: float,
        n_rows: int,
        n_cities_activated: int,
        training_cutoff: str,
    ) -> None:
        object.__setattr__(self, "metric", str(metric))
        object.__setattr__(self, "by_city", dict(by_city))
        object.__setattr__(self, "global_mean", float(global_mean))
        object.__setattr__(self, "tau2", float(tau2))
        object.__setattr__(self, "n_rows", int(n_rows))
        object.__setattr__(self, "n_cities_activated", int(n_cities_activated))
        object.__setattr__(self, "training_cutoff", str(training_cutoff))
        object.__setattr__(
            self,
            "param_hash",
            hashlib.sha256(
                json.dumps(
                    {
                        "metric": str(metric),
                        "by_city": {
                            str(k): float(v) for k, v in sorted(by_city.items())
                        },
                        "global_mean": float(global_mean),
                        "tau2": float(tau2),
                        "n_rows": int(n_rows),
                        "n_cities_activated": int(n_cities_activated),
                        "training_cutoff": str(training_cutoff),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CenterDebiasArtifact is frozen")

    def shift_for(self, city: str) -> float:
        """The shift for ``city``; the pooled mean for a city never fitted."""

        return float(self.by_city.get(str(city), self.global_mean))


class CenterDebiasCorrection:
    """What the live path consumes: one shift plus the identity that produced it."""

    __slots__ = ("shift_c", "param_hash", "training_cutoff", "n_rows")

    def __init__(
        self,
        *,
        shift_c: float,
        param_hash: str,
        training_cutoff: str,
        n_rows: int,
    ) -> None:
        object.__setattr__(self, "shift_c", float(shift_c))
        object.__setattr__(self, "param_hash", str(param_hash))
        object.__setattr__(self, "training_cutoff", str(training_cutoff))
        object.__setattr__(self, "n_rows", int(n_rows))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CenterDebiasCorrection is frozen")


def window_cutoff(now: datetime) -> str:
    """``now`` floored to the UTC 6h boundary, as an ISO8601 Z timestamp.

    The floor is what makes the fit reproducible across processes: every daemon
    asking within one window derives the same cutoff, therefore trains on the
    same rows, therefore serves the same shift.
    """

    now_utc = now.astimezone(timezone.utc)
    floored = now_utc.replace(
        hour=(now_utc.hour // WINDOW_HOURS) * WINDOW_HOURS,
        minute=0,
        second=0,
        microsecond=0,
    )
    return floored.isoformat().replace("+00:00", "Z")


def load_residual_rows(
    conn: sqlite3.Connection, *, metric: str, training_cutoff: str
) -> list[tuple[str, float]]:
    """``(city, e)`` for every settled decision-proxy row, ``e = settled - center``.

    ``center`` is the UNCORRECTED served fused center recorded in provenance, so
    ``e`` keeps measuring the raw error of the fusion no matter what shift the
    live path is applying on top of it.
    """

    rows = conn.execute(
        _RESIDUAL_SQL,
        {"metric": str(metric), "cutoff": str(training_cutoff)},
    ).fetchall()

    residuals: list[tuple[str, float]] = []
    for row in rows:
        city = row["city"] if isinstance(row, sqlite3.Row) else row[0]
        center = row["center_c"] if isinstance(row, sqlite3.Row) else row[1]
        settled = row["settled_c"] if isinstance(row, sqlite3.Row) else row[2]
        if city is None or center is None or settled is None:
            continue
        try:
            residual = float(settled) - float(center)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(residual):
            continue
        residuals.append((str(city), residual))
    return residuals


def fit(
    rows: list[tuple[str, float]], *, metric: str, training_cutoff: str
) -> CenterDebiasArtifact:
    """Empirical-Bayes shrink each city's mean residual toward the pooled mean.

    ``tau2`` is the between-city variance net of sampling noise, estimated only
    over cities that cleared ``N_MIN_CITY``: including a two-row city there would
    inflate the spread with its own standard error and hand every city a shift
    too close to its raw mean. Each activated city then gets
    ``lambda * mean_c + (1 - lambda) * global_mean`` with
    ``lambda = tau2 / (tau2 + se_c^2)`` — a precisely measured city keeps its own
    mean, a noisy one collapses toward the pool. A city below the threshold
    takes the pooled mean outright.
    """

    if not rows:
        raise ValueError("CENTER_DEBIAS_NO_ROWS")

    by_city: dict[str, list[float]] = {}
    for city, residual in rows:
        by_city.setdefault(city, []).append(residual)

    n_rows = len(rows)
    global_mean = math.fsum(residual for _, residual in rows) / n_rows

    stats: dict[str, tuple[int, float, float]] = {}
    for city, residuals in by_city.items():
        count = len(residuals)
        mean = math.fsum(residuals) / count
        if count >= 2:
            variance = math.fsum((r - mean) ** 2 for r in residuals) / (count - 1)
            standard_error = math.sqrt(variance / count)
        else:
            standard_error = math.inf
        stats[city] = (count, mean, standard_error)

    activated = [
        (count, mean, standard_error)
        for count, mean, standard_error in stats.values()
        if count >= N_MIN_CITY and math.isfinite(standard_error)
    ]
    if len(activated) > 1:
        mean_of_means = math.fsum(mean for _, mean, _ in activated) / len(activated)
        var_between = math.fsum(
            (mean - mean_of_means) ** 2 for _, mean, _ in activated
        ) / (len(activated) - 1)
        mean_se2 = math.fsum(se**2 for _, _, se in activated) / len(activated)
        tau2 = max(0.0, var_between - mean_se2)
    else:
        tau2 = 0.0

    shifts: dict[str, float] = {}
    for city, (count, mean, standard_error) in stats.items():
        if count >= N_MIN_CITY and math.isfinite(standard_error):
            denominator = tau2 + standard_error**2
            lam = tau2 / denominator if denominator > 0.0 else 0.0
            shift = lam * mean + (1.0 - lam) * global_mean
        else:
            shift = global_mean
        if abs(shift) > MAX_ABS_SHIFT_C:
            _LOG.warning(
                "center de-bias clamped: metric=%s city=%s fitted=%.4f clamped=%.4f "
                "n_city=%d — a shift this large is a data pathology, not a forecast bias",
                metric,
                city,
                shift,
                math.copysign(MAX_ABS_SHIFT_C, shift),
                count,
            )
            shift = math.copysign(MAX_ABS_SHIFT_C, shift)
        shifts[city] = shift

    if abs(global_mean) > MAX_ABS_SHIFT_C:
        _LOG.warning(
            "center de-bias pooled fallback clamped: metric=%s fitted=%.4f",
            metric,
            global_mean,
        )
        global_mean = math.copysign(MAX_ABS_SHIFT_C, global_mean)

    return CenterDebiasArtifact(
        metric=str(metric),
        by_city=shifts,
        global_mean=global_mean,
        tau2=tau2,
        n_rows=n_rows,
        n_cities_activated=len(activated),
        training_cutoff=str(training_cutoff),
    )


class CenterDebiasFitProvider:
    """Window-cached shift source, one cache entry per metric.

    ``conn`` is LENT, never closed: on the live path it is the batch's own
    forecasts connection, shared with the rest of the materialization and
    outliving this fit by a wide margin. It is touched at most once per metric
    per 6h window, so the hot path never reaches sqlite, and a failed fit is
    cached as None for that same window — an unreadable database is not re-read
    once per candidate.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, CenterDebiasArtifact | None]] = {}

    def correction(
        self,
        conn: sqlite3.Connection,
        *,
        city: str,
        metric: str,
        now: datetime,
    ) -> CenterDebiasCorrection | None:
        """The shift for one cell, or None whenever it cannot be earned."""

        try:
            metric_key = str(metric)
            if metric_key not in ENABLED_METRICS:
                return None
            cutoff = window_cutoff(now)
            artifact = self._artifact(conn, metric=metric_key, cutoff=cutoff)
            if artifact is None:
                return None
            shift = artifact.shift_for(city)
            if not math.isfinite(shift):
                return None
            return CenterDebiasCorrection(
                shift_c=shift,
                param_hash=artifact.param_hash,
                training_cutoff=artifact.training_cutoff,
                n_rows=artifact.n_rows,
            )
        except Exception:  # noqa: BLE001 - an unavailable fit must never block serving
            return None

    def _artifact(
        self, conn: sqlite3.Connection, *, metric: str, cutoff: str
    ) -> CenterDebiasArtifact | None:
        with self._lock:
            cached = self._cache.get(metric)
            if cached is not None and cached[0] == cutoff:
                return cached[1]
            artifact = self._fit(conn, metric=metric, cutoff=cutoff)
            self._cache[metric] = (cutoff, artifact)
            return artifact

    def _fit(
        self, conn: sqlite3.Connection, *, metric: str, cutoff: str
    ) -> CenterDebiasArtifact | None:
        try:
            rows = load_residual_rows(conn, metric=metric, training_cutoff=cutoff)
        except Exception as exc:  # noqa: BLE001 - degrade to the uncorrected center
            _LOG.warning(
                "center de-bias row load failed (serving uncorrected center): "
                "metric=%s cutoff=%s error=%s",
                metric,
                cutoff,
                type(exc).__name__,
            )
            return None
        if len(rows) < MIN_ROWS:
            _LOG.warning(
                "center de-bias not fitted (serving uncorrected center): "
                "metric=%s cutoff=%s n_rows=%d below MIN_ROWS=%d",
                metric,
                cutoff,
                len(rows),
                MIN_ROWS,
            )
            return None
        try:
            artifact = fit(rows, metric=metric, training_cutoff=cutoff)
        except Exception as exc:  # noqa: BLE001 - degrade to the uncorrected center
            _LOG.warning(
                "center de-bias fit failed (serving uncorrected center): "
                "metric=%s cutoff=%s error=%s",
                metric,
                cutoff,
                type(exc).__name__,
            )
            return None
        _LOG.info(
            "center de-bias fitted: metric=%s n_rows=%d global_mean=%+.4f tau=%.4f "
            "n_activated=%d cutoff=%s param_hash=%s",
            artifact.metric,
            artifact.n_rows,
            artifact.global_mean,
            math.sqrt(artifact.tau2),
            artifact.n_cities_activated,
            artifact.training_cutoff,
            artifact.param_hash,
        )
        return artifact


# One provider per process: the cache is only worth anything if every
# materialization shares it.
PROVIDER = CenterDebiasFitProvider()
