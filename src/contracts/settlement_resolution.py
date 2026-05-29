# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL replay redesign §3 Finding 2 (winner must derive from
#   settlement_value, NOT the stored winning_bin string) + §4.2 (settlement truth
#   object) + Fitz Constraint #4 (data provenance: stored label is evidence, value
#   is truth). Composes existing primitives: CanonicalBinGrid.bin_for_value
#   (calibration_bins) and SettlementOutcome (settlement_outcome). Adds NO new
#   bin-derivation math.
#
# NAME NOTE: the plan called this "SettlementObject", but that name is already
# taken by src/contracts/residual_key.py for the residual-PAIRING settlement
# value object (ForecastTarget + settlement_value). This class is the resolution
# truth for replay SCORING (value-derived winning bin + outcome state +
# eligibility) — a distinct concern, so it gets a distinct name
# (`SettlementResolution`) to keep the two unmixable.
"""Typed resolution-truth object for replay/backtest SKILL scoring.

A ``SettlementResolution`` answers one question unambiguously: *which ordered
bin settled YES, and is this resolution clean enough to learn from / promote on?*

The load-bearing design decision (TRIBUNAL Finding 2): the winning bin is
DERIVED from ``settlement_value`` via the canonical bin grid. The stored
``winning_bin`` string column is retained only as EVIDENCE and a drift signal —
it never overrides the value-derived winner. This makes a whole bug category
(stale/backfilled/label-drifted ``winning_bin`` silently corrupting coverage
scores) impossible: the truth source is the physical settlement value, not a
denormalized label that can rot.

Exceptional resolutions (50/50, disputed, unresolved, venue-unresolved) are
classified via the existing ``SettlementOutcome`` state machine and marked
``promotion_eligible = False`` / ``learning_eligible = False`` so they cannot
silently enter a promotion or training set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.contracts.calibration_bins import CanonicalBinGrid
from src.contracts.settlement_outcome import SettlementOutcome
from src.types.market import Bin

# Resolution states under which a settlement is a clean, single-winner outcome
# eligible to feed promotion-grade scoring and calibration learning. Everything
# else (50/50, disputed, unresolved, venue-published-but-unresolved) is excluded.
_PROMOTION_ELIGIBLE_OUTCOMES: frozenset[SettlementOutcome] = frozenset(
    {
        SettlementOutcome.PHYSICALLY_CONFIRMED,
        SettlementOutcome.VENUE_RESOLVED_WIN,
        SettlementOutcome.VENUE_RESOLVED_LOSE,
        SettlementOutcome.REDEEMED,
        SettlementOutcome.OBSERVATION_REVISED,
        SettlementOutcome.SOURCE_REVISION,
    }
)


class SettlementResolutionUndeterminedError(ValueError):
    """Raised when a winning bin cannot be derived because ``settlement_value``
    is absent. The row is refused rather than scored against a guessed/stored
    label — fail-closed per Finding 2.
    """


@dataclass(frozen=True)
class SettlementResolution:
    """The resolution truth a forecast vector is scored against.

    ``winning_bin_index`` is the 0-indexed position of the settled bin in the
    SAME ordered grid a forecast vector must be laid out on, so it can be fed
    directly to ``src.calibration.scoring`` as ``winner``.
    """

    city: str
    temperature_metric: str  # 'high' | 'low'
    target_local_date: str
    settlement_value: float
    settlement_unit: str  # 'F' | 'C'
    grid_label: str
    ordered_bin_labels: tuple[str, ...]
    winning_bin_index: int
    winning_bin_label: str
    # Stored ``winning_bin`` column kept as evidence only. None when absent.
    stored_winning_bin_evidence: str | None
    # None when no stored label to compare; else whether stored == derived.
    stored_matches_derived: bool | None
    truth_source: str  # always 'settlement_value_derived' on a determined object
    outcome_state: SettlementOutcome
    resolution_status: str  # 'resolved' | 'exceptional'
    promotion_eligible: bool
    learning_eligible: bool
    provenance: dict = field(default_factory=dict)

    @classmethod
    def from_settlement_row(
        cls,
        row: dict,
        grid: CanonicalBinGrid,
        *,
        outcome_state: SettlementOutcome | None = None,
    ) -> "SettlementResolution":
        """Build from a ``settlement_outcomes`` row dict + the city's bin grid.

        Args:
            row: a ``settlement_outcomes`` row (needs ``settlement_value``;
                ``winning_bin`` / ``outcome_type`` optional).
            grid: the canonical bin grid for this city's settlement unit. The
                caller resolves it (``grid_for_city`` / by unit); kept explicit so
                the contract is decoupled from City lookup and fully testable.
            outcome_state: optional explicit state; otherwise derived from the
                row's integer ``outcome_type`` column, defaulting to
                PHYSICALLY_CONFIRMED when absent (a row with a value but no
                lifecycle marker is a confirmed physical settlement).

        Raises:
            SettlementResolutionUndeterminedError: ``settlement_value`` is missing.
            ValueError: ``settlement_value`` falls in a grid partition gap
                (a grid-construction bug, surfaced loudly — never silently dropped).
        """
        raw_value = row.get("settlement_value")
        if raw_value is None:
            raise SettlementResolutionUndeterminedError(
                f"SettlementResolution refused: settlement_value is None for "
                f"city={row.get('city')!r} date={row.get('target_date')!r} "
                f"metric={row.get('temperature_metric')!r}. The winning bin is "
                f"derived from the value, so an absent value is undeterminable."
            )
        settlement_value = float(raw_value)

        state = outcome_state if outcome_state is not None else _coerce_outcome(row)

        winner_bin: Bin = grid.bin_for_value(settlement_value)
        ordered_bins = grid.as_bins()
        winning_index = _index_of(ordered_bins, winner_bin)
        ordered_labels = tuple(b.label for b in ordered_bins)

        stored = row.get("winning_bin")
        stored = str(stored) if stored not in (None, "") else None
        stored_matches = None if stored is None else (stored == winner_bin.label)

        eligible = state in _PROMOTION_ELIGIBLE_OUTCOMES
        resolution_status = "resolved" if eligible else "exceptional"

        return cls(
            city=str(row.get("city", "")),
            temperature_metric=str(row.get("temperature_metric", "")),
            target_local_date=str(row.get("target_date", "")),
            settlement_value=settlement_value,
            settlement_unit=str(row.get("settlement_unit") or grid_unit(grid)),
            grid_label=grid.label,
            ordered_bin_labels=ordered_labels,
            winning_bin_index=winning_index,
            winning_bin_label=winner_bin.label,
            stored_winning_bin_evidence=stored,
            stored_matches_derived=stored_matches,
            truth_source="settlement_value_derived",
            outcome_state=state,
            resolution_status=resolution_status,
            promotion_eligible=eligible,
            learning_eligible=eligible,
            provenance={
                "settlement_source": row.get("settlement_source"),
                "authority": row.get("authority"),
                "outcome_type_raw": row.get("outcome_type"),
            },
        )


def _coerce_outcome(row: dict) -> SettlementOutcome:
    """Map the row's integer ``outcome_type`` to a SettlementOutcome.

    Absent → PHYSICALLY_CONFIRMED (a settled value with no lifecycle marker is a
    confirmed physical observation). Unknown int → UNRESOLVED (fail-closed: an
    unrecognized state is treated as not-eligible, never as a clean win).
    """
    raw = row.get("outcome_type")
    if raw is None:
        return SettlementOutcome.PHYSICALLY_CONFIRMED
    try:
        return SettlementOutcome(int(raw))
    except (ValueError, TypeError):
        return SettlementOutcome.UNRESOLVED


def _index_of(bins: list[Bin], target: Bin) -> int:
    """Identity-first index lookup (frozen Bins are equality-comparable, but two
    distinct bins could compare equal on degenerate grids; identity is exact)."""
    for i, b in enumerate(bins):
        if b is target:
            return i
    # Fallback to equality if the grid returned a fresh-but-equal instance.
    return bins.index(target)


def grid_unit(grid: CanonicalBinGrid) -> str:
    """Best-effort settlement unit for a grid, from its first bin carrying a unit."""
    for b in grid.as_bins():
        if b.unit:
            return b.unit
    return ""
