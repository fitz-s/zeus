# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md
#   E1-E6 (the exit law). Composes src/strategy/exit_policy + exit_belief +
#   exit_calibration_alarm into ONE flag-gated shadow evaluation, mirroring the
#   C2/C3 shadow-logging rollout (flag default OFF ⇒ shadow telemetry only, live
#   behavior byte-identical). Plan: docs/evidence/plans/2026-06-13_exit_capability.md.
"""Flag-gated shadow orchestration of the exit capability (consult-3 Q1, task #52).

This is the single seam the live monitor calls right after the current
``Position.evaluate_exit``. It NEVER changes ``should_exit`` while the
``replacement_exit_*`` flags are OFF — it only computes the exit-policy quantities
(market-blended q_exit, partial-exit fraction, sell-all dominance gap, the
anytime-valid e-process E_n) and returns them as a plain dict for shadow logging.

When the flags ARE on, the caller may consult ``exit_fraction`` / ``q_exit`` /
``suspended`` as the principled sell decision (E2-E6). The g* default is 0
(conservative) until scripts/fit_opportunity_growth_rate.py licenses a non-zero
value; q_exit defaults to the raw agent posterior until exit_belief licenses the
market blend; the e-process suspension only flips when E_n crosses the
cost-derived h*. Every degrade carries a loud source label.

Pure: reads settings flags + the read-only fitted artifacts (mtime-cached). No DB
writes, no engine imports — safe to import from cycle_runtime, scripts, and tests.
"""
from __future__ import annotations

from typing import Any, Mapping

from src.strategy import exit_belief
from src.strategy.exit_calibration_alarm import derive_h_star
from src.strategy.exit_policy import exit_fraction_binary

# Cost inputs for the e-process suspension threshold h* (Q4d cost functional).
# These are the DEFAULT cost ratio (missed-miscalibration vs false-alarm) in
# log-growth units; the operator tunes them from realized exit-refusal losses.
# h* = c_miss / (c_false + c_impl) — NOT a hardcoded 20 (see exit_calibration_alarm).
DEFAULT_C_MISS = 10.0
DEFAULT_C_FALSE = 1.0
DEFAULT_C_IMPL = 0.0


def _edli_flag(settings: Mapping[str, Any], key: str, default: bool = False) -> bool:
    try:
        return bool(settings["edli"].get(key, default))
    except Exception:
        return default


