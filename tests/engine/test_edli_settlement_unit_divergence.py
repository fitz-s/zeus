# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: SETTLEMENT_CORRECTNESS_AUDIT_2026-06-01.md Axis-1 DEFECT U1 (RT-U1) +
#   Fitz Constraint #1 (make the wrong-unit category unconstructable) / #4 (data provenance).
"""RED relationship test: q-seam unit identity (snapshot ⟷ city ⟷ bins).

The live q-computation seam (``_market_analysis_from_event_snapshot``) combines
member values and bins on FAITH that ingest aligned their units — the
``_snapshot_unit()`` return was discarded, and ``MarketAnalysis`` received
``unit=`` and ``bins=`` from two INDEPENDENT unit derivations that were never
compared. A future ingest unit-swap (new city / source swap / Kelvin leak) that
writes members in the wrong unit while bin labels stay correct collapses q into
the wrong bins → a silent wrong-SIDE buy on a KNOWN market (Paris-class). There
was no fail-closed tripwire.

This is the cross-module invariant (Fitz: test relationships, not functions):
when the snapshot's members flow into the bin/city q computation, the three unit
sources MUST agree, else the computation is meaningless. ``FORECAST_SETTLEMENT_
UNIT_DIVERGENCE`` fail-closes the decision.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.types.market import Bin

# Does not exist on pre-#101 HEAD — ImportError IS the RED signal (feature missing).
from src.engine.event_reactor_adapter import (
    _assert_settlement_unit_identity,
    _market_analysis_from_event_snapshot,
)


def _city(unit: str, name: str = "TestCity"):
    return SimpleNamespace(settlement_unit=unit, name=name)


# --------------------------------------------------------------------------- #
# TIER 1 — the pure 3-way identity rule. Distinct unit combos per case.
# --------------------------------------------------------------------------- #


def test_aligned_fahrenheit_returns_unit():
    unit = _assert_settlement_unit_identity(
        snapshot={"settlement_unit": "F"},
        payload={},
        city=_city("F"),
        bins=[Bin(70, 71, "F", "70-71°F"), Bin(71, 72, "F", "71-72°F")],
    )
    assert unit == "F"


def test_aligned_celsius_returns_unit():
    unit = _assert_settlement_unit_identity(
        snapshot={"settlement_unit": "C"},
        payload={},
        city=_city("C"),
        bins=[Bin(14, 14, "C", "14°C")],  # °C settled-degree bins are width-1 (low==high)
    )
    assert unit == "C"


def test_snapshot_unit_diverges_from_city_and_bins_raises():
    """Snapshot says °C, city+bins are °F (the silent ingest-swap hazard) -> raise."""
    with pytest.raises(ValueError, match="FORECAST_SETTLEMENT_UNIT_DIVERGENCE"):
        _assert_settlement_unit_identity(
            snapshot={"settlement_unit": "C"},
            payload={},
            city=_city("F", name="San Francisco"),
            bins=[Bin(60, 61, "F", "60-61°F")],
        )


def test_bins_unit_diverges_from_city_and_snapshot_raises():
    with pytest.raises(ValueError, match="FORECAST_SETTLEMENT_UNIT_DIVERGENCE"):
        _assert_settlement_unit_identity(
            snapshot={"settlement_unit": "F"},
            payload={},
            city=_city("F"),
            bins=[Bin(14, 14, "C", "14°C")],  # °C settled-degree bin width-1
        )


def test_mixed_bin_units_raise():
    with pytest.raises(ValueError, match="FORECAST_SETTLEMENT_UNIT_DIVERGENCE"):
        _assert_settlement_unit_identity(
            snapshot={"settlement_unit": "F"},
            payload={},
            city=_city("F"),
            bins=[Bin(60, 61, "F", "60-61°F"), Bin(14, 14, "C", "14°C")],
        )


# --------------------------------------------------------------------------- #
# TIER 2 — wiring: the q seam itself fail-closes on divergence (RT-U1).
# --------------------------------------------------------------------------- #


def test_market_analysis_from_event_snapshot_rejects_unit_divergence():
    """RELATIONSHIP (snapshot members -> bin/city q computation): a snapshot whose
    settlement_unit contradicts the city+bins must NOT silently compute q. RED on
    pre-#101 HEAD: _snapshot_unit() return was discarded and q was computed."""
    family = SimpleNamespace(
        city="Chicago",          # Chicago is a °F city in runtime config
        target_date="2026-06-04",
        metric="high",
        event_type="FORECAST_SNAPSHOT_READY",
        bins=[Bin(70, 71, "F", "70-71°F")],
        candidates=[],
        family_id="run-x",
    )
    snapshot = {
        "members_json": json.dumps([70.5] * 41 + [71.5] * 10, separators=(",", ":")),
        "settlement_unit": "C",   # DIVERGENT: Chicago + bins are °F
        "temperature_metric": "high",
    }
    with pytest.raises(ValueError, match="FORECAST_SETTLEMENT_UNIT_DIVERGENCE"):
        _market_analysis_from_event_snapshot(
            calibration_conn=sqlite3.connect(":memory:"),
            snapshot=snapshot,
            family=family,
            native_costs={},
            payload={},
            decision_time=None,
        )
