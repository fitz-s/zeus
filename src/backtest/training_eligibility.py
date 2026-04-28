"""Training-eligibility filter for forecast rows (F11.5).

Per packet 2026-04-28 §01 §5: SKILL purpose accepts FETCH_TIME, RECORDED,
DERIVED_FROM_DISSEMINATION; rejects RECONSTRUCTED. ECONOMICS rejects all
but FETCH_TIME / RECORDED. This module exposes both a Python predicate
and a SQL fragment so consumers (training rebuilds, backtest queries)
filter consistently without hand-rolling each call site.

The SQL fragment is parameterless — it embeds the canonical enum values
directly so a typo in a hand-rolled WHERE clause cannot silently widen
the eligibility set.
"""

from src.backtest.decision_time_truth import AvailabilityProvenance


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


def is_skill_eligible(provenance: str | AvailabilityProvenance | None) -> bool:
    if provenance is None:
        return False
    value = provenance.value if isinstance(provenance, AvailabilityProvenance) else str(provenance)
    return value in SKILL_ELIGIBLE_PROVENANCE


def is_economics_eligible(provenance: str | AvailabilityProvenance | None) -> bool:
    if provenance is None:
        return False
    value = provenance.value if isinstance(provenance, AvailabilityProvenance) else str(provenance)
    return value in ECONOMICS_ELIGIBLE_PROVENANCE
