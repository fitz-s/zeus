# Created: 2026-06-13
# Last reused/audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md
#   E1-E6 + standing shadow-rollout law (flag default OFF ⇒ shadow telemetry only,
#   live behavior byte-identical). Relationship test: the shadow orchestration
#   computes exit_fraction + q_exit + E_n next to the live decision WITHOUT the
#   flags changing what the rule would have done, and the e-process suspension
#   makes q_exit follow the market when it fires (E5b).
"""Wiring/shadow relationship tests for the exit capability (consult-3 Q1)."""
from __future__ import annotations

import numpy as np

from src.strategy import exit_belief
from src.strategy.exit_policy_shadow import evaluate_exit_policy_shadow

FLAGS_OFF = {
    "edli": {
        "replacement_exit_policy_enabled": False,
        "replacement_exit_belief_blend_enabled": False,
        "replacement_exit_calibration_alarm_enabled": False,
    }
}


def test_shadow_computes_quantities_with_flags_off():
    """Flags OFF: the shadow still COMPUTES q_exit / fraction / E_n (telemetry),
    and reports the flags as off — it does not require the flags to do its math."""
    out = evaluate_exit_policy_shadow(
        settings=FLAGS_OFF,
        q_agent=0.62, q_market=0.58, best_bid=0.55,
        position_units=100, wealth_ex_position=1000, t_remaining_days=2.0,
    )
    assert out["exit_policy_flags"] == {"policy": False, "belief_blend": False, "alarm": False}
    assert out["exit_policy_q_exit"] is not None
    assert out["exit_policy_fraction"] is not None
    assert out["exit_policy_e_value"] is not None
    assert out["exit_policy_h_star"] is not None


def test_shadow_degrades_q_exit_to_agent_without_artifact():
    """Flag-off golden: with no fitted blend artifact present, q_exit == raw agent
    q (the conservative degrade), so the shadow rule belief equals the agent
    posterior the live path already uses — byte-identical belief."""
    out = evaluate_exit_policy_shadow(
        settings=FLAGS_OFF,
        q_agent=0.62, q_market=0.58, best_bid=0.55,
        position_units=100, wealth_ex_position=1000, t_remaining_days=2.0,
    )
    # In the worktree there is no state/exit_belief_fit.json → degrade to agent q.
    assert out["exit_policy_q_exit"] == 0.62
    assert out["exit_policy_q_exit_blend_applied"] is False
    assert "degrade" in out["exit_policy_q_exit_source"]


def test_shadow_skips_sizing_on_incomplete_inputs():
    """Missing best_bid / units / wealth → sizing is skipped gracefully (no crash,
    no fabricated fraction) — the live exit path is never disturbed by the shadow."""
    out = evaluate_exit_policy_shadow(
        settings=FLAGS_OFF,
        q_agent=0.62, q_market=None, best_bid=None,
        position_units=None, wealth_ex_position=None, t_remaining_days=None,
    )
    assert out["exit_policy_fraction"] is None
    assert "sizing_skipped" in out["exit_policy_source"]


def test_shadow_e_process_suspension_makes_rule_follow_market(tmp_path):
    """E5b end-to-end: when a prior e-process state has crossed h* this cycle, the
    shadow uses the MARKET prob as the rule belief (suspension authority), so the
    exit fraction is computed against q_market, not the over-stated agent q."""
    # Build the running e-process near the threshold, then fold one more strongly
    # against the agent so E_n crosses h*.
    # agent says 0.85 (winning); market 0.35; held side LOSES (y=0) repeatedly.
    log_e = 0.0
    from src.strategy.exit_calibration_alarm import log_e_increment

    for _ in range(20):
        log_e += log_e_increment(0.85, 0.35, 0)
    out = evaluate_exit_policy_shadow(
        settings=FLAGS_OFF,
        q_agent=0.85, q_market=0.35, best_bid=0.40,
        position_units=100, wealth_ex_position=1000, t_remaining_days=1.0,
        e_process_prev_log_e=log_e, e_process_prev_n=20,
        resolved_outcome=0,
    )
    assert out["exit_policy_suspended"] is True
    # The rule belief deferred to the market (0.35), so sell-dominance is evaluated
    # against the lower prob — the source records the suspension authority.
    assert "suspended_market_authority" in out["exit_policy_source"]


def test_shadow_no_suspension_when_calibrated():
    """A calibrated agent (q≈market) never trips the alarm, so the rule belief
    stays the agent/blend q — no spurious market override."""
    out = evaluate_exit_policy_shadow(
        settings=FLAGS_OFF,
        q_agent=0.50, q_market=0.50, best_bid=0.45,
        position_units=100, wealth_ex_position=1000, t_remaining_days=1.0,
        e_process_prev_log_e=0.0, e_process_prev_n=0,
        resolved_outcome=1,
    )
    assert out["exit_policy_suspended"] is False
    assert "suspended_market_authority" not in out["exit_policy_source"]


def test_shadow_licensed_blend_pulls_q_exit_toward_market(tmp_path, monkeypatch):
    """With a licensed blend artifact wired in, the shadow q_exit follows the
    market when the agent over-states (the Denver-class fix surfaced in shadow)."""
    rng = np.random.default_rng(0)
    n = 400
    q_market = rng.uniform(0.1, 0.9, n)
    y = (rng.uniform(size=n) < q_market).astype(float)
    q_agent = np.clip(q_market + 0.18, 0.02, 0.98)
    fit = exit_belief.fit_blended_exit_belief(y, q_agent, q_market)
    path = tmp_path / "exit_belief_fit.json"
    exit_belief.write_exit_belief_fit(fit, path)
    # Point the module's artifact path at the licensed fit.
    monkeypatch.setattr(exit_belief, "ARTIFACT_PATH", path)
    exit_belief._cache.update({"mtime": None, "artifact": None})

    out = evaluate_exit_policy_shadow(
        settings=FLAGS_OFF,
        q_agent=0.80, q_market=0.55, best_bid=0.50,
        position_units=100, wealth_ex_position=1000, t_remaining_days=1.0,
    )
    assert out["exit_policy_q_exit_blend_applied"] is True
    assert out["exit_policy_q_exit"] < 0.80
