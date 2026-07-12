"""Stable JSON canonicalization and hashing for decision certificates."""

from __future__ import annotations

import dataclasses
import hashlib
import json
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


def qkernel_current_state_identity_hash(economics: Mapping[str, Any]) -> str:
    """Recomputable identity for the current-posterior execution certificate."""

    return stable_hash(
        {field: economics.get(field) for field in _QKERNEL_CURRENT_STATE_IDENTITY_FIELDS}
    )


def qkernel_declares_current_state(economics: Mapping[str, Any]) -> bool:
    """Whether a payload has entered the non-downgradable current-state grammar."""

    basis = "CURRENT_POSTERIOR_BAND"
    return (
        str(economics.get("q_lcb_guard_basis") or "").strip() == basis
        or str(economics.get("selection_guard_basis") or "").strip() == basis
        or bool(str(economics.get("current_state_identity_hash") or "").strip())
    )
