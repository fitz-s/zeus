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

from src.contracts.global_auction_receipt import GlobalAuctionReceiptRef
from src.contracts.strategy_capital_allocation import STRATEGY_LOG_UTILITY_BASIS

CANONICALIZATION_VERSION = "decision-kernel-json-v1"
_EXPECTED_IDENTITY_UNSET = object()


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
    "global_winner_event_id",
    "global_auction_receipt",
    "global_optimum_semantics",
    "global_candidate_id",
    "global_execution_mode",
    "global_condition_id",
    "global_token_id",
    "global_family_key",
    "global_probability_witness_identity",
    "global_probability_authority",
    "global_posterior_id",
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
    "global_current_token_shares",
    "global_full_kelly_target_shares",
    "global_fractional_kelly_target_shares",
    "global_buy_sizing_mode",
    "global_expected_cost_usd",
    "global_limit_price",
    "global_expected_fill_price_before_fee",
    "global_max_spend_usd",
    "global_probability_functional",
    "payoff_q_action",
    "edge_expected",
    "global_expected_delta_log_wealth",
    "global_expected_ev_usd",
    "global_expected_capital_efficiency",
    "global_ruin_probability_reduction",
    "global_terminal_ruin_probability_reduction",
    "global_utility_basis",
    "global_proposal_expected_delta_log_wealth",
    "global_proposal_expected_ev_usd",
    "global_proposal_expected_log_growth_per_hour",
    "global_proposal_expected_capital_efficiency",
    "global_proposal_capital_lock_hours",
    "global_proposal_fill_semantics",
    "global_robust_delta_log_wealth",
    "global_robust_ev_usd",
    "global_capital_efficiency",
    "global_cut_time_win_probability_mean",
    "global_cut_time_loss_probability_mean",
    "global_terminal_win_probability_mean",
    "global_terminal_loss_probability_mean",
    "global_cut_time_win_probability_lcb",
    "global_cut_time_loss_probability_ucb",
    "global_terminal_win_probability_lcb",
    "global_terminal_loss_probability_ucb",
    "global_terminal_loss_payoff_usd",
    "global_terminal_win_payoff_usd",
    "global_terminal_median_payoff_usd",
    "global_terminal_wealth_after_loss_usd",
    "global_terminal_wealth_after_win_usd",
    "global_cut_time_expected_value_usd",
    "global_expected_value_usd",
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
    "global_buy_fak_probability_basis",
    "global_buy_fak_full_expected_delta_log_wealth",
    "global_buy_fak_full_expected_ev_usd",
    "global_buy_fak_full_robust_delta_log_wealth",
    "global_buy_fak_full_robust_ev_usd",
)

_QKERNEL_MAKER_REST_IDENTITY_FIELDS: tuple[str, ...] = (
    "global_fill_probability",
    "global_fill_probability_source",
    "global_rest_deadline_minutes",
    "global_maker_fill_witness",
)

_QKERNEL_GLOBAL_CURRENT_STATE_MARKERS: tuple[str, ...] = (
    "global_actuation_identity",
    "global_candidate_id",
    "global_target_shares",
    "global_expected_cost_usd",
    "global_max_spend_usd",
    "global_robust_delta_log_wealth",
    "global_robust_ev_usd",
)


def _declares_global_current_state(economics: Mapping[str, Any]) -> bool:
    return any(field in economics for field in _QKERNEL_GLOBAL_CURRENT_STATE_MARKERS)


def qkernel_current_state_identity_hash(economics: Mapping[str, Any]) -> str:
    """Recomputable identity for the current-posterior execution certificate."""

    fields = _QKERNEL_CURRENT_STATE_IDENTITY_FIELDS
    if (
        "global_execution_mode" not in economics
        and not _declares_global_current_state(economics)
    ):
        fields = tuple(
            field for field in fields if field != "global_execution_mode"
        )
    elif economics.get("global_execution_mode") == "MAKER_REST":
        fields += _QKERNEL_MAKER_REST_IDENTITY_FIELDS
    if "global_buy_fak_prefix_semantics" in economics:
        fields += _QKERNEL_BUY_FAK_PREFIX_IDENTITY_FIELDS
    return stable_hash(
        {field: economics.get(field) for field in fields}
    )


