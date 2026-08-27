# Created: 2026-08-27
# Last reused or audited: 2026-08-27
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 ("Market-anchored walk-forward calibrator") — live wiring. Row
#   extraction mirrors scripts/calibrator_walkforward_report.py (load_rows /
#   build_walk_forward_rows); the calibrator math is imported, never restated.
"""In-process fit provider for the market-anchored residual calibrator.

Fits ONE artifact from settled history and caches it in module state behind a
TTL. There is deliberately no artifact FILE: a written artifact plus a separate
refitter is a known failure class here — the refitter stops, the file goes
stale, and the live path keeps acting on frozen parameters while every
freshness check it has still passes. An in-process cache cannot outlive the
process that fitted it, so staleness is bounded by the TTL by construction.

Walk-forward law: ``training_cutoff`` is the fit instant, and only rows that
SETTLED strictly before it are trained on. A row settling later cannot reach
back into an artifact already fitted, so a live decision is never informed by
an outcome that had not yet resolved when the decision was made.

Fail-open is the whole contract. Too few rows, an unreadable database, a lead
outside day0/day1/day2, a non-finite probability — every one of these returns
None, and the caller keeps the raw q it already had. This module never raises
into the decision path and never degrades an unfittable case into a guess.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

from src.calibration.market_anchored_residual import (
    LAMBDA_GRID,
    MIN_TRAIN_ROWS,
    FitRow,
    ResidualCalibratorArtifact,
    apply_artifact,
    fit,
    lead_bucket_of,
)

# One fit serves this long before a refit is attempted. Six hours matches the
# forecast cycle interval (00/06/12/18Z): settled rows arrive in bursts tied to
# market resolution, so refitting faster re-reads the same table to recompute
# the same parameters, and refitting slower lets a full cycle of settled
# evidence sit unused.
DEFAULT_TTL = timedelta(hours=6)

# Lambda for the live fit. The walk-forward report selects lambda on an early
# tuning fold; live has no such fold (it fits once over all settled history),
# so it takes the grid's most-regularized value. Under-regularizing a live
# acting probability manufactures edge; over-regularizing shrinks toward the
# market price, which is the plan's explicit safe direction.
LIVE_LAMBDA = max(LAMBDA_GRID)


def _parse_ts(value: object) -> datetime | None:
    """Parse an ISO8601 timestamp to tz-aware UTC, or None (never raises)."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def load_fit_rows(
    conn: sqlite3.Connection, *, training_cutoff: datetime
) -> list[FitRow]:
    """Extract settled training rows whose outcome preceded ``training_cutoff``.

    Predicates mirror ``load_rows`` in scripts/calibrator_walkforward_report.py
    (q_in_bin / market_in_bin_prob / settled_in_bin / direction all NOT NULL);
    the settled_at-before-cutoff filter is what makes the live fit
    walk-forward-safe, and is applied in SQL so an unsettled row is never
    materialized.
    """

    rows = conn.execute(
        """
        SELECT q_in_bin, market_in_bin_prob, settled_in_bin,
               decision_posterior_computed_at, target_date, settled_at, graded_at
        FROM settlement_attribution
        WHERE q_in_bin IS NOT NULL
          AND market_in_bin_prob IS NOT NULL
          AND settled_in_bin IS NOT NULL
          AND direction IS NOT NULL
        """
    ).fetchall()

    fit_rows: list[FitRow] = []
    for row in rows:
        record = dict(row) if not isinstance(row, dict) else row
        settled_at = _parse_ts(record.get("settled_at")) or _parse_ts(
            record.get("graded_at")
        )
        if settled_at is None or settled_at >= training_cutoff:
            continue
        decision_at = _parse_ts(record.get("decision_posterior_computed_at"))
        target_date = _parse_date(record.get("target_date"))
        if decision_at is None or target_date is None:
            continue
        lead_bucket = lead_bucket_of(decision_at.date(), target_date)
        if lead_bucket is None:
            continue
        try:
            outcome = int(record["settled_in_bin"])
        except (KeyError, TypeError, ValueError):
            continue
        fit_rows.append(
            FitRow(
                p0=record.get("market_in_bin_prob"),
                q_raw=record.get("q_in_bin"),
                lead_bucket=lead_bucket,
                y=outcome,
            )
        )
    return fit_rows


class MarketAnchoredFitProvider:
    """TTL-cached artifact source for one world-DB connection factory.

    ``connect`` LENDS a connection: the provider reads through it and never
    closes it, because on the live path it is the batch's own world connection,
    shared with the rest of the decision and outliving this fit by a wide
    margin. It is called at most once per TTL, so the hot path never touches
    sqlite, and a failed fit is cached as None for that same TTL — an
    unreachable database is not re-dialed once per candidate.
    """

    def __init__(
        self,
        connect,
        *,
        ttl: timedelta = DEFAULT_TTL,
        min_train_rows: int = MIN_TRAIN_ROWS,
        lambda_: float = LIVE_LAMBDA,
    ) -> None:
        self._connect = connect
        self._ttl = ttl
        self._min_train_rows = min_train_rows
        self._lambda = lambda_
        self._lock = threading.Lock()
        self._artifact: ResidualCalibratorArtifact | None = None
        self._fitted_at: datetime | None = None

    def artifact(self, *, now: datetime) -> ResidualCalibratorArtifact | None:
        """The current artifact, refitting when the cached one has aged out."""

        now_utc = now.astimezone(timezone.utc)
        with self._lock:
            if (
                self._fitted_at is not None
                and now_utc - self._fitted_at < self._ttl
            ):
                return self._artifact
            self._artifact = self._fit(now_utc)
            self._fitted_at = now_utc
            return self._artifact

    def _fit(self, training_cutoff: datetime) -> ResidualCalibratorArtifact | None:
        try:
            conn = self._connect()
            if conn is None:
                return None
            rows = load_fit_rows(conn, training_cutoff=training_cutoff)
        except Exception:  # noqa: BLE001 - an unavailable fit must never block serving
            return None
        if len(rows) < self._min_train_rows:
            return None
        cutoff_iso = training_cutoff.isoformat().replace("+00:00", "Z")
        try:
            return fit(rows, lambda_=self._lambda, training_cutoff=cutoff_iso)
        except Exception:  # noqa: BLE001 - a failed fit degrades to raw q, never raises
            return None


def corrected_probability(
    artifact: ResidualCalibratorArtifact | None,
    *,
    p0: float,
    q_raw: float,
    decision_date: date,
    target_date: date,
) -> tuple[float, str, float] | None:
    """Apply ``artifact`` to one candidate, or None when it cannot be applied.

    Returns ``(corrected_q, lead_bucket, alpha_lead)``. None means every
    fail-open case at once — no artifact, an unmodeled lead, a non-finite
    input — because each has the identical consequence for the caller: keep
    the raw q.
    """

    if artifact is None:
        return None
    lead_bucket = lead_bucket_of(decision_date, target_date)
    if lead_bucket is None:
        return None
    corrected = apply_artifact(artifact, p0, q_raw, lead_bucket)
    if corrected is None:
        return None
    return corrected, lead_bucket, float(artifact.alpha[lead_bucket])
