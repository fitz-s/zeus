# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/04_PHASE_3_SHOULDER.md §"Kelly + FDR + risk rules" + docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T1

"""Relationship and invariant tests for Phase 3 T1 selection_family extensions.

Probe coverage (plan §2 T1, §3 invariants, dossier §7.5):
  G5 / plan §2 T1  : make_hypothesis_family_id and make_edge_family_id accept
                     source + regime kwargs without breaking existing callers
  §3 invariant      : make_shoulder_hypothesis_family_id rejects empty source or regime
  §7.5 grammar      : shoulder family ID grammar "shoulder:{city}:{metric}:{target_date}:{source}:{regime}"
  backward compat   : source="" / regime="" default produces same ID as pre-T1

Tests land BEFORE production logic per Fitz methodology.
"""

from __future__ import annotations

import pytest

from src.strategy.selection_family import (
    make_edge_family_id,
    make_hypothesis_family_id,
    make_shoulder_hypothesis_family_id,
)


# ---------------------------------------------------------------------------
# G5: make_hypothesis_family_id accepts source + regime kwargs
# ---------------------------------------------------------------------------

def test_make_hypothesis_family_id_accepts_source_regime_kwargs():
    """G5: make_hypothesis_family_id accepts source and regime without TypeError."""
    fid = make_hypothesis_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        discovery_mode="standard",
        source="ENS_GFS",
        regime="heat_dome",
    )
    assert isinstance(fid, str)
    assert "ENS_GFS" in fid
    assert "heat_dome" in fid


def test_make_hypothesis_family_id_backward_compat_empty_source_regime():
    """Backward compat: source="" / regime="" produce same ID as pre-T1 callers."""
    old_style = make_hypothesis_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        discovery_mode="standard",
    )
    new_style_defaults = make_hypothesis_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        discovery_mode="standard",
        source="",
        regime="",
    )
    assert old_style == new_style_defaults, (
        f"Backward compat broken: {old_style!r} != {new_style_defaults!r}"
    )


def test_make_hypothesis_family_id_source_regime_extend_id():
    """Non-empty source and regime produce a different (longer) ID than empty defaults."""
    base = make_hypothesis_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        discovery_mode="standard",
    )
    extended = make_hypothesis_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        discovery_mode="standard",
        source="ENS_GFS",
        regime="heat_dome",
    )
    assert base != extended, "source+regime must produce a distinct family ID"
    assert extended.startswith(base + "|"), (
        f"Extended ID should extend base with '|': {extended!r}"
    )


# ---------------------------------------------------------------------------
# G5: make_edge_family_id accepts source + regime kwargs
# ---------------------------------------------------------------------------

def test_make_edge_family_id_accepts_source_regime_kwargs():
    """G5: make_edge_family_id accepts source and regime without TypeError."""
    fid = make_edge_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        strategy_key="shoulder_sell",
        discovery_mode="standard",
        source="ENS_GFS",
        regime="heat_dome",
    )
    assert isinstance(fid, str)
    assert "ENS_GFS" in fid
    assert "heat_dome" in fid


def test_make_edge_family_id_backward_compat_empty_source_regime():
    """Backward compat: source="" / regime="" produce same ID as pre-T1 edge callers."""
    old_style = make_edge_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        strategy_key="shoulder_sell",
        discovery_mode="standard",
    )
    new_style_defaults = make_edge_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        strategy_key="shoulder_sell",
        discovery_mode="standard",
        source="",
        regime="",
    )
    assert old_style == new_style_defaults, (
        f"Backward compat broken: {old_style!r} != {new_style_defaults!r}"
    )


def test_make_edge_family_id_source_regime_extend_id():
    """Non-empty source and regime produce a distinct (longer) edge ID."""
    base = make_edge_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        strategy_key="shoulder_sell",
        discovery_mode="standard",
    )
    extended = make_edge_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        strategy_key="shoulder_sell",
        discovery_mode="standard",
        source="ENS_GFS",
        regime="heat_dome",
    )
    assert base != extended
    assert extended.startswith(base + "|")


# ---------------------------------------------------------------------------
# §3 invariant: make_shoulder_hypothesis_family_id rejects empty source or regime
# ---------------------------------------------------------------------------

def test_inv_shoulder_family_id_requires_source_and_regime():
    """§3 invariant: make_shoulder_hypothesis_family_id raises ValueError for empty source or regime.

    Shoulder family ID requires both non-empty per dossier §7.5 and plan §3.
    Prevents shoulder hypotheses silently collapsing into center-hypothesis families.
    """
    with pytest.raises(ValueError, match="source"):
        make_shoulder_hypothesis_family_id(
            city="Chicago",
            metric="high",
            target_date="2026-07-15",
            source="",
            regime="heat_dome",
        )

    with pytest.raises(ValueError, match="regime"):
        make_shoulder_hypothesis_family_id(
            city="Chicago",
            metric="high",
            target_date="2026-07-15",
            source="ENS_GFS",
            regime="",
        )


def test_make_shoulder_hypothesis_family_id_grammar():
    """§7.5: Shoulder family ID grammar is "shoulder:{city}:{metric}:{target_date}:{source}:{regime}"."""
    fid = make_shoulder_hypothesis_family_id(
        city="Chicago",
        metric="high",
        target_date="2026-07-15",
        source="ENS_GFS",
        regime="heat_dome",
    )
    assert fid == "shoulder:Chicago:high:2026-07-15:ENS_GFS:heat_dome", (
        f"Grammar mismatch: {fid!r}"
    )


def test_make_shoulder_hypothesis_family_id_distinct_from_center():
    """Shoulder family ID must differ from make_hypothesis_family_id with same fields.

    This is the structural invariant that prevents the BH FDR gate from lumping
    shoulder hypotheses with center hypotheses per 04_PHASE_3_SHOULDER.md §"Kelly + FDR + risk rules".
    """
    shoulder_id = make_shoulder_hypothesis_family_id(
        city="Chicago",
        metric="high",
        target_date="2026-07-15",
        source="ENS_GFS",
        regime="heat_dome",
    )
    center_id = make_hypothesis_family_id(
        cycle_mode="live",
        city="Chicago",
        target_date="2026-07-15",
        temperature_metric="high",
        discovery_mode="standard",
        source="ENS_GFS",
        regime="heat_dome",
    )
    assert shoulder_id != center_id, (
        "Shoulder family ID must differ from center hypothesis family ID"
    )
    assert shoulder_id.startswith("shoulder:"), (
        f"Shoulder ID must have 'shoulder:' prefix, got: {shoulder_id!r}"
    )


def test_make_shoulder_hypothesis_family_id_both_empty_raises():
    """Both source and regime empty raises — most specific guard check."""
    with pytest.raises(ValueError):
        make_shoulder_hypothesis_family_id(
            city="Chicago",
            metric="high",
            target_date="2026-07-15",
            source="",
            regime="",
        )
