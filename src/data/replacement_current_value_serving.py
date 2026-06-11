# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: Task #32 follow-up (operator 2026-06-11) — generalize the gem_global
#   previous_runs exception (edc598b440 / K2 2026-06-09) into the operator law 没有新的就用老的
#   applied to fusion membership: a provider absent from single_runs at the selected cycle serves
#   its previous_runs row at the SAME natural key instead of being dropped. Live evidence: JMA
#   publishes 00/12Z only, so at every 06Z-cadence cycle jma_seamless can NEVER appear in
#   single_runs (06Z: 0/49 cities) while its previous_runs leg is complete (49/49) — the fusion
#   ran served=4/5 and the whole city lost its conservative edge (Beijing 06-12: max q_lcb 0.068).
"""SINGLE-AUTHORITY current-value serving for the replacement multi-model fusion.

``read_current_instrument_values`` is the ONE function that decides, per provider, whether its
CURRENT value for a (city, metric, target_date, source_cycle_time) scope is served from its
``single_runs`` row (the forward live capture — always preferred) or substituted from its
``previous_runs`` row at the SAME natural key. Both the materializer's q path
(``_read_persisted_current_capture`` is a thin shape-adapter over this function) and the
fusion-upgrade trigger's capturable-set computation call it, so "what can be fused" can never
drift between the two sites (single-builder; registry member #10).

THE GENERALIZED RULE (supersedes the gem-only exception, which becomes one instance of it):

  1. A model's single_runs row at the selected cycle ALWAYS wins (forward capture priority).
  2. A model with NO single_runs row but a previous_runs row at the SAME natural key is served
     from that previous_runs row, BRANDED ``served_via="previous_runs"`` — never silently. The
     substituted value is the SAME physical product the model's walk-forward de-bias history is
     fit on (previous_runs at this lead), so the de-bias and the lead-bucket residual variance
     already price the older run honestly: NO manual down-weighting exists anywhere — a
     substituted instrument's precision weight derives from its own lead-bucket history exactly
     like a forward-captured one.
  3. A model absent from BOTH endpoints at the cycle stays dropped (exactly as today).

K-DECISION on the eligibility guard (task constraint 3, judged + documented): the substitution
does NOT try to distinguish "structurally unpublished at this cycle" (JMA at 06Z) from
"transient mid-capture failure at a cycle the provider normally publishes" (gfs HTTP 400 at
00Z). Building that distinction would require a per-provider publication-cadence table — a new
guessed-constant authority of exactly the class the 2026-06-11 run-selection rework killed.
Instead the freshness horizon admits both: the previous_runs row must sit at the SAME
source_cycle_time (the primary freshness anchor — a different cycle's row never leaks, pinned
since edc598b440) AND its capture must be recent relative to that cycle
(``PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS``). A transiently-failed provider is therefore
served from its freshest previous run too — 没有新的就用老的: serving the one-run-older value
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


@dataclass(frozen=True)
class ServedInstrumentValue:
    """One instrument's served CURRENT value + the honest serving provenance (brand law)."""

    value_c: float
    raw_model_forecast_id: int
    served_via: str            # SERVED_VIA_SINGLE_RUNS | SERVED_VIA_PREVIOUS_RUNS
    served_cycle: str          # the served row's source_cycle_time (== the selected cycle)
    captured_at: str | None    # the served row's capture timestamp (None on stripped schemas)
    age_hours: float           # captured_at − source_cycle_time, hours (0.0 when unknowable)
    lead_days: int | None      # the served row's lead bucket — the SAME bucket its history uses

    def as_provenance(self) -> dict[str, object]:
        """The per-instrument provenance payload recorded in u0r_fusion.current_value_serving."""
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
) -> dict[str, ServedInstrumentValue]:
    """THE single authority: per-model served CURRENT value for one (scope, cycle).

    Returns {model: ServedInstrumentValue}. single_runs rows win; models without one are
    substituted from their previous_runs row at the SAME natural key when the freshness horizon
    admits it; models absent from both stay absent (dropped by the fusion exactly as today).

    LEAD_DAYS IS NOT A FILTER (preserved from the 2026-06-09 fix): the natural key
    (city, metric, target_date, source_cycle_time) uniquely identifies the forecast; lead_days
    is derived and is only REPORTED (it names the history lead bucket the instrument's de-bias
    and residual variance are fit on). Fail-soft: any DB error -> empty dict (treated as missing
    capture; never raises into the q path).
    """
    try:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(raw_model_forecasts)")
        }
    except Exception:
        return {}
    has_captured_at = "captured_at" in columns
    captured_select = ", captured_at" if has_captured_at else ""

    def _rows(endpoint: str) -> list:
        try:
            return conn.execute(
                f"""
                SELECT raw_model_forecast_id, model, forecast_value_c, lead_days{captured_select}
                FROM raw_model_forecasts
                WHERE city = ? AND metric = ? AND target_date = ?
                  AND source_cycle_time = ? AND endpoint = ?
                ORDER BY model, lead_days, raw_model_forecast_id
                """,
                (city, metric, target_date, source_cycle_time_iso, endpoint),
            ).fetchall()
        except Exception:
            return []

    out: dict[str, ServedInstrumentValue] = {}
    # (1) forward single_runs capture — always wins. First row per model (deterministic ORDER BY).
    for row in _rows(SERVED_VIA_SINGLE_RUNS):
        try:
            rid = int(row[0])
            model = str(row[1])
            value = float(row[2])
            lead = None if row[3] is None else int(row[3])
            captured = str(row[4]) if has_captured_at and row[4] is not None else None
        except Exception:
            continue
        if model in out:
            continue
        age = _age_hours_or_none(captured, source_cycle_time_iso)
        out[model] = ServedInstrumentValue(
            value_c=value, raw_model_forecast_id=rid, served_via=SERVED_VIA_SINGLE_RUNS,
            served_cycle=source_cycle_time_iso, captured_at=captured,
            age_hours=0.0 if age is None else age, lead_days=lead,
        )
    # (2) previous_runs substitution for models the forward capture does not serve at this cycle
    #     (没有新的就用老的 generalization of the gem exception; BRANDED, never silent).
    for row in _rows(SERVED_VIA_PREVIOUS_RUNS):
        try:
            rid = int(row[0])
            model = str(row[1])
            value = float(row[2])
            lead = None if row[3] is None else int(row[3])
            captured = str(row[4]) if has_captured_at and row[4] is not None else None
        except Exception:
            continue
        if model in out:
            continue
        age = _age_hours_or_none(captured, source_cycle_time_iso)
        # Freshness horizon: an unknowable age fails OPEN (the same-cycle natural key is the
        # primary anchor); a parsed age beyond the horizon rejects the anomalous stale row.
        if age is not None and age > float(max_substitution_age_hours):
            continue
        out[model] = ServedInstrumentValue(
            value_c=value, raw_model_forecast_id=rid, served_via=SERVED_VIA_PREVIOUS_RUNS,
            served_cycle=source_cycle_time_iso, captured_at=captured,
            age_hours=0.0 if age is None else age, lead_days=lead,
        )
    return out
