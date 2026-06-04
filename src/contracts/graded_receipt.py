# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: STRUCTURAL_FIX_PLAN_2026-06-03 §P0.1 (D1 keystone — the single
#   settlement-grounded, unit-correct truth function). Direction Law
#   (feedback_buy_direction_semantic / GOAL#36):
#       buy_yes WIN iff settled_bin == traded_bin
#       buy_no  WIN iff settled_bin != traded_bin
#   Composes existing-correct primitives: src.types.market.Bin (+ bin_kind),
#   src.types.temperature.UnitMismatchError, src.contracts.settlement_semantics
#   .SettlementSemantics (per-city rounding). Adds NO new bin-derivation math.
"""grade_receipt — the one truth function for "did this position win?"

Why this exists (D1 keystone): the measurement spine had no single,
unit-correct, BinKind-aware grading function. Win/loss was decided in three
incompatible places:
  - ``settlement_attribution.compute_realized_pnl`` via a ``startswith('no_'/
    'below')`` string heuristic that is structurally wrong for temperature
    labels (a "64-65°F" winning bin starts with neither token);
  - the live harvester via venue-declared YES-won labels (SAFE — venue-grounded);
  - ad-hoc measurement scripts via endpoint-equality, which mis-grades ceiling
    bins ("74°F or higher" never == an exact "76°F").

``grade_receipt`` replaces the value-derived grading paths with ONE function
carrying three composed antibodies, each making an error CATEGORY
unconstructable rather than patching an instance:

  Antibody 1 — UNIT. ``bin.unit != settlement.settlement_unit`` raises
  ``UnitMismatchError`` (a ``TypeError`` subclass) at the call boundary.
  Grading a °F receipt against a °C settlement is a type error, not a
  silently-wrong number. This cannot be commented out the way an ``assert``
  can — the boundary refuses the call.

  Antibody 2 — BINKIND. Membership dispatches on ``bin.bin_kind``
  (exact | ceiling | floor) — a cached property of the ``Bin`` TYPE, not a
  local heuristic. A ceiling bin graded with exact endpoint-equality is
  unconstructable: the kind is computed once, on the type, and switched on here.

  Antibody 3 — MEMBERSHIP. The raw settlement value is rounded per the city's
  ``SettlementSemantics`` (WMO half-up / HKO truncation) and tested with
  RANGE CONTAINMENT ``low <= round(value) <= high`` — NOT a hardcoded
  ``{low, low+1}`` set. A 64.5°F settlement rounds to 65 and grades INTO
  "64-65°F"; it is not silently dropped as a non-member.

The Direction Law is then applied to produce ``won``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from src.contracts.settlement_semantics import SettlementSemantics
from src.types.market import Bin, BinKind
from src.types.temperature import UnitMismatchError

# The two trade directions Zeus expresses. Anything else is refused.
_VALID_DIRECTIONS = frozenset({"buy_yes", "buy_no"})


class _SettlementLike(Protocol):
    """Structural type for the settlement object grade_receipt reads.

    Both ``src.contracts.settlement_resolution.SettlementResolution`` and any
    row-shaped stand-in satisfy this — grade_receipt depends only on the
    settled VALUE and its UNIT, never on the full resolution object, keeping
    the truth function decoupled and testable.
    """

    settlement_value: float
    settlement_unit: str


@dataclass(frozen=True)
class GradedReceipt:
    """The settlement-grounded verdict for one traded (bin, direction).

    Frozen truth object. Every consumer (ARM measurement, attribution,
    coverage) reads ``won`` from HERE — there is no second grading path.
    """

    direction: str  # "buy_yes" | "buy_no"
    bin_kind: BinKind
    bin_label: str
    unit: str
    settlement_value: float          # rounded per the city's SettlementSemantics
    settlement_value_raw: float      # the pre-rounding settled value (evidence)
    settled_in_bin: bool             # did the settled value land IN the traded bin?
    won: bool                        # Direction Law applied to settled_in_bin


def grade_receipt(
    bin: Bin,
    direction: str,
    settlement: _SettlementLike,
    *,
    semantics: Optional[SettlementSemantics] = None,
) -> GradedReceipt:
    """Grade one traded position against its value-derived settlement.

    Args:
        bin: the traded ``Bin`` (carries its unit + open/closed bounds).
        direction: ``"buy_yes"`` or ``"buy_no"``.
        settlement: object exposing ``settlement_value`` + ``settlement_unit``
            (e.g. ``SettlementResolution`` or a settlement_outcomes row stand-in).
        semantics: the city's ``SettlementSemantics`` used to round the raw
            settled value before the membership test (Antibody 3). When None,
            the value is assumed already settlement-rounded and used as-is —
            callers grading off ``settlement_outcomes.settlement_value`` (which
            is WMO-rounded at write time) may omit it; callers grading off a raw
            observation MUST pass it.

    Returns:
        A frozen ``GradedReceipt`` with ``won`` decided by the Direction Law.

    Raises:
        UnitMismatchError: ``bin.unit != settlement.settlement_unit`` — degF vs
            degC grading is refused at the boundary (Antibody 1).
        ValueError: ``direction`` is not ``"buy_yes"`` / ``"buy_no"``.
    """
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(
            f"grade_receipt: unknown direction {direction!r}; "
            f"expected one of {sorted(_VALID_DIRECTIONS)!r}"
        )

    settle_unit = str(settlement.settlement_unit)
    # Antibody 1 — UNIT mismatch is a TypeError at the call boundary.
    if bin.unit != settle_unit:
        raise UnitMismatchError(
            f"grade_receipt: bin.unit={bin.unit!r} (bin {bin.label!r}) does not "
            f"match settlement.settlement_unit={settle_unit!r}. Grading across "
            f"temperature units is refused — convert before grading."
        )

    raw_value = float(settlement.settlement_value)
    # Antibody 3 — round the raw value per the city rule, THEN range-test.
    if semantics is not None:
        if semantics.measurement_unit != bin.unit:
            # The rounding contract must agree with the unit we already proved
            # matches the settlement — a mismatched semantics object is a
            # provenance error, refused on the same footing as Antibody 1.
            raise UnitMismatchError(
                f"grade_receipt: semantics.measurement_unit="
                f"{semantics.measurement_unit!r} does not match bin.unit="
                f"{bin.unit!r}; the per-city rounding rule must be the one for "
                f"the bin's settlement unit."
            )
        graded_value = float(semantics.round_single(raw_value))
    else:
        graded_value = raw_value

    kind: BinKind = bin.bin_kind
    # Antibody 2 — membership dispatches on the bin's canonical kind.
    # Bin.contains() implements the correct open-ended (ceiling/floor) and
    # closed-range (exact) containment via -inf/+inf normalization; the
    # bin_kind read above is the type-level guard that the right semantics are
    # in force (a ceiling bin's high is open, so contains() reduces to >= low).
    if kind == "ceiling":
        settled_in_bin = bin.contains(graded_value)  # value >= low
    elif kind == "floor":
        settled_in_bin = bin.contains(graded_value)  # value <= high
    else:  # exact
        settled_in_bin = bin.contains(graded_value)  # low <= value <= high

    # Direction Law.
    if direction == "buy_yes":
        won = settled_in_bin
    else:  # buy_no
        won = not settled_in_bin

    return GradedReceipt(
        direction=direction,
        bin_kind=kind,
        bin_label=bin.label,
        unit=bin.unit,
        settlement_value=graded_value,
        settlement_value_raw=raw_value,
        settled_in_bin=settled_in_bin,
        won=won,
    )
