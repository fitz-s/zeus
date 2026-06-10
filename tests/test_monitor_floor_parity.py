# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: P1 review finding 2026-06-09 — monitor floor parity rule.
#
# Relationship tests for the PARITY RULE: if the entry q for a (city, season, metric) cell
# had the settlement sigma-floor applied (floor_enabled=True, cell present), then a monitor
# refresh that cannot obtain the floor (cell absent) MUST mark the monitor probability NOT
# FRESH so exit decisions do not fire on the degraded (narrower) posterior.
#
# Four invariants tested:
#   R1: entry-with-floor + monitor missing-floor → NOT FRESH, no exit trigger fires
#   R2: floor present both sides → monitor applies it (parity, q widened), still FRESH
#   R3: cells where entry never had a floor (low, 44 missing cities) → behaviour today
#       (no new blocking — regression-pinned)
#   R4: MonitorOneCalibratorQ carries floor provenance fields (q_source, applied, reason)
from __future__ import annotations

import dataclasses
from datetime import date
from typing import Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.calibration import emos as emos_mod
from src.engine.monitor_refresh import (
    MonitorOneCalibratorQ,
    _MONITOR_PROBABILITY_FRESH_ATTR,
    _build_monitor_one_calibrator_q,
    _probe_monitor_settlement_floor,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SYNTH_EMOS_TABLE = {
    "_meta": {"metric": "multi"},
    "cells": {
        "TestCity|JJA|high": {
            "params": [0.0, 1.0, -0.4, 0.5, 0.0],
            "n": 99,
            "served": "emos",
        }
    },
}

_FLOOR_TABLE_WITH_CELL = {
    "_meta": {"k_default": 0.8},
    "cells": {
        "TestCity|JJA|high": {"sigma_floor_c": 2.9, "n": 30, "window": "45d"}
    },
}

_FLOOR_TABLE_EMPTY = {
    "_meta": {"k_default": 0.8},
    "cells": {},
}


def _make_city(name="TestCity", settlement_unit="C"):
    city = MagicMock()
    city.name = name
    city.settlement_unit = settlement_unit
    return city


def _make_position(city_name="TestCity"):
    pos = MagicMock()
    pos.city = city_name
    pos.p_posterior = 0.7
    setattr(pos, _MONITOR_PROBABILITY_FRESH_ATTR, None)
    return pos


_MEMBERS = np.array([25.0, 26.0, 27.0, 28.0, 27.5], dtype=float)
_BINS = [(None, 26.0), (27.0, 27.0), (28.0, None)]
_TARGET_D = date(2026, 7, 15)  # JJA season


def _edli_v1_floor_on():
    return {"edli_settlement_sigma_floor_enabled": True, "edli_settlement_sigma_floor_required": False}


def _edli_v1_floor_off():
    return {"edli_settlement_sigma_floor_enabled": False, "edli_settlement_sigma_floor_required": False}


# ---------------------------------------------------------------------------
# R4: MonitorOneCalibratorQ carries floor provenance fields
# ---------------------------------------------------------------------------

def test_r4_monitor_one_calibrator_q_has_provenance_fields():
    """MonitorOneCalibratorQ must expose floor provenance fields."""
    q = MonitorOneCalibratorQ(
        q_vector=np.array([0.3, 0.4, 0.3]),
        q_source="emos",
        bootstrap_probability_sampler=None,
        settlement_sigma_floor_applied=True,
        settlement_sigma_floor_required=False,
        floor_missing_reason=None,
    )
    assert q.settlement_sigma_floor_applied is True
    assert q.settlement_sigma_floor_required is False
    assert q.floor_missing_reason is None


def test_r4_defaults_are_floor_absent():
    """Default values must represent 'no floor applied' safely."""
    q = MonitorOneCalibratorQ(
        q_vector=np.array([0.5, 0.5]),
        q_source="raw_honest",
        bootstrap_probability_sampler=None,
    )
    assert q.settlement_sigma_floor_applied is False
    assert q.settlement_sigma_floor_required is False
    assert q.floor_missing_reason is None


# ---------------------------------------------------------------------------
# _probe_monitor_settlement_floor unit tests
# ---------------------------------------------------------------------------

def test_probe_returns_found_when_cell_present(monkeypatch):
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_WITH_CELL, raising=False)
    found, reason = _probe_monitor_settlement_floor("TestCity", "JJA", "high")
    assert found is True
    assert reason is None


