# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §3 (causality: previous-runs fixed-lead train ONLY;
#   run_time != source_available_at), §1 observation model (residual z_s - Y), §5 walk-forward
#   (no same-day leak); §7 antibodies ("top-K-uses-target-truth (walk-forward only)",
#   "previous-runs-for-live-decision", "C/F unit mix (settlement-unit residual)").
#   CONTINUITY_AND_WIRING.md §4 step 4 (the real BayesPrecisionFusionHistoryProvider). IRON RULE #3
#   (provenance/no-leak): walk-forward history uses ONLY target_date < decision_date with
#   VERIFIED settlement. INV-37: intra-DB JOIN on ONE zeus-forecasts.db connection.
"""F1/step-4 — the real walk-forward history provider for the BAYES_PRECISION_FUSION-Bayes fusion.

Implements the ``BayesPrecisionFusionHistoryProvider`` Protocol (src/data/bayes_precision_fusion_capture.py:89-103):
reads the PERSISTED previous-runs forecasts from raw_model_forecasts JOINed to VERIFIED
settlement_outcomes, strictly target_date < decision_date, on the SINGLE zeus-forecasts.db
connection (both tables are FORECAST_CLASS on the same DB -> intra-DB JOIN, INV-37 safe).

THE NO-LEAK GUARANTEE (IRON RULE #3, structural — not a comment):
  - endpoint = 'previous_runs' ONLY: single_runs (live capture, variable-lead) NEVER trains
    (SPEC §3 antibody "previous-runs-for-live-decision"; run_time != source_available_at).
  - settlement authority = 'VERIFIED' ONLY: UNVERIFIED / QUARANTINED excluded (provenance gate).
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


def _settlement_to_celsius(value: float, unit: str | None) -> float:
    """Convert a settlement value to degC. Settlement is stored in the city settlement unit
    ('F' or 'C'); forecast_value_c is ALWAYS degC, so F settlement MUST convert before the
    residual (SPEC §7 C/F unit-mix antibody). Unknown/None unit -> assume already degC."""
    if unit == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


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
            #   endpoint='previous_runs'  -> fixed-lead train only (never single_runs)
            #   authority='VERIFIED'      -> provenance gate (no UNVERIFIED/QUARANTINED)
            #   r.target_date < :decision -> strict no-leak (no same-day / future settlement)
            sql = f"""
                SELECT r.model AS model,
                       r.target_date AS target_date,
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
                  AND r.endpoint = 'previous_runs'
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
        per_model_fc: dict[str, list[float]] = {}
        per_model_settle_c: dict[str, list[float]] = {}
        per_model_dates: dict[str, list[str]] = {}
        for row in rows:
            try:
                model = row["model"]
                target_date = str(row["target_date"])
                fc = float(row["forecast_value_c"])
                settle_c = _settlement_to_celsius(
                    row["settlement_value"], row["settlement_unit"]
                )
            except Exception:  # a single malformed row must not poison the whole model.
                continue
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
