# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: PR#400 the_path audit BLOCKER 7 (q_lcb floor unsafe live fallback).
#   docs/the_path/QLCB_HONESTY.md FIX-C established the LIVE replacement_0_1 settlement
#   sigma-floor. BLOCKER 7 makes the missing-floor behavior MODE-DEPENDENT: LIVE/authority/
#   capital must BLOCK on a missing floor input (see
#   tests/test_replacement_live_qlcb_missing_floor_blocks.py), but SHADOW (observation only,
#   no capital at risk) keeps the current fail-soft-to-raw behavior — it LOGS the raw
#   fallback and returns the raw bound. This test pins the SHADOW half of that mode split.
"""BLOCKER 7 — relationship test: SHADOW missing-floor path LOGS the raw fallback.

Relationship under test: the mode-aware floor resolver
(_resolve_replacement_settlement_floor_lcb) is the single boundary where "is this q_lcb
authoritative for capital?" decides the missing-floor outcome. In SHADOW mode a missing
floor input must NOT block (no capital is at risk) — it must emit a queryable raw-fallback
log record and return None so the caller keeps the raw q_lcb. The symmetric LIVE behavior
(raise / block) is covered by the sibling live test.

This test fails if the shadow branch is made to block (over-tightening) or if it stops
emitting the raw-fallback observability record.
"""
from __future__ import annotations

import logging

import pytest

from src.engine import event_reactor_adapter as adapter


def test_shadow_missing_sigma_floor_returns_none_and_logs_raw_fallback(caplog) -> None:
    """SHADOW + missing sigma-floor cell: the resolver returns None (caller keeps raw) and
    emits a raw-fallback shadow log record. It does NOT raise."""
    with caplog.at_level(logging.WARNING, logger="zeus.replacement_qlcb_shadow"):
        result = adapter._resolve_replacement_settlement_floor_lcb(
            live_authority=False,
            city="Testopolis",
            condition_id="cond-28",
            bin_id="bin-28",
            anchor_mu_c=28.0,
            sigma_floor_c=None,  # missing floor cell
            bounds=(28.0, 28.0),
        )
    assert result is None
    records = [r for r in caplog.records if r.name == "zeus.replacement_qlcb_shadow"]
    assert records, "shadow mode must emit a raw-fallback log record on a missing floor"
    msg = records[0].getMessage().lower()
    assert "raw" in msg and "fallback" in msg
    assert "cond-28" in records[0].getMessage()


def test_shadow_missing_anchor_returns_none_and_logs(caplog) -> None:
    """SHADOW + missing anchor mu: return None (keep raw), log the raw fallback, no raise."""
    with caplog.at_level(logging.WARNING, logger="zeus.replacement_qlcb_shadow"):
        result = adapter._resolve_replacement_settlement_floor_lcb(
            live_authority=False,
            city="Testopolis",
            condition_id="cond-28",
            bin_id="bin-28",
            anchor_mu_c=None,  # missing anchor
            sigma_floor_c=3.18,
            bounds=(28.0, 28.0),
        )
    assert result is None
    assert any(r.name == "zeus.replacement_qlcb_shadow" for r in caplog.records)


def test_shadow_missing_bounds_returns_none_and_logs(caplog) -> None:
    """SHADOW + missing bin bounds: return None (keep raw), log the raw fallback, no raise."""
    with caplog.at_level(logging.WARNING, logger="zeus.replacement_qlcb_shadow"):
        result = adapter._resolve_replacement_settlement_floor_lcb(
            live_authority=False,
            city="Testopolis",
            condition_id="cond-28",
            bin_id="bin-28",
            anchor_mu_c=28.0,
            sigma_floor_c=3.18,
            bounds=None,  # missing topology
        )
    assert result is None
    assert any(r.name == "zeus.replacement_qlcb_shadow" for r in caplog.records)


def test_shadow_floor_present_returns_grounded_ceiling(caplog) -> None:
    """Control: with all inputs present even SHADOW mode computes the settlement-grounded
    ceiling (the floor still floors in shadow; only the MISSING case differs by mode)."""
    from src.calibration.emos import bin_probability_settlement

    result = adapter._resolve_replacement_settlement_floor_lcb(
        live_authority=False,
        city="Testopolis",
        condition_id="cond-28",
        bin_id="bin-28",
        anchor_mu_c=28.0,
        sigma_floor_c=3.18,
        bounds=(28.0, 28.0),
    )
    assert result == pytest.approx(bin_probability_settlement(28.0, 3.18, 28.0, 28.0))


def test_live_resolver_raises_on_missing_floor(caplog) -> None:
    """Mode-split sanity (mirror of the live e2e test, at the resolver boundary): the SAME
    resolver with live_authority=True RAISES on the same missing-floor input — proving the
    raw-fallback is reachable ONLY in shadow mode and never leaks to live capital."""
    with pytest.raises(ValueError) as excinfo:
        adapter._resolve_replacement_settlement_floor_lcb(
            live_authority=True,
            city="Testopolis",
            condition_id="cond-28",
            bin_id="bin-28",
            anchor_mu_c=28.0,
            sigma_floor_c=None,  # missing floor cell
            bounds=(28.0, 28.0),
        )
    assert "REPLACEMENT_0_1_LIVE_AUTHORITY_QLCB_FLOOR_MISSING" in str(excinfo.value)
