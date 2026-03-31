"""Spec-owned validation manifest derived from Blueprint v2 and design philosophy."""

from math import ceil


SPEC_ENTRY_VALIDATIONS = [
    "ens_fetch",
    "mc_instrument_noise",
    "platt_calibration",
    "normalization",
    "alpha_posterior",
    "bootstrap_ci",
    "fdr_filter",
    "kelly_sizing",
    "dynamic_multiplier",
    "risk_limits",
    "anti_churn",
    "edge_recheck",
]


SPEC_EXIT_VALIDATIONS = [
    "fresh_ens_fetch",
    "mc_instrument_noise",
    "platt_recalibration",
    "forward_edge_compute",
    "consecutive_cycle_check",
    "ev_gate",
    "near_settlement_gate",
    "vig_extreme_gate",
    "day0_observation",
]


SYMMETRY_PAIRS = {
    "ens_fetch": "fresh_ens_fetch",
    "mc_instrument_noise": "mc_instrument_noise",
    "platt_calibration": "platt_recalibration",
    "alpha_posterior": "forward_edge_compute",
    "anti_churn": "consecutive_cycle_check",
}


EXIT_REQUIRED_STEPS = [
    "fresh_ens_fetch",
    "mc_instrument_noise",
    "forward_edge_compute",
    "consecutive_cycle_check",
]


def required_exit_ratio() -> int:
    return ceil(len(SPEC_ENTRY_VALIDATIONS) * 0.7)
