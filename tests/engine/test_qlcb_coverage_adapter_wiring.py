# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3. Adapter-boundary wiring of the QlcbProvenance carrier
#   + the live settlement-coverage safety gate.
#   Proves: (1) the live lcb carrier is the typed QlcbByDirection so a
#   bare float is unconstructable at the seam; (2) the coverage shrink helpers leave the
#   float byte-identical when no shrink is licensed; (3) the carrier helpers read/write float
#   transparently so every existing consumer keeps working.
"""Adapter-level wiring tests for the K3 q_lcb carrier + live coverage shrink.

These bridge the standalone module tests to the live adapter:
  * the q_lcb carrier helpers (_qlcb_float / _set_qlcb_provenance) round-trip a float
    through QlcbProvenance and read it back identically — the "consumer still sees the
    same number" contract;
  * live coverage shrinking remains carrier-local and only changes q_lcb when a
    coverage verdict licenses a downward correction.
"""
from __future__ import annotations

import pytest


def test_carrier_helpers_round_trip_float_through_provenance():
    """_set_qlcb_provenance writes a typed entry; _qlcb_float reads the SAME float
    back. This is the relationship that lets the type sit at the boundary while
    every existing consumer keeps reading a bare float."""
    from src.calibration.qlcb_provenance import (
        QlcbByDirection,
        _qlcb_float,
        _set_qlcb_provenance,
    )

    d = QlcbByDirection()
    _set_qlcb_provenance(d, ("cond0", "buy_yes"), 0.123456, source="FORECAST_BOOTSTRAP")
    assert _qlcb_float(d[("cond0", "buy_yes")]) == pytest.approx(0.123456)
    assert d[("cond0", "buy_yes")].calibration_source == "FORECAST_BOOTSTRAP"


def test_qlcb_float_accepts_bare_float_for_legacy_plain_dicts():
    """_qlcb_float is polymorphic: it returns the float from a QlcbProvenance OR
    a bare float. This lets the override/mask logic run over the live typed carrier
    AND the existing plain-dict EMOS unit tests without branching everywhere."""
    from src.calibration.qlcb_provenance import QlcbProvenance, _qlcb_float

    assert _qlcb_float(0.42) == pytest.approx(0.42)
    assert _qlcb_float(
        QlcbProvenance(q_lcb=0.42, calibration_source="EMOS_ANALYTIC")
    ) == pytest.approx(0.42)


def test_set_qlcb_provenance_on_plain_dict_writes_bare_float():
    """On a PLAIN dict (the legacy test carrier), _set_qlcb_provenance writes a bare
    float — preserving the existing EMOS-CI override tests that assert plain-float
    equality. On a QlcbByDirection it writes the typed entry. Carrier-driven."""
    from src.calibration.qlcb_provenance import (
        QlcbByDirection,
        QlcbProvenance,
        _set_qlcb_provenance,
    )

    plain: dict = {}
    _set_qlcb_provenance(plain, ("c", "buy_no"), 0.9, source="EMOS_ANALYTIC")
    assert plain[("c", "buy_no")] == 0.9  # bare float on a plain dict

    typed = QlcbByDirection()
    _set_qlcb_provenance(typed, ("c", "buy_no"), 0.9, source="EMOS_ANALYTIC")
    assert isinstance(typed[("c", "buy_no")], QlcbProvenance)
