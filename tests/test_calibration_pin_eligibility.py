# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: TIGGE spec v3 §3 Phase 0 #6 / critic v2 A2 BLOCKER
"""A2: cycle_stratified_pin_eligible_after gate.

Critic v2 A2 BLOCKER: c12 IFS-ENS has no data on disk for 2024-01-01..2024-06-01,
so cycle-stratified Platt resolution must degrade to pooled-cycle when the
requested frozen_as_of is older than the eligibility floor.

This test exercises the resolver directly (pre + post the floor) and confirms
the degrade-to-pooled-cycle behavior fires only on the pre-eligibility side.
"""
from __future__ import annotations

import src.calibration.manager as cm


def _reset_pin_cache() -> None:
    """Invalidate the module-level cache so per-test settings.json patches apply."""
    cm._PIN_CONFIG_CACHE = None


def test_pre_eligibility_cycle_stratified_falls_back_to_pooled(monkeypatch):
    """frozen_as_of < floor → resolver returns pooled-cycle frozen_as_of."""
    _reset_pin_cache()
    monkeypatch.setattr(
        cm,
        "get_calibration_pin_config",
        lambda: {
            "frozen_as_of": {
                "00": "2025-01-01T00:00:00Z",   # pooled fallback (single-cycle bucket)
                "12": "2025-04-15T00:00:00Z",   # cycle-stratified, BELOW floor 2025-06-02
            },
            "model_keys": {},
        },
    )
    monkeypatch.setattr(
        cm,
        "_cycle_stratified_pin_eligibility_floor",
        lambda: "2025-06-02",
    )
    fao, _ = cm._resolve_pin_for_bucket(
        temperature_metric="C",
        cluster="cluster_a",
        season="winter",
        cycle="12",
    )
    # Should have degraded to pooled-cycle ("00") frozen_as_of, not the c12 stratified value.
    assert fao == "2025-01-01T00:00:00Z", (
        f"pre-eligibility c12 pin must degrade to pooled-cycle; got {fao!r}"
    )


def test_post_eligibility_cycle_stratified_returns_unchanged(monkeypatch):
    """frozen_as_of >= floor → resolver returns the cycle-stratified value as-is."""
    _reset_pin_cache()
    monkeypatch.setattr(
        cm,
        "get_calibration_pin_config",
        lambda: {
            "frozen_as_of": {
                "00": "2025-01-01T00:00:00Z",
                "12": "2025-09-01T00:00:00Z",   # ABOVE floor 2025-06-02
            },
            "model_keys": {},
        },
    )
    monkeypatch.setattr(
        cm,
        "_cycle_stratified_pin_eligibility_floor",
        lambda: "2025-06-02",
    )
    fao, _ = cm._resolve_pin_for_bucket(
        temperature_metric="C",
        cluster="cluster_a",
        season="autumn",
        cycle="12",
    )
    assert fao == "2025-09-01T00:00:00Z", (
        f"post-eligibility c12 pin must pass through unchanged; got {fao!r}"
    )


def test_floor_loaded_from_settings_json():
    """Sanity: the live settings.json carries the documented eligibility floor."""
    _reset_pin_cache()
    floor = cm._cycle_stratified_pin_eligibility_floor()
    assert floor == "2025-06-02", (
        f"settings.json::calibration.pin.cycle_stratified_pin_eligible_after "
        f"should be '2025-06-02' per A2 fix; got {floor!r}"
    )


def test_below_floor_lexical_compare():
    """The lexical YYYY-MM-DD compare correctly orders pre/post floor dates."""
    assert cm._frozen_as_of_below_floor("2025-04-15T12:00:00Z", "2025-06-02") is True
    assert cm._frozen_as_of_below_floor("2025-06-01", "2025-06-02") is True
    assert cm._frozen_as_of_below_floor("2025-06-02", "2025-06-02") is False
    assert cm._frozen_as_of_below_floor("2025-08-01T00:00:00Z", "2025-06-02") is False
    # Either side missing → no gate (legacy back-compat)
    assert cm._frozen_as_of_below_floor(None, "2025-06-02") is False
    assert cm._frozen_as_of_below_floor("2025-04-15", None) is False


def test_no_pooled_fallback_keeps_original(monkeypatch):
    """If neither 'pooled' nor '00' exists, no degrade happens (cannot degrade)."""
    _reset_pin_cache()
    monkeypatch.setattr(
        cm,
        "get_calibration_pin_config",
        lambda: {
            "frozen_as_of": {
                "12": "2025-04-15T00:00:00Z",   # only c12, no pooled / 00
            },
            "model_keys": {},
        },
    )
    monkeypatch.setattr(
        cm,
        "_cycle_stratified_pin_eligibility_floor",
        lambda: "2025-06-02",
    )
    fao, _ = cm._resolve_pin_for_bucket(
        temperature_metric="C",
        cluster="cluster_a",
        season="winter",
        cycle="12",
    )
    # No pooled fallback available → original cycle-stratified value preserved.
    assert fao == "2025-04-15T00:00:00Z"
