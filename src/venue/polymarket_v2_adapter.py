# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
"""Polymarket CLOB V2 adapter.

This module is the only R3 Z2 surface that may import py_clob_client_v2. It
pins provenance in VenueSubmissionEnvelope while tolerating one-step and
two-step SDK order submission shapes.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from src.contracts import Direction, ExecutionIntent
from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

DEFAULT_V2_HOST = "https://clob-v2.polymarket.com"
DEFAULT_Q1_EGRESS_EVIDENCE = Path(
    "docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/q1_zeus_egress_2026-04-26.txt"
)


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    error_code: Optional[str] = None
    message: str = ""


@dataclass(frozen=True)
class SubmitResult:
    status: str
    envelope: VenueSubmissionEnvelope
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class CancelResult:
    status: str
    order_id: str
    raw_response_json: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class OrderState:
    order_id: str
    status: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ClobMarketInfo:
    condition_id: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class OpenOrdersFilter:
    market: Optional[str] = None
    asset_id: Optional[str] = None


@dataclass(frozen=True)
class TradeFact:
    raw: dict[str, Any]


@dataclass(frozen=True)
class PositionFact:
    raw: dict[str, Any]


@dataclass(frozen=True)
class HeartbeatAck:
    ok: bool
    raw: dict[str, Any]


@runtime_checkable
class PolymarketV2AdapterProtocol(Protocol):
    """Shared live/paper venue adapter contract.

    T1 fake venues implement this protocol so paper tests exercise the same
    call surface as the live V2 adapter without credentials or network I/O.
    """

    host: str
    funder_address: str
    chain_id: int
    sdk_version: str

    def preflight(self) -> PreflightResult: ...

    def get_clob_market_info(self, condition_id: str) -> ClobMarketInfo: ...

    def create_submission_envelope(
        self,
        intent: ExecutionIntent,
        snapshot: Any,
        order_type: str,
        post_only: bool = False,
    ) -> VenueSubmissionEnvelope: ...

    def submit(self, envelope: VenueSubmissionEnvelope) -> SubmitResult: ...

    def cancel(self, order_id: str) -> CancelResult: ...

    def get_order(self, order_id: str) -> OrderState: ...

    def get_open_orders(self, filter: OpenOrdersFilter | None = None) -> list[OrderState]: ...

    def get_trades(self, since: Optional[str] = None) -> list[TradeFact]: ...

    def get_positions(self) -> list[PositionFact]: ...

    def get_collateral_payload(self) -> dict[str, Any]: ...

    def get_balance(self, conn=None) -> Any: ...

    def redeem(self, condition_id: str) -> dict[str, Any]: ...

    def post_heartbeat(self, heartbeat_id: str) -> HeartbeatAck: ...

    def submit_limit_order(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> SubmitResult: ...


class StaleMarketSnapshotError(ValueError):
    """Raised when the supplied executable market snapshot is stale."""


class V2AdapterError(RuntimeError):
    """Base adapter exception for unexpected local contract failures."""


class V2ReadUnavailable(V2AdapterError):
    """Raised when a read surface is unavailable and absence cannot be proven."""


class PolymarketV2Adapter:
    def __init__(
        self,
        *,
        host: str = DEFAULT_V2_HOST,
        funder_address: str,
        signer_key: str,
        api_creds: Any = None,
        chain_id: int = 137,
        builder_code: Optional[str] = None,
        q1_egress_evidence_path: Path | None = DEFAULT_Q1_EGRESS_EVIDENCE,
        client_factory: Optional[Callable[..., Any]] = None,
        sdk_version: Optional[str] = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.funder_address = funder_address
        self.signer_key = signer_key
        self.api_creds = api_creds
        self.chain_id = chain_id
        self.builder_code = builder_code
        self.q1_egress_evidence_path = q1_egress_evidence_path
        self._client_factory = client_factory or self._default_client_factory
        self._client = None
        self.sdk_version = sdk_version or _sdk_version()

    def _default_client_factory(self, **kwargs: Any) -> Any:
        from py_clob_client_v2.client import ClobClient

        return ClobClient(
            kwargs["host"],
            kwargs["chain_id"],
            key=kwargs.get("signer_key"),
            creds=kwargs.get("api_creds"),
            signature_type=2,
            funder=kwargs.get("funder_address"),
        )

    def _sdk_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory(
                host=self.host,
                chain_id=self.chain_id,
                signer_key=self.signer_key,
                api_creds=self.api_creds,
                funder_address=self.funder_address,
                builder_code=self.builder_code,
            )
        return self._client

    def preflight(self) -> PreflightResult:
        if self.q1_egress_evidence_path is not None and not Path(self.q1_egress_evidence_path).exists():
            return PreflightResult(
                ok=False,
                error_code="Q1_EGRESS_EVIDENCE_ABSENT",
                message=f"missing Q1 egress evidence: {self.q1_egress_evidence_path}",
            )
        try:
            client = self._sdk_client()
            get_ok = getattr(client, "get_ok", None)
            if callable(get_ok):
                get_ok()
            return PreflightResult(ok=True)
        except Exception as exc:
            return PreflightResult(
                ok=False,
                error_code="V2_PREFLIGHT_FAILED",
                message=str(exc),
            )

    def get_clob_market_info(self, condition_id: str) -> ClobMarketInfo:
        raw = self._sdk_client().get_clob_market_info(condition_id)
        return ClobMarketInfo(condition_id=condition_id, raw=dict(raw or {}))

    def create_submission_envelope(
        self,
        intent: ExecutionIntent,
        snapshot: Any,
        order_type: str,
        post_only: bool = False,
    ) -> VenueSubmissionEnvelope:
        _assert_snapshot_fresh(snapshot)
        outcome_label = _outcome_label(intent.direction)
        side = _side_for_direction(intent.direction)
        selected_token = intent.token_id or (
            _snapshot_attr(snapshot, "yes_token_id") if outcome_label == "YES" else _snapshot_attr(snapshot, "no_token_id")
        )
        price = Decimal(str(intent.limit_price))
        size = _size_from_intent(intent)
        canonical_payload = _canonical_json({
            "token_id": selected_token,
            "side": side,
            "price": str(price),
            "size": str(size),
            "order_type": order_type,
            "post_only": bool(post_only),
            "condition_id": _snapshot_attr(snapshot, "condition_id"),
        })
        payload_hash = _sha256_text(canonical_payload)
        raw_request_hash = _sha256_text(canonical_payload)
        return VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version=self.sdk_version,
            host=self.host,
            chain_id=self.chain_id,
            funder_address=self.funder_address,
            condition_id=str(_snapshot_attr(snapshot, "condition_id")),
            question_id=str(_snapshot_attr(snapshot, "question_id")),
            yes_token_id=str(_snapshot_attr(snapshot, "yes_token_id")),
            no_token_id=str(_snapshot_attr(snapshot, "no_token_id")),
            selected_outcome_token_id=str(selected_token),
            outcome_label=outcome_label,
            side=side,
            price=price,
            size=size,
            order_type=str(order_type),
            post_only=bool(post_only),
            tick_size=Decimal(str(_snapshot_attr(snapshot, "tick_size"))),
            min_order_size=Decimal(str(_snapshot_attr(snapshot, "min_order_size"))),
            neg_risk=bool(_snapshot_attr(snapshot, "neg_risk")),
            fee_details=dict(_snapshot_attr(snapshot, "fee_details") or {}),
            canonical_pre_sign_payload_hash=payload_hash,
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash=raw_request_hash,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

    def submit(self, envelope: VenueSubmissionEnvelope) -> SubmitResult:
        try:
            preflight = self.preflight()
        except Exception as exc:
            return _rejected_submit_result(
                envelope,
                error_code="V2_PREFLIGHT_EXCEPTION",
                error_message=str(exc),
            )
        if not preflight.ok:
            rejected = envelope.with_updates(
                error_code=preflight.error_code or "V2_PREFLIGHT_FAILED",
                error_message=preflight.message,
            )
            return SubmitResult(
                status="rejected",
                envelope=rejected,
                error_code=rejected.error_code,
                error_message=rejected.error_message,
            )

        try:
            client = self._sdk_client()
            order_args = _order_args_from_envelope(envelope)
            options = SimpleNamespace(tick_size=str(envelope.tick_size), neg_risk=envelope.neg_risk)
        except Exception as exc:
            return _rejected_submit_result(
                envelope,
                error_code="V2_PRE_SUBMIT_EXCEPTION",
                error_message=str(exc),
            )
        signed_order = None
        signed_hash = None
        if callable(getattr(client, "create_and_post_order", None)):
            raw_response = client.create_and_post_order(
                order_args,
                options=options,
                order_type=envelope.order_type,
                post_only=envelope.post_only,
                defer_exec=False,
            )
        elif callable(getattr(client, "create_order", None)) and callable(getattr(client, "post_order", None)):
            try:
                signed_order = client.create_order(order_args, options=options)
                signed_bytes = _signed_order_bytes(signed_order)
                signed_hash = hashlib.sha256(signed_bytes).hexdigest()
            except Exception as exc:
                return _rejected_submit_result(
                    envelope,
                    error_code="V2_PRE_SUBMIT_EXCEPTION",
                    error_message=str(exc),
                )
            raw_response = client.post_order(
                signed_order,
                order_type=envelope.order_type,
                post_only=envelope.post_only,
                defer_exec=False,
            )
            signed_order = signed_bytes
        else:
            return _rejected_submit_result(
                envelope,
                error_code="V2_SUBMIT_UNSUPPORTED",
                error_message="SDK client exposes neither one-step nor two-step order submission",
            )
        return _submit_result_from_response(
            envelope,
            raw_response,
            signed_order=signed_order,
            signed_order_hash=signed_hash,
        )

    def cancel(self, order_id: str) -> CancelResult:
        client = self._sdk_client()
        cancel = getattr(client, "cancel", None) or getattr(client, "cancel_order", None)
        if not callable(cancel):
            return CancelResult(status="rejected", order_id=order_id, error_code="CANCEL_UNSUPPORTED")
        raw = cancel(order_id)
        return CancelResult(status="accepted", order_id=order_id, raw_response_json=_canonical_json(raw or {}))

    def get_order(self, order_id: str) -> OrderState:
        raw = self._sdk_client().get_order(order_id)
        raw_dict = dict(raw or {})
        return OrderState(order_id=_extract_order_id(raw_dict) or order_id, status=str(raw_dict.get("status") or raw_dict.get("state") or "UNKNOWN"), raw=raw_dict)

    def get_open_orders(self, filter: OpenOrdersFilter | None = None) -> list[OrderState]:
        client = self._sdk_client()
        get_orders = getattr(client, "get_orders", None)
        if not callable(get_orders):
            raise V2ReadUnavailable("SDK client does not expose get_orders; open-order absence is unknown")
        raw = get_orders()
        if isinstance(raw, dict):
            raw = raw.get("data", []) or []
        return [OrderState(order_id=_extract_order_id(item) or "", status=str(item.get("status") or item.get("state") or "UNKNOWN"), raw=dict(item)) for item in raw]

    def get_trades(self, since: Optional[str] = None) -> list[TradeFact]:
        get_trades = getattr(self._sdk_client(), "get_trades", None)
        if not callable(get_trades):
            raise V2ReadUnavailable("SDK client does not expose get_trades; trade absence is unknown")
        raw = get_trades() or []
        return [TradeFact(raw=dict(item)) for item in raw]

    def get_positions(self) -> list[PositionFact]:
        get_positions = getattr(self._sdk_client(), "get_positions", None)
        if not callable(get_positions):
            raise V2ReadUnavailable("SDK client does not expose get_positions; position absence is unknown")
        raw = get_positions() or []
        return [PositionFact(raw=dict(item)) for item in raw]

    def get_collateral_payload(self) -> dict[str, Any]:
        """Return SDK-derived collateral facts for CollateralLedger.refresh().

        All py_clob_client_v2 imports stay confined to this adapter. The state
        ledger receives plain dictionaries and never depends on SDK types.
        """

        client = self._sdk_client()
        get_balance_allowance = getattr(client, "get_balance_allowance", None)
        if not callable(get_balance_allowance):
            raise V2AdapterError("SDK client does not expose get_balance_allowance")
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        except Exception:
            params = SimpleNamespace(asset_type="COLLATERAL")
        raw = get_balance_allowance(params)
        if not isinstance(raw, dict):
            raw = dict(raw)
        if raw.get("balance") is None:
            raise V2AdapterError("balance allowance response missing balance")

        balances: dict[str, int] = {}
        allowances: dict[str, int] = {}
        for position in self.get_positions():
            item = dict(position.raw)
            token_id = item.get("asset") or item.get("token_id") or item.get("tokenId")
            if not token_id:
                continue
            token_key = str(token_id)
            balance_units = _ctf_balance_units(item.get("size", item.get("balance", 0)))
            balances[token_key] = balances.get(token_key, 0) + balance_units
            allowance_raw = item.get("allowance", item.get("token_allowance", item.get("approved_amount")))
            if allowance_raw is not None:
                allowance_units = _ctf_balance_units(allowance_raw)
            elif item.get("approved") is True or item.get("isApprovedForAll") is True:
                allowance_units = balance_units
            else:
                allowance_units = 0
            allowances[token_key] = allowances.get(token_key, 0) + allowance_units

        return {
            "pusd_balance_micro": raw.get("balance", 0),
            "pusd_allowance_micro": raw.get("allowance", 0),
            "usdc_e_legacy_balance_micro": 0,
            "ctf_token_balances_units": balances,
            "ctf_token_allowances_units": allowances,
            "authority_tier": "CHAIN",
        }

    def get_balance(self, conn=None) -> "CollateralSnapshot":
        """Return the funded wallet's Z4 collateral snapshot.

        Z4 makes this adapter boundary snapshot-shaped, not a raw float, so
        callers cannot accidentally treat pUSD buy collateral as CTF sell
        inventory or silently ignore legacy USDC.e/wrap state.
        """

        from src.state.collateral_ledger import CollateralLedger

        own_conn = conn is None
        if own_conn:
            from src.state.db import get_trade_connection_with_world

            conn = get_trade_connection_with_world()
        try:
            snapshot = CollateralLedger(conn).refresh(self)
            if own_conn:
                conn.commit()
            return snapshot
        finally:
            if own_conn:
                conn.close()

    def redeem(self, condition_id: str) -> dict[str, Any]:
        """Redeem winning shares when the SDK exposes a redeem method.

        Z2 does not verify a V2 redeem surface. Missing support therefore
        produces a typed response rather than falling back to the legacy SDK.
        """

        return {
            "success": False,
            "errorCode": "REDEEM_DEFERRED_TO_R1",
            "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
            "condition_id": condition_id,
        }

    def post_heartbeat(self, heartbeat_id: str) -> HeartbeatAck:
        raw = self._sdk_client().post_heartbeat(heartbeat_id)
        return HeartbeatAck(ok=True, raw=dict(raw or {}))

    def submit_limit_order(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> SubmitResult:
        """Compatibility helper for legacy PolymarketClient.place_limit_order.

        This path exists until U1 wires executable market snapshots into the
        executor. It still produces a VenueSubmissionEnvelope before SDK contact,
        but its market identity fields are compatibility placeholders rather than
        U1-certified snapshot facts. The canonical request hash is computed from
        the final side/size values; the envelope is never mutated post-hash.
        """

        try:
            preflight = self.preflight()
        except Exception as exc:
            return self._compat_rejected_submit_result(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=order_type,
                error_code="V2_PREFLIGHT_EXCEPTION",
                error_message=str(exc),
            )
        if not preflight.ok:
            return self._compat_rejected_submit_result(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=order_type,
                error_code=preflight.error_code or "V2_PREFLIGHT_FAILED",
                error_message=preflight.message,
            )

        try:
            sdk_snapshot = self._compat_snapshot_for_token(token_id)
        except Exception as exc:
            return self._compat_rejected_submit_result(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=order_type,
                error_code="V2_PRE_SUBMIT_EXCEPTION",
                error_message=str(exc),
            )
        envelope = self._create_compat_submission_envelope(
            token_id=token_id,
            price=Decimal(str(price)),
            size=Decimal(str(size)),
            side=side,
            order_type=order_type,
            sdk_snapshot=sdk_snapshot,
        )
        return self.submit(envelope)

    def _compat_rejected_submit_result(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str,
        error_code: str,
        error_message: str,
    ) -> SubmitResult:
        envelope = self._create_compat_submission_envelope(
            token_id=token_id,
            price=Decimal(str(price)),
            size=Decimal(str(size)),
            side=side,
            order_type=order_type,
            sdk_snapshot=SimpleNamespace(
                tick_size=Decimal("0.01"),
                min_order_size=Decimal("0.01"),
                neg_risk=bool(None),
                fee_details={
                    "bps": None,
                    "builder_fee_bps": 0,
                    "source": "pre_submit_rejected_no_sdk_snapshot",
                },
            ),
        ).with_updates(
            error_code=error_code,
            error_message=error_message,
        )
        return SubmitResult(
            status="rejected",
            envelope=envelope,
            error_code=envelope.error_code,
            error_message=envelope.error_message,
        )

    def _create_compat_submission_envelope(
        self,
        *,
        token_id: str,
        price: Decimal,
        size: Decimal,
        side: str,
        order_type: str,
        sdk_snapshot: SimpleNamespace,
    ) -> VenueSubmissionEnvelope:
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        condition_id = f"legacy:{token_id}"
        canonical_payload = _canonical_json({
            "token_id": token_id,
            "side": side,
            "price": str(price),
            "size": str(size),
            "order_type": order_type,
            "post_only": False,
            "condition_id": condition_id,
        })
        payload_hash = _sha256_text(canonical_payload)
        return VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version=self.sdk_version,
            host=self.host,
            chain_id=self.chain_id,
            funder_address=self.funder_address,
            condition_id=condition_id,
            question_id="legacy-compat",
            yes_token_id=token_id,
            no_token_id=token_id,
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side=side,
            price=price,
            size=size,
            order_type=str(order_type),
            post_only=False,
            tick_size=sdk_snapshot.tick_size,
            min_order_size=sdk_snapshot.min_order_size,
            neg_risk=sdk_snapshot.neg_risk,
            fee_details=sdk_snapshot.fee_details,
            canonical_pre_sign_payload_hash=payload_hash,
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash=payload_hash,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

    def _compat_snapshot_for_token(self, token_id: str) -> SimpleNamespace:
        client = self._sdk_client()
        neg_risk_fn = getattr(client, "get_neg_risk", None)
        tick_size_fn = getattr(client, "get_tick_size", None)
        if not callable(neg_risk_fn):
            raise V2AdapterError("SDK client does not expose get_neg_risk for legacy submit compatibility")
        if not callable(tick_size_fn):
            raise V2AdapterError("SDK client does not expose get_tick_size for legacy submit compatibility")
        fee_rate_fn = getattr(client, "get_fee_rate_bps", None)
        fee_rate_bps = fee_rate_fn(token_id) if callable(fee_rate_fn) else None
        return SimpleNamespace(
            tick_size=Decimal(str(tick_size_fn(token_id))),
            min_order_size=Decimal("0.01"),
            neg_risk=bool(neg_risk_fn(token_id)),
            fee_details={
                "bps": int(fee_rate_bps) if fee_rate_bps is not None else None,
                "builder_fee_bps": 0,
                "source": "py-clob-client-v2",
            },
        )


def _sdk_version() -> str:
    try:
        return importlib.metadata.version("py-clob-client-v2")
    except importlib.metadata.PackageNotFoundError:
        return "uninstalled"


def _snapshot_attr(snapshot: Any, name: str) -> Any:
    if isinstance(snapshot, dict):
        return snapshot[name]
    return getattr(snapshot, name)


def _assert_snapshot_fresh(snapshot: Any) -> None:
    has_is_fresh = hasattr(snapshot, "is_fresh") or (isinstance(snapshot, dict) and "is_fresh" in snapshot)
    if has_is_fresh:
        is_fresh = _snapshot_attr(snapshot, "is_fresh")
        if callable(is_fresh):
            is_fresh = is_fresh()
        if not is_fresh:
            raise StaleMarketSnapshotError("ExecutableMarketSnapshotV2 is stale")

    has_captured_at = hasattr(snapshot, "captured_at") or (isinstance(snapshot, dict) and "captured_at" in snapshot)
    has_window = hasattr(snapshot, "freshness_window_seconds") or (
        isinstance(snapshot, dict) and "freshness_window_seconds" in snapshot
    )
    if has_captured_at or has_window:
        if not (has_captured_at and has_window):
            raise StaleMarketSnapshotError("ExecutableMarketSnapshotV2 freshness fields are incomplete")
        captured_at = _parse_snapshot_datetime(_snapshot_attr(snapshot, "captured_at"))
        window_seconds = float(_snapshot_attr(snapshot, "freshness_window_seconds"))
        if window_seconds <= 0:
            raise StaleMarketSnapshotError("ExecutableMarketSnapshotV2 freshness window must be positive")
        age_seconds = (datetime.now(timezone.utc) - captured_at).total_seconds()
        if age_seconds > window_seconds:
            raise StaleMarketSnapshotError("ExecutableMarketSnapshotV2 is outside freshness window")
        return

    if not has_is_fresh:
        raise StaleMarketSnapshotError("ExecutableMarketSnapshotV2 freshness contract missing")


def _outcome_label(direction: Direction) -> str:
    if direction == Direction.YES:
        return "YES"
    if direction == Direction.NO:
        return "NO"
    raise ValueError(f"ExecutionIntent direction must be buy_yes or buy_no, got {direction!r}")


def _parse_snapshot_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _side_for_direction(direction: Direction) -> str:
    if direction in {Direction.YES, Direction.NO}:
        return "BUY"
    raise ValueError(f"ExecutionIntent direction must be buy_yes or buy_no, got {direction!r}")


def _size_from_intent(intent: ExecutionIntent) -> Decimal:
    price = Decimal(str(intent.limit_price))
    if price <= 0:
        raise ValueError("intent.limit_price must be positive to derive order size")
    return Decimal(str(intent.target_size_usd)) / price


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _order_args_from_envelope(envelope: VenueSubmissionEnvelope) -> SimpleNamespace:
    try:
        from py_clob_client_v2.clob_types import OrderArgs

        return OrderArgs(
            token_id=envelope.selected_outcome_token_id,
            price=float(envelope.price),
            size=float(envelope.size),
            side=envelope.side,
        )
    except Exception:
        return SimpleNamespace(
            token_id=envelope.selected_outcome_token_id,
            price=float(envelope.price),
            size=float(envelope.size),
            side=envelope.side,
            builder_code=None,
        )


def _signed_order_bytes(signed_order: Any) -> bytes:
    if isinstance(signed_order, bytes):
        return signed_order
    if isinstance(signed_order, str):
        return signed_order.encode("utf-8")
    return _canonical_json(_to_jsonish(signed_order)).encode("utf-8")


def _to_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    return repr(value)


def _extract_order_id(raw: Any) -> Optional[str]:
    if not isinstance(raw, dict):
        return None
    return raw.get("orderID") or raw.get("orderId") or raw.get("order_id") or raw.get("id")


def _response_error(raw: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(raw, dict):
        return None, None
    code = raw.get("errorCode") or raw.get("error_code") or raw.get("code")
    message = raw.get("errorMessage") or raw.get("error_message") or raw.get("message")
    return (str(code) if code else None, str(message) if message else None)


def _rejected_submit_result(
    envelope: VenueSubmissionEnvelope,
    *,
    error_code: str,
    error_message: str,
    signed_order: bytes | None = None,
    signed_order_hash: str | None = None,
) -> SubmitResult:
    updated = envelope.with_updates(
        signed_order=signed_order,
        signed_order_hash=signed_order_hash,
        error_code=error_code,
        error_message=error_message,
    )
    return SubmitResult(
        status="rejected",
        envelope=updated,
        error_code=updated.error_code,
        error_message=updated.error_message,
    )


def _submit_result_from_response(
    envelope: VenueSubmissionEnvelope,
    raw_response: Any,
    *,
    signed_order: bytes | None,
    signed_order_hash: str | None,
) -> SubmitResult:
    raw_json = _canonical_json(raw_response or {})
    if isinstance(raw_response, dict) and raw_response.get("success") is False:
        code, message = _response_error(raw_response)
        code = code or "SUBMIT_REJECTED"
        updated = envelope.with_updates(
            signed_order=signed_order,
            signed_order_hash=signed_order_hash,
            raw_response_json=raw_json,
            error_code=code,
            error_message=message,
        )
        return SubmitResult(status="rejected", envelope=updated, error_code=code, error_message=message)
    order_id = _extract_order_id(raw_response)
    if not order_id:
        updated = envelope.with_updates(
            signed_order=signed_order,
            signed_order_hash=signed_order_hash,
            raw_response_json=raw_json,
            error_code="MISSING_ORDER_ID",
            error_message="submit response did not include order id",
        )
        return SubmitResult(
            status="rejected",
            envelope=updated,
            error_code="MISSING_ORDER_ID",
            error_message="submit response did not include order id",
        )
    updated = envelope.with_updates(
        signed_order=signed_order,
        signed_order_hash=signed_order_hash,
        raw_response_json=raw_json,
        order_id=str(order_id),
    )
    return SubmitResult(status="accepted", envelope=updated)


def _ctf_balance_units(value: Any) -> int:
    try:
        return int((Decimal(str(value or "0")) * Decimal("1000000")).to_integral_value(rounding=ROUND_FLOOR))
    except Exception:
        return 0
