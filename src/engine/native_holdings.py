"""Bind reconciled native inventory to the current family topology."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping


NativeHoldingSide = Literal["YES", "NO"]


@dataclass(frozen=True)
class NativeHolding:
    position_id: str
    family_key: str
    bin_id: str
    side: NativeHoldingSide
    token_id: str
    shares: Decimal

    def __post_init__(self) -> None:
        for name in ("position_id", "family_key", "bin_id", "token_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"NativeHolding.{name} must be non-empty")
        if self.side not in ("YES", "NO"):
            raise ValueError(f"NativeHolding.side must be YES or NO, got {self.side!r}")
        if not self.shares.is_finite() or self.shares <= 0:
            raise ValueError(f"NativeHolding.shares must be finite and positive, got {self.shares}")


@dataclass(frozen=True)
class NativePendingEndowment:
    obligation_id: str
    family_key: str
    bin_id: str
    side: NativeHoldingSide
    token_id: str
    shares: Decimal

    def __post_init__(self) -> None:
        for name in ("obligation_id", "family_key", "bin_id", "token_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"NativePendingEndowment.{name} must be non-empty")
        if self.side not in ("YES", "NO"):
            raise ValueError(f"NativePendingEndowment.side must be YES or NO, got {self.side!r}")
        if not self.shares.is_finite() or self.shares <= 0:
            raise ValueError(
                "NativePendingEndowment.shares must be finite and positive, "
                f"got {self.shares}"
            )


@dataclass(frozen=True)
class NativeHoldingsSnapshot:
    family_key: str
    ledger_snapshot_id: str
    holdings: tuple[NativeHolding, ...] = ()
    pending_endowments: tuple[NativePendingEndowment, ...] = ()

    @property
    def endowment_claims(self) -> tuple[NativeHolding | NativePendingEndowment, ...]:
        return (*self.holdings, *self.pending_endowments)

    def __post_init__(self) -> None:
        if not self.family_key.strip() or not self.ledger_snapshot_id.strip():
            raise ValueError("NativeHoldingsSnapshot requires family and ledger identities")
        position_ids: set[str] = set()
        for holding in self.holdings:
            if holding.family_key != self.family_key:
                raise ValueError(
                    f"NativeHoldingsSnapshot family mismatch: {holding.position_id} belongs "
                    f"to {holding.family_key}, not {self.family_key}"
                )
            if holding.position_id in position_ids:
                raise ValueError(f"duplicate NativeHolding position_id: {holding.position_id}")
            position_ids.add(holding.position_id)
        obligation_ids: set[str] = set()
        for endowment in self.pending_endowments:
            if endowment.family_key != self.family_key:
                raise ValueError(
                    f"NativeHoldingsSnapshot family mismatch: {endowment.obligation_id} belongs "
                    f"to {endowment.family_key}, not {self.family_key}"
                )
            if endowment.obligation_id in obligation_ids:
                raise ValueError(
                    f"duplicate NativePendingEndowment obligation_id: {endowment.obligation_id}"
                )
            obligation_ids.add(endowment.obligation_id)


def native_holdings_snapshot_from_positions(
    *,
    family_key: str,
    omega,
    positions,
    ledger_snapshot_id: str,
    token_shares_by_id: Mapping[str, Decimal] | None = None,
    pending_entry_endowments: tuple[tuple[str, str, Decimal], ...] = (),
) -> NativeHoldingsSnapshot:
    """Bind canonical open positions to exact current YES/NO token identities."""

    bindings = {str(outcome.condition_id or ""): outcome for outcome in omega.bins}
    token_bindings: dict[str, tuple[object, NativeHoldingSide]] = {}
    for outcome in omega.bins:
        for token_id, side in (
            (str(outcome.yes_token_id or ""), "YES"),
            (str(outcome.no_token_id or ""), "NO"),
        ):
            if not token_id or token_id in token_bindings:
                raise ValueError("current omega has missing or duplicate native token identity")
            token_bindings[token_id] = (outcome, side)
    if not family_key.strip() or not ledger_snapshot_id.strip() or not bindings:
        raise ValueError("native holdings snapshot requires family, ledger, and omega identities")

    holdings: list[NativeHolding] = []
    for position in tuple(positions or ()):
        condition_id = str(getattr(position, "condition_id", "") or "")
        outcome = bindings.get(condition_id)
        if outcome is None:
            continue
        direction_raw = getattr(position, "direction", "")
        direction = str(getattr(direction_raw, "value", direction_raw) or "").lower()
        if direction == "buy_yes":
            side: NativeHoldingSide = "YES"
            token_id = str(getattr(position, "token_id", "") or "")
            expected_token = str(getattr(outcome, "yes_token_id", "") or "")
        elif direction == "buy_no":
            side = "NO"
            token_id = str(getattr(position, "no_token_id", "") or "")
            expected_token = str(getattr(outcome, "no_token_id", "") or "")
        else:
            raise ValueError(
                f"position {getattr(position, 'trade_id', '')!r} has unsupported direction "
                f"{direction!r}"
            )
        uses_ledger_balance = token_shares_by_id is not None
        shares = Decimal(
            token_shares_by_id.get(token_id, Decimal("0"))
            if uses_ledger_balance
            else str(getattr(position, "chain_shares", 0) or 0)
        )
        if not shares.is_finite() or shares < 0:
            source = "ledger token balance" if uses_ledger_balance else "chain_shares"
            raise ValueError(
                f"position {getattr(position, 'trade_id', '')!r} has invalid {source} {shares}"
            )
        if shares == 0:
            continue
        if not token_id or token_id != expected_token:
            raise ValueError(
                f"position {getattr(position, 'trade_id', '')!r} token does not match "
                f"current omega for {condition_id}"
            )
        holdings.append(
            NativeHolding(
                position_id=str(
                    getattr(position, "position_id", "")
                    or getattr(position, "trade_id", "")
                    or ""
                ),
                family_key=family_key,
                bin_id=str(outcome.bin_id),
                side=side,
                token_id=token_id,
                shares=shares,
            )
        )

    pending: list[NativePendingEndowment] = []
    for obligation_id, token_id, shares in pending_entry_endowments:
        binding = token_bindings.get(str(token_id))
        if binding is None:
            continue
        outcome, side = binding
        pending.append(
            NativePendingEndowment(
                obligation_id=str(obligation_id),
                family_key=family_key,
                bin_id=str(getattr(outcome, "bin_id")),
                side=side,
                token_id=str(token_id),
                shares=Decimal(shares),
            )
        )
    return NativeHoldingsSnapshot(
        family_key=family_key,
        ledger_snapshot_id=ledger_snapshot_id,
        holdings=tuple(holdings),
        pending_endowments=tuple(pending),
    )