def evaluate_exit_policy_shadow(
    *,
    settings: Mapping[str, Any],
    q_agent: float | None,
    q_market: float | None,
    best_bid: float | None,
    position_units: float | None,
    wealth_ex_position: float | None,
    t_remaining_days: float | None,
    q_sd: float = 0.0,
    z_quantile: float = 0.0,
    fees: float = 0.0,
    g_star: float = 0.0,
    depth: list[tuple[float, float]] | None = None,
    e_process_prev_log_e: float = 0.0,
    e_process_prev_n: int = 0,
    resolved_outcome: int | None = None,
) -> dict[str, Any]:
    """Compute the exit-policy shadow quantities. Returns a flat telemetry dict.

    Args mirror what ``_build_exit_context`` already has in hand: the held-side
    agent posterior q_agent (= exit_context.fresh_prob), the market-implied prob
    q_market (= held-side current_market_price), the executable best_bid, the
    position size n, liquid wealth W (= bankroll), and time to settlement. Cost
    basis is NOT among them — by construction (E1).

    The dict keys are stable shadow-telemetry names; the caller logs them next to
    the live exit decision. NONE of this mutates the live ``should_exit`` when the
    flags are off.
    """
    policy_on = _edli_flag(settings, "replacement_exit_policy_enabled")
    blend_on = _edli_flag(settings, "replacement_exit_belief_blend_enabled")
    alarm_on = _edli_flag(settings, "replacement_exit_calibration_alarm_enabled")

    out: dict[str, Any] = {
        "exit_policy_flags": {
            "policy": policy_on,
            "belief_blend": blend_on,
            "alarm": alarm_on,
        },
        "exit_policy_q_agent": None if q_agent is None else float(q_agent),
        "exit_policy_q_market": None if q_market is None else (
            float(q_market) if q_market == q_market else None  # NaN guard
        ),
        "exit_policy_q_exit": None,
        "exit_policy_q_exit_source": "",
        "exit_policy_q_exit_blend_applied": False,
        "exit_policy_fraction": None,
        "exit_policy_fraction_feasible": None,
        "exit_policy_dominance_gap": None,
        "exit_policy_sell_dominates": None,
        "exit_policy_take_profit_threshold": None,
        "exit_policy_e_value": None,
        "exit_policy_h_star": None,
        "exit_policy_suspended": None,
        "exit_policy_source": "",
    }

    if q_agent is None:
        out["exit_policy_source"] = "shadow_skip_no_agent_q"
        return out

    qa = float(q_agent)

    # ---- E5a: market-blended exit belief (q_exit) ----------------------------
    # Always COMPUTED (shadow); the blend is applied to q_exit only when licensed.
    belief = exit_belief.predict_q_exit(qa, q_market)
    out["exit_policy_q_exit"] = float(belief.q_exit)
    out["exit_policy_q_exit_source"] = belief.source
    out["exit_policy_q_exit_blend_applied"] = bool(belief.blend_applied)

    # ---- E5b: anytime-valid calibration alarm (e-process) --------------------
    # Folds the most recent RESOLVED outcome (if supplied) into the running
    # e-process; the caller carries (log_e, n) across cycles. When no resolved
    # outcome is available this cycle, the state is unchanged but h* is still
    # reported so the suspension can be evaluated against the carried e-value.
    h_star = derive_h_star(DEFAULT_C_MISS, DEFAULT_C_FALSE, DEFAULT_C_IMPL)
    out["exit_policy_h_star"] = float(h_star)
    log_e = float(e_process_prev_log_e)
    if resolved_outcome is not None and q_market is not None and q_market == q_market:
        from src.strategy.exit_calibration_alarm import log_e_increment

        log_e += log_e_increment(qa, float(q_market), int(resolved_outcome))
    import math

    e_value = math.exp(log_e)
    out["exit_policy_e_value"] = float(e_value)
    suspended = bool(e_value >= h_star)
    out["exit_policy_suspended"] = suspended

    # The belief used by the exit rule: when the alarm has SUSPENDED raw-posterior
    # authority (E5b), the exit rule uses the market-blend q_exit even if the
    # blend was not otherwise licensed for routine use — the e-process is the
    # higher-authority miscalibration signal. Otherwise q_exit follows E5a.
    q_for_rule = float(belief.q_exit)
    rule_source = belief.source
    if suspended and q_market is not None and q_market == q_market:
        # Suspension authority: defer to the market second-forecaster directly.
        q_for_rule = float(q_market)
        rule_source = f"e_process_suspended_market_authority E_n={e_value:.3f}>=h*={h_star:.3f}"

    # ---- E2/E3/E6: depth-aware partial-exit fraction + sell-dominance ---------
    can_size = (
        best_bid is not None
        and position_units is not None and float(position_units) > 0
        and wealth_ex_position is not None and float(wealth_ex_position) > 0
    )
    if can_size:
        t_days = float(t_remaining_days) if t_remaining_days is not None else 0.0
        frac = exit_fraction_binary(
            q=q_for_rule,
            bid=float(best_bid),
            position_units=float(position_units),
            wealth_ex_position=float(wealth_ex_position),
            t_remaining_days=t_days,
            g_star=float(g_star),
            fees=float(fees),
            q_sd=float(q_sd),
            z_quantile=float(z_quantile),
            depth=depth,
        )
        out["exit_policy_fraction"] = float(frac.fraction_to_sell)
        out["exit_policy_fraction_feasible"] = bool(frac.feasible)
        out["exit_policy_dominance_gap"] = float(frac.dominance_gap)
        out["exit_policy_sell_dominates"] = bool(frac.sell_dominates)
        out["exit_policy_take_profit_threshold"] = float(frac.take_profit_threshold)
        out["exit_policy_source"] = (
            f"shadow rule_q={q_for_rule:.4f} ({rule_source}); {frac.source}"
        )
    else:
        out["exit_policy_source"] = (
            f"shadow rule_q={q_for_rule:.4f} ({rule_source}); sizing_skipped_incomplete_inputs"
        )

    # Carry the e-process running state forward for the caller's next cycle.
    out["exit_policy_e_process_log_e"] = float(log_e)
    out["exit_policy_e_process_n"] = int(e_process_prev_n) + (
        1 if resolved_outcome is not None else 0
    )
    return out
