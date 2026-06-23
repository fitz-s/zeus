# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: per-city historical settlement-skill gate
#   (team-lead approved (a) 2026-06-22; live_order_pathology 2026-06-22).
"""Estimator antibodies for scripts/fit_city_skill_gate.py (walk-forward per-city skill gate).

Invariants:
  1. WALK-FORWARD NO-LEAK: a city's prior_skill at the artifact's as-of date uses ONLY that city's
     rows with target_date strictly before the boundary.
  2. SKILL SIGN: a city whose forecast beat the market on prior rows has prior_skill > 0; a city that
     lost has prior_skill < 0.
  3. LEARNED HYPERPARAMETERS: (min_track_record, skill_floor) are chosen by inner walk-forward
     prequential admitted-EV, returned in _meta — not hard-coded.
  4. SCHEMA: artifact carries _meta.posterior_version + cities[city] = {prior_skill, prior_n}.
"""
from __future__ import annotations

import scripts.fit_city_skill_gate as fcsg
from src.decision import city_skill_gate as g


def _bet(city, target_date, our_brier, mkt_brier, ev):
    # A settled bet row: city, target_date (no-leak key), our/market Brier (for skill), realized ev.
    return fcsg.SettledCityBet(
        city=city, target_date=target_date, our_brier=our_brier, market_brier=mkt_brier, realized_ev=ev,
    )


def test_prior_skill_walk_forward_no_leak():
    rows = [
        _bet("Tokyo", "2026-06-10", 0.02, 0.10, 0.3),
        _bet("Tokyo", "2026-06-11", 0.03, 0.09, 0.2),
        _bet("Tokyo", "2026-06-12", 0.50, 0.10, -0.5),  # AFTER boundary -> must not count
    ]
    skill, n = fcsg.prior_skill(rows, city="Tokyo", boundary="2026-06-12")
    # Only the 2 rows strictly before 2026-06-12: mean(mkt-our) = mean(0.08, 0.06) = 0.07.
    assert n == 2
    assert abs(skill - 0.07) < 1e-9


def test_skill_sign_positive_for_beat_negative_for_loss():
    rows = [
        _bet("Tokyo", "2026-06-10", 0.02, 0.10, 0.3),
        _bet("Karachi", "2026-06-10", 0.78, 0.52, -0.7),
    ]
    sk_t, _ = fcsg.prior_skill(rows, city="Tokyo", boundary="2099-01-01")
    sk_k, _ = fcsg.prior_skill(rows, city="Karachi", boundary="2099-01-01")
    assert sk_t > 0  # beat the market
    assert sk_k < 0  # lost to the market


def test_fit_artifact_schema_and_learned_hyperparams():
    rows = []
    # Tokyo: reliably skilled across many days.
    for d in range(10, 20):
        rows.append(_bet("Tokyo", f"2026-06-{d}", 0.02, 0.10, 0.25))
    # Karachi: reliably bad.
    for d in range(10, 20):
        rows.append(_bet("Karachi", f"2026-06-{d}", 0.78, 0.52, -0.7))
    artifact = fcsg.fit_city_skill_gate(rows, posterior_version=g.DEFAULT_POSTERIOR_VERSION)
    assert "_meta" in artifact and "cities" in artifact
    meta = artifact["_meta"]
    assert meta["posterior_version"] == g.DEFAULT_POSTERIOR_VERSION
    assert "min_track_record" in meta and "skill_floor" in meta  # LEARNED, present in artifact
    assert isinstance(meta["min_track_record"], int)
    # Cities present with end-of-window skill.
    assert artifact["cities"]["Tokyo"]["prior_skill"] > 0
    assert artifact["cities"]["Karachi"]["prior_skill"] < 0


def test_fit_artifact_admits_tokyo_blocks_karachi_via_runtime():
    rows = []
    for d in range(10, 20):
        rows.append(_bet("Tokyo", f"2026-06-{d}", 0.02, 0.10, 0.25))
    for d in range(10, 20):
        rows.append(_bet("Karachi", f"2026-06-{d}", 0.78, 0.52, -0.7))
    artifact = fcsg.fit_city_skill_gate(rows, posterior_version=g.DEFAULT_POSTERIOR_VERSION)
    g.reset_artifact_cache()
    assert g.apply_city_skill_gate(city="Tokyo", artifact=artifact).admit is True
    assert g.apply_city_skill_gate(city="Karachi", artifact=artifact).admit is False
