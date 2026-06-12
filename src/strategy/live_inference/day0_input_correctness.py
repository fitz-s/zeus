# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/operations/day0_multiangle_critique_2026-06-12.md
#   Blind spot C, re-scoped 2026-06-12 per operator anti-over-design directive:
#   this is an INPUT-CORRECTNESS check, not a configurable ban window. It fires
#   ONLY when a decision input is genuinely incoherent.
"""Day0 decision input-ordering correctness.

THE CORRECTNESS PROPERTY
------------------------
A day0 decision's orderbook snapshot must be newer than the observation state
that produced its probability. If the quote that prices a bin was captured
at/before the observation availability that moved the bin's probability, the
quote is pricing a STALE, pre-update book — an incoherent decision input.

This is NOT a throttle and NOT a time-window cap. There is no configurable
"quiet period". The check has exactly one degree of freedom: the strict ordering
``quote_captured_at > observation_available_at``. It returns a rejection reason
ONLY when that ordering is genuinely violated; otherwise it annotates the
decision with the observed lag and passes.

Pure: no DB access, no settings. The caller supplies the two timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from src.contracts.rejection_reasons import RejectionReason

UTC = timezone.utc


@dataclass(frozen=True)
class Day0InputOrderingVerdict:
    """Result of the input-ordering check.

    ``rejection_reason`` is None when the inputs are coherent (or when the check
    is not applicable because a timestamp is absent — see ``applicable``).
    ``lag_seconds`` is quote_captured_at - observation_available_at when both are
    parseable (positive == quote is newer, the healthy direction).
    """

    applicable: bool
    rejection_reason: Optional[str]
    lag_seconds: Optional[float]
    annotation: str


def _coerce_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def evaluate_quote_after_observation(
    *,
    quote_captured_at: Any,
    observation_available_at: Any,
) -> Day0InputOrderingVerdict:
    """Verify the quote was captured strictly after the observation availability.

    Returns a :class:`Day0InputOrderingVerdict`. Semantics:
      - Both timestamps parseable AND quote <= observation -> rejection
        (DAY0_QUOTE_PRECEDES_OBSERVATION, annotated with the inversion).
      - Both parseable AND quote > observation -> pass (annotated with the lag).
      - A timestamp absent/unparseable -> NOT applicable (rejection_reason None):
        the check cannot conclude an inversion, so it does not invent one. The
        caller's existing honest-data freshness gates own the missing-data case;
        this check never duplicates them into a new failure.
    """
    quote = _coerce_utc(quote_captured_at)
    obs = _coerce_utc(observation_available_at)
    if quote is None or obs is None:
        return Day0InputOrderingVerdict(
            applicable=False,
            rejection_reason=None,
            lag_seconds=None,
            annotation="input_ordering=not_applicable:missing_timestamp",
        )
    lag = (quote.astimezone(UTC) - obs.astimezone(UTC)).total_seconds()
    if lag <= 0:
        return Day0InputOrderingVerdict(
            applicable=True,
            rejection_reason=(
                f"{RejectionReason.DAY0_QUOTE_PRECEDES_OBSERVATION.value}"
                f":lag_s={lag:.1f}"
            ),
            lag_seconds=lag,
            annotation=f"input_ordering=VIOLATED:quote_not_after_observation:lag_s={lag:.1f}",
        )
    return Day0InputOrderingVerdict(
        applicable=True,
        rejection_reason=None,
        lag_seconds=lag,
        annotation=f"input_ordering=ok:lag_s={lag:.1f}",
    )
