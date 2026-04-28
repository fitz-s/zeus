"""Training-eligibility filter for forecast rows (F11.5).

Per packet 2026-04-28 §01 §5: SKILL purpose accepts FETCH_TIME, RECORDED,
DERIVED_FROM_DISSEMINATION; rejects RECONSTRUCTED. ECONOMICS rejects all
but FETCH_TIME / RECORDED. This module exposes both a Python predicate
and a SQL fragment so consumers (training rebuilds, backtest queries)
filter consistently without hand-rolling each call site.

Single source of truth: the predicate is_skill_eligible / is_economics_eligible
delegates to src.backtest.decision_time_truth.gate_for_purpose, so any future
change to the gate's tier acceptance is mirrored here without needing two
edits. The SQL fragments are derived from the same frozensets that
gate_for_purpose's behavior implies.
"""

from datetime import datetime, timezone

from src.backtest.decision_time_truth import (
    AvailabilityProvenance,
    DecisionTimeTruth,
    HindsightLeakageRefused,
    gate_for_purpose,
)
from src.backtest.purpose import BacktestPurpose


SKILL_ELIGIBLE_PROVENANCE = frozenset({
    AvailabilityProvenance.FETCH_TIME.value,
    AvailabilityProvenance.RECORDED.value,
    AvailabilityProvenance.DERIVED_FROM_DISSEMINATION.value,
})

ECONOMICS_ELIGIBLE_PROVENANCE = frozenset({
    AvailabilityProvenance.FETCH_TIME.value,
    AvailabilityProvenance.RECORDED.value,
})


def _quote_in_clause(values: frozenset[str]) -> str:
    return ", ".join(repr(v) for v in sorted(values))


SKILL_ELIGIBLE_SQL = (
    f"availability_provenance IN ({_quote_in_clause(SKILL_ELIGIBLE_PROVENANCE)})"
)

ECONOMICS_ELIGIBLE_SQL = (
    f"availability_provenance IN ({_quote_in_clause(ECONOMICS_ELIGIBLE_PROVENANCE)})"
)


_DUMMY_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _eligible_via_gate(
    provenance: str | AvailabilityProvenance | None,
    purpose: BacktestPurpose,
) -> bool:
    if provenance is None:
        return False
    if isinstance(provenance, str):
        try:
            provenance = AvailabilityProvenance(provenance)
        except ValueError:
            return False
    truth = DecisionTimeTruth(snapshot_id="eligibility_check", available_at=_DUMMY_TIME, provenance=provenance)
    try:
        gate_for_purpose(truth, purpose)
    except HindsightLeakageRefused:
        return False
    return True


def is_skill_eligible(provenance: str | AvailabilityProvenance | None) -> bool:
    """SKILL eligibility — delegates to gate_for_purpose for single-source-of-truth."""
    return _eligible_via_gate(provenance, BacktestPurpose.SKILL)


def is_economics_eligible(provenance: str | AvailabilityProvenance | None) -> bool:
    """ECONOMICS eligibility — delegates to gate_for_purpose for single-source-of-truth."""
    return _eligible_via_gate(provenance, BacktestPurpose.ECONOMICS)