def qkernel_declares_current_state(economics: Mapping[str, Any]) -> bool:
    """Whether a payload has entered the non-downgradable current-state grammar."""

    band_basis = "CURRENT_POSTERIOR_BAND"
    selection_bases = {
        band_basis,
        "CURRENT_POSTERIOR_PREDICTIVE_MEAN",
    }
    return (
        str(economics.get("q_lcb_guard_basis") or "").strip() == band_basis
        or str(economics.get("selection_guard_basis") or "").strip()
        in selection_bases
        or bool(str(economics.get("current_state_identity_hash") or "").strip())
    )


def qkernel_current_state_rejection_reason(economics: Any) -> str | None:
    """Return the broken field, or ``None`` for one sealed current-state proof."""

    if not isinstance(economics, Mapping):
        return "payload_not_mapping"
    if _declares_global_current_state(economics) and economics.get(
        "global_execution_mode"
    ) not in {"TAKER_LIMIT", "MAKER_REST"}:
        return "global_execution_mode"
    band_basis = "CURRENT_POSTERIOR_BAND"
    selection_bases = {
        band_basis,
        "CURRENT_POSTERIOR_PREDICTIVE_MEAN",
    }
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
        (
            str(economics.get("q_lcb_guard_basis") or "").strip() == band_basis,
            "q_lcb_guard_basis",
        ),
        (
            str(economics.get("selection_guard_basis") or "").strip()
            in selection_bases,
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
    expected_candidate_id: str | None = None,
    expected_condition_id: str | None = None,
    expected_token_id: str | None = None,
    expected_family_key: str | None = None,
    expected_probability_authority: str | None = None,
    expected_posterior_id: object = _EXPECTED_IDENTITY_UNSET,
    expected_actuation_identity: str | None = None,
    expected_economic_identity: str | None = None,
    expected_probability_witness_identity: str | None = None,
    expected_universe_witness_identity: str | None = None,
    expected_wealth_witness_identity: str | None = None,
    expected_wealth_economic_identity: str | None = None,
    expected_selection_epoch_identity: str | None = None,
    expected_selection_cut_at: str | None = None,
    expected_selection_decision_at: str | None = None,
) -> str | None:
    """Validate one sealed global winner independent of legacy route fields."""

    current_reason = qkernel_current_state_rejection_reason(economics)
    if current_reason is not None:
        return f"current_state:{current_reason}"
    assert isinstance(economics, Mapping)
    if not str(economics.get("global_actuation_identity") or "").strip():
        return "global_actuation_identity"
    try:
        receipt_ref = GlobalAuctionReceiptRef.from_payload(
            economics.get("global_auction_receipt")
        )
        receipt_ref.assert_matches_actuation(
            winner_event_id=economics.get("global_winner_event_id"),
            winner_candidate_id=economics.get("global_candidate_id"),
            winner_actuation_identity=economics.get("global_actuation_identity"),
            selection_epoch_identity=economics.get(
                "global_selection_epoch_identity"
            ),
        )
    except ValueError:
        return "global_auction_receipt"
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
    exact_binding_requested = any(
        expected is not None
        for expected in (
            expected_candidate_id,
            expected_condition_id,
            expected_token_id,
            expected_family_key,
            expected_probability_authority,
            expected_actuation_identity,
            expected_economic_identity,
            expected_probability_witness_identity,
            expected_universe_witness_identity,
            expected_wealth_witness_identity,
            expected_wealth_economic_identity,
            expected_selection_epoch_identity,
            expected_selection_cut_at,
            expected_selection_decision_at,
        )
    ) or expected_posterior_id is not _EXPECTED_IDENTITY_UNSET
    if exact_binding_requested:
        for field in (
            "global_candidate_id",
            "global_condition_id",
            "global_token_id",
            "global_family_key",
            "global_probability_witness_identity",
            "global_probability_authority",
        ):
            if not str(economics.get(field) or "").strip():
                return field
        if "global_posterior_id" not in economics:
            return "global_posterior_id"
    expected_identity = (
        ("global_candidate_id", expected_candidate_id),
        ("global_condition_id", expected_condition_id),
        ("global_token_id", expected_token_id),
        ("global_family_key", expected_family_key),
        ("global_probability_authority", expected_probability_authority),
        ("global_actuation_identity", expected_actuation_identity),
        ("global_economic_identity", expected_economic_identity),
        (
            "global_probability_witness_identity",
            expected_probability_witness_identity,
        ),
        ("global_universe_witness_identity", expected_universe_witness_identity),
        ("global_wealth_witness_identity", expected_wealth_witness_identity),
        ("global_wealth_economic_identity", expected_wealth_economic_identity),
        ("global_selection_epoch_identity", expected_selection_epoch_identity),
        ("global_selection_cut_at", expected_selection_cut_at),
        ("global_selection_decision_at", expected_selection_decision_at),
    )
    for field, expected in expected_identity:
        if expected is None:
            continue
        if str(economics.get(field) or "").strip() != str(expected).strip():
            return f"{field}_mismatch"
    if expected_posterior_id is not _EXPECTED_IDENTITY_UNSET:
        if str(economics.get("global_posterior_id") or "").strip() != str(
            expected_posterior_id or ""
        ).strip():
            return "global_posterior_id_mismatch"
    for field in (
        "global_candidate_id",
        "global_winner_event_id",
        "global_bin_id",
        "global_economic_identity",
        "global_universe_witness_identity",
        "global_wealth_witness_identity",
        "global_wealth_economic_identity",
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
    execution_mode = economics.get("global_execution_mode")
    if execution_mode is not None and execution_mode not in {
        "TAKER_LIMIT",
        "MAKER_REST",
    }:
        return "global_execution_mode"
    if economics.get("global_utility_basis") != STRATEGY_LOG_UTILITY_BASIS:
        return "global_utility_basis"
    try:
        ruin_reduction = float(
            economics.get("global_ruin_probability_reduction")
        )
        terminal_ruin_reduction = float(
            economics.get("global_terminal_ruin_probability_reduction")
        )
        proposal_du = float(
            economics.get("global_proposal_expected_delta_log_wealth")
        )
        proposal_ev = float(economics.get("global_proposal_expected_ev_usd"))
        proposal_lock = float(
            economics.get("global_proposal_capital_lock_hours")
        )
        proposal_rate = float(
            economics.get("global_proposal_expected_log_growth_per_hour")
        )
        proposal_efficiency = float(
            economics.get("global_proposal_expected_capital_efficiency")
        )
    except (TypeError, ValueError):
        return "global_expected_growth_invalid"
    if not all(
        math.isfinite(value)
        for value in (
            ruin_reduction,
            terminal_ruin_reduction,
            proposal_du,
            proposal_ev,
            proposal_lock,
            proposal_rate,
            proposal_efficiency,
        )
    ):
        return "global_expected_growth_non_finite"
    if (
        not 0.0 <= ruin_reduction <= 1.0
        or not 0.0 <= terminal_ruin_reduction <= 1.0
        or ruin_reduction != terminal_ruin_reduction
        or proposal_lock <= 0.0
        or proposal_rate != proposal_du / proposal_lock
    ):
        return "global_expected_growth_identity"
    if direction_text.startswith("buy_") and ruin_reduction != 0.0:
        return "global_buy_ruin_probability_reduction"
    if (
        execution_mode == "TAKER_LIMIT"
        and economics.get("global_proposal_fill_semantics") != "IMMEDIATE_FILL"
    ):
        return "global_proposal_fill_semantics"
    if execution_mode == "MAKER_REST":
        return _qkernel_global_maker_rest_rejection_reason(
            economics,
            direction=direction,
        )
    functional = str(
        economics.get("global_probability_functional")
        or "LOWER_CVAR_PARAMETER_DRAWS"
    ).strip()
    if functional == "POSTERIOR_PREDICTIVE_MEAN":
        mean_reason = _qkernel_global_mean_buy_rejection_reason(
            economics,
            direction=direction,
        )
        if mean_reason is not None:
            return mean_reason
        try:
            mean_du = float(economics.get("global_expected_delta_log_wealth"))
            mean_ev = float(economics.get("global_expected_ev_usd"))
            mean_efficiency = float(
                economics.get("global_expected_capital_efficiency")
            )
        except (TypeError, ValueError):
            return "global_mean_proposal_identity"
        if not (
            proposal_du == mean_du
            and proposal_ev == mean_ev
            and proposal_efficiency == mean_efficiency
        ):
            return "global_mean_proposal_identity"
        return None
    if functional != "LOWER_CVAR_PARAMETER_DRAWS":
        return "global_probability_functional"
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
        "global_cut_time_expected_value_usd",
        "global_expected_value_usd",
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
    cut_ev = numeric["global_cut_time_expected_value_usd"]
    expected_value = numeric["global_expected_value_usd"]
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
        0.0 < terminal_win <= cut_win + probability_tol
        and cut_win <= 1.0
        and 0.0 <= cut_loss <= terminal_loss + probability_tol
        and terminal_loss < 1.0
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
        and (
            (
                terminal_win > 0.5
                and math.isclose(
                    median_payoff,
                    win_payoff,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
            or (
                terminal_win < 0.5
                and math.isclose(
                    median_payoff,
                    loss_payoff,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
            or (
                terminal_win == 0.5
                and loss_payoff - 1e-12 <= median_payoff <= win_payoff + 1e-12
            )
        )
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
        "POINT_EVIDENCE_EXPECTATION_NOT_REALIZED_GAIN"
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


def _maker_fill_parts_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode())
        digest.update(b"\x1f")
    return digest.hexdigest()


def _maker_fill_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _qkernel_global_maker_rest_rejection_reason(
    economics: Mapping[str, Any],
    *,
    direction: str | None,
) -> str | None:
    """Recompute the candidate-bound partial-fill witness and maker objective."""

    payload = economics.get("global_maker_fill_witness")
    if not isinstance(payload, Mapping):
        return "CURRENT_MAKER_FILL_WITNESS_UNAVAILABLE"
    required_fields = {
        "schema_version",
        "witness_identity",
        "candidate_binding_identity",
        "action",
        "ledger_snapshot_id",
        "position_id",
        "held_shares",
        "asset_epoch_identity",
        "proposal_identity",
        "book_snapshot_id",
        "book_hash",
        "limit_price",
        "rest_deadline_minutes",
        "source_identity",
        "model_identity",
        "sample_identity",
        "training_cutoff_at_utc",
        "issued_at_utc",
        "valid_until_at_utc",
        "validated_at_utc",
        "outcomes",
    }
    if set(payload) != required_fields or payload.get("schema_version") != 1:
        return "CURRENT_MAKER_FILL_WITNESS_SHAPE_INVALID"
    text_fields = (
        "witness_identity",
        "candidate_binding_identity",
        "action",
        "ledger_snapshot_id",
        "asset_epoch_identity",
        "proposal_identity",
        "book_snapshot_id",
        "book_hash",
        "source_identity",
        "model_identity",
        "sample_identity",
    )
    if any(not str(payload.get(field) or "").strip() for field in text_fields):
        return "CURRENT_MAKER_FILL_WITNESS_SHAPE_INVALID"
    action = str(payload["action"]).strip().upper()
    direction_text = str(direction or "").strip().lower()
    if action != "BUY" or (direction_text and not direction_text.startswith("buy_")):
        return "CURRENT_MAKER_FILL_WITNESS_ACTION_INVALID"
    try:
        limit_price = Decimal(str(payload["limit_price"]))
        rest_deadline = float(payload["rest_deadline_minutes"])
        outer_limit = Decimal(str(economics.get("global_limit_price")))
        outer_cost = Decimal(str(economics.get("cost")))
        outer_fill_probability = float(economics.get("global_fill_probability"))
        outer_rest_deadline = float(
            economics.get("global_rest_deadline_minutes")
        )
    except (ArithmeticError, TypeError, ValueError):
        return "CURRENT_MAKER_FILL_WITNESS_NUMERIC_INVALID"
    if (
        not all(value.is_finite() for value in (limit_price, outer_limit, outer_cost))
        or not Decimal("0.05") <= limit_price <= Decimal("0.95")
        or limit_price != outer_limit
        or limit_price != outer_cost
        or not math.isfinite(rest_deadline)
        or rest_deadline <= 0.0
        or not math.isclose(
            rest_deadline,
            outer_rest_deadline,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or not math.isfinite(outer_fill_probability)
    ):
        return "CURRENT_MAKER_FILL_WITNESS_NUMERIC_INVALID"
    timestamps = {
        field: _maker_fill_timestamp(payload.get(field))
        for field in (
            "training_cutoff_at_utc",
            "issued_at_utc",
            "valid_until_at_utc",
            "validated_at_utc",
        )
    }
    selection_decision_at = _maker_fill_timestamp(
        economics.get("global_selection_decision_at")
    )
    if any(value is None for value in timestamps.values()) or selection_decision_at is None:
        return "CURRENT_MAKER_FILL_WITNESS_TEMPORAL_INVALID"
    training_at = timestamps["training_cutoff_at_utc"]
    issued_at = timestamps["issued_at_utc"]
    valid_until = timestamps["valid_until_at_utc"]
    validated_at = timestamps["validated_at_utc"]
    assert training_at is not None
    assert issued_at is not None
    assert valid_until is not None
    assert validated_at is not None
    if not (
        training_at <= issued_at <= selection_decision_at <= validated_at <= valid_until
    ):
        return "CURRENT_MAKER_FILL_WITNESS_TEMPORAL_INVALID"
    for witness_field, economics_field in (
        ("book_snapshot_id", "global_jit_book_snapshot_id"),
        ("book_hash", "global_jit_venue_book_hash"),
    ):
        if str(payload[witness_field]) != str(economics.get(economics_field) or ""):
            return "CURRENT_MAKER_FILL_WITNESS_BOOK_MISMATCH"
    witness_identity = str(payload["witness_identity"])
    if witness_identity != str(
        economics.get("global_fill_probability_source") or ""
    ):
        return "CURRENT_MAKER_FILL_WITNESS_SOURCE_MISMATCH"
    candidate_fields = (
        "global_family_key",
        "global_bin_id",
        "global_condition_id",
        "side",
        "global_token_id",
    )
    if any(not str(economics.get(field) or "").strip() for field in candidate_fields):
        return "CURRENT_MAKER_FILL_WITNESS_BINDING_INVALID"
    expected_binding = _maker_fill_parts_hash(
        "CURRENT_MAKER_FILL_V1",
        action,
        str(economics["global_family_key"]),
        str(economics["global_bin_id"]),
        str(economics["global_condition_id"]),
        str(economics["side"]),
        str(economics["global_token_id"]),
        str(payload["ledger_snapshot_id"]),
        str(payload.get("position_id") or ""),
        str(payload.get("held_shares") or ""),
        str(payload["asset_epoch_identity"]),
        str(payload["proposal_identity"]),
    )
    if str(payload["candidate_binding_identity"]) != expected_binding:
        return "CURRENT_MAKER_FILL_WITNESS_BINDING_INVALID"
    raw_outcomes = payload.get("outcomes")
    if not isinstance(raw_outcomes, list) or not raw_outcomes:
        return "CURRENT_MAKER_FILL_WITNESS_OUTCOMES_INVALID"
    outcomes: list[tuple[Decimal, Decimal, Decimal]] = []
    for raw in raw_outcomes:
        if not isinstance(raw, Mapping) or set(raw) != {
            "probability",
            "fill_fraction",
            "proceeds_per_share_usd",
        }:
            return "CURRENT_MAKER_FILL_WITNESS_OUTCOMES_INVALID"
        try:
            probability = Decimal(str(raw["probability"]))
            fraction = Decimal(str(raw["fill_fraction"]))
            proceeds = Decimal(str(raw["proceeds_per_share_usd"]))
        except (ArithmeticError, TypeError, ValueError):
            return "CURRENT_MAKER_FILL_WITNESS_OUTCOMES_INVALID"
        if (
            not all(value.is_finite() for value in (probability, fraction, proceeds))
            or probability <= 0
            or not Decimal("0") <= fraction <= Decimal("1")
            or (fraction == 0 and proceeds != 0)
            or (fraction > 0 and proceeds != -limit_price)
        ):
            return "CURRENT_MAKER_FILL_WITNESS_OUTCOMES_INVALID"
        outcomes.append((probability, fraction, proceeds))
    probability_total = sum((row[0] for row in outcomes), Decimal("0"))
    fill_probability = sum(
        (row[0] for row in outcomes if row[1] > 0),
        Decimal("0"),
    )
    expected_fill_fraction = sum(
        (row[0] * row[1] for row in outcomes),
        Decimal("0"),
    )
    if (
        probability_total != Decimal("1")
        or expected_fill_fraction <= 0
        or not math.isclose(
            float(fill_probability),
            outer_fill_probability,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        return "CURRENT_MAKER_FILL_WITNESS_OUTCOMES_INVALID"
    identity_rows = tuple(
        sorted("\x1e".join(str(value) for value in row) for row in outcomes)
    )
    expected_witness_identity = _maker_fill_parts_hash(
        "CURRENT_MAKER_FILL_V2",
        str(payload["candidate_binding_identity"]),
        str(payload["asset_epoch_identity"]),
        str(payload["book_snapshot_id"]),
        str(payload["book_hash"]),
        str(limit_price),
        repr(rest_deadline),
        str(payload["source_identity"]),
        str(payload["model_identity"]),
        str(payload["sample_identity"]),
        training_at.isoformat(),
        issued_at.isoformat(),
        valid_until.isoformat(),
        *identity_rows,
    )
    if witness_identity != expected_witness_identity:
        return "CURRENT_MAKER_FILL_WITNESS_IDENTITY_MISMATCH"
    if economics.get("global_probability_functional") != (
        "POSTERIOR_PREDICTIVE_MEAN"
    ):
        return "CURRENT_MAKER_FILL_WITNESS_PROBABILITY_FUNCTIONAL_INVALID"
    mean_reason = _qkernel_global_mean_buy_rejection_reason(
        economics,
        direction=direction,
    )
    if mean_reason is not None:
        return f"CURRENT_MAKER_FILL_WITNESS_MEAN_ECONOMICS:{mean_reason}"
    if economics.get("global_proposal_fill_semantics") != (
        "FILL_WEIGHTED_ZERO_CONTINUATION_LOWER_BOUND"
    ):
        return "CURRENT_MAKER_FILL_WITNESS_FILL_SEMANTICS_INVALID"
    try:
        shares = float(economics["global_target_shares"])
        win_probability = float(economics["global_terminal_win_probability_mean"])
        loss_probability = float(economics["global_terminal_loss_probability_mean"])
        full_loss_payoff = float(economics["global_terminal_loss_payoff_usd"])
        full_win_payoff = float(economics["global_terminal_win_payoff_usd"])
        wealth_after_loss = float(economics["global_terminal_wealth_after_loss_usd"])
        wealth_after_win = float(economics["global_terminal_wealth_after_win_usd"])
        proposal_du = float(economics["global_proposal_expected_delta_log_wealth"])
        proposal_ev = float(economics["global_proposal_expected_ev_usd"])
        proposal_efficiency = float(
            economics["global_proposal_expected_capital_efficiency"]
        )
        proposal_lock = float(economics["global_proposal_capital_lock_hours"])
        proposal_rate = float(
            economics["global_proposal_expected_log_growth_per_hour"]
        )
    except (KeyError, TypeError, ValueError):
        return "CURRENT_MAKER_FILL_WITNESS_EXPECTED_GROWTH_INVALID"
    loss_base = wealth_after_loss - full_loss_payoff
    win_base = wealth_after_win - full_win_payoff
    expected_du = 0.0
    expected_ev = 0.0
    expected_spend = 0.0
    for probability, fraction, proceeds_per_share in outcomes:
        filled = shares * float(fraction)
        proceeds = filled * float(proceeds_per_share)
        loss_after = loss_base + proceeds
        win_after = win_base + filled + proceeds
        if min(loss_after, win_after, loss_base, win_base) <= 0.0:
            return "CURRENT_MAKER_FILL_WITNESS_EXPECTED_GROWTH_INVALID"
        weight = float(probability)
        expected_du += weight * (
            loss_probability * math.log(loss_after / loss_base)
            + win_probability * math.log(win_after / win_base)
        )
        expected_ev += weight * (win_probability * filled + proceeds)
        expected_spend += weight * (-proceeds)
    if not (
        expected_spend > 0.0
        and proposal_lock > 0.0
        and math.isclose(proposal_du, expected_du, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(proposal_ev, expected_ev, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            proposal_efficiency,
            expected_du / expected_spend,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and math.isclose(
            proposal_rate,
            proposal_du / proposal_lock,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        return "CURRENT_MAKER_FILL_WITNESS_EXPECTED_GROWTH_INVALID"
    return None


def _qkernel_global_mean_buy_rejection_reason(
    economics: Mapping[str, Any],
    *,
    direction: str | None,
) -> str | None:
    """Validate one BUY whose action law is posterior-mean expected growth."""

    if any(
        field in economics
        for field in (
            "global_robust_delta_log_wealth",
            "global_robust_ev_usd",
            "global_capital_efficiency",
            "global_cut_time_win_probability_lcb",
            "global_cut_time_loss_probability_ucb",
            "global_terminal_win_probability_lcb",
            "global_terminal_loss_probability_ucb",
        )
    ):
        return "mean_action_carries_robust_economics"
    fields = (
        "payoff_q_point",
        "payoff_q_lcb",
        "payoff_q_action",
        "global_current_sample_payoff_q_mean",
        "cost",
        "edge_lcb",
        "edge_expected",
        "global_target_shares",
        "global_expected_cost_usd",
        "global_max_spend_usd",
        "global_expected_delta_log_wealth",
        "global_expected_ev_usd",
        "global_expected_capital_efficiency",
        "global_cut_time_win_probability_mean",
        "global_cut_time_loss_probability_mean",
        "global_terminal_win_probability_mean",
        "global_terminal_loss_probability_mean",
        "global_terminal_loss_payoff_usd",
        "global_terminal_win_payoff_usd",
        "global_terminal_median_payoff_usd",
        "global_terminal_wealth_after_loss_usd",
        "global_terminal_wealth_after_win_usd",
        "global_cut_time_expected_value_usd",
        "global_expected_value_usd",
    )
    try:
        numeric = {field: float(economics.get(field)) for field in fields}
    except (TypeError, ValueError):
        return "mean_numeric_field_invalid"
    if not all(math.isfinite(value) for value in numeric.values()):
        return "mean_numeric_field_non_finite"
    point = numeric["payoff_q_point"]
    lcb = numeric["payoff_q_lcb"]
    action_q = numeric["payoff_q_action"]
    sample_mean = numeric["global_current_sample_payoff_q_mean"]
    cost = numeric["cost"]
    edge_lcb = numeric["edge_lcb"]
    edge_expected = numeric["edge_expected"]
    shares = numeric["global_target_shares"]
    expected_cost = numeric["global_expected_cost_usd"]
    max_spend = numeric["global_max_spend_usd"]
    expected_du = numeric["global_expected_delta_log_wealth"]
    expected_ev = numeric["global_expected_ev_usd"]
    expected_efficiency = numeric["global_expected_capital_efficiency"]
    cut_win = numeric["global_cut_time_win_probability_mean"]
    cut_loss = numeric["global_cut_time_loss_probability_mean"]
    terminal_win = numeric["global_terminal_win_probability_mean"]
    terminal_loss = numeric["global_terminal_loss_probability_mean"]
    loss_payoff = numeric["global_terminal_loss_payoff_usd"]
    win_payoff = numeric["global_terminal_win_payoff_usd"]
    median_payoff = numeric["global_terminal_median_payoff_usd"]
    wealth_after_loss = numeric["global_terminal_wealth_after_loss_usd"]
    wealth_after_win = numeric["global_terminal_wealth_after_win_usd"]
    cut_ev = numeric["global_cut_time_expected_value_usd"]
    current_ev = numeric["global_expected_value_usd"]
    if not (
        0.0 <= lcb <= min(point, action_q, sample_mean)
        and max(point, action_q, sample_mean) <= 1.0
    ):
        return "probability_order"
    if not (
        math.isclose(action_q, point, rel_tol=0.0, abs_tol=1e-12)
        and 0.0 < cost < 1.0
        and edge_expected > 0.0
        and math.isclose(
            edge_expected,
            action_q - cost,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
        and math.isclose(
            edge_lcb,
            lcb - cost,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    ):
        return "mean_execution_edge"
    if not (
        shares > 0.0
        and expected_cost > 0.0
        and max_spend + 1e-9 >= expected_cost
        and expected_du > 0.0
        and expected_ev > 0.0
        and expected_efficiency > 0.0
    ):
        return "mean_global_utility_envelope"
    if not math.isclose(cost, expected_cost / shares, rel_tol=1e-9, abs_tol=1e-9):
        return "global_cost_identity"
    if not (
        0.0 <= terminal_win <= 1.0
        and 0.0 <= terminal_loss <= 1.0
        and math.isclose(cut_win, action_q, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(cut_loss, 1.0 - action_q, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(terminal_win, action_q, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            terminal_loss,
            1.0 - action_q,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and math.isclose(
            terminal_win + terminal_loss,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        return "mean_terminal_probability_identity"
    loss_base = wealth_after_loss - loss_payoff
    win_base = wealth_after_win - win_payoff
    if not (
        math.isclose(loss_payoff, -expected_cost, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            win_payoff,
            shares - expected_cost,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and (
            (
                terminal_win > 0.5
                and math.isclose(
                    median_payoff,
                    win_payoff,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
            or (
                terminal_win < 0.5
                and math.isclose(
                    median_payoff,
                    loss_payoff,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
            or (
                terminal_win == 0.5
                and loss_payoff - 1e-12 <= median_payoff <= win_payoff + 1e-12
            )
        )
        and min(loss_base, win_base, wealth_after_loss, wealth_after_win) > 0.0
    ):
        return "mean_terminal_payoff_identity"
    recomputed_du = terminal_loss * math.log(
        wealth_after_loss / loss_base
    ) + terminal_win * math.log(wealth_after_win / win_base)
    recomputed_ev = terminal_win * shares - expected_cost
    if not (
        math.isclose(expected_du, recomputed_du, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(expected_ev, recomputed_ev, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(cut_ev, recomputed_ev, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(current_ev, recomputed_ev, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            expected_efficiency,
            expected_du / expected_cost,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        return "mean_objective_identity"
    if economics.get("selection_guard_basis") != (
        "CURRENT_POSTERIOR_PREDICTIVE_MEAN"
    ):
        return "selection_guard_basis"
    if economics.get("global_expected_value_semantics") != (
        "POINT_EVIDENCE_EXPECTATION_NOT_REALIZED_GAIN"
    ):
        return "global_expected_value_semantics"
    if economics.get("global_terminal_payoff_semantics") != "BINARY_0_1":
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
    mean_basis = (
        economics.get("global_buy_fak_probability_basis")
        == "POSTERIOR_PREDICTIVE_MEAN"
    )
    probability_fields = (
        (
            "global_terminal_win_probability_mean",
            "global_terminal_loss_probability_mean",
        )
        if mean_basis
        else (
            "global_terminal_win_probability_lcb",
            "global_terminal_loss_probability_ucb",
        )
    )
    objective_fields = (
        (
            "global_buy_fak_full_expected_delta_log_wealth",
            "global_buy_fak_full_expected_ev_usd",
        )
        if mean_basis
        else (
            "global_buy_fak_full_robust_delta_log_wealth",
            "global_buy_fak_full_robust_ev_usd",
        )
    )
    fields = (
        "global_target_shares",
        "global_limit_price",
        *probability_fields,
        "global_terminal_loss_payoff_usd",
        "global_terminal_win_payoff_usd",
        "global_terminal_wealth_after_loss_usd",
        "global_terminal_wealth_after_win_usd",
        "global_buy_fak_fee_rate",
        "global_buy_fak_worst_fee_shape",
        "global_buy_fak_worst_fee_per_share",
        "global_buy_fak_worst_unit_cost",
        "global_buy_fak_full_worst_cost_usd",
        *objective_fields,
    )
    try:
        values = {field: float(economics.get(field)) for field in fields}
    except (TypeError, ValueError):
        return "numeric_field_invalid"
    if not all(math.isfinite(value) for value in values.values()):
        return "numeric_field_non_finite"
    shares = values["global_target_shares"]
    limit = values["global_limit_price"]
    win_q = values[probability_fields[0]]
    loss_q = values[probability_fields[1]]
    fee_rate = values["global_buy_fak_fee_rate"]
    if not (
        shares > 0
        and 0 < limit < 1
        and 0 <= fee_rate <= 0.5
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
    max_fee_shape = limit * (1.0 - limit)
    worst_fee_per_share = 2.0 * fee_rate * max_fee_shape
    unit_cost = limit + worst_fee_per_share
    full_cost = unit_cost * shares
    loss_after = loss_baseline - full_cost
    win_after = win_baseline - full_cost + shares
    if min(loss_baseline, win_baseline, loss_after, win_after) <= 0:
        return "wealth"
    delta_log_wealth = loss_q * math.log(
        loss_after / loss_baseline
    ) + win_q * math.log(
        win_after / win_baseline
    )
    ev = win_q * shares - full_cost
    expected = {
        "global_buy_fak_worst_fee_shape": max_fee_shape,
        "global_buy_fak_worst_fee_per_share": worst_fee_per_share,
        "global_buy_fak_worst_unit_cost": unit_cost,
        "global_buy_fak_full_worst_cost_usd": full_cost,
        objective_fields[0]: delta_log_wealth,
        objective_fields[1]: ev,
    }
    for field, expected_value in expected.items():
        if not math.isclose(
            values[field], expected_value, rel_tol=1e-12, abs_tol=1e-12
        ):
            return field
    if delta_log_wealth <= 0 or ev <= 0:
        return "non_positive"
    return None