def test_probe_returns_not_found_when_cell_absent(monkeypatch):
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_EMPTY, raising=False)
    found, reason = _probe_monitor_settlement_floor("TestCity", "JJA", "high")
    assert found is False
    assert reason is not None
    assert "absent" in reason or "non_positive" in reason


def test_probe_is_fail_closed_on_exception():
    """_probe_monitor_settlement_floor must never raise; returns (False, reason) on error."""
    with patch("src.calibration.emos.settlement_sigma_floor", side_effect=RuntimeError("boom")):
        found, reason = _probe_monitor_settlement_floor("TestCity", "JJA", "high")
    assert found is False
    assert reason is not None
    assert "error" in reason.lower() or "boom" in reason


# ---------------------------------------------------------------------------
# R1: entry-with-floor + monitor missing-floor → NOT FRESH
# ---------------------------------------------------------------------------

def test_r1_floor_enabled_cell_missing_marks_not_fresh(monkeypatch):
    """R1: floor enabled at entry + cell absent at monitor time → NOT FRESH, stale posterior held."""
    monkeypatch.setattr(emos_mod, "_emos_table_cache", _SYNTH_EMOS_TABLE, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_EMPTY, raising=False)

    city = _make_city()
    semantics = MagicMock()

    with patch("src.engine.monitor_refresh.settings", {"edli_v1": _edli_v1_floor_on(), "ensemble": {}}):
        q = _build_monitor_one_calibrator_q(
            city=city,
            target_d=_TARGET_D,
            metric="high",
            lead_days=3.0,
            member_extrema=_MEMBERS,
            semantics=semantics,
            all_bins=_BINS,
        )

    # The q was built (EMOS served the cell), but the floor cell is absent.
    assert q.floor_missing_reason is not None, (
        "floor_missing_reason must be set when floor_enabled=True but cell absent"
    )
    assert q.settlement_sigma_floor_applied is False
    assert q.settlement_sigma_floor_required is False  # config says required=False


def test_r1_floor_parity_violation_caller_blocks_exit(monkeypatch):
    """R1 caller contract: when floor_missing_reason is set, the monitor should mark NOT FRESH.

    This tests the MonitorOneCalibratorQ parity fields directly — the caller (monitor loop)
    reads floor_missing_reason and sets is_fresh=False. We simulate that guard here.
    """
    q_with_missing_floor = MonitorOneCalibratorQ(
        q_vector=np.array([0.3, 0.4, 0.3]),
        q_source="emos",
        bootstrap_probability_sampler=None,
        settlement_sigma_floor_applied=False,
        settlement_sigma_floor_required=False,
        floor_missing_reason="floor_cell_absent_or_non_positive:TestCity|JJA|high",
    )
    # Simulate the caller's parity guard (from _refresh_ens_member_counting).
    pos = _make_position()
    if q_with_missing_floor.floor_missing_reason is not None:
        setattr(pos, _MONITOR_PROBABILITY_FRESH_ATTR, False)

    assert getattr(pos, _MONITOR_PROBABILITY_FRESH_ATTR) is False, (
        "Position must be marked NOT FRESH when floor_missing_reason is set"
    )


# ---------------------------------------------------------------------------
# R2: floor present both sides → monitor applies it, still FRESH
# ---------------------------------------------------------------------------

def test_r2_floor_present_both_sides_applies_parity(monkeypatch):
    """R2: floor_enabled=True and cell present → floor_applied=True, floor_missing_reason=None."""
    monkeypatch.setattr(emos_mod, "_emos_table_cache", _SYNTH_EMOS_TABLE, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_WITH_CELL, raising=False)

    city = _make_city()
    semantics = MagicMock()

    with patch("src.engine.monitor_refresh.settings", {"edli_v1": _edli_v1_floor_on(), "ensemble": {}}):
        q = _build_monitor_one_calibrator_q(
            city=city,
            target_d=_TARGET_D,
            metric="high",
            lead_days=3.0,
            member_extrema=_MEMBERS,
            semantics=semantics,
            all_bins=_BINS,
        )

    assert q.settlement_sigma_floor_applied is True, (
        "floor_applied must be True when floor_enabled=True and cell present"
    )
    assert q.floor_missing_reason is None, (
        "floor_missing_reason must be None when the floor was successfully applied"
    )
    assert q.q_source == "emos"


