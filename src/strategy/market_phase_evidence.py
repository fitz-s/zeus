# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A5 + Bug review Finding F (phase observability boundary).
"""MarketPhaseEvidence — typed phase determination + provenance.

Pre-A5 the cycle runtime computed ``MarketPhase`` as a bare enum and lost
the *provenance* of the determination — was the endDate from a verified
Gamma payload, an F1 fallback (12:00 UTC of target_date), or unknown?
Was POST_TRADING a heuristic (endDate < now) or actually-resolved
(UMA on-chain settle observed)? Bug review Finding F flagged this as
the phase observability gap: a single ``Optional[MarketPhase]`` couldn't
distinguish "missing", "parse-failed", or "pre-flag-flip" — three states
that need different operator responses.

A5 introduces ``MarketPhaseEvidence``: a frozen dataclass that pairs the
phase with its source signal and the timestamps the determination used.
Downstream callers (dispatch, attribution writers, A6 Kelly resolver)
read the evidence object and can:

  - Reject ``phase=None`` under flag ON for live entries (Finding F floor)
  - Apply a 0.7× Kelly haircut when ``phase_source == "fallback_f1"``
    (degraded; A6 wires this in the resolver)
  - Distinguish ``onchain_resolved`` from heuristic ``POST_TRADING`` once
    the UMA listener (``src/state/uma_resolution_listener.py``) lands
    a resolution row

phase_source values
-------------------
- ``verified_gamma``   Phase determined from a market dict that carried
                       both ``market_start_at`` (or equivalent) and
                       ``market_end_at`` parsed cleanly. Highest authority.
- ``fallback_f1``      Phase determined using the F1 invariant fallback
                       (Polymarket weather endDate uniformly 12:00 UTC of
                       target_date). Used when the market dict lacks an
                       explicit endDate; degraded.
- ``onchain_resolved`` Phase is RESOLVED because a UMA OO Settle event
                       was observed on-chain for this condition_id.
                       Strictly stronger than POST_TRADING (which is a
                       heuristic "endDate has passed").
- ``unknown``          Phase determination failed (parse error, missing
                       target_date, etc.). The evidence carries phase=None
                       and a non-empty ``failure_reason`` so the
                       dispatch reject path can log a useful message.

Why a dataclass and not a tuple
-------------------------------
1. Six fields is past the comfort zone of positional reads.
2. ``frozen=True`` prevents downstream mutation that would corrupt
   provenance — once Phase is determined for a candidate, the evidence
   is immutable for the rest of that candidate's evaluation.
3. Self-describing in logs/repr without manual formatting.
4. The type system distinguishes ``MarketPhase`` (the enum) from
   ``MarketPhaseEvidence`` (the wrapped record) so a function signature
   can demand evidence rather than the bare phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal, Optional

from src.strategy.market_phase import (
    MarketPhase,
    _f1_fallback_end_utc,
    _parse_utc,
    market_phase_for_decision,
    settlement_day_entry_utc,
)


PhaseSource = Literal["verified_gamma", "fallback_f1", "onchain_resolved", "unknown"]


@dataclass(frozen=True)
class MarketPhaseEvidence:
    """Phase determination + the evidence the determination rested on.

    ``phase`` is ``None`` only when ``phase_source == "unknown"`` (i.e.,
    determination failed). Live-authority callers should treat
    phase=None as fail-closed — see ``src/engine/dispatch.py`` reject path
    under flag ON.
    """
    phase: Optional[MarketPhase]
    phase_source: PhaseSource
    market_start_at: Optional[datetime]
    market_end_at: Optional[datetime]
    settlement_day_entry_utc: Optional[datetime]
    uma_resolved_source: Optional[str] = None
    """Tx hash of the UMA OO Settle event when ``phase_source ==
    "onchain_resolved"``; otherwise None."""
    failure_reason: Optional[str] = field(default=None, compare=False)
    """Populated when ``phase_source == "unknown"``. Surfaces the parse
    or lookup error so operators can grep audit logs by failure type
    (compare=False so two evidence rows are equal when the determination
    succeeded, regardless of incidental error text)."""

    def is_live_authoritative(self) -> bool:
        """True iff the evidence is strong enough to gate a live entry.

        ``verified_gamma`` and ``onchain_resolved`` are unambiguous;
        ``fallback_f1`` is permitted but the A6 Kelly resolver applies
        a 0.7× haircut. ``unknown`` is never live-authoritative.
        """
        return self.phase_source in ("verified_gamma", "onchain_resolved", "fallback_f1")

    def is_strict_authoritative(self) -> bool:
        """True iff the determination is BOTH unambiguous AND non-degraded.
        Used by callers that want to opt out of fallback_f1 (e.g.,
        critical-path settlement decisions where the F1 anchor's silent
        drift would be the bug)."""
        return self.phase_source in ("verified_gamma", "onchain_resolved")


def _evidence_for_known_phase(
    *,
    phase: MarketPhase,
    phase_source: PhaseSource,
    market_start_at: Optional[datetime],
    market_end_at: Optional[datetime],
    settlement_day_entry_utc_value: Optional[datetime],
    uma_resolved_source: Optional[str] = None,
) -> MarketPhaseEvidence:
    return MarketPhaseEvidence(
        phase=phase,
        phase_source=phase_source,
        market_start_at=market_start_at,
        market_end_at=market_end_at,
        settlement_day_entry_utc=settlement_day_entry_utc_value,
        uma_resolved_source=uma_resolved_source,
    )


def _unknown(reason: str) -> MarketPhaseEvidence:
    return MarketPhaseEvidence(
        phase=None,
        phase_source="unknown",
        market_start_at=None,
        market_end_at=None,
        settlement_day_entry_utc=None,
        uma_resolved_source=None,
        failure_reason=reason,
    )


def from_market_dict(
    *,
    market: dict,
    city_timezone: str,
    target_date_str: str,
    decision_time_utc: datetime,
    uma_resolved_source: Optional[str] = None,
) -> MarketPhaseEvidence:
    """Build evidence from a market dict (Gamma payload shape).

    Determines ``phase_source`` from the dict's content:

    - ``onchain_resolved`` if ``uma_resolved_source`` is provided (caller
      passes the UMA tx hash from ``uma_resolution_listener``).
    - ``verified_gamma`` if ``market_end_at`` (or equivalent) parsed
      cleanly from the dict.
    - ``fallback_f1`` if no explicit end timestamp; uses the F1 anchor
      (12:00 UTC of target_date).
    - ``unknown`` if parsing the target_date or computing
      settlement_day_entry_utc raised.
    """
    try:
        target_local_date = date.fromisoformat(target_date_str)
    except (TypeError, ValueError) as exc:
        return _unknown(f"target_date parse failed: {exc}")

    try:
        sd_entry = settlement_day_entry_utc(
            target_local_date=target_local_date,
            city_timezone=city_timezone,
        )
    except Exception as exc:  # noqa: BLE001 -- need to surface every parse error path
        return _unknown(f"settlement_day_entry_utc failed: {exc}")

    end_str = market.get("market_end_at") or market.get("endDate") or market.get("end_date")
    start_str = market.get("market_start_at") or market.get("startDate") or market.get("start_date")

    polymarket_end_utc: Optional[datetime] = None
    polymarket_start_utc: Optional[datetime] = None
    explicit_end = False

    try:
        if end_str:
            polymarket_end_utc = _parse_utc(end_str)
            explicit_end = True
        else:
            polymarket_end_utc = _f1_fallback_end_utc(target_local_date)
        if start_str:
            polymarket_start_utc = _parse_utc(start_str)
    except (TypeError, ValueError) as exc:
        return _unknown(f"market timestamp parse failed: {exc}")

    uma_resolved_flag = uma_resolved_source is not None
    try:
        phase = market_phase_for_decision(
            target_local_date=target_local_date,
            city_timezone=city_timezone,
            decision_time_utc=decision_time_utc,
            polymarket_start_utc=polymarket_start_utc,
            polymarket_end_utc=polymarket_end_utc,
            uma_resolved=uma_resolved_flag,
        )
    except (TypeError, ValueError) as exc:
        return _unknown(f"market_phase_for_decision failed: {exc}")

    if uma_resolved_flag:
        phase_source: PhaseSource = "onchain_resolved"
    elif explicit_end:
        phase_source = "verified_gamma"
    else:
        phase_source = "fallback_f1"

    return _evidence_for_known_phase(
        phase=phase,
        phase_source=phase_source,
        market_start_at=polymarket_start_utc,
        market_end_at=polymarket_end_utc,
        settlement_day_entry_utc_value=sd_entry,
        uma_resolved_source=uma_resolved_source,
    )


def from_target_date_only(
    *,
    target_date_str: str,
    city_timezone: str,
    decision_time_utc: datetime,
    uma_resolved_source: Optional[str] = None,
) -> MarketPhaseEvidence:
    """Build evidence when no market dict is available — only target_date
    + city + decision_time. Used by the monitor loop where each position
    only carries ``target_date`` and the city, no Gamma payload.

    Always uses the F1 fallback for endDate. ``phase_source`` is
    ``onchain_resolved`` if a UMA tx hash is supplied, else ``fallback_f1``.
    """
    try:
        target_local_date = date.fromisoformat(target_date_str)
    except (TypeError, ValueError) as exc:
        return _unknown(f"target_date parse failed: {exc}")

    try:
        sd_entry = settlement_day_entry_utc(
            target_local_date=target_local_date,
            city_timezone=city_timezone,
        )
        polymarket_end_utc = _f1_fallback_end_utc(target_local_date)
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"phase setup failed: {exc}")

    uma_resolved_flag = uma_resolved_source is not None
    try:
        phase = market_phase_for_decision(
            target_local_date=target_local_date,
            city_timezone=city_timezone,
            decision_time_utc=decision_time_utc,
            polymarket_start_utc=None,
            polymarket_end_utc=polymarket_end_utc,
            uma_resolved=uma_resolved_flag,
        )
    except (TypeError, ValueError) as exc:
        return _unknown(f"market_phase_for_decision failed: {exc}")

    return _evidence_for_known_phase(
        phase=phase,
        phase_source="onchain_resolved" if uma_resolved_flag else "fallback_f1",
        market_start_at=None,
        market_end_at=polymarket_end_utc,
        settlement_day_entry_utc_value=sd_entry,
        uma_resolved_source=uma_resolved_source,
    )
