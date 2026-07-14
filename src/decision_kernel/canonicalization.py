"""Stable JSON canonicalization and hashing for decision certificates."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

CANONICALIZATION_VERSION = "decision-kernel-json-v1"


def normalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return normalize(dataclasses.asdict(value))
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, set):
        return [normalize(item) for item in sorted(value, key=lambda item: repr(item))]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


_QKERNEL_CURRENT_STATE_IDENTITY_FIELDS: tuple[str, ...] = (
    "source",
    "decision_id",
    "receipt_hash",
    "q_version",
    "sample_hash",
    "candidate_id",
    "route_id",
    "side",
    "bin_id",
    "payoff_q_point",
    "payoff_q_lcb",
    "edge_lcb",
    "point_ev",
    "delta_u_at_min",
    "optimal_stake_usd",
    "optimal_delta_u",
    "q_dot_payoff",
    "cost",
    "cost_basis",
    "route_cost",
    "route_edge_lcb",
    "route_point_ev",
    "chosen_stake_cost",
    "q_lcb_guard_basis",
    "q_lcb_guard_abstained",
    "q_lcb_guard_cell_key",
    "selection_guard_basis",
    "selection_guard_abstained",
    "selection_guard_cell_key",
    "selection_guard_n",
    "selection_guard_q_safe",
    "direction_law_ok",
    "coherence_allows",
    "robust_trade_score",
    "false_edge_rate",
    "global_actuation_identity",
    "global_optimum_semantics",
    "global_candidate_id",
    "global_bin_id",
    "global_economic_identity",
    "global_universe_witness_identity",
    "global_wealth_witness_identity",
    "global_wealth_economic_identity",
    "global_selection_epoch_identity",
    "global_selection_cut_at",
    "global_selection_decision_at",
    "global_book_hash",
    "global_jit_book_hash",
    "global_jit_venue_book_hash",
    "global_jit_book_snapshot_id",
    "global_jit_execution_curve_identity",
    "global_target_shares",
    "global_expected_cost_usd",
    "global_limit_price",
    "global_expected_fill_price_before_fee",
    "global_max_spend_usd",
    "global_robust_delta_log_wealth",
    "global_robust_ev_usd",
    "global_capital_efficiency",
    "global_cut_time_win_probability_lcb",
    "global_cut_time_loss_probability_ucb",
    "global_terminal_win_probability_lcb",
    "global_terminal_loss_probability_ucb",
    "global_terminal_loss_payoff_usd",
    "global_terminal_win_payoff_usd",
    "global_terminal_median_payoff_usd",
    "global_terminal_wealth_after_loss_usd",
    "global_terminal_wealth_after_win_usd",
    "global_cut_time_expected_value_diagnostic_usd",
    "global_expected_value_diagnostic_usd",
    "global_expected_value_semantics",
    "global_terminal_payoff_semantics",
)

_QKERNEL_BUY_FAK_PREFIX_IDENTITY_FIELDS: tuple[str, ...] = (
    "global_buy_fak_prefix_semantics",
    "global_buy_fak_fee_rate_source",
    "global_buy_fak_execution_curve_identity",
    "global_buy_fak_fee_rate",
    "global_buy_fak_fee_rounding_bound",
    "global_buy_fak_worst_fee_shape",
    "global_buy_fak_worst_fee_per_share",
    "global_buy_fak_worst_unit_cost",
    "global_buy_fak_full_worst_cost_usd",
    "global_buy_fak_full_robust_delta_log_wealth",
    "global_buy_fak_full_robust_ev_usd",
)


def qkernel_current_state_identity_hash(economics: Mapping[str, Any]) -> str:
    """Recomputable identity for the current-posterior execution certificate."""

    fields = _QKERNEL_CURRENT_STATE_IDENTITY_FIELDS
    if "global_buy_fak_prefix_semantics" in economics:
        fields += _QKERNEL_BUY_FAK_PREFIX_IDENTITY_FIELDS
    return stable_hash(
        {field: economics.get(field) for field in fields}
    )


def qkernel_declares_current_state(economics: Mapping[str, Any]) -> bool:
    """Whether a payload has entered the non-downgradable current-state grammar."""

    basis = "CURRENT_POSTERIOR_BAND"
    return (
        str(economics.get("q_lcb_guard_basis") or "").strip() == basis
        or str(economics.get("selection_guard_basis") or "").strip() == basis
        or bool(str(economics.get("current_state_identity_hash") or "").strip())
    )


def qkernel_current_state_rejection_reason(economics: Any) -> str | None:
    """Return the broken field, or ``None`` for one sealed current-state proof."""

    if not isinstance(economics, Mapping):
        return "payload_not_mapping"
    basis = "CURRENT_POSTERIOR_BAND"
    sample_hash = str(economics.get("sample_hash") or "").strip()
    try:
        n_draws = int(economics.get("selection_guard_n") or 0)
    except (TypeError, ValueError):
        return "selection_guard_n_invalid"
    checks = (
        (str(economics.get("source") or "").strip() == "qkernel_spine", "source"),
        (bool(str(economics.get("decision_id") or "").strip()), "decision_id"),
        (bool(str(economics.get("receipt_hash") or "").strip()), "receipt_hash"),
        (bool(str(economics.get("q_version") or "").strip()), "q_version"),
        (str(economics.get("q_lcb_guard_basis") or "").strip() == basis, "q_lcb_guard_basis"),
        (
            str(economics.get("selection_guard_basis") or "").strip() == basis,
            "selection_guard_basis",
        ),
        (economics.get("q_lcb_guard_abstained") is False, "q_lcb_guard_abstained"),
        (
            economics.get("selection_guard_abstained") is False,
            "selection_guard_abstained",
        ),
        (bool(sample_hash), "sample_hash"),
        (
            str(economics.get("q_lcb_guard_cell_key") or "").strip() == sample_hash,
            "q_lcb_guard_cell_key",
        ),
        (
            str(economics.get("selection_guard_cell_key") or "").strip() == sample_hash,
            "selection_guard_cell_key",
        ),
        (n_draws >= 2, "selection_guard_n"),
        (
            str(economics.get("current_state_identity_hash") or "").strip()
            == qkernel_current_state_identity_hash(economics),
            "current_state_identity_hash",
        ),
    )
    for passed, field in checks:
        if not passed:
            return field
    return None


def qkernel_global_current_state_rejection_reason(
    economics: Any,
    *,
    direction: str | None = None,
) -> str | None:
    """Validate one sealed global winner independent of legacy route fields."""

    current_reason = qkernel_current_state_rejection_reason(economics)
    if current_reason is not None:
        return f"current_state:{current_reason}"
    assert isinstance(economics, Mapping)
    if not str(economics.get("global_actuation_identity") or "").strip():
        return "global_actuation_identity"
    side = str(economics.get("side") or "").strip().upper()
    direction_text = str(direction or "").strip().lower()
    native_side = (
        "YES"
        if direction_text.endswith("_yes")
        else "NO"
        if direction_text.endswith("_no")
        else None
    )
    if side not in {"YES", "NO"}:
        return "side"
    if native_side is not None and side != native_side:
        return "side_direction_mismatch"
    for field in (
        "global_candidate_id",
        "global_bin_id",
        "global_universe_witness_identity",
        "global_wealth_witness_identity",
        "global_selection_epoch_identity",
        "global_selection_cut_at",
        "global_selection_decision_at",
        "global_jit_book_hash",
        "global_jit_venue_book_hash",
        "global_jit_book_snapshot_id",
        "global_jit_execution_curve_identity",
        "global_expected_value_semantics",
        "global_terminal_payoff_semantics",
    ):
        if not str(economics.get(field) or "").strip():
            return field
    if economics.get("global_optimum_semantics") != "CUT_TIME_GLOBAL_OPTIMUM":
        return "global_optimum_semantics"
    numeric: dict[str, float] = {}
    for field in (
        "payoff_q_point",
        "payoff_q_lcb",
        "cost",
        "edge_lcb",
        "global_target_shares",
        "global_expected_cost_usd",
        "global_max_spend_usd",
        "global_robust_delta_log_wealth",
        "global_robust_ev_usd",
        "global_cut_time_win_probability_lcb",
        "global_cut_time_loss_probability_ucb",
        "global_terminal_win_probability_lcb",
        "global_terminal_loss_probability_ucb",
        "global_terminal_loss_payoff_usd",
        "global_terminal_win_payoff_usd",
        "global_terminal_median_payoff_usd",
        "global_terminal_wealth_after_loss_usd",
        "global_terminal_wealth_after_win_usd",
        "global_cut_time_expected_value_diagnostic_usd",
        "global_expected_value_diagnostic_usd",
    ):
        try:
            value = float(economics.get(field))
        except (TypeError, ValueError):
            return f"{field}_invalid"
        if not math.isfinite(value):
            return f"{field}_non_finite"
        numeric[field] = value
    point = numeric["payoff_q_point"]
    lcb = numeric["payoff_q_lcb"]
    cost = numeric["cost"]
    edge = numeric["edge_lcb"]
    shares = numeric["global_target_shares"]
    expected_cost = numeric["global_expected_cost_usd"]
    max_spend = numeric["global_max_spend_usd"]
    robust_du = numeric["global_robust_delta_log_wealth"]
    robust_ev = numeric["global_robust_ev_usd"]
    cut_win = numeric["global_cut_time_win_probability_lcb"]
    cut_loss = numeric["global_cut_time_loss_probability_ucb"]
    terminal_win = numeric["global_terminal_win_probability_lcb"]
    terminal_loss = numeric["global_terminal_loss_probability_ucb"]
    loss_payoff = numeric["global_terminal_loss_payoff_usd"]
    win_payoff = numeric["global_terminal_win_payoff_usd"]
    median_payoff = numeric["global_terminal_median_payoff_usd"]
    wealth_after_loss = numeric["global_terminal_wealth_after_loss_usd"]
    wealth_after_win = numeric["global_terminal_wealth_after_win_usd"]
    cut_ev = numeric["global_cut_time_expected_value_diagnostic_usd"]
    expected_value = numeric["global_expected_value_diagnostic_usd"]
    if not (0.0 <= lcb <= point <= 1.0):
        return "probability_order"
    if not (0.0 < cost < 1.0 and edge > 0.0):
        return "execution_edge"
    if not math.isclose(lcb, cost + edge, rel_tol=1e-9, abs_tol=1e-9):
        return "edge_identity"
    if not (
        shares > 0.0
        and expected_cost > 0.0
        and max_spend + 1e-9 >= expected_cost
        and robust_du > 0.0
        and robust_ev > 0.0
    ):
        return "global_utility_envelope"
    if not math.isclose(cost, expected_cost / shares, rel_tol=1e-9, abs_tol=1e-9):
        return "global_cost_identity"
    probability_tol = 1e-12
    if not (
        0.5 < terminal_win <= cut_win + probability_tol
        and cut_win <= 1.0
        and 0.0 <= cut_loss <= terminal_loss + probability_tol
        and terminal_loss < 0.5
        and math.isclose(cut_win + cut_loss, 1.0, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            terminal_win + terminal_loss,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and math.isclose(terminal_win, lcb, rel_tol=0.0, abs_tol=1e-12)
    ):
        return "global_terminal_probability_identity"
    if not (
        math.isclose(loss_payoff, -expected_cost, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            win_payoff,
            shares - expected_cost,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and math.isclose(median_payoff, win_payoff, rel_tol=0.0, abs_tol=1e-12)
        and median_payoff > 0.0
        and wealth_after_loss > 0.0
        and wealth_after_win > 0.0
        and math.isclose(cut_ev, robust_ev, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            expected_value,
            terminal_win * shares - expected_cost,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        return "global_terminal_payoff_identity"
    if economics["global_expected_value_semantics"] != (
        "DIAGNOSTIC_EXPECTATION_NOT_REALIZED_GAIN"
    ):
        return "global_expected_value_semantics"
    if economics["global_terminal_payoff_semantics"] != "BINARY_0_1":
        return "global_terminal_payoff_semantics"
    if "global_buy_fak_prefix_semantics" in economics:
        prefix_reason = qkernel_global_buy_fak_prefix_rejection_reason(
            economics,
            direction=direction,
        )
        if prefix_reason is not None:
            return f"global_buy_fak:{prefix_reason}"
    return None


def qkernel_global_buy_fak_prefix_rejection_reason(
    economics: Any,
    *,
    direction: str | None = None,
) -> str | None:
    """Independently recompute the worst-limit proof required for BUY FAK."""

    if not isinstance(economics, Mapping):
        return "payload_not_mapping"
    if economics.get("global_buy_fak_prefix_semantics") != (
        "CONCAVE_WORST_LIMIT_ALL_NONZERO_PREFIXES_POSITIVE"
    ):
        return "semantics"
    if economics.get("global_buy_fak_fee_rate_source") != "CURRENT_EXECUTABLE_CURVE":
        return "fee_rate_source"
    if economics.get("global_buy_fak_fee_rounding_bound") != (
        "ROUNDED_FEE_AT_MOST_TWO_X_UNROUNDED"
    ):
        return "fee_rounding_bound"
    if str(economics.get("global_buy_fak_execution_curve_identity") or "") != str(
        economics.get("global_jit_execution_curve_identity") or ""
    ):
        return "execution_curve_identity"
    direction_text = str(direction or "").strip().lower()
    side = str(economics.get("side") or "").strip().upper()
    native_side = (
        "YES"
        if direction_text.endswith("_yes")
        else "NO"
        if direction_text.endswith("_no")
        else None
    )
    if side not in {"YES", "NO"} or (
        native_side is not None and native_side != side
    ):
        return "side"
    fields = (
        "global_target_shares",
        "global_limit_price",
        "global_terminal_win_probability_lcb",
        "global_terminal_loss_probability_ucb",
        "global_terminal_loss_payoff_usd",
        "global_terminal_win_payoff_usd",
        "global_terminal_wealth_after_loss_usd",
        "global_terminal_wealth_after_win_usd",
        "global_buy_fak_fee_rate",
        "global_buy_fak_worst_fee_shape",
        "global_buy_fak_worst_fee_per_share",
        "global_buy_fak_worst_unit_cost",
        "global_buy_fak_full_worst_cost_usd",
        "global_buy_fak_full_robust_delta_log_wealth",
        "global_buy_fak_full_robust_ev_usd",
    )
    try:
        values = {field: float(economics.get(field)) for field in fields}
    except (TypeError, ValueError):
        return "numeric_field_invalid"
    if not all(math.isfinite(value) for value in values.values()):
        return "numeric_field_non_finite"
    shares = values["global_target_shares"]
    limit = values["global_limit_price"]
    win_q = values["global_terminal_win_probability_lcb"]
    loss_q = values["global_terminal_loss_probability_ucb"]
    fee_rate = values["global_buy_fak_fee_rate"]
    if not (
        shares > 0
        and 0 < limit < 1
        and 0 <= fee_rate < 1
        and 0 < win_q <= 1
        and 0 <= loss_q < 1
        and math.isclose(win_q + loss_q, 1.0, rel_tol=0.0, abs_tol=1e-12)
    ):
        return "domain"
    loss_baseline = (
        values["global_terminal_wealth_after_loss_usd"]
        - values["global_terminal_loss_payoff_usd"]
    )
    win_baseline = (
        values["global_terminal_wealth_after_win_usd"]
        - values["global_terminal_win_payoff_usd"]
    )
    max_fee_shape = 0.25 if limit >= 0.5 else limit * (1.0 - limit)
    worst_fee_per_share = 2.0 * fee_rate * max_fee_shape
    unit_cost = limit + worst_fee_per_share
    full_cost = unit_cost * shares
    loss_after = loss_baseline - full_cost
    win_after = win_baseline - full_cost + shares
    if min(loss_baseline, win_baseline, loss_after, win_after) <= 0:
        return "wealth"
    robust_du = loss_q * math.log(loss_after / loss_baseline) + win_q * math.log(
        win_after / win_baseline
    )
    robust_ev = win_q * shares - full_cost
    expected = {
        "global_buy_fak_worst_fee_shape": max_fee_shape,
        "global_buy_fak_worst_fee_per_share": worst_fee_per_share,
        "global_buy_fak_worst_unit_cost": unit_cost,
        "global_buy_fak_full_worst_cost_usd": full_cost,
        "global_buy_fak_full_robust_delta_log_wealth": robust_du,
        "global_buy_fak_full_robust_ev_usd": robust_ev,
    }
    for field, expected_value in expected.items():
        if not math.isclose(
            values[field], expected_value, rel_tol=1e-12, abs_tol=1e-12
        ):
            return field
    if robust_du <= 0 or robust_ev <= 0:
        return "non_positive"
    return None
