# Created: 2026-06-15
# Last reused or audited: 2026-06-15
# Authority basis: docs/rebuild/consult_review_pr409.md §5 BLOCKER
#   "live-vs-replay forecast-case mismatch" (src/engine/qkernel_spine_bridge.py:29-31)
#   + §6 "Ideal: replay/live identical forecast case ... Upgrade: one shared
#   ForecastCaseFactory used by ARM replay and reactor bridge". The ARM replay
#   (scripts/qkernel_arm_replay.py:283-319) constructs the pure-predictive
#   decision-cycle case with season=season_for(target), lead_hours=24.0,
#   regime_key="default"; the live bridge MUST derive the SAME (season, lead,
#   regime) so the served sigma is the validated realized floor, not a missed
#   lookup. season_for is the canonical NH season helper already used by the
#   settlement sigma floor table (src/calibration/emos.py:389 / :408 emos_season,
#   byte-identical). This module is the ONE place both paths derive those fields.
"""ForecastCaseFactory — the single (season, lead_hours, regime_key) derivation.

The ARM replay validated the q-kernel spine's center+sigma at the DECISION CYCLE
one day before the target (lead ~24h), pure-predictive (no-day0) path, with
``season = season_for(target_local_date)`` and ``regime_key = "default"`` (see
``scripts/qkernel_arm_replay.py``). The live reactor bridge MUST construct its
``ForecastCase`` with the SAME semantics, or the settlement sigma floor lookup
(keyed by season + lead bucket via the artifact identity) and every downstream
cell-keyed authority diverge from what the replay validated — the live sigma
would no longer be the realized walk-forward floor it was certified to be.

``forecast_case_metadata`` is that single derivation. Both the ARM replay and the
live bridge call it so their ``ForecastCase`` season / lead_hours / regime_key are
identical for the same ``(city, target_date, metric, decision_time)``.

Lead derivation. The ARM replay decides one day before the target finalization,
so its lead is a fixed ``24.0`` hours. The honest live equivalent is the real
elapsed lead from the decision instant to the target-date settlement finalization
(the local finalization wall-clock on the target date, in the city's settlement
timezone). For a decision exactly one day before that finalization this is ~24h —
the SAME lead bucket the replay validated — so the live case and the replay case
land in the same sigma-floor lead bucket. The replay's fixed-24h convention is
exposed as ``REPLAY_LEAD_HOURS`` so the replay and any equivalence test use the
one constant.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional

from src.calibration.emos import emos_season

# The canonical regime key the ARM-replay pure-predictive path uses. There is no
# fitted per-cell regime artifact at this seam (the sigma floor table is keyed by
# city/season/metric); "default" is the replay convention and the live bridge
# matches it so the cell identity is the same.
DEFAULT_REGIME_KEY = "default"

# The ARM replay's fixed decision-cycle lead (target − 1 day ⇒ ~24h). Exposed so
# the replay and the live/replay equivalence test share one constant.
REPLAY_LEAD_HOURS = 24.0

# The ONLY lead bucket the settlement-EV replay validated (consult_review_pr409_round2
# §3): v1 live qkernel is restricted to the 24h bucket. Other buckets need their own
# settlement-EV replay before live. The bridge returns a typed no-trade outside it.
REPLAYED_LEAD_BUCKET = "24h"


@dataclass(frozen=True)
class ForecastCaseMetadata:
    """The (season, lead_hours, regime_key) the ForecastCase must carry.

    Derived ONCE here so the live bridge and the ARM replay agree by construction.
    """

    season: str
    lead_hours: float
    regime_key: str


def _finalization_instant_utc(
    *,
    target_local_date: date,
    finalization_local_time: time,
    settlement_timezone: str,
) -> Optional[datetime]:
    """The target-date settlement finalization instant in UTC, or None if unknowable.

    The settlement finalizes at ``finalization_local_time`` wall-clock on
    ``target_local_date`` in the city's settlement timezone. Returns None when the
    timezone is absent/malformed (the caller falls back to the replay-equivalent
    fixed lead rather than guessing a tz).
    """
    tzname = (settlement_timezone or "").strip()
    if not tzname:
        return None
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tzname)
    except Exception:  # noqa: BLE001 — unknown tz: fall back to the fixed replay lead
        return None
    local_final = datetime.combine(target_local_date, finalization_local_time, tzinfo=tz)
    return local_final.astimezone(timezone.utc)


def lead_hours_for(
    *,
    decision_time: datetime,
    target_local_date: date,
    finalization_local_time: time,
    settlement_timezone: str,
) -> float:
    """The real elapsed lead (hours) from the decision instant to target finalization.

    Mirrors the ARM replay's decision-cycle lead: when the decision is one day
    before the target finalization this is ~24h (the validated replay bucket).
    Falls back to the fixed ``REPLAY_LEAD_HOURS`` when the finalization instant
    cannot be resolved (missing/unknown settlement timezone), so the live case
    still lands in the replay-validated lead bucket rather than the spurious
    ``lead_hours=0.0 -> "day0"`` bucket. Never negative (a decision after
    finalization clamps to 0.0 — but the forecast lane gate already refuses such a
    family upstream).
    """
    issue = decision_time if decision_time.tzinfo else decision_time.replace(tzinfo=timezone.utc)
    final_utc = _finalization_instant_utc(
        target_local_date=target_local_date,
        finalization_local_time=finalization_local_time,
        settlement_timezone=settlement_timezone,
    )
    if final_utc is None:
        return REPLAY_LEAD_HOURS
    delta = final_utc - issue
    hours = delta.total_seconds() / 3600.0
    return hours if hours > 0.0 else 0.0


def forecast_case_metadata(
    *,
    target_local_date: date,
    source_cycle_time_utc: datetime,
    finalization_local_time: time,
    settlement_timezone: str,
    regime_key: str = DEFAULT_REGIME_KEY,
) -> ForecastCaseMetadata:
    """Derive (season, lead_hours, regime_key) — the single source for both paths.

    ``season`` is ``emos_season(target_local_date)`` — the SAME helper the settlement
    sigma-floor lookup keys on (``src/forecast/sigma_authority.realized_sigma_floor``),
    so the live cell IS the replay-validated cell. ``lead_hours`` is the real elapsed
    lead from the FORECAST SOURCE CYCLE (not decision_time) to the target settlement
    finalization — the same quantity the replay computes (replay decides one cycle
    before target ⇒ ~24h). ``regime_key`` defaults to the replay's ``"default"``.
    """
    return ForecastCaseMetadata(
        season=emos_season(target_local_date),
        lead_hours=lead_hours_for(
            decision_time=source_cycle_time_utc,
            target_local_date=target_local_date,
            finalization_local_time=finalization_local_time,
            settlement_timezone=settlement_timezone,
        ),
        regime_key=regime_key,
    )