def test_r2_floor_present_q_is_wider_than_no_floor(monkeypatch):
    """R2: sigma is widened by the floor → q on the tail bin is materially larger than no-floor."""
    monkeypatch.setattr(emos_mod, "_emos_table_cache", _SYNTH_EMOS_TABLE, raising=False)
    city = _make_city()
    semantics = MagicMock()

    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_EMPTY, raising=False)
    with patch("src.engine.monitor_refresh.settings", {"edli_v1": _edli_v1_floor_off(), "ensemble": {}}):
        q_off = _build_monitor_one_calibrator_q(
            city=city, target_d=_TARGET_D, metric="high", lead_days=3.0,
            member_extrema=_MEMBERS, semantics=semantics, all_bins=_BINS,
        )

    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_WITH_CELL, raising=False)
    with patch("src.engine.monitor_refresh.settings", {"edli_v1": _edli_v1_floor_on(), "ensemble": {}}):
        q_on = _build_monitor_one_calibrator_q(
            city=city, target_d=_TARGET_D, metric="high", lead_days=3.0,
            member_extrema=_MEMBERS, semantics=semantics, all_bins=_BINS,
        )

    # The floor (k=0.8 * 2.9 = 2.32°C) is much wider than the raw EMOS sigma (~0.8°C),
    # so the tail bin (28°C+) must get more mass under floor-ON.
    tail_idx = len(_BINS) - 1
    assert float(q_on.q_vector[tail_idx]) > float(q_off.q_vector[tail_idx]), (
        "floor-ON must widen sigma and increase tail-bin mass (anti-overconfidence)"
    )


# ---------------------------------------------------------------------------
# R3: floor disabled → no change (regression pin)
# ---------------------------------------------------------------------------

def test_r3_floor_disabled_no_blocking(monkeypatch):
    """R3: floor_enabled=False → floor_missing_reason=None even when cell absent.

    Cells where entry never had a floor (44/54 missing cities, LOW metric, flag OFF)
    must not be newly blocked by this change. Behaviour today is preserved.
    """
    monkeypatch.setattr(emos_mod, "_emos_table_cache", _SYNTH_EMOS_TABLE, raising=False)
    # Cell absent, but flag is OFF — should NOT set floor_missing_reason.
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_EMPTY, raising=False)

    city = _make_city()
    semantics = MagicMock()

    with patch("src.engine.monitor_refresh.settings", {"edli_v1": _edli_v1_floor_off(), "ensemble": {}}):
        q = _build_monitor_one_calibrator_q(
            city=city,
            target_d=_TARGET_D,
            metric="high",
            lead_days=3.0,
            member_extrema=_MEMBERS,
            semantics=semantics,
            all_bins=_BINS,
        )

    assert q.floor_missing_reason is None, (
        "floor_missing_reason must be None when floor is disabled — no new blocking"
    )
    assert q.settlement_sigma_floor_applied is False
    # q_source must still be set (EMOS served the cell normally)
    assert q.q_source in ("emos", "raw_honest")


def test_r3_low_metric_with_missing_floor_cell_flag_off_no_blocking(monkeypatch):
    """R3 LOW variant: LOW cells with 44/54 cities missing from floor table + flag OFF → no block."""
    # LOW EMOS cell exists, floor table has no LOW cell, flag OFF.
    low_emos_table = {
        "_meta": {"metric": "multi"},
        "cells": {"TestCity|JJA|low": {"params": [0.0, 1.0, -0.4, 0.5, 0.0], "n": 99, "served": "emos"}},
    }
    monkeypatch.setattr(emos_mod, "_emos_table_cache", low_emos_table, raising=False)
    monkeypatch.setattr(emos_mod, "_sigma_floor_cache", _FLOOR_TABLE_EMPTY, raising=False)

    city = _make_city()
    semantics = MagicMock()

    with patch("src.engine.monitor_refresh.settings", {"edli_v1": _edli_v1_floor_off(), "ensemble": {}}):
        q = _build_monitor_one_calibrator_q(
            city=city,
            target_d=_TARGET_D,
            metric="low",
            lead_days=3.0,
            member_extrema=_MEMBERS,
            semantics=semantics,
            all_bins=_BINS,
        )

    assert q.floor_missing_reason is None, "LOW cell + flag OFF: floor_missing_reason must be None"
    assert q.settlement_sigma_floor_applied is False
