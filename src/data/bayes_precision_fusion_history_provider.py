# Created: 2026-06-08
# Last reused or audited: 2026-07-20
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §3 (causality: fixed-lead walk-forward history;
#   run_time != source_available_at), §1 observation model (residual z_s - Y), §5 walk-forward
#   (no same-day leak); §7 antibodies ("top-K-uses-target-truth (walk-forward only)",
#   "previous-runs-for-live-decision", "C/F unit mix (settlement-unit residual)").
#   CONTINUITY_AND_WIRING.md §4 step 4 (the real BayesPrecisionFusionHistoryProvider). IRON RULE #3
#   (provenance/no-leak): walk-forward history uses ONLY target_date < decision_date with
#   VERIFIED settlement. INV-37: intra-DB JOIN on ONE zeus-forecasts.db connection.
"""F1/step-4 — the real walk-forward history provider for the BAYES_PRECISION_FUSION-Bayes fusion.

Implements the ``BayesPrecisionFusionHistoryProvider`` Protocol (src/data/bayes_precision_fusion_capture.py:89-103):
reads persisted fixed-lead forecasts from raw_model_forecasts JOINed to VERIFIED
settlement_outcomes, strictly target_date < decision_date, on the SINGLE zeus-forecasts.db
connection (both tables are FORECAST_CLASS on the same DB -> intra-DB JOIN, INV-37 safe).

THE NO-LEAK GUARANTEE (IRON RULE #3, structural — not a comment):
  - previous_runs is the default and only history for gridded models.
  - cwa_township/hko_fnd have no previous-runs product, so positive-lead single_runs may train
    after per-target-date latest-available-issue selection, provided source_available_at is before
    the target date. Day0 single_runs never train because this interface lacks the decision
    time-of-day needed to align historical issues causally.
  - settlement authority = 'VERIFIED' ONLY: UNVERIFIED / DISPUTED excluded (provenance gate).
  - r.target_date < :decision_date (STRICT): same-day and future settlement can never leak.
  - residual = forecast_value_c - settlement_in_C: an F-settlement city's settlement_value
    (degF) is converted to degC BEFORE the residual so forecast_value_c (always degC) and the
    settlement are unit-coherent (SPEC §7 "C/F unit mix" antibody).

FAIL-SOFT (Protocol contract): the provider NEVER raises. Any error (closed connection,
missing table, bad row) is caught and yields an EMPTY mapping for the affected model ->
the capture treats it as "no history" -> anchor fallback / equal-weight (never a crash).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date
from typing import Mapping, Sequence

from src.data.bayes_precision_fusion_capture import ModelHistory

_LOG = logging.getLogger("zeus.bayes_precision_fusion_history_provider")

_SINGLE_RUNS_HISTORY_MODELS = frozenset({"cwa_township", "hko_fnd"})


def _settlement_to_celsius(value: float, unit: str | None) -> float:
    """Convert a settlement value to degC. Settlement is stored in the city settlement unit
    ('F' or 'C'); forecast_value_c is ALWAYS degC, so F settlement MUST convert before the
    residual (SPEC §7 C/F unit-mix antibody). Unknown/None unit -> assume already degC."""
    if unit == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def raw_second_moment_by_model(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    lead_days: int,
    target_date: date | str,
    models: Sequence[str],
) -> dict[str, tuple[float, int]]:
    """RAW second moment Ê[(x−Y)²] + walk-forward n per model (single-serving-rule §1).

    The strictly-prior, date-aligned RAW second moment of the residual ``x − Y``
    (forecast minus settlement, in degC) for each requested model at this
    (city, metric, lead). REUSES the EXACT SAME walk-forward residual source the
    capture/fusion path uses (``BayesPrecisionFusionHistoryProvider`` →
    ``ModelHistory.residual_by_target_date``) — NOT a parallel residual pipeline,
    NOT EB-corrected. For each model it SQUARES every raw residual then AVERAGES:

        ``Ê[(x − Y)²] = mean( (forecast − settlement)² )``

    over settlements with ``target_date < decision_date`` (the same no-leak SQL:
    previous-runs by default, positive-lead station single-runs exception,
    authority='VERIFIED', strict target_date <). Returns
    ``{model: (raw_m2_degC, n_train)}`` for every model with ≥1 walk-forward
    residual; a model with no history is simply absent (the caller treats absent as
    equal-weight). FAIL-SOFT: any error → empty mapping (never raises).

    This is the precision basis ``center.walk_forward_model_weights`` consumes via
    ``RawModelMember.walk_forward_raw_m2_native`` — the RAW diagonal 1/E[r²] center.
    It does NOT de-bias (no EB shift on the residual), so threading it onto the
    member keeps the served center RAW (zero shift) while upgrading equal-weight to
    raw-precision weight.
    """
    provider = BayesPrecisionFusionHistoryProvider(conn)
    try:
        histories = provider(
            city=city, metric=metric, lead_days=int(lead_days),
            target_date=target_date, models=list(models),
        )
    except Exception as exc:  # FAIL-SOFT: never raise into the live producer.
        _LOG.warning(
            "raw_second_moment_by_model query failed (fail-soft, no precision): %s", exc
        )
        return {}
    out: dict[str, tuple[float, int]] = {}
    for model, hist in histories.items():
        # residual_by_target_date = {date: forecast - settlement} (RAW, no EB). The raw
        # second moment is the mean of the SQUARED raw residuals (bias² INCLUDED).
        resids = list(hist.residual_by_target_date.values())
        n = len(resids)
        if n <= 0:
            continue
        raw_m2 = sum(r * r for r in resids) / n
        out[str(model)] = (float(raw_m2), int(n))
    return out


class BayesPrecisionFusionHistoryProvider:
    """Walk-forward residual history reader. Constructed with an OPEN zeus-forecasts.db
    connection (the live materializer wires the forecast-store connection; tests inject an
    in-memory conn). Callable per the ``BayesPrecisionFusionHistoryProvider`` Protocol.

    The provider is process-stateless beyond its connection; it does NOT cache, so a wired
    instance always reflects the latest accrued raw_model_forecasts rows.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __call__(
        self,
        *,
        city: str,
        metric: str,
        lead_days: int,
        target_date: date | str,
        models: Sequence[str],
    ) -> Mapping[str, ModelHistory]:
        models = list(models)
        if not models:
            return {}
        decision_date = (
            target_date.isoformat() if isinstance(target_date, date) else str(target_date)
        )
        try:
            placeholders = ",".join("?" for _ in models)
            # Single-DB intra-DB JOIN (raw_model_forecasts + settlement_outcomes both on
            # zeus-forecasts.db). The WHERE clause IS the no-leak antibody:
            #   lead_days = :lead          -> fixed-lead train (both endpoints filtered to ONE lead)
            #   endpoint IN (prev, single) -> grouping below preserves previous-runs for every
            #                                 gridded model and permits single-runs only for the named
            #                                 station products at positive lead.
            #   authority='VERIFIED'       -> provenance gate (no UNVERIFIED/DISPUTED)
            #   r.target_date < :decision  -> strict no-leak (no same-day / future settlement)
            sql = f"""
                SELECT r.model AS model,
                       r.target_date AS target_date,
                       r.endpoint AS endpoint,
                       r.source_available_at AS source_available_at,
                       r.forecast_value_c AS forecast_value_c,
                       s.settlement_value AS settlement_value,
                       s.settlement_unit AS settlement_unit
                FROM raw_model_forecasts AS r
                JOIN settlement_outcomes AS s
                  ON s.city = r.city
                 AND s.target_date = r.target_date
                 AND s.temperature_metric = r.metric
                WHERE r.city = ?
                  AND r.metric = ?
                  AND r.lead_days = ?
                  AND r.endpoint IN ('previous_runs', 'single_runs')
                  AND r.model IN ({placeholders})
                  AND s.authority = 'VERIFIED'
                  AND s.settlement_value IS NOT NULL
                  AND r.target_date < ?
                ORDER BY r.model, r.target_date
            """
            params: list[object] = [city, metric, int(lead_days), *models, decision_date]
            # ROW-FACTORY SELF-SUFFICIENCY (2026-06-09 hardening): the loop below accesses rows
            # by COLUMN NAME. On a caller connection without row_factory=sqlite3.Row, sqlite3
            # yields plain tuples -> row["model"] raises -> the per-row fail-soft except silently
            # skipped EVERY row -> empty history -> n_train=0 with no error (the exact silent
            # de-bias-off failure observed as a probe-conn artifact on 2026-06-09). A per-CURSOR
            # row factory guarantees named access regardless of the caller's conn config, without
            # mutating the caller's connection.
            cursor = self._conn.cursor()
            cursor.row_factory = sqlite3.Row
            rows = cursor.execute(sql, params).fetchall()
        except Exception as exc:  # FAIL-SOFT: any DB error -> no history (Protocol contract).
            _LOG.warning(
                "BAYES_PRECISION_FUSION history provider query failed (fail-soft, no history): %s", exc
            )
            return {}

        # Group aligned (target_date, forecast, settlement-in-C) triples per model, ordered by
        # target_date. BLOCKER 2: the target_date is carried into ModelHistory.target_dates so
        # the fusion can align the covariance by date (NOT by positional index). The SQL ORDER BY
        # r.model, r.target_date keeps each model's series date-sorted.
        # Per-model source, never an endpoint mix. Previous-runs wins whenever present. Only the two
        # named station products may fall back to single-runs, and only at positive lead. "No rows in
        # this query" is not proof that an arbitrary gridded model lacks a previous-runs product.
        # Day0 is excluded because latest issue per target date could be later than the historical
        # decision time; this provider has only a decision date and cannot align time-of-day causally.
        # Positive-lead station rows also require pre-target source availability.
        _has_prev = {str(r["model"]) for r in rows if str(r["endpoint"]) == "previous_runs"}
        _single_fallback = (
            _SINGLE_RUNS_HISTORY_MODELS.intersection(str(model) for model in models)
            if int(lead_days) > 0
            else frozenset()
        )
        per_model_fc: dict[str, list[float]] = {}
        per_model_settle_c: dict[str, list[float]] = {}
        per_model_dates: dict[str, list[str]] = {}
        _station_latest: dict[str, dict[str, tuple[str, float, float]]] = {}
        for row in rows:
            try:
                model = str(row["model"])
                target_date = str(row["target_date"])
                endpoint = str(row["endpoint"])
                fc = float(row["forecast_value_c"])
                settle_c = _settlement_to_celsius(
                    row["settlement_value"], row["settlement_unit"]
                )
            except Exception:  # a single malformed row must not poison the whole model.
                continue
            if model in _has_prev:
                if endpoint != "previous_runs":
                    continue
                per_model_fc.setdefault(model, []).append(fc)
                per_model_settle_c.setdefault(model, []).append(settle_c)
                per_model_dates.setdefault(model, []).append(target_date)
            elif model in _single_fallback:
                if endpoint != "single_runs":
                    continue
                available = str(row["source_available_at"] or "")
                if not available or available >= f"{target_date}T00:00:00":
                    continue
                by_date = _station_latest.setdefault(model, {})
                prior = by_date.get(target_date)
                if prior is None or available > prior[0]:
                    by_date[target_date] = (available, fc, settle_c)
        for model, by_date in _station_latest.items():
            for target_date in sorted(by_date):
                _available, fc, settle_c = by_date[target_date]
                per_model_fc.setdefault(model, []).append(fc)
                per_model_settle_c.setdefault(model, []).append(settle_c)
                per_model_dates.setdefault(model, []).append(target_date)

        out: dict[str, ModelHistory] = {}
        for model, fcs in per_model_fc.items():
            settles = per_model_settle_c.get(model, [])
            dates = per_model_dates.get(model, [])
            if not fcs or len(fcs) != len(settles) or len(fcs) != len(dates):
                continue
            out[model] = ModelHistory(
                model=model,
                forecast_values=tuple(fcs),
                settlement_values=tuple(settles),
                target_dates=tuple(dates),
            )
        return out
