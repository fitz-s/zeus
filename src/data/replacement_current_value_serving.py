# Created: 2026-06-11
# Last reused or audited: 2026-06-11  (Task #40 freshness/row-selection fix)
# Authority basis: Task #32 follow-up (operator 2026-06-11) — generalize the gem_global
#   previous_runs exception (edc598b440 / K2 2026-06-09) into the operator law 没有新的就用老的
#   applied to fusion membership: a provider absent from single_runs at the selected cycle serves
#   its previous_runs row at the SAME natural key instead of being dropped. Live evidence: JMA
#   publishes 00/12Z only, so at every 06Z-cadence cycle jma_seamless can NEVER appear in
#   single_runs (06Z: 0/49 cities) while its previous_runs leg is complete (49/49) — the fusion
#   ran served=4/5 and the whole city lost its conservative edge (Beijing 06-12: max q_lcb 0.068).
"""SINGLE-AUTHORITY current-value serving for the replacement multi-model fusion.

``read_current_instrument_values`` is the ONE function that decides, per provider, whether its
CURRENT value for a (city, metric, target_date, selected source_cycle_time) scope is served from
its ``single_runs`` row (the forward live capture — always preferred) or from the newest
persisted ``previous_runs`` row that was actually available no later than the selected cycle.
Both the materializer's q path
(``_read_persisted_current_capture`` is a thin shape-adapter over this function) and the
fusion-upgrade trigger's capturable-set computation call it, so "what can be fused" can never
drift between the two sites (single-builder; registry member #10).

THE GENERALIZED RULE (supersedes the gem-only exception, which becomes one instance of it):

  1. A model's single_runs row at the selected cycle ALWAYS wins (forward capture priority).
  2. A model with NO single_runs row at the selected cycle may be served from the newest
     persisted row for the same model/city/metric/target_date whose ``source_cycle_time`` is not
     after the selected cycle, BRANDED by its real ``served_via`` and ``served_cycle`` — never
     silently. The
     substituted value is the SAME physical product the model's walk-forward de-bias history is
     fit on (previous_runs at this lead), so the de-bias and the lead-bucket residual variance
     already price the older run honestly: NO manual down-weighting exists anywhere — a
     substituted instrument's precision weight derives from its own lead-bucket history exactly
     like a forward-captured one.
  3. A model absent from BOTH endpoints at or before the selected cycle stays dropped.

K-DECISION on the eligibility guard (task constraint 3, judged + documented): the substitution
does NOT try to distinguish "structurally unpublished at this cycle" (JMA at 06Z) from
"transient mid-capture failure at a cycle the provider normally publishes" (gfs HTTP 400 at
00Z). Building that distinction would require a per-provider publication-cadence table — a new
guessed-constant authority of exactly the class the 2026-06-11 run-selection rework killed.
Instead the freshness horizon admits both: the previous_runs row must sit at the SAME
source_cycle_time (the primary freshness anchor — a different cycle's row never leaks, pinned
since edc598b440) AND its capture must be recent relative to that cycle
(``PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS``). A transiently-failed provider is therefore
served from its freshest possessed run too — 没有新的就用老的: serving the one-run-older value
of the SAME de-biased product beats dropping the instrument and inflating sigma, and the honest
``served_via`` provenance + the lead-bucketed residual variance carry the cost. The horizon is
belt-and-suspenders against anomalous stale-keyed rows (e.g. a backfill captured a day after
its cycle); every live capture lands within hours of its cycle.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

# Freshness horizon for a previous_runs substitution: the row's captured_at may be at most this
# many hours after its (== the selected) source_cycle_time. Live extras captures land 0-9h after
# the cycle (e.g. Beijing 06Z captured 14:06Z = 8.1h); anything beyond 24h is an anomalous
# stale-keyed row, not a live capture, and is rejected. Cycles themselves are bounded at 30h by
# replacement_source_cycle_max_age_hours, so 24h post-cycle capture recency is strictly tighter.
PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS = 24.0

SERVED_VIA_SINGLE_RUNS = "single_runs"
SERVED_VIA_PREVIOUS_RUNS = "previous_runs"

# 删了0.25 (2026-07-01): a model whose previous_runs product is a DIFFERENT (coarser) physical product
# than its live single_runs — NOT just an older run of the same product. ECMWF's OM previous-runs feed
# serves ecmwf_ifs025 (0.25° grid) while single_runs serves ecmwf_ifs (9km). The substitution law
# (没有新的就用老的) is correct for same-product models (an older run of the SAME product) but WRONG here:
# substituting ifs025 injects a coarse-grid representativeness artifact into the served center (measured
# ifs025↔ifs9 per-city gap sd 1.52C, e.g. Jeddah +2.2C; Jeddah's whole apparent −1.44 bias was this
# artifact — +0.08 on ifs9). So when the fresh 9km value is missing, DROP the model (the scheme
# renormalizes over present sources) rather than serve the 0.25° coarse product.
_PRODUCT_MISMATCHED_PREVIOUS_RUNS = frozenset({"ecmwf_ifs"})


@dataclass(frozen=True)
class ServedInstrumentValue:
    """One instrument's served CURRENT value + the honest serving provenance (brand law)."""

    value_c: float
    raw_model_forecast_id: int
    served_via: str            # SERVED_VIA_SINGLE_RUNS | SERVED_VIA_PREVIOUS_RUNS
    served_cycle: str          # the served row's source_cycle_time (<= the selected cycle)
    captured_at: str | None    # the served row's capture timestamp (None on stripped schemas)
    age_hours: float           # captured_at − source_cycle_time, hours (0.0 when unknowable)
    lead_days: int | None      # the served row's lead bucket — the SAME bucket its history uses

    def as_provenance(self) -> dict[str, object]:
        """The per-instrument provenance payload recorded in bayes_precision_fusion.current_value_serving."""
        return {
            "served_via": self.served_via,
            "previous_run_substitution": self.served_via == SERVED_VIA_PREVIOUS_RUNS,
            "raw_model_forecast_id": int(self.raw_model_forecast_id),
            "served_cycle": self.served_cycle,
            "captured_at": self.captured_at,
            "age_hours": round(float(self.age_hours), 3),
            "lead_days": self.lead_days,
        }


