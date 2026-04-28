# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml
"""Fake Polymarket V2 venue used by T1 paper/live parity tests.

This fake is intentionally test-scoped. It implements the public adapter
surface consumed by Zeus while avoiding credentials, network, production DB
mutation, and live venue side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from typing import Any

from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
from src.venue.polymarket_v2_adapter import (
    CancelResult,
    ClobMarketInfo,
    HeartbeatAck,
    OpenOrdersFilter,
    OrderState,
    PolymarketV2Adapter,
    PositionFact,
    PreflightResult,
    SubmitResult,
    TradeFact,
    _submit_result_from_response,
)


class FailureMode(str, Enum):
    TIMEOUT_AFTER_POST = "timeout_after_post"
    PARTIAL_RESPONSE = "partial_response"
    NETWORK_JITTER = "network_jitter"
    ORACLE_CONFLICT = "oracle_conflict"
    RESTART_MID_CYCLE = "restart_mid_cycle"
    HEARTBEAT_MISS = "heartbeat_miss"
    OPEN_ORDER_WIPE = "open_order_wipe"
    CANCEL_NOT_CANCELED = "cancel_not_canceled"
    ORDER_TICK_REJECTION = "order_tick_rejection"
    ORDER_MIN_SIZE_REJECTION = "order_min_size_rejection"
    INSUFFICIENT_PUSD = "insufficient_pusd"
    INSUFFICIENT_TOKEN_BALANCE = "insufficient_token_balance"


@dataclass
class FakeClock:
    now: datetime = field(default_factory=lambda: datetime(2026, 4, 27, tzinfo=timezone.utc))

    def advance(self, seconds: int) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now

    def isoformat(self) -> str:
        return self.now.isoformat()


@dataclass
class FakeCollateralLedger:
    pusd_balance_micro: int = 1_000_000_000
    pusd_allowance_micro: int = 1_000_000_000
    ctf_token_balances_units: dict[str, int] = field(default_factory=dict)
    ctf_token_allowances_units: dict[str, int] = field(default_factory=dict)

    def token_balance(self, token_id: str) -> int:
        return int(self.ctf_token_balances_units.get(token_id, 0))

    def debit_buy(self, cost_micro: int) -> None:
        self.pusd_balance_micro -= cost_micro

    def debit_sell(self, token_id: str, units: int) -> None:
        self.ctf_token_balances_units[token_id] = self.token_balance(token_id) - units


class FakePolymarketVenue:
    """Deterministic fake implementing the PolymarketV2Adapter protocol.

    The fake mirrors live adapter result dataclasses and envelope shapes. Failure
    modes are explicit; an uninjected fake accepts valid limit orders and keeps
    all state in memory.
    """

    def __init__(
        self,
        *,
        ledger: FakeCollateralLedger | None = None,
        clock: FakeClock | None = None,
        host: str = "https://clob-v2.polymarket.com",
        funder_address: str = "0xfake-funder",
        chain_id: int = 137,
        sdk_version: str = "fake-polymarket-v2",
    ) -> None:
        self.ledger = ledger or FakeCollateralLedger()
        self.clock = clock or FakeClock()
        self.host = host.rstrip("/")
        self.funder_address = funder_address
        self.signer_key = "fake-signer"
        self.api_creds = None
        self.chain_id = chain_id
        self.builder_code = None
        self.sdk_version = sdk_version
        self._orders: dict[str, dict[str, Any]] = {}
        self._orders_by_request_hash: dict[str, str] = {}
        self._trades: list[dict[str, Any]] = []
        self._positions: dict[str, dict[str, Any]] = {}
        self._receipts: dict[str, dict[str, Any]] = {}
        self._injections: dict[FailureMode, dict[str, Any]] = {}
        self._cancel_not_canceled: set[str] = set()
        self._seq = 0
        self._restart_generation = 0
        self._restart_events: list[dict[str, Any]] = []
        self._envelope_adapter = PolymarketV2Adapter(
            host=self.host,
            funder_address=self.funder_address,
            signer_key="fake-signer",
            chain_id=self.chain_id,
            q1_egress_evidence_path=None,
            client_factory=lambda **_kwargs: _FakeEnvelopeClient(),
            sdk_version=self.sdk_version,
        )

    def inject(self, mode: FailureMode | str, **params: Any) -> None:
        self._injections[FailureMode(mode)] = dict(params)

    def clear_injection(self, mode: FailureMode | str) -> None:
        self._injections.pop(FailureMode(mode), None)

    def heartbeat_miss_window(self, seconds: int) -> None:
        self.inject(FailureMode.HEARTBEAT_MISS, seconds=int(seconds))

    def open_order_wipe(self) -> None:
        self.inject(FailureMode.OPEN_ORDER_WIPE)

    def cancel_not_canceled(self, order_id: str) -> None:
        self._cancel_not_canceled.add(order_id)
        self.inject(FailureMode.CANCEL_NOT_CANCELED, order_id=order_id)

    def force_partial_fill(self, order_id: str, partial_size: int | float | Decimal) -> None:
        order = self._require_order(order_id)
        matched = Decimal(str(partial_size))
        order["status"] = "PARTIALLY_MATCHED"
        order["matched_size"] = str(matched)
        order["remaining_size"] = str(max(Decimal("0"), Decimal(str(order["size"])) - matched))
        self._trades.append(
            {
                "trade_id": f"fake-trade-{len(self._trades) + 1}",
                "orderID": order_id,
                "state": "MATCHED",
                "size": str(matched),
                "asset": order["token_id"],
                "side": order["side"],
                "observed_at": self.clock.isoformat(),
            }
        )
        self._positions[order["token_id"]] = {
            "asset": order["token_id"],
            "token_id": order["token_id"],
            "size": str(matched),
            "state": "OPTIMISTIC_EXPOSURE",
            "orderID": order_id,
        }

    def MATCHED_then_FAILED_chain(self, order_id: str) -> None:  # noqa: N802 - mirrors phase-card wording.
        order = self._require_order(order_id)
        matched_size = Decimal(str(order.get("matched_size") or order["size"]))
        self._trades.append(
            {
                "trade_id": f"fake-trade-{len(self._trades) + 1}",
                "orderID": order_id,
                "state": "FAILED",
                "size": str(matched_size),
                "asset": order["token_id"],
                "side": order["side"],
                "observed_at": self.clock.isoformat(),
            }
        )
        self._positions.pop(order["token_id"], None)
        order["status"] = "FAILED"

    def preflight(self) -> PreflightResult:
        if FailureMode.NETWORK_JITTER in self._injections:
            return PreflightResult(ok=False, error_code="NETWORK_JITTER", message="fake network jitter")
        return PreflightResult(ok=True)

    def get_clob_market_info(self, condition_id: str) -> ClobMarketInfo:
        raw = {"condition_id": condition_id, "status": "ok", "source": "FAKE_VENUE"}
        if FailureMode.ORACLE_CONFLICT in self._injections:
            raw["status"] = "oracle_conflict"
        return ClobMarketInfo(condition_id=condition_id, raw=raw)

    def create_submission_envelope(
        self,
        intent: Any,
        snapshot: Any,
        order_type: str,
        post_only: bool = False,
    ) -> VenueSubmissionEnvelope:
        return self._envelope_adapter.create_submission_envelope(intent, snapshot, order_type, post_only)

    def submit(self, envelope: VenueSubmissionEnvelope) -> SubmitResult:
        preflight = self.preflight()
        if not preflight.ok:
            rejected = envelope.with_updates(error_code=preflight.error_code, error_message=preflight.message)
            return SubmitResult(status="rejected", envelope=rejected, error_code=rejected.error_code, error_message=rejected.error_message)

        validation_error = self._validate_envelope_for_submit(envelope)
        if validation_error is not None:
            code, message = validation_error
            return _submit_result_from_response(
                envelope,
                {"success": False, "errorCode": code, "errorMessage": message},
                signed_order=None,
                signed_order_hash=None,
            )

        if envelope.raw_request_hash in self._orders_by_request_hash:
            order_id = self._orders_by_request_hash[envelope.raw_request_hash]
            raw = self._orders[order_id]
            return _submit_result_from_response(envelope, raw, signed_order=None, signed_order_hash=None)

        order_id = self._next_order_id()
        raw = {
            "success": True,
            "orderID": order_id,
            "status": "LIVE",
            "token_id": envelope.selected_outcome_token_id,
            "side": envelope.side,
            "price": str(envelope.price),
            "size": str(envelope.size),
            "remaining_size": str(envelope.size),
            "raw_request_hash": envelope.raw_request_hash,
        }
        self._orders[order_id] = raw
        self._orders_by_request_hash[envelope.raw_request_hash] = order_id
        self._apply_collateral_debit(envelope)

        if FailureMode.TIMEOUT_AFTER_POST in self._injections:
            raise TimeoutError("fake timeout after post")
        response_raw = {"success": True, "orderID": order_id, "status": "LIVE"}
        if FailureMode.PARTIAL_RESPONSE in self._injections:
            response_raw = {"success": True, "orderID": order_id}
        return _submit_result_from_response(envelope, response_raw, signed_order=None, signed_order_hash=None)

    def cancel(self, order_id: str) -> CancelResult:
        order = self._require_order(order_id)
        if order_id in self._cancel_not_canceled or FailureMode.CANCEL_NOT_CANCELED in self._injections:
            order["status"] = "LIVE"
            return CancelResult(status="accepted", order_id=order_id, raw_response_json='{"status":"CANCEL_NOT_CANCELED"}')
        order["status"] = "CANCEL_CONFIRMED"
        return CancelResult(status="accepted", order_id=order_id, raw_response_json='{"status":"CANCEL_CONFIRMED"}')

    def get_order(self, order_id: str) -> OrderState:
        self._maybe_record_restart("get_order")
        order = self._require_order(order_id)
        return OrderState(order_id=order_id, status=str(order.get("status") or "UNKNOWN"), raw=dict(order))

    def get_open_orders(self, filter: OpenOrdersFilter | None = None) -> list[OrderState]:
        self._maybe_record_restart("get_open_orders")
        if FailureMode.OPEN_ORDER_WIPE in self._injections:
            return []
        states = []
        for order_id, raw in self._orders.items():
            if raw.get("status") not in {"LIVE", "PARTIALLY_MATCHED"}:
                continue
            if filter is not None:
                if filter.asset_id and str(raw.get("token_id")) != str(filter.asset_id):
                    continue
                if filter.market and str(raw.get("condition_id", filter.market)) != str(filter.market):
                    continue
            states.append(OrderState(order_id=order_id, status=str(raw["status"]), raw=dict(raw)))
        return states

    def get_trades(self, since: str | None = None) -> list[TradeFact]:
        self._maybe_record_restart("get_trades")
        return [TradeFact(raw=dict(item)) for item in self._trades]

    def get_positions(self) -> list[PositionFact]:
        self._maybe_record_restart("get_positions")
        return [PositionFact(raw=dict(item)) for item in self._positions.values()]

    def get_collateral_payload(self) -> dict[str, Any]:
        return {
            "pusd_balance_micro": self.ledger.pusd_balance_micro,
            "pusd_allowance_micro": self.ledger.pusd_allowance_micro,
            "usdc_e_legacy_balance_micro": 0,
            "ctf_token_balances_units": dict(self.ledger.ctf_token_balances_units),
            "ctf_token_allowances_units": dict(self.ledger.ctf_token_allowances_units),
            "authority_tier": "VENUE",
        }

    def get_balance(self, conn=None) -> Any:
        if conn is None:
            return SimpleNamespace(pusd_balance_micro=self.ledger.pusd_balance_micro)
        from src.state.collateral_ledger import CollateralLedger

        return CollateralLedger(conn).refresh(self)

    def redeem(self, condition_id: str) -> dict[str, Any]:
        return {
            "success": False,
            "errorCode": "REDEEM_DEFERRED_TO_R1",
            "errorMessage": "Fake venue preserves R1 redeem command-ledger boundary",
            "condition_id": condition_id,
        }

    def post_heartbeat(self, heartbeat_id: str) -> HeartbeatAck:
        self._maybe_record_restart("post_heartbeat")
        if FailureMode.HEARTBEAT_MISS in self._injections:
            return HeartbeatAck(ok=False, raw={"heartbeat_id": heartbeat_id, "status": "missed", **self._injections[FailureMode.HEARTBEAT_MISS]})
        return HeartbeatAck(ok=True, raw={"heartbeat_id": heartbeat_id, "status": "ok", "observed_at": self.clock.isoformat()})

    def submit_limit_order(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> SubmitResult:
        envelope = self._envelope_adapter._create_compat_submission_envelope(
            token_id=token_id,
            price=Decimal(str(price)),
            size=Decimal(str(size)),
            side=side,
            order_type=order_type,
            sdk_snapshot=SimpleNamespace(
                tick_size=Decimal("0.01"),
                min_order_size=Decimal("0.01"),
                neg_risk=False,
                fee_details={"bps": 0, "builder_fee_bps": 0, "source": "fake-polymarket-v2"},
            ),
        )
        return self.submit(envelope)

    def chain_receipt(self, tx_hash: str, *, status: int = 1, confirmations: int = 1) -> dict[str, Any]:
        receipt = {"transactionHash": tx_hash, "status": status, "confirmations": confirmations, "blockNumber": confirmations}
        self._receipts[tx_hash] = receipt
        return receipt

    def restart_events(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self._restart_events]

    def _validate_envelope_for_submit(self, envelope: VenueSubmissionEnvelope) -> tuple[str, str] | None:
        if FailureMode.ORDER_TICK_REJECTION in self._injections or envelope.price % Decimal("0.01") != 0:
            return "ORDER_TICK_REJECTION", "price does not satisfy tick size"
        if FailureMode.ORDER_MIN_SIZE_REJECTION in self._injections or envelope.size < envelope.min_order_size:
            return "ORDER_MIN_SIZE_REJECTION", "size below min order size"
        cost_micro = int((envelope.price * envelope.size * Decimal("1000000")).to_integral_value())
        token_units = int((envelope.size * Decimal("1000000")).to_integral_value())
        if envelope.side == "BUY" and (FailureMode.INSUFFICIENT_PUSD in self._injections or self.ledger.pusd_balance_micro < cost_micro):
            return "INSUFFICIENT_PUSD", "not enough pUSD"
        if envelope.side == "SELL" and (
            FailureMode.INSUFFICIENT_TOKEN_BALANCE in self._injections
            or self.ledger.token_balance(envelope.selected_outcome_token_id) < token_units
        ):
            return "INSUFFICIENT_TOKEN_BALANCE", "not enough CTF tokens"
        return None

    def _apply_collateral_debit(self, envelope: VenueSubmissionEnvelope) -> None:
        if envelope.side == "BUY":
            cost_micro = int((envelope.price * envelope.size * Decimal("1000000")).to_integral_value())
            self.ledger.debit_buy(cost_micro)
        elif envelope.side == "SELL":
            token_units = int((envelope.size * Decimal("1000000")).to_integral_value())
            self.ledger.debit_sell(envelope.selected_outcome_token_id, token_units)

    def _require_order(self, order_id: str) -> dict[str, Any]:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise KeyError(f"unknown fake order_id {order_id!r}") from exc

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"fake-ord-{self._seq:06d}"

    def _maybe_record_restart(self, surface: str) -> None:
        params = self._injections.pop(FailureMode.RESTART_MID_CYCLE, None)
        if params is None:
            return
        self._restart_generation += 1
        self._restart_events.append(
            {
                "generation": self._restart_generation,
                "surface": surface,
                "open_order_ids": sorted(self._orders),
                "observed_at": self.clock.isoformat(),
                **params,
            }
        )


class _FakeEnvelopeClient:
    def get_ok(self) -> dict[str, bool]:
        return {"ok": True}
