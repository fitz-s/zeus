# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: STRUCTURAL_FIX_PLAN_2026-06-03 §P0.1 (D1 keystone — grade_receipt
#   truth function). Direction Law (feedback_buy_direction_semantic / GOAL#36):
#     buy_yes WIN iff settled_bin == traded_bin
#     buy_no  WIN iff settled_bin != traded_bin
#   3 composed antibodies: (1) unit TypeError at boundary, (2) BinKind dispatch,
#   (3) range-containment membership via per-city rounding (NOT a {low,low+1} set).
"""Relationship tests for grade_receipt() — the settlement-grounded truth fn.

These are RELATIONSHIP tests, not function tests: they verify the property that
holds when a traded Bin (one module's output) flows into a value-derived
SettlementResolution (another module's output) across the grading boundary.
Written RED-first per the strict-TDD contract.
"""
from __future__ import annotations

import pytest

from src.contracts.graded_receipt import (
    GradedReceipt,
    grade_receipt,
)
from src.contracts.settlement_semantics import SettlementSemantics
from src.types.market import Bin
from src.types.temperature import UnitMismatchError


# ---------------------------------------------------------------------------
# Lightweight settlement stand-in — only the fields grade_receipt reads.
# Mirrors src.contracts.settlement_resolution.SettlementResolution's surface
# (settlement_value + settlement_unit) without forcing a full grid build.
# ---------------------------------------------------------------------------
class _FakeSettlement:
    def __init__(self, settlement_value: float, settlement_unit: str):
        self.settlement_value = settlement_value
        self.settlement_unit = settlement_unit


def _f_semantics() -> SettlementSemantics:
    return SettlementSemantics.default_wu_fahrenheit("KTST")


def _c_semantics() -> SettlementSemantics:
    return SettlementSemantics.default_wu_celsius("CTST")


# ---------------------------------------------------------------------------
# ANTIBODY 1 — unit mismatch is a TypeError at the boundary
# ---------------------------------------------------------------------------
def test_grade_receipt_unit_mismatch_raises():
    """A degF bin graded against a degC settlement must raise at entry —
    a TypeError at the call boundary, not a silently-wrong grade."""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    settlement_c = _FakeSettlement(settlement_value=17.0, settlement_unit="C")
    with pytest.raises(UnitMismatchError):
        grade_receipt(bin_f, "buy_yes", settlement_c, semantics=_c_semantics())


# ---------------------------------------------------------------------------
# ANTIBODY 2 — ceiling-bin (BinKind=ceiling) graded as a threshold, not a point
# ---------------------------------------------------------------------------
def test_grade_receipt_ceiling_buy_no_above_threshold_is_loss():
    """Bin(74, None, F) is a ceiling bin '74°F or higher'. buy_no on it WINS
    iff settlement is NOT in the bin. value=76 IS in (>=74) → buy_no LOSES."""
    ceiling = Bin(low=74.0, high=None, unit="F", label="74°F or higher")
    settlement = _FakeSettlement(settlement_value=76.0, settlement_unit="F")
    g = grade_receipt(ceiling, "buy_no", settlement, semantics=_f_semantics())
    assert g.bin_kind == "ceiling"
    assert g.settled_in_bin is True
    assert g.won is False


def test_grade_receipt_ceiling_buy_no_below_threshold_is_win():
    """Same ceiling bin, value=72 is NOT in (>=74 false) → buy_no WINS."""
    ceiling = Bin(low=74.0, high=None, unit="F", label="74°F or higher")
    settlement = _FakeSettlement(settlement_value=72.0, settlement_unit="F")
    g = grade_receipt(ceiling, "buy_no", settlement, semantics=_f_semantics())
    assert g.bin_kind == "ceiling"
    assert g.settled_in_bin is False
    assert g.won is True


# ---------------------------------------------------------------------------
# ANTIBODY 2/Direction Law — exact-bin (°C point) both directions
# ---------------------------------------------------------------------------
def test_grade_receipt_exact_bin_direction_law():
    """Bin(17,17,C) is an exact point bin. settlement=17.0 lands in it:
        buy_yes → WIN (settled_bin == traded_bin)
        buy_no  → LOSS (settled_bin == traded_bin, so 'does not land' is false)."""
    exact = Bin(low=17.0, high=17.0, unit="C", label="17°C")
    settlement = _FakeSettlement(settlement_value=17.0, settlement_unit="C")

    g_yes = grade_receipt(exact, "buy_yes", settlement, semantics=_c_semantics())
    assert g_yes.bin_kind == "exact"
    assert g_yes.settled_in_bin is True
    assert g_yes.won is True

    g_no = grade_receipt(exact, "buy_no", settlement, semantics=_c_semantics())
    assert g_no.bin_kind == "exact"
    assert g_no.settled_in_bin is True
    assert g_no.won is False


# ---------------------------------------------------------------------------
# ANTIBODY 3 — fractional °F membership via range containment, NOT {low, low+1}
# ---------------------------------------------------------------------------
def test_grade_receipt_fahrenheit_fractional_membership():
    """A 64.5°F settlement, rounded per the city's WMO-half-up rule (→65),
    is contained in '64-65°F'. A hardcoded discrete {64,65} membership set
    would also include 65, but the REAL antibody is that the raw fractional
    value flows through round_per_city before the [low,high] range test —
    so 64.5 must grade IN, not be silently dropped as a non-member."""
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    settlement = _FakeSettlement(settlement_value=64.5, settlement_unit="F")
    g = grade_receipt(bin_f, "buy_yes", settlement, semantics=_f_semantics())
    assert g.bin_kind == "exact"
    assert g.settled_in_bin is True
    assert g.won is True


# ---------------------------------------------------------------------------
# Returned object is a typed GradedReceipt carrying provenance
# ---------------------------------------------------------------------------
def test_grade_receipt_returns_typed_object_with_fields():
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    settlement = _FakeSettlement(settlement_value=64.0, settlement_unit="F")
    g = grade_receipt(bin_f, "buy_yes", settlement, semantics=_f_semantics())
    assert isinstance(g, GradedReceipt)
    assert g.direction == "buy_yes"
    assert g.settlement_value == 64.0
    assert g.unit == "F"


def test_grade_receipt_rejects_unknown_direction():
    bin_f = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    settlement = _FakeSettlement(settlement_value=64.0, settlement_unit="F")
    with pytest.raises(ValueError):
        grade_receipt(bin_f, "sell_yes", settlement, semantics=_f_semantics())


# ---------------------------------------------------------------------------
# Bin.bin_kind cached_property — the BinKind dispatch antibody at the type level
# ---------------------------------------------------------------------------
def test_bin_kind_classifies_exact_ceiling_floor():
    """low&high → exact; low&¬high → ceiling; ¬low&high → floor.
    Makes 'ceiling-graded-as-exact' unconstructable by giving the type a single
    canonical classification grade_receipt switches on."""
    exact_point = Bin(low=17.0, high=17.0, unit="C", label="17°C")
    exact_range = Bin(low=64.0, high=65.0, unit="F", label="64-65°F")
    ceiling = Bin(low=74.0, high=None, unit="F", label="74°F or higher")
    floor = Bin(low=None, high=15.0, unit="C", label="15°C or below")

    assert exact_point.bin_kind == "exact"
    assert exact_range.bin_kind == "exact"
    assert ceiling.bin_kind == "ceiling"
    assert floor.bin_kind == "floor"