def _age_hours_or_none(captured_at: str | None, source_cycle_time_iso: str) -> float | None:
    """Hours from the cycle to the row's capture; None when unknowable (stripped schema /
    unparseable stamp). Unknowable FAILS OPEN to admission with age 0.0 — the same-natural-key
    cycle match is the primary freshness anchor; the parsed age is belt-and-suspenders only.
    Negative values (capture stamped before the cycle — the downloader stamps max(now, cycle),
    so this is defensive) clamp to 0.0."""
    if not captured_at:
        return None
    try:
        cap = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        cyc = datetime.fromisoformat(str(source_cycle_time_iso).replace("Z", "+00:00"))
    except Exception:
        return None
    try:
        return max(0.0, (cap - cyc).total_seconds() / 3600.0)
    except Exception:
        return None


def read_current_instrument_values(
    conn: sqlite3.Connection,
    *,
    city: str,
    metric: str,
    target_date: str,
    source_cycle_time_iso: str,
    max_substitution_age_hours: float = PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS,
    include_station_sources: bool = False,
) -> dict[str, ServedInstrumentValue]:
    """THE single authority: per-model served CURRENT value for one (scope, cycle).

    Returns {model: ServedInstrumentValue}. single_runs rows win; models without one are
    substituted from their previous_runs row at the SAME natural key when the freshness horizon
    admits it; models absent from both stay absent (dropped by the fusion exactly as today).

    LEAD_DAYS IS NOT A FILTER (preserved from the 2026-06-09 fix): the selected cycle is the
    freshness ceiling, while the served row reports its own real lead bucket (which names the
    history residual variance that prices that older/current value). Fail-soft: any DB error ->
    empty dict (treated as missing capture; never raises into the q path).
    """
    try:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(raw_model_forecasts)")
        }
    except Exception:
        return {}
    has_captured_at = "captured_at" in columns
    captured_select = ", captured_at" if has_captured_at else ""

    # ORDER suffix depends on whether captured_at is present in the schema:
    #   With captured_at: ORDER BY captured_at DESC NULLS LAST, raw_model_forecast_id DESC
    #     (1) Freshest-row-per-natural-key: a later corrected row (higher captured_at or
    #         higher raw_model_forecast_id as tiebreak) wins — `if model in out: continue`
    #         takes the FIRST row seen per model, so DESC order means freshest arrives first.
    #     (2) NULL captured_at fails CLOSED: NULLS LAST puts unstamped rows after all stamped
    #         siblings — a stamped sibling always outranks a NULL-captured_at row. A solo
    #         NULL-captured_at row (no stamped sibling) still serves, branded age_hours=0.0.
    #   Without captured_at (stripped schema): deterministic by raw_model_forecast_id DESC
    #     only — still freshest-by-id, fail-open on stripped schema (same as before the fix).
    if has_captured_at:
        order_clause = "captured_at DESC NULLS LAST, raw_model_forecast_id DESC"
    else:
        order_clause = "raw_model_forecast_id DESC"

    def _rows(endpoint: str, *, exact_cycle: bool) -> list:
        try:
            cycle_predicate = "source_cycle_time = ?" if exact_cycle else "source_cycle_time < ?"
            return conn.execute(
                f"""
                SELECT raw_model_forecast_id, model, forecast_value_c, lead_days,
                       source_cycle_time{captured_select}
                FROM raw_model_forecasts
                WHERE city = ? AND metric = ? AND target_date = ?
                  AND {cycle_predicate} AND endpoint = ?
                ORDER BY model,
                         source_cycle_time DESC,
                         lead_days,
                         {order_clause}
                """,
                (city, metric, target_date, source_cycle_time_iso, endpoint),
            ).fetchall()
        except Exception:
            return []

    out: dict[str, ServedInstrumentValue] = {}

    def _serve(endpoint: str, *, exact_cycle: bool) -> None:
        for row in _rows(endpoint, exact_cycle=exact_cycle):
            try:
                rid = int(row[0])
                model = str(row[1])
                value = float(row[2])
                lead = None if row[3] is None else int(row[3])
                served_cycle = str(row[4])
                captured = str(row[5]) if has_captured_at and row[5] is not None else None
            except Exception:
                continue
            if model in out:
                continue
            age = _age_hours_or_none(captured, served_cycle)
            if endpoint == SERVED_VIA_PREVIOUS_RUNS and age is not None and age > float(max_substitution_age_hours):
                continue
            # 删了0.25: never substitute a product-mismatched previous_runs (ECMWF ifs025 0.25° coarse)
            # for the live 9km center — drop it, let the scheme renormalize over the present sources.
            if endpoint == SERVED_VIA_PREVIOUS_RUNS and model in _PRODUCT_MISMATCHED_PREVIOUS_RUNS:
                continue
            out[model] = ServedInstrumentValue(
                value_c=value, raw_model_forecast_id=rid, served_via=endpoint,
                served_cycle=served_cycle, captured_at=captured,
                age_hours=0.0 if age is None else age, lead_days=lead,
            )

    # Priority is about possession time first, then endpoint quality:
    # exact-cycle single_runs > exact-cycle previous_runs > newest prior single_runs > newest prior previous_runs.
    _serve(SERVED_VIA_SINGLE_RUNS, exact_cycle=True)
    _serve(SERVED_VIA_PREVIOUS_RUNS, exact_cycle=True)
    _serve(SERVED_VIA_SINGLE_RUNS, exact_cycle=False)
    _serve(SERVED_VIA_PREVIOUS_RUNS, exact_cycle=False)

    # Station-calibrated sources (cwa_*/hko_*) carry their OWN provider cycle clock, independent of
    # the gridded freshness ceiling: their latest captured single_runs row IS the current value and
    # must not be excluded just because its cycle is newer/older than the selected gridded cycle (the
    # gridded passes above serve source_cycle_time <= ceiling, which drops a station row issued after
    # the gridded cycle). OPT-IN: the gridded passes are the unchanged default contract for every
    # existing consumer (seed_discovery, completeness, upgrade-trigger); only the materializer center
    # path opts in, so a station source enters the precision fusion at its initial-precision weight
    # (raw_second_moment_weights) — DATA PRECISION, never a frozen-scheme hard weight.
    if include_station_sources:
        try:
            station_rows = conn.execute(
                f"""
                SELECT raw_model_forecast_id, model, forecast_value_c, lead_days,
                       source_cycle_time{captured_select}
                FROM raw_model_forecasts
                WHERE city = ? AND metric = ? AND target_date = ? AND endpoint = ?
                  AND (model LIKE 'cwa%' OR model LIKE 'hko%')
                ORDER BY model, source_cycle_time DESC, {order_clause}
                """,
                (city, metric, target_date, SERVED_VIA_SINGLE_RUNS),
            ).fetchall()
        except Exception:
            station_rows = []
        for row in station_rows:
            try:
                rid = int(row[0])
                model = str(row[1])
                value = float(row[2])
                lead = None if row[3] is None else int(row[3])
                served_cycle = str(row[4])
                captured = str(row[5]) if has_captured_at and row[5] is not None else None
            except Exception:
                continue
            # Match the materializer's station-family convention exactly (cwa_/hko_ prefixes); the
            # broad SQL LIKE is narrowed here so a hypothetical non-station "cwa…"/"hko…" name cannot
            # leak in.
            if not model.startswith(("cwa_", "hko_")) or model in out:
                continue
            _age = _age_hours_or_none(captured, served_cycle)
            out[model] = ServedInstrumentValue(
                value_c=value, raw_model_forecast_id=rid, served_via=SERVED_VIA_SINGLE_RUNS,
                served_cycle=served_cycle, captured_at=captured,
                age_hours=0.0 if _age is None else _age, lead_days=lead,
            )
    return out
