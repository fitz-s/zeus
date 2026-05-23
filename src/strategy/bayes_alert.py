# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §10
#                  + docs/reference/zeus_strategy_spec.md §14
"""Bayes-factor alert arbitrage math — shared by weather_event_arbitrage.

§10 theorem:
  Alert A is a public signal. For bin B_i:
    O_i  = Pr(B_i) / (1 − Pr(B_i))          (pre-alert odds)
    LR_i = Pr(A | B_i) / Pr(A | ¬B_i)       (likelihood ratio, learned historically)
    O'_i = O_i · LR_i                         (Bayes-updated odds)
    p'_i = O'_i / (1 + O'_i)                 (Bayes-updated probability)
  Enter iff  p'⁻_i − a_i − φ(a_i) > 0.

LR_i MUST be learned from historical (alertType, city, season, leadTime) data.
It is NEVER guessed from alert type name.

DATA-GATED: The NWS alert feed and alert_event_fact table are not yet wired.
AlertLRStub (the default production stub) always returns None, causing
weather_event_arbitrage to emit no_trade with WEATHER_ALERT_LR_TABLE_MISSING.
Real LR estimates will be supplied by a fitted AlertLRTable once the archival
pipeline is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# LR table protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LRRecord:
    """A single learned likelihood-ratio estimate.

    point: LR point estimate (used for mean-posterior calculation).
    lower: Conservative lower bound on LR (used for p'⁻ calculation).
           If None, falls back to using `point` as the lower bound.
    alert_type: Descriptive tag for the alert (e.g. "ExtremeHeat").
    city: Market city slug (e.g. "chicago").
    season: Season tag (e.g. "summer", "winter").
    lead_time_hours: Forecast lead time at time of alert (integer hours).
    """

    point: float
    lower: Optional[float]
    alert_type: str
    city: str
    season: str
    lead_time_hours: int

    def effective_lower(self) -> float:
        """Return the conservative lower bound, falling back to point."""
        return self.lower if self.lower is not None else self.point


@runtime_checkable
class AlertLRTable(Protocol):
    """Protocol for a fitted alert likelihood-ratio table.

    Implementations must return None when the combination is out-of-domain
    (no historical data). Callers treat None as WEATHER_ALERT_LR_TABLE_MISSING.
    """

    def lookup(
        self,
        *,
        alert_type: str,
        city: str,
        season: str,
        lead_time_hours: int,
    ) -> Optional[LRRecord]:
        """Return an LRRecord, or None when no historical estimate exists."""
        ...


class AlertLRStub:
    """Production default stub: always returns None (data-gated).

    Wire a real AlertLRTable implementation once alert_event_fact is populated
    and LR estimates have been fit. Until then, every lookup returns None,
    keeping weather_event_arbitrage in shadow no_trade mode.
    """

    def lookup(
        self,
        *,
        alert_type: str,
        city: str,
        season: str,
        lead_time_hours: int,
    ) -> Optional[LRRecord]:
        return None


# ---------------------------------------------------------------------------
# Pure Bayes math (testable in isolation, no I/O)
# ---------------------------------------------------------------------------

def bayes_update(prior_p: float, lr: float) -> float:
    """Apply a single Bayes likelihood-ratio update.

    O  = prior_p / (1 − prior_p)
    O' = O · lr
    p' = O' / (1 + O')

    Args:
        prior_p: Pre-alert probability Pr(B_i) in (0, 1).
        lr: Likelihood ratio LR_i = Pr(A | B_i) / Pr(A | ¬B_i) in (0, ∞).

    Returns:
        Posterior probability p'_i in (0, 1).

    Raises:
        ValueError: if prior_p not in (0, 1) or lr <= 0.

    Relationship invariant (LR=1):
        bayes_update(p, 1.0) == p  for all p in (0, 1).
        An alert with no diagnosticity must leave the probability unchanged.
    """
    if not (0.0 < prior_p < 1.0):
        raise ValueError(f"prior_p must be in (0, 1), got {prior_p!r}")
    if lr <= 0.0:
        raise ValueError(f"lr must be > 0, got {lr!r}")

    odds = prior_p / (1.0 - prior_p)
    posterior_odds = odds * lr
    return posterior_odds / (1.0 + posterior_odds)


def posterior_lower_bound(
    prior_p: float,
    lr_record: LRRecord,
) -> float:
    """Compute p'⁻ — the conservative posterior lower bound.

    Uses the lower bound of the LR estimate (lr_record.effective_lower()) to
    derive the worst-case posterior. This feeds the entry condition:
        p'⁻ − a_i − φ(a_i) > 0.

    Args:
        prior_p: Pre-alert probability Pr(B_i) in (0, 1).
        lr_record: Fitted LRRecord carrying point + optional lower estimates.

    Returns:
        p'⁻ in (0, 1), always ≤ bayes_update(prior_p, lr_record.point).
    """
    return bayes_update(prior_p, lr_record.effective_lower())
