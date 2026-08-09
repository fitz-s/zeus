# Created: 2026-08-09
# Authority basis: AGENTS.md §2 strategy allocation and the active finite-evidence
# probability-symmetry packet's 2026-08-09 capital-allocation decision.
"""Immutable strategy-capital allocation for one current auction cut.

Venue cash answers whether a BUY can be paid.  Strategy allocation answers how
much Zeus is authorized to own.  Existing commitments consume that allocation,
but they never suppress HOLD or a reducing SELL.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Mapping


CapitalAllocationMode = Literal["wallet_total", "fraction", "absolute"]

STRATEGY_CAPITAL_ALLOCATION_VERSION = "strategy_capital_allocation_v2"
STRATEGY_CAPITAL_BASIS_SEMANTICS = (
    "canonical_available_cash_plus_cost_commitments_v1"
)
STRATEGY_CAPITAL_ALLOCATION_SOURCE = "active_config:zeus_capital_allocation"
STRATEGY_LOG_UTILITY_BASIS = (
    "ZEUS_OWNED_STRATEGY_EQUITY_LEXICOGRAPHIC_LOG_V1"
)
_ALLOCATION_MODES = frozenset({"wallet_total", "fraction", "absolute"})


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _allocation_terms(
    allocation: Mapping[str, object] | None,
) -> tuple[CapitalAllocationMode, Decimal | None, Decimal | None]:
    if allocation is None:
        allocation = {"mode": "wallet_total"}
    if not isinstance(allocation, Mapping):
        raise ValueError("zeus_capital_allocation must be a mapping")
    if "mode" not in allocation:
        raise ValueError("zeus_capital_allocation.mode is required")
    mode = str(allocation["mode"] or "").strip()
    if not mode:
        raise ValueError("zeus_capital_allocation.mode must be non-empty")
    if mode not in _ALLOCATION_MODES:
        raise ValueError(
            "zeus_capital_allocation.mode must be one of "
            f"{sorted(_ALLOCATION_MODES)}; got {mode!r}"
        )
    allowed_keys = {"mode", "buy_commitment_limit_usd"}
    if mode != "wallet_total":
        allowed_keys.add("value")
    unknown_keys = sorted(
        str(key)
        for key in allocation
        if key not in allowed_keys and not str(key).startswith("_")
    )
    if unknown_keys:
        raise ValueError(
            "zeus_capital_allocation has unsupported fields: "
            f"{unknown_keys}"
        )
    if mode == "wallet_total" and "value" in allocation:
        raise ValueError(
            "zeus_capital_allocation mode='wallet_total' must not define value"
        )
    configured_limit: Decimal | None = None
    if "buy_commitment_limit_usd" in allocation:
        raw_limit = allocation["buy_commitment_limit_usd"]
        if raw_limit is None:
            raise ValueError(
                "zeus_capital_allocation buy_commitment_limit_usd must be finite and >= 0"
            )
        configured_limit = _decimal(
            raw_limit,
            field="zeus_capital_allocation buy_commitment_limit_usd",
        )
        if configured_limit < 0:
            raise ValueError(
                "zeus_capital_allocation buy_commitment_limit_usd must be >= 0"
            )
    if mode == "wallet_total":
        return "wallet_total", None, configured_limit

    raw_value = allocation.get("value")
    if raw_value is None:
        raise ValueError(
            f"zeus_capital_allocation mode={mode!r} requires a numeric 'value'"
        )
    value = _decimal(raw_value, field=f"zeus_capital_allocation {mode} value")
    if mode == "fraction":
        if not Decimal("0") <= value <= Decimal("1"):
            raise ValueError(
                "zeus_capital_allocation fraction value must be in [0, 1]; "
                f"got {value!r}"
            )
        return "fraction", value, configured_limit
    if value < 0:
        raise ValueError(
            "zeus_capital_allocation absolute value must be >= 0; "
            f"got {value!r}"
        )
    return "absolute", value, configured_limit


def resolve_allocated_equity_usd(
    capital_basis_usd: object,
    *,
    allocation: Mapping[str, object] | None = None,
) -> Decimal:
    """Apply the active allocation rule to an observed non-negative basis."""

    basis = _decimal(capital_basis_usd, field="capital_basis_usd")
    if basis < 0:
        raise ValueError("capital_basis_usd must be >= 0")
    mode, value, _ = _allocation_terms(allocation)
    if mode == "wallet_total":
        return basis
    assert value is not None
    if mode == "fraction":
        return basis * value
    return min(basis, value)


def strategy_capital_allocation_identity(
    *,
    mode: CapitalAllocationMode,
    configured_value: Decimal | None,
    capital_basis_usd: Decimal,
    allocated_equity_usd: Decimal,
    committed_capital_usd: Decimal,
    venue_spendable_cash_usd: Decimal,
    remaining_buy_capacity_usd: Decimal,
    buy_commitment_limit_usd: Decimal | None = None,
    utility_liquid_cash_usd: Decimal | None = None,
    configured_buy_commitment_limit_usd: Decimal | None = None,
) -> str:
    """Hash the complete allocation policy and current capital inputs."""

    effective_limit = (
        allocated_equity_usd
        if buy_commitment_limit_usd is None
        else buy_commitment_limit_usd
    )
    utility_cash = (
        max(Decimal("0"), allocated_equity_usd - committed_capital_usd)
        if utility_liquid_cash_usd is None
        else utility_liquid_cash_usd
    )
    fields = (
        STRATEGY_CAPITAL_ALLOCATION_VERSION,
        STRATEGY_CAPITAL_BASIS_SEMANTICS,
        STRATEGY_CAPITAL_ALLOCATION_SOURCE,
        mode,
        "" if configured_value is None else _decimal_text(configured_value),
        _decimal_text(capital_basis_usd),
        _decimal_text(allocated_equity_usd),
        _decimal_text(effective_limit),
        _decimal_text(committed_capital_usd),
        _decimal_text(utility_cash),
        _decimal_text(venue_spendable_cash_usd),
        _decimal_text(remaining_buy_capacity_usd),
        ""
        if configured_buy_commitment_limit_usd is None
        else _decimal_text(configured_buy_commitment_limit_usd),
    )
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StrategyCapitalAllocationWitness:
    """Exact active-config allocation and remaining BUY capacity at one cut."""

    mode: CapitalAllocationMode
    configured_value: Decimal | None
    capital_basis_usd: Decimal
    allocated_equity_usd: Decimal
    committed_capital_usd: Decimal
    venue_spendable_cash_usd: Decimal
    buy_commitment_limit_usd: Decimal
    utility_liquid_cash_usd: Decimal
    remaining_buy_capacity_usd: Decimal
    configured_buy_commitment_limit_usd: Decimal | None
    witness_identity: str
    allocation_version: str = STRATEGY_CAPITAL_ALLOCATION_VERSION
    capital_basis_semantics: str = STRATEGY_CAPITAL_BASIS_SEMANTICS
    source: str = STRATEGY_CAPITAL_ALLOCATION_SOURCE

    @classmethod
    def build(
        cls,
        *,
        capital_basis_usd: object,
        committed_capital_usd: object,
        venue_spendable_cash_usd: object,
        allocation: Mapping[str, object] | None = None,
    ) -> "StrategyCapitalAllocationWitness":
        basis = _decimal(capital_basis_usd, field="capital_basis_usd")
        committed = _decimal(
            committed_capital_usd,
            field="committed_capital_usd",
        )
        venue_cash = _decimal(
            venue_spendable_cash_usd,
            field="venue_spendable_cash_usd",
        )
        if basis < 0 or committed < 0 or venue_cash < 0:
            raise ValueError("strategy capital inputs must be >= 0")
        if committed + venue_cash > basis:
            raise ValueError(
                "strategy capital inputs exceed the observed capital basis"
            )

        mode, configured_value, configured_limit = _allocation_terms(allocation)
        allocated = resolve_allocated_equity_usd(
            basis,
            allocation={
                "mode": mode,
                **(
                    {"value": configured_value}
                    if configured_value is not None
                    else {}
                ),
            },
        )
        effective_limit = (
            allocated
            if configured_limit is None
            else min(configured_limit, allocated)
        )
        utility_cash = max(Decimal("0"), allocated - committed)
        remaining = min(venue_cash, max(Decimal("0"), effective_limit - committed))
        identity = strategy_capital_allocation_identity(
            mode=mode,
            configured_value=configured_value,
            capital_basis_usd=basis,
            allocated_equity_usd=allocated,
            committed_capital_usd=committed,
            venue_spendable_cash_usd=venue_cash,
            remaining_buy_capacity_usd=remaining,
            buy_commitment_limit_usd=effective_limit,
            utility_liquid_cash_usd=utility_cash,
            configured_buy_commitment_limit_usd=configured_limit,
        )
        return cls(
            mode=mode,
            configured_value=configured_value,
            capital_basis_usd=basis,
            allocated_equity_usd=allocated,
            committed_capital_usd=committed,
            venue_spendable_cash_usd=venue_cash,
            buy_commitment_limit_usd=effective_limit,
            utility_liquid_cash_usd=utility_cash,
            remaining_buy_capacity_usd=remaining,
            configured_buy_commitment_limit_usd=configured_limit,
            witness_identity=identity,
        )

    def __post_init__(self) -> None:
        values = (
            self.capital_basis_usd,
            self.allocated_equity_usd,
            self.committed_capital_usd,
            self.venue_spendable_cash_usd,
            self.buy_commitment_limit_usd,
            self.utility_liquid_cash_usd,
            self.remaining_buy_capacity_usd,
        )
        if (
            self.allocation_version != STRATEGY_CAPITAL_ALLOCATION_VERSION
            or self.capital_basis_semantics
            != STRATEGY_CAPITAL_BASIS_SEMANTICS
            or self.source != STRATEGY_CAPITAL_ALLOCATION_SOURCE
            or self.mode not in _ALLOCATION_MODES
            or any(
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < 0
                for value in values
            )
            or (
                self.configured_value is not None
                and (
                    not isinstance(self.configured_value, Decimal)
                    or not self.configured_value.is_finite()
                )
            )
            or (
                self.configured_buy_commitment_limit_usd is not None
                and (
                    not isinstance(self.configured_buy_commitment_limit_usd, Decimal)
                    or not self.configured_buy_commitment_limit_usd.is_finite()
                    or self.configured_buy_commitment_limit_usd < 0
                )
            )
            or (
                self.mode == "wallet_total"
                and self.configured_value is not None
            )
            or (
                self.mode != "wallet_total"
                and self.configured_value is None
            )
            or self.committed_capital_usd + self.venue_spendable_cash_usd
            > self.capital_basis_usd
        ):
            raise ValueError("strategy capital allocation witness is invalid")

        allocation = {
            "mode": self.mode,
            **(
                {"value": self.configured_value}
                if self.configured_value is not None
                else {}
            ),
        }
        expected_allocated = resolve_allocated_equity_usd(
            self.capital_basis_usd,
            allocation=allocation,
        )
        expected_limit = (
            expected_allocated
            if self.configured_buy_commitment_limit_usd is None
            else min(
                self.configured_buy_commitment_limit_usd,
                expected_allocated,
            )
        )
        expected_utility_cash = max(
            Decimal("0"),
            expected_allocated - self.committed_capital_usd,
        )
        expected_remaining = min(
            self.venue_spendable_cash_usd,
            max(
                Decimal("0"),
                expected_limit - self.committed_capital_usd,
            ),
        )
        expected_identity = strategy_capital_allocation_identity(
            mode=self.mode,
            configured_value=self.configured_value,
            capital_basis_usd=self.capital_basis_usd,
            allocated_equity_usd=expected_allocated,
            committed_capital_usd=self.committed_capital_usd,
            venue_spendable_cash_usd=self.venue_spendable_cash_usd,
            remaining_buy_capacity_usd=expected_remaining,
            buy_commitment_limit_usd=expected_limit,
            utility_liquid_cash_usd=expected_utility_cash,
            configured_buy_commitment_limit_usd=(
                self.configured_buy_commitment_limit_usd
            ),
        )
        if (
            self.allocated_equity_usd != expected_allocated
            or self.buy_commitment_limit_usd != expected_limit
            or self.utility_liquid_cash_usd != expected_utility_cash
            or self.remaining_buy_capacity_usd != expected_remaining
            or self.witness_identity != expected_identity
        ):
            raise ValueError("strategy capital allocation witness is inconsistent")
