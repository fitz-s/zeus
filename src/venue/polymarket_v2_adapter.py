# Created: 2026-04-27
# Last reused/audited: 2026-05-17
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
#                  + docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
"""Polymarket CLOB V2 adapter.

This module is the only R3 Z2 surface that may import py_clob_client_v2. It
pins provenance in VenueSubmissionEnvelope while tolerating one-step and
two-step SDK order submission shapes.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from src.contracts import Direction, ExecutionIntent
from src.contracts.executable_market_snapshot_v2 import (
    MarketSnapshotMismatchError,
    canonicalize_fee_details,
)
from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
from src.observability.counters import increment as _cnt_inc

logger = logging.getLogger(__name__)

DEFAULT_V2_HOST = "https://clob.polymarket.com"
POLYMARKET_DATA_API_BASE = "https://data-api.polymarket.com"
DEFAULT_Q1_EGRESS_EVIDENCE = Path(
    "docs/operations/live_egress/q1_zeus_egress_current.txt"
)
Q1_EGRESS_EVIDENCE_ENV = "POLYMARKET_CLOB_V2_Q1_EGRESS_EVIDENCE"
Q1_EGRESS_REJECTED_PATH_FRAGMENTS = (
    "task_2026-04-26_polymarket_clob_v2_migration",
)
Q1_EGRESS_REQUIRED_MARKERS = (
    "Q1 Zeus egress evidence sentinel",
    "authority_basis:",
    "operator_attestation:",
    "live_side_effects: none",
    "raw_secrets_or_signed_payloads: none",
    "probe_results:",
    "https://clob.polymarket.com/ok",
)
DEFAULT_SIGNATURE_TYPE = 2  # Current Zeus keychain funder is a POLY_GNOSIS_SAFE contract.
DEFAULT_POLYGON_RPC_URL = "https://polygon-bor-rpc.publicnode.com"
POLYGON_PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
POLYGON_EXCHANGE_V2_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"
POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"
# PR-I.5.c: Gnosis Conditional Tokens Framework canonical deployment on Polygon
# mainnet. Source: Polymarket public contract docs; on-chain bytecode
# verification deferred to operator dry-run before first live submission.
POLYGON_CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
# PR-I.5.c: redeemPositions(address,bytes32,bytes32,uint256[]) keccak256[:4].
# Verified locally with eth_utils.keccak; pinned to prevent silent ABI drift.
CTF_REDEEM_POSITIONS_SELECTOR = "0x01b7037c"
# negRisk adapter — ALL Polymarket daily-temperature markets route here.
# Address verified on-chain 2026-05-19 (Polygon mainnet, 17KB bytecode).
POLYGON_NEGRISK_ADAPTER_ADDRESS = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
# redeemPositions(bytes32 conditionId, uint256[] amounts) keccak256[:4].
# Verified by decoding successful Polygon tx
# 0x4ce58f2683bd81f7f49b7dd19e35cc2db4fc137c2f841712876ace23afe39448.
NEGRISK_REDEEM_POSITIONS_SELECTOR = "0xdbeccb23"
# PR-I.5.c kill switch. Default OFF — adapter returns the existing
# REDEEM_DEFERRED_TO_R1 stub so settlement_commands routes to
# REDEEM_OPERATOR_REQUIRED (per SCAFFOLD §K.3 v5). Operator flips this ON
# AFTER dry-run mock verification. See settlement_commands.py:426 — the
# OPERATOR_REQUIRED branch keys on errorCode == "REDEEM_DEFERRED_TO_R1"
# exactly; do NOT change that string.
AUTONOMOUS_REDEEM_ENABLED_ENV = "ZEUS_AUTONOMOUS_REDEEM_ENABLED"
# Safe wrap dry-run gate. Default OFF.  When ON: build+sign+log raw hex,
# skip eth_sendRawTransaction, return REDEEM_DRY_RUN_LOGGED so operator can
# validate via Tenderly before first live broadcast.
AUTONOMOUS_REDEEM_DRY_RUN_ENV = "ZEUS_AUTONOMOUS_REDEEM_DRY_RUN"


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
    """Shared live/fake venue adapter contract.

    T1 fake venues implement this protocol so parity tests exercise the same
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

    def get_pusd_balance_micro(self) -> int: ...

    def get_collateral_payload(self) -> dict[str, Any]: ...

    def get_balance(self, conn=None) -> Any: ...

    def redeem(
        self,
        condition_id: str,
        *,
        index_sets: list[int] | None = None,
    ) -> dict[str, Any]: ...

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
        signature_type: int = DEFAULT_SIGNATURE_TYPE,
        polygon_rpc_url: str | None = None,
        rpc_call: Optional[Callable[[str, str, list[Any]], Any]] = None,
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
        self.signature_type = _normalize_signature_type(signature_type)
        self.polygon_rpc_url = polygon_rpc_url
        self._rpc_call = rpc_call or _json_rpc_call
        self.builder_code = builder_code
        self.q1_egress_evidence_path = q1_egress_evidence_path
        self._client_factory = client_factory or self._default_client_factory
        self._client = None
        self.sdk_version = sdk_version or _sdk_version()

    def _default_client_factory(self, **kwargs: Any) -> Any:
        from py_clob_client_v2.client import ClobClient

        client = ClobClient(
            kwargs["host"],
            kwargs["chain_id"],
            key=kwargs.get("signer_key"),
            creds=kwargs.get("api_creds"),
            signature_type=kwargs.get("signature_type", DEFAULT_SIGNATURE_TYPE),
            funder=kwargs.get("funder_address"),
        )
        # CLOB v2 L2 endpoints (balance/order) require API creds. The canonical
        # SDK path is `set_api_creds(create_or_derive_api_key())` — this derives
        # them deterministically from the L1 signer rather than relying on a
        # separately-stored copy that can drift out of sync. We only derive when
        # no static creds were provided so callers (eg. tests) can still inject
        # specific credentials.
        if not kwargs.get("api_creds"):
            try:
                client.set_api_creds(client.create_or_derive_api_key())
                logger.warning(
                    "VENUE_AUTH_FALLBACK_TRIGGERED: create_or_derive_api_key used "
                    "(primary /auth/api-key creds absent); L2 calls proceeding via derived creds",
                )
            except Exception as exc:  # pragma: no cover - upstream SDK behaviour
                env_api_creds = _api_creds_from_env()
                if env_api_creds is None:
                    logger.warning(
                        "create_or_derive_api_key failed; L2-authenticated calls will "
                        "fail until creds are provided: %s", exc,
                    )
                else:
                    client.set_api_creds(env_api_creds)
                    logger.warning(
                        "VENUE_AUTH_STATIC_FALLBACK_TRIGGERED: create_or_derive_api_key "
                        "failed; using POLYMARKET_API_* creds and deferring validity "
                        "to the next L2-authenticated preflight: %s",
                        exc,
                    )
        return client

    def _sdk_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory(
                host=self.host,
                chain_id=self.chain_id,
                signer_key=self.signer_key,
                api_creds=self.api_creds,
                signature_type=self.signature_type,
                funder_address=self.funder_address,
                builder_code=self.builder_code,
            )
        return self._client

    def preflight(self) -> PreflightResult:
        if self.q1_egress_evidence_path is not None:
            evidence_result = _validate_q1_egress_evidence(self.q1_egress_evidence_path)
            if not evidence_result.ok:
                return evidence_result
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
            fee_details=_canonical_fee_details_for_envelope(_snapshot_attr(snapshot, "fee_details")),
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
        # T1F-ADAPTER-ASSERTS-LIVE-BOUND-BEFORE-SDK: reject placeholder envelopes
        # before any SDK call.  Mirror: src/data/polymarket_client.py:407-424.
        try:
            envelope.assert_live_submit_bound()
        except ValueError as exc:
            _cnt_inc("placeholder_envelope_blocked_total")
            logger.warning(
                "telemetry_counter event=placeholder_envelope_blocked_total path=submit"
            )
            return _rejected_submit_result(
                envelope,
                error_code="BOUND_ENVELOPE_NOT_LIVE_AUTHORITY",
                error_message=str(exc),
            )
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
        cancel = getattr(client, "cancel", None)
        if not callable(cancel):
            cancel_order = getattr(client, "cancel_order", None)
            if not callable(cancel_order):
                return CancelResult(status="rejected", order_id=order_id, error_code="CANCEL_UNSUPPORTED")
            raw = cancel_order(_order_payload(order_id))
        else:
            raw = cancel(order_id)
        return _cancel_result_from_response(order_id, raw)

    def get_order(self, order_id: str) -> OrderState:
        raw = self._sdk_client().get_order(order_id)
        raw_dict = dict(raw or {})
        return OrderState(order_id=_extract_order_id(raw_dict) or order_id, status=str(raw_dict.get("status") or raw_dict.get("state") or "UNKNOWN"), raw=raw_dict)

    def get_open_orders(self, filter: OpenOrdersFilter | None = None) -> list[OrderState]:
        client = self._sdk_client()
        get_orders = getattr(client, "get_open_orders", None)
        if not callable(get_orders):
            get_orders = getattr(client, "get_orders", None)
        if not callable(get_orders):
            raise V2ReadUnavailable(
                "SDK client exposes neither get_open_orders nor get_orders; "
                "open-order absence is unknown"
            )
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
            return self._get_positions_from_data_api()
        raw = get_positions() or []
        return [PositionFact(raw=dict(item)) for item in raw]

    def _get_positions_from_data_api(self) -> list[PositionFact]:
        if not self.funder_address:
            raise V2ReadUnavailable("funder_address is required for data-api position enumeration")
        query = urllib.parse.urlencode(
            {"user": self.funder_address, "sizeThreshold": "0.01"}
        )
        request = urllib.request.Request(
            f"{POLYMARKET_DATA_API_BASE}/positions?{query}",
            headers={"user-agent": "zeus-readonly/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                decoded = json.loads(response.read())
        except Exception as exc:
            raise V2ReadUnavailable(f"data-api position enumeration failed: {exc}") from exc
        if isinstance(decoded, dict):
            decoded = decoded.get("data", []) or []
        if not isinstance(decoded, list):
            raise V2ReadUnavailable("data-api position enumeration returned non-list payload")
        return [PositionFact(raw=dict(item)) for item in decoded if isinstance(item, dict)]

    def get_pusd_balance_micro(self) -> int:
        """Return pUSD wallet balance without touching local trade-state DBs."""

        raw = self._collateral_balance_allowance_raw()
        balance = _micro_int_or_none(raw.get("balance"))
        if balance is None:
            raise V2AdapterError("balance allowance response missing balance")
        return balance

    def get_collateral_payload(self) -> dict[str, Any]:
        """Return SDK-derived collateral facts for CollateralLedger.refresh().

        All py_clob_client_v2 imports stay confined to this adapter. The state
        ledger receives plain dictionaries and never depends on SDK types.
        """

        raw = self._collateral_balance_allowance_raw()
        pusd_allowance_raw = raw.get("allowance")
        allowance_int = _micro_int_or_none(pusd_allowance_raw)
        authority_tier = "CHAIN"
        allowance_source = "clob_balance_allowance"
        if allowance_int is None or allowance_int == 0:
            chain_allowance = self._chain_collateral_allowance_micro()
            if chain_allowance is not None:
                pusd_allowance_raw = chain_allowance
                allowance_source = "chain_erc20_allowance"
            elif allowance_int is None:
                pusd_allowance_raw = None
                allowance_source = "missing"
            else:
                pusd_allowance_raw = allowance_int
                authority_tier = "DEGRADED"
                allowance_source = "chain_erc20_unavailable_clob_zero"

        balances: dict[str, int] = {}
        allowances: dict[str, int] = {}
        # CTF position enumeration goes through a separate read surface (the
        # Polymarket data-api) — `py_clob_client_v2.ClobClient` does not expose
        # `get_positions`. When the SDK lacks the method we degrade to an empty
        # CTF map rather than fail the entire collateral payload, since pUSD
        # balance is sufficient for boot wallet checks. CTF position truth is
        # consumed elsewhere (data-api adapter, position reconciliation jobs).
        try:
            positions_iter = self.get_positions()
        except V2ReadUnavailable as exc:
            import logging
            logging.getLogger(__name__).debug(
                "get_collateral_payload: skipping CTF enumeration (%s)", exc,
            )
            positions_iter = []
        for position in positions_iter:
            item = dict(position.raw)
            token_id = item.get("asset") or item.get("token_id") or item.get("tokenId")
            if not token_id:
                continue
            token_key = str(token_id)
            conditional_raw = self._conditional_balance_allowance_raw(token_key)
            conditional_balance_units = _micro_int_or_none(conditional_raw.get("balance"))
            balance_units = (
                conditional_balance_units
                if conditional_balance_units is not None
                else _ctf_balance_units(item.get("size", item.get("balance", 0)))
            )
            balances[token_key] = balances.get(token_key, 0) + balance_units
            allowance_raw = conditional_raw.get(
                "allowance",
                item.get("allowance", item.get("token_allowance", item.get("approved_amount"))),
            )
            if allowance_raw is not None:
                allowance_micro = _micro_int_or_none(allowance_raw)
                allowance_units = (
                    allowance_micro
                    if allowance_micro is not None
                    else _ctf_balance_units(allowance_raw)
                )
            elif conditional_balance_units is not None:
                # CLOB currently returns conditional-token balance without a
                # separate allowance field. Treat the CLOB conditional balance
                # as sell-cover proof; the submit response remains the authority
                # for any approval-specific rejection.
                allowance_units = conditional_balance_units
            elif item.get("approved") is True or item.get("isApprovedForAll") is True:
                allowance_units = balance_units
            else:
                allowance_units = 0
            allowances[token_key] = allowances.get(token_key, 0) + allowance_units

        return {
            "pusd_balance_micro": raw.get("balance", 0),
            "pusd_allowance_micro": pusd_allowance_raw if pusd_allowance_raw is not None else 0,
            "usdc_e_legacy_balance_micro": 0,
            "ctf_token_balances_units": balances,
            "ctf_token_allowances_units": allowances,
            "authority_tier": authority_tier,
            "signature_type": self.signature_type,
            "pusd_allowance_source": allowance_source,
        }

    def _collateral_balance_allowance_raw(self) -> dict[str, Any]:
        """Read the CLOB collateral balance/allowance surface once."""

        client = self._sdk_client()
        get_balance_allowance = getattr(client, "get_balance_allowance", None)
        if not callable(get_balance_allowance):
            raise V2AdapterError("SDK client does not expose get_balance_allowance")
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self.signature_type,
            )
        except Exception:
            params = SimpleNamespace(
                asset_type="COLLATERAL",
                signature_type=self.signature_type,
            )
        update_balance_allowance = getattr(client, "update_balance_allowance", None)
        if callable(update_balance_allowance):
            update_balance_allowance(params)
        raw = get_balance_allowance(params)
        if not isinstance(raw, dict):
            raw = dict(raw)
        if raw.get("balance") is None:
            raise V2AdapterError("balance allowance response missing balance")
        return raw

    def _conditional_balance_allowance_raw(self, token_id: str) -> dict[str, Any]:
        """Read CLOB conditional-token balance/allowance for one outcome token."""

        if not token_id:
            return {}
        client = self._sdk_client()
        get_balance_allowance = getattr(client, "get_balance_allowance", None)
        if not callable(get_balance_allowance):
            return {}
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=self.signature_type,
            )
        except Exception:
            params = SimpleNamespace(
                asset_type="CONDITIONAL",
                token_id=token_id,
                signature_type=self.signature_type,
            )
        try:
            return dict(get_balance_allowance(params) or {})
        except Exception as exc:
            logger.debug(
                "conditional balance allowance unavailable for token %s...%s: %s",
                token_id[:8],
                token_id[-4:],
                exc,
            )
            return {}

    def _chain_collateral_allowance_micro(self) -> int | None:
        if not self.polygon_rpc_url:
            return None
        try:
            collateral, spenders = _collateral_allowance_contracts(self.chain_id)
            allowances = [
                _eth_call_uint(
                    self.polygon_rpc_url,
                    self._rpc_call,
                    to=collateral,
                    data="0xdd62ed3e"
                    + _abi_address(self.funder_address)
                    + _abi_address(spender),
                )
                for spender in spenders
            ]
            return min(allowances)
        except Exception as exc:
            logger.warning(
                "pUSD allowance chain fallback failed; preserving fail-closed "
                "missing-allowance semantics: %s",
                exc,
            )
            return None

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

    def redeem(
        self,
        condition_id: str,
        *,
        index_sets: list[int] | None = None,
        neg_risk: bool = False,
        amount_per_slot: int | None = None,
    ) -> dict[str, Any]:
        """Redeem winning shares via the Polygon CTF or negRisk adapter.

        PR-I.5.c (2026-05-18) Path A wire: eth_abi.encode + eth_utils.keccak
        for calldata, eth_account.Account.sign_transaction for signing, and the
        existing self._rpc_call (urllib JSON-RPC) for nonce/gas/broadcast.
        No `web3` library dependency.

        neg_risk: When True, routes to the NegRiskCtfAdapter
        (POLYGON_NEGRISK_ADAPTER_ADDRESS) instead of the standard CTF. ALL
        Polymarket daily-temperature markets are negRisk. The caller (typically
        submit_redeem in settlement_commands) is responsible for deriving this
        flag from the executable_market_snapshots table.

        negRisk ABI: redeemPositions(bytes32 conditionId, uint256[] amounts)
        amounts is always length-2 for binary markets. indexSet-to-slot mapping
        (verified on-chain 2026-05-19 via Karachi eth_call probes):
          indexSet=2 (YES won) → amounts=[amount, 0]  (slot 0 = YES)
          indexSet=1 (NO won)  → amounts=[0, amount]  (slot 1 = NO)
        Live proof tx: 0x4ce58f2683bd81f7f49b7dd19e35cc2db4fc137c2f841712876ace23afe39448

        Kill switch: ZEUS_AUTONOMOUS_REDEEM_ENABLED env-var defaults OFF. When
        OFF, returns the legacy REDEEM_DEFERRED_TO_R1 stub bytes-for-bytes so
        settlement_commands.py:426 still routes to REDEEM_OPERATOR_REQUIRED
        (operator CLI completes via scripts/operator_record_redeem.py).
        Flipping ON enables autonomous web3 submission; do NOT do so without
        a dry-run smoke test first (Karachi position c30f28a5-d4e is the first
        live target).

        index_sets: CTF redeemPositions indexSets — binary outcome wins as
        [1]=NO or [2]=YES. Multi-bin (ranged) markets pass the union of winning
        bins. None means caller did not derive the bin — adapter cannot guess
        and returns the stub so operator CLI handles it.
        """

        # Karachi safety / Path A precedence: default OFF returns the stub
        # verbatim. settlement_commands.py:426-454 already handles this
        # errorCode by routing to REDEEM_OPERATOR_REQUIRED (NOT terminal;
        # operator CLI exits the state).
        autonomous_enabled = (
            os.environ.get(AUTONOMOUS_REDEEM_ENABLED_ENV, "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if not autonomous_enabled:
            # Return verbatim legacy stub so settlement_commands.py:426 routes
            # to REDEEM_OPERATOR_REQUIRED exactly as before this PR (errorCode
            # AND errorMessage unchanged — byte-for-byte audited fallback).
            return {
                "success": False,
                "errorCode": "REDEEM_DEFERRED_TO_R1",
                "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
                "condition_id": condition_id,
            }

        if not index_sets:
            return {
                "success": False,
                "errorCode": "REDEEM_INDEX_SETS_MISSING",
                "errorMessage": (
                    "redeem() requires index_sets (e.g. [2] for YES win, [1] for NO win); "
                    "harvester must populate winning_index_set in settlement_commands"
                ),
                "condition_id": condition_id,
            }
        # negRisk adapter requires the actual token balance (in micro-units).
        # The standard CTF takes bitmask index_sets; negRisk takes slot amounts.
        # Without the amount, calldata would be wrong — fail-closed here rather
        # than silently encoding 0 or the index_set integer as the amount.
        if neg_risk and amount_per_slot is None:
            return {
                "success": False,
                "errorCode": "REDEEM_NEGRISK_AMOUNT_MISSING",
                "errorMessage": (
                    "negRisk redeem requires amount_per_slot (token balance in micro-units); "
                    "settlement_commands must derive from token_amounts_json before calling"
                ),
                "condition_id": condition_id,
            }
        if not self.polygon_rpc_url:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_URL_MISSING",
                "errorMessage": "polygon_rpc_url required for autonomous redeem path",
                "condition_id": condition_id,
            }
        if not self.signer_key or not self.funder_address:
            return {
                "success": False,
                "errorCode": "REDEEM_CREDENTIALS_MISSING",
                "errorMessage": "signer_key and funder_address required for autonomous redeem",
                "condition_id": condition_id,
            }

        # Fail-closed: Polygon-specific calldata must never be broadcast on
        # another network.  Check chain_id BEFORE any path routing so the
        # Safe branch cannot bypass this guard.
        if int(self.chain_id) != 137:
            return {
                "success": False,
                "errorCode": "REDEEM_WRONG_CHAIN",
                "errorMessage": (
                    f"autonomous redeem only supported on Polygon mainnet (chain_id=137); "
                    f"configured chain_id={self.chain_id}"
                ),
                "condition_id": condition_id,
            }

        # Derive the EOA that will sign/broadcast.  For the Safe wrap path
        # (signature_type==2, EOA != funder) this is the signer EOA, not the
        # Safe address.  Any other EOA/funder mismatch is a hard config error.
        try:
            from eth_account import Account as _Account

            signer_eoa = _Account.from_key(self.signer_key).address
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_SIGNER_DERIVE_FAILED",
                "errorMessage": f"cannot derive EOA from signer_key: {exc}",
                "condition_id": condition_id,
            }
        # signature_type==2: funder is a Safe proxy (Zeus default deployment).
        # When signer EOA != funder, enter the Safe execTransaction wrap path
        # if and only if signature_type==2.  Any other type with a mismatch is
        # still a hard fail (REDEEM_SIGNER_FUNDER_MISMATCH).
        if signer_eoa.lower() != self.funder_address.lower():
            if self.signature_type == 2:
                # negRisk markets: route to negRisk adapter Safe wrap path.
                # Standard CTF: route to existing CTF Safe wrap path.
                if neg_risk:
                    return self._redeem_via_negrisk_safe(
                        condition_id=condition_id,
                        index_sets=index_sets,
                        signer_eoa=signer_eoa,
                        amount=int(amount_per_slot),  # type: ignore[arg-type]
                    )
                # Delegate to Safe wrap path (returns a result dict directly).
                return self._redeem_via_safe(
                    condition_id=condition_id,
                    index_sets=index_sets,
                    signer_eoa=signer_eoa,
                )
            return {
                "success": False,
                "errorCode": "REDEEM_SIGNER_FUNDER_MISMATCH",
                "errorMessage": (
                    f"signer EOA {signer_eoa} != funder_address {self.funder_address}; "
                    "autonomous redeem requires an EOA funder when signature_type != 2"
                ),
                "condition_id": condition_id,
            }

        # negRisk EOA path (signer_eoa == funder_address, neg_risk=True):
        # use negRisk adapter address and negRisk calldata builder.
        _to_address = POLYGON_CTF_ADDRESS
        try:
            if neg_risk:
                calldata = _build_negrisk_redeem_calldata(
                    condition_id, index_sets, int(amount_per_slot)  # type: ignore[arg-type]
                )
                _to_address = POLYGON_NEGRISK_ADAPTER_ADDRESS
            else:
                calldata = _build_redeem_calldata(condition_id, index_sets)
        except Exception as exc:  # ABI-encode failure is a structural defect
            return {
                "success": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": f"calldata build failed: {exc}",
                "condition_id": condition_id,
            }

        # Nonce: 'pending' so a prior unconfirmed tx from this wallet does not
        # collide. Gas price: eth_gasPrice RPC. Gas limit: eth_estimateGas with
        # 1.2x buffer; if estimate reverts (e.g. already-redeemed), route to
        # REVIEW_REQUIRED rather than RETRYING-loop forever.
        try:
            nonce_hex = self._rpc_call(
                self.polygon_rpc_url,
                "eth_getTransactionCount",
                [self.funder_address, "pending"],
            )
            nonce = int(str(nonce_hex), 16)
            gas_price_hex = self._rpc_call(
                self.polygon_rpc_url, "eth_gasPrice", []
            )
            gas_price = int(str(gas_price_hex), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"nonce/gasPrice fetch failed: {exc}",
                "condition_id": condition_id,
            }

        estimate_params = {
            "from": self.funder_address,
            "to": _to_address,
            "data": calldata,
        }
        try:
            gas_estimate_hex = self._rpc_call(
                self.polygon_rpc_url, "eth_estimateGas", [estimate_params]
            )
            gas_estimate = int(str(gas_estimate_hex), 16)
            # 1.2x buffer for variation; integer floor (conservative — always
            # <= true 1.2x, never over-estimates gas).
            gas_limit = (gas_estimate * 12) // 10
        except V2AdapterError as exc:
            # eth_estimateGas reverts when the call would fail on-chain
            # (already-redeemed, wrong index_sets, no balance). Surface as a
            # typed errorCode that settlement_commands routes to REVIEW_REQUIRED,
            # NOT RETRYING — re-broadcasting won't change the on-chain truth.
            return {
                "success": False,
                "errorCode": "REDEEM_GAS_ESTIMATE_REVERTED",
                "errorMessage": f"eth_estimateGas reverted: {exc}",
                "condition_id": condition_id,
            }
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_estimateGas failed: {exc}",
                "condition_id": condition_id,
            }

        tx = {
            "to": _to_address,
            "data": calldata,
            "value": 0,
            "chainId": int(self.chain_id),
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
        }

        try:
            from eth_account import Account

            signed = Account.sign_transaction(tx, self.signer_key)
            raw_hex = "0x" + signed.raw_transaction.hex().removeprefix("0x")
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_SIGN_FAILED",
                "errorMessage": f"sign_transaction failed: {exc}",
                "condition_id": condition_id,
            }

        # ── Dry-run gate (EOA path: standard CTF and negRisk EOA) ───────────────
        # Thread 2 fix: EOA path (signer_eoa == funder_address) previously
        # bypassed ZEUS_AUTONOMOUS_REDEEM_DRY_RUN and called eth_sendRawTransaction
        # unconditionally. The Safe wrap paths (_redeem_via_safe,
        # _redeem_via_negrisk_safe) already have this gate; this brings the EOA
        # path into alignment so ALL broadcast sites respect the dry-run flag.
        _eoa_dry_run = os.environ.get(AUTONOMOUS_REDEEM_DRY_RUN_ENV, "").lower() in (
            "1", "true", "yes", "on",
        )
        if _eoa_dry_run:
            import hashlib as _hashlib
            import logging as _logging
            _logger = _logging.getLogger(__name__)
            # SECURITY: never log or return the signed raw_tx_hex. A signed
            # raw transaction is a broadcastable payload; anyone with log or
            # DB read access could broadcast it and bypass the no-side-effect
            # gate. Log only non-sensitive metadata: tx-type label + calldata
            # length + a short fingerprint (first 16 hex chars of SHA-256).
            _dry_run_fingerprint = _hashlib.sha256(raw_hex.encode()).hexdigest()[:16]
            _logger.warning(
                "REDEEM_DRY_RUN_LOGGED funder_address=%s "
                "condition_id=%s neg_risk=%s raw_tx_hex_len=%d "
                "dry_run_fingerprint=%s tx_type=EOA",
                self.funder_address, condition_id, neg_risk,
                len(raw_hex), _dry_run_fingerprint,
            )
            return {
                "success": False,
                "errorCode": "REDEEM_DRY_RUN_LOGGED",
                "errorMessage": "dry-run mode: raw tx built+signed but not broadcast (EOA path)",
                "condition_id": condition_id,
                "dry_run_fingerprint": _dry_run_fingerprint,
                "neg_risk": neg_risk,
            }

        # ── Broadcast ────────────────────────────────────────────────────────
        try:
            tx_hash = self._rpc_call(
                self.polygon_rpc_url,
                "eth_sendRawTransaction",
                [raw_hex],
            )
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_BROADCAST_FAILED",
                "errorMessage": f"eth_sendRawTransaction failed: {exc}",
                "condition_id": condition_id,
            }

        # Validate tx_hash before returning success: a null/malformed JSON-RPC
        # result (result=null) would stringify to "None" and get persisted as
        # REDEEM_TX_HASHED with an unreconcilable bogus hash.
        tx_hash_str = str(tx_hash) if tx_hash is not None else None
        import re as _re
        if not tx_hash_str or not _re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash_str):
            return {
                "success": False,
                "errorCode": "REDEEM_INVALID_TX_HASH",
                "errorMessage": (
                    f"eth_sendRawTransaction returned non-hash result: {tx_hash!r}"
                ),
                "condition_id": condition_id,
            }

        return {
            "success": True,
            "tx_hash": tx_hash_str,
            "condition_id": condition_id,
            "index_sets": list(index_sets),
            "nonce": nonce,
            "gas_price": gas_price,
            "gas_limit": gas_limit,
        }

    def _redeem_via_safe(
        self,
        condition_id: str,
        index_sets: list[int],
        signer_eoa: str,
    ) -> dict[str, Any]:
        """Safe v1.3.0 execTransaction wrapper for autonomous redeem.

        Called when signature_type==2 AND signer_eoa != funder_address.
        The funder_address is the Safe proxy; signer_eoa is the Safe owner EOA.

        Pre-flight: VERSION, getOwners, nonce, EOA MATIC balance.
        Builds inner CTF calldata, wraps in execTransaction, signs, broadcasts
        (or dry-runs if ZEUS_AUTONOMOUS_REDEEM_DRY_RUN is truthy).
        """
        import logging
        import os

        from src.venue.safe_exec import (
            SAFE_V1_3_VERSION,
            build_exec_transaction_calldata,
            build_safe_tx_hash,
            sign_safe_tx,
        )

        logger = logging.getLogger(__name__)
        safe_address = self.funder_address
        dry_run = os.environ.get(AUTONOMOUS_REDEEM_DRY_RUN_ENV, "").lower() in (
            "1", "true", "yes", "on",
        )

        # ── Pre-flight 1: Safe VERSION ────────────────────────────────────────
        try:
            # Safe.VERSION() selector: keccak256('VERSION()')[:4] = 0xffa1ad74
            version_calldata = "0xffa1ad74"
            raw_version = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": version_calldata}, "latest"],
            )
            import eth_abi
            version_str = eth_abi.decode(
                ["string"], bytes.fromhex(str(raw_version).removeprefix("0x"))
            )[0]
        except Exception as exc:
            # RPC/network failure or ABI-decode error — NOT a semantic version
            # mismatch.  Use REDEEM_RPC_PRECHECK_FAILED so a network blip does
            # not quarantine a healthy Safe as "unsupported version".
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"VERSION() eth_call failed: {exc}",
                "condition_id": condition_id,
            }
        if version_str != SAFE_V1_3_VERSION:
            return {
                "success": False,
                "errorCode": "REDEEM_SAFE_VERSION_UNSUPPORTED",
                "errorMessage": (
                    f"Safe at {safe_address} reports VERSION={version_str!r}; "
                    f"expected {SAFE_V1_3_VERSION!r}"
                ),
                "condition_id": condition_id,
            }

        # ── Pre-flight 2: getOwners ───────────────────────────────────────────
        try:
            # Safe.getOwners() selector: keccak256('getOwners()')[:4] = 0xa0e67e2b
            owners_calldata = "0xa0e67e2b"
            raw_owners = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": owners_calldata}, "latest"],
            )
            owners_list = eth_abi.decode(
                ["address[]"], bytes.fromhex(str(raw_owners).removeprefix("0x"))
            )[0]
        except Exception as exc:
            # RPC failure — not a semantic ownership mismatch.
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"getOwners() eth_call failed: {exc}",
                "condition_id": condition_id,
            }
        owners_lower = [o.lower() for o in owners_list]
        if signer_eoa.lower() not in owners_lower:
            return {
                "success": False,
                "errorCode": "REDEEM_SAFE_OWNER_MISMATCH",
                "errorMessage": (
                    f"signer EOA {signer_eoa} not in Safe.getOwners() {owners_list}"
                ),
                "condition_id": condition_id,
            }

        # ── Pre-flight 3: Safe nonce ──────────────────────────────────────────
        try:
            # Safe.nonce() selector: keccak256('nonce()')[:4] = 0xaffed0e0
            nonce_calldata = "0xaffed0e0"
            raw_nonce = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": nonce_calldata}, "latest"],
            )
            safe_nonce = int(str(raw_nonce), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"Safe nonce() eth_call failed: {exc}",
                "condition_id": condition_id,
            }

        # ── Pre-flight 4: EOA MATIC balance ──────────────────────────────────
        _MATIC_FLOOR_WEI = 50_000_000_000_000_000  # 0.05 MATIC
        try:
            raw_balance = self._rpc_call(
                self.polygon_rpc_url,
                "eth_getBalance",
                [signer_eoa, "latest"],
            )
            eoa_balance_wei = int(str(raw_balance), 16)
        except Exception as exc:
            # RPC failure — not a semantic balance-too-low result.
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_getBalance failed for signer EOA: {exc}",
                "condition_id": condition_id,
            }
        if eoa_balance_wei < _MATIC_FLOOR_WEI:
            return {
                "success": False,
                "errorCode": "REDEEM_EOA_MATIC_INSUFFICIENT",
                "errorMessage": (
                    f"signer EOA {signer_eoa} has {eoa_balance_wei} wei MATIC; "
                    f"need >= {_MATIC_FLOOR_WEI} wei (0.05 MATIC)"
                ),
                "condition_id": condition_id,
            }

        # ── Build inner CTF calldata ──────────────────────────────────────────
        try:
            inner_calldata_hex = _build_redeem_calldata(condition_id, index_sets)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": f"inner CTF calldata build failed: {exc}",
                "condition_id": condition_id,
            }
        inner_data = bytes.fromhex(inner_calldata_hex.removeprefix("0x"))

        # ── Build Safe tx hash + sign ─────────────────────────────────────────
        try:
            safe_tx_hash_bytes = build_safe_tx_hash(
                safe_address=safe_address,
                chain_id=int(self.chain_id),
                to=POLYGON_CTF_ADDRESS,
                value=0,
                data=inner_data,
                operation=0,  # CALL
                nonce=safe_nonce,
            )
            signature = sign_safe_tx(safe_tx_hash_bytes, self.signer_key)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_SIGN_FAILED",
                "errorMessage": f"Safe sign failed: {exc}",
                "condition_id": condition_id,
            }

        # ── Build outer execTransaction calldata ─────────────────────────────
        try:
            exec_calldata = build_exec_transaction_calldata(
                to=POLYGON_CTF_ADDRESS,
                value=0,
                data=inner_data,
                operation=0,
                signatures=signature,
            )
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": f"execTransaction calldata build failed: {exc}",
                "condition_id": condition_id,
            }

        # ── EOA nonce + gas ───────────────────────────────────────────────────
        try:
            eoa_nonce_hex = self._rpc_call(
                self.polygon_rpc_url,
                "eth_getTransactionCount",
                [signer_eoa, "pending"],
            )
            eoa_nonce = int(str(eoa_nonce_hex), 16)
            gas_price_hex = self._rpc_call(
                self.polygon_rpc_url, "eth_gasPrice", []
            )
            gas_price = int(str(gas_price_hex), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"EOA nonce/gasPrice fetch failed: {exc}",
                "condition_id": condition_id,
            }

        estimate_params = {
            "from": signer_eoa,
            "to": safe_address,
            "data": exec_calldata,
        }
        try:
            gas_hex = self._rpc_call(
                self.polygon_rpc_url, "eth_estimateGas", [estimate_params]
            )
            gas_limit = (int(str(gas_hex), 16) * 12) // 10
        except V2AdapterError as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_GAS_ESTIMATE_REVERTED",
                "errorMessage": f"eth_estimateGas reverted (Safe wrap): {exc}",
                "condition_id": condition_id,
            }
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_estimateGas failed (Safe wrap): {exc}",
                "condition_id": condition_id,
            }

        outer_tx = {
            "to": safe_address,
            "data": exec_calldata,
            "value": 0,
            "chainId": int(self.chain_id),
            "nonce": eoa_nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
        }

        try:
            from eth_account import Account

            signed = Account.sign_transaction(outer_tx, self.signer_key)
            raw_hex = "0x" + signed.raw_transaction.hex().removeprefix("0x")
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_SIGN_FAILED",
                "errorMessage": f"sign_transaction (outer EOA tx) failed: {exc}",
                "condition_id": condition_id,
            }

        # ── Dry-run gate ──────────────────────────────────────────────────────
        if dry_run:
            # SECURITY (codereview-may19 P0-2): never log or return the signed
            # raw_tx_hex. A signed raw transaction is a broadcastable payload;
            # any observer (logs, DB events, alerting collectors, backups) can
            # broadcast it and bypass the no-side-effect gate. Log only
            # non-sensitive metadata (tx_type label + length + short SHA-256
            # fingerprint) — matching the EOA dry-run path's secure pattern.
            import hashlib as _hashlib
            _dry_run_fingerprint = _hashlib.sha256(raw_hex.encode()).hexdigest()[:16]
            logger.warning(
                "REDEEM_DRY_RUN_LOGGED safe_address=%s safe_nonce=%d "
                "condition_id=%s raw_tx_hex_len=%d "
                "dry_run_fingerprint=%s tx_type=SAFE",
                safe_address, safe_nonce, condition_id,
                len(raw_hex), _dry_run_fingerprint,
            )
            return {
                "success": False,
                "errorCode": "REDEEM_DRY_RUN_LOGGED",
                "errorMessage": "dry-run mode: raw tx built+signed but not broadcast",
                "condition_id": condition_id,
                "dry_run_fingerprint": _dry_run_fingerprint,
                "safe_nonce": safe_nonce,
            }

        # ── Broadcast ─────────────────────────────────────────────────────────
        try:
            tx_hash = self._rpc_call(
                self.polygon_rpc_url,
                "eth_sendRawTransaction",
                [raw_hex],
            )
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_BROADCAST_FAILED",
                "errorMessage": f"eth_sendRawTransaction failed (Safe wrap): {exc}",
                "condition_id": condition_id,
            }

        import re as _re
        tx_hash_str = str(tx_hash) if tx_hash is not None else None
        if not tx_hash_str or not _re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash_str):
            return {
                "success": False,
                "errorCode": "REDEEM_INVALID_TX_HASH",
                "errorMessage": (
                    f"eth_sendRawTransaction returned non-hash result: {tx_hash!r}"
                ),
                "condition_id": condition_id,
            }

        return {
            "success": True,
            "tx_hash": tx_hash_str,
            "condition_id": condition_id,
            "index_sets": list(index_sets),
            "safe_nonce": safe_nonce,
            "eoa_nonce": eoa_nonce,
            "gas_price": gas_price,
            "gas_limit": gas_limit,
        }

    def _redeem_via_negrisk_safe(
        self,
        condition_id: str,
        index_sets: list[int],
        signer_eoa: str,
        amount: int,
    ) -> dict[str, Any]:
        """Safe v1.3.0 execTransaction wrapper for negRisk autonomous redeem.

        Mirrors _redeem_via_safe but targets POLYGON_NEGRISK_ADAPTER_ADDRESS
        with NEGRISK_REDEEM_POSITIONS_SELECTOR calldata instead of the
        standard CTF.

        Called when neg_risk=True AND signature_type==2 AND signer_eoa !=
        funder_address. The funder_address is the Safe proxy; signer_eoa is
        the Safe owner EOA.

        amount: token balance in micro-units (e.g. 1587297 for 1.587297 tokens).
        Derived from settlement_commands.token_amounts_json by submit_redeem().

        negRisk ABI: redeemPositions(bytes32 conditionId, uint256[] amounts)
        indexSet→slot mapping (verified on-chain 2026-05-19):
          indexSet=2 (YES) → amounts=[amount, 0]   (slot 0)
          indexSet=1 (NO)  → amounts=[0, amount]   (slot 1)
        Reference tx: 0x4ce58f2683bd81f7f49b7dd19e35cc2db4fc137c2f841712876ace23afe39448
        """
        import logging
        import os

        from src.venue.safe_exec import (
            SAFE_V1_3_VERSION,
            build_exec_transaction_calldata,
            build_safe_tx_hash,
            sign_safe_tx,
        )

        logger = logging.getLogger(__name__)
        safe_address = self.funder_address
        dry_run = os.environ.get(AUTONOMOUS_REDEEM_DRY_RUN_ENV, "").lower() in (
            "1", "true", "yes", "on",
        )

        # ── Pre-flight 1: Safe VERSION ────────────────────────────────────────
        try:
            version_calldata = "0xffa1ad74"
            raw_version = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": version_calldata}, "latest"],
            )
            import eth_abi
            version_str = eth_abi.decode(
                ["string"], bytes.fromhex(str(raw_version).removeprefix("0x"))
            )[0]
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"VERSION() eth_call failed (negRisk path): {exc}",
                "condition_id": condition_id,
            }
        if version_str != SAFE_V1_3_VERSION:
            return {
                "success": False,
                "errorCode": "REDEEM_SAFE_VERSION_UNSUPPORTED",
                "errorMessage": (
                    f"Safe at {safe_address} reports VERSION={version_str!r}; "
                    f"expected {SAFE_V1_3_VERSION!r}"
                ),
                "condition_id": condition_id,
            }

        # ── Pre-flight 2: getOwners ───────────────────────────────────────────
        try:
            owners_calldata = "0xa0e67e2b"
            raw_owners = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": owners_calldata}, "latest"],
            )
            owners_list = eth_abi.decode(
                ["address[]"], bytes.fromhex(str(raw_owners).removeprefix("0x"))
            )[0]
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"getOwners() eth_call failed (negRisk path): {exc}",
                "condition_id": condition_id,
            }
        owners_lower = [o.lower() for o in owners_list]
        if signer_eoa.lower() not in owners_lower:
            return {
                "success": False,
                "errorCode": "REDEEM_SAFE_OWNER_MISMATCH",
                "errorMessage": (
                    f"signer EOA {signer_eoa} not in Safe.getOwners() {owners_list}"
                ),
                "condition_id": condition_id,
            }

        # ── Pre-flight 3: Safe nonce ──────────────────────────────────────────
        try:
            nonce_calldata = "0xaffed0e0"
            raw_nonce = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": nonce_calldata}, "latest"],
            )
            safe_nonce = int(str(raw_nonce), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"Safe nonce() eth_call failed (negRisk path): {exc}",
                "condition_id": condition_id,
            }

        # ── Pre-flight 4: EOA MATIC balance ──────────────────────────────────
        _MATIC_FLOOR_WEI = 50_000_000_000_000_000  # 0.05 MATIC
        try:
            raw_balance = self._rpc_call(
                self.polygon_rpc_url,
                "eth_getBalance",
                [signer_eoa, "latest"],
            )
            eoa_balance_wei = int(str(raw_balance), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_getBalance failed for signer EOA (negRisk path): {exc}",
                "condition_id": condition_id,
            }
        if eoa_balance_wei < _MATIC_FLOOR_WEI:
            return {
                "success": False,
                "errorCode": "REDEEM_EOA_MATIC_INSUFFICIENT",
                "errorMessage": (
                    f"signer EOA {signer_eoa} has {eoa_balance_wei} wei MATIC; "
                    f"need >= {_MATIC_FLOOR_WEI} wei (0.05 MATIC)"
                ),
                "condition_id": condition_id,
            }

        # ── Build inner negRisk calldata ──────────────────────────────────────
        try:
            inner_calldata_hex = _build_negrisk_redeem_calldata(condition_id, index_sets, amount)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": f"inner negRisk calldata build failed: {exc}",
                "condition_id": condition_id,
            }
        inner_data = bytes.fromhex(inner_calldata_hex.removeprefix("0x"))

        # ── Build Safe tx hash + sign ─────────────────────────────────────────
        try:
            safe_tx_hash_bytes = build_safe_tx_hash(
                safe_address=safe_address,
                chain_id=int(self.chain_id),
                to=POLYGON_NEGRISK_ADAPTER_ADDRESS,
                value=0,
                data=inner_data,
                operation=0,  # CALL
                nonce=safe_nonce,
            )
            signature = sign_safe_tx(safe_tx_hash_bytes, self.signer_key)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_SIGN_FAILED",
                "errorMessage": f"Safe sign failed (negRisk path): {exc}",
                "condition_id": condition_id,
            }

        # ── Build outer execTransaction calldata ─────────────────────────────
        try:
            exec_calldata = build_exec_transaction_calldata(
                to=POLYGON_NEGRISK_ADAPTER_ADDRESS,
                value=0,
                data=inner_data,
                operation=0,
                signatures=signature,
            )
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": f"execTransaction calldata build failed (negRisk path): {exc}",
                "condition_id": condition_id,
            }

        # ── EOA nonce + gas ───────────────────────────────────────────────────
        try:
            eoa_nonce_hex = self._rpc_call(
                self.polygon_rpc_url,
                "eth_getTransactionCount",
                [signer_eoa, "pending"],
            )
            eoa_nonce = int(str(eoa_nonce_hex), 16)
            gas_price_hex = self._rpc_call(
                self.polygon_rpc_url, "eth_gasPrice", []
            )
            gas_price = int(str(gas_price_hex), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"EOA nonce/gasPrice fetch failed (negRisk path): {exc}",
                "condition_id": condition_id,
            }

        estimate_params = {
            "from": signer_eoa,
            "to": safe_address,
            "data": exec_calldata,
        }
        try:
            gas_hex = self._rpc_call(
                self.polygon_rpc_url, "eth_estimateGas", [estimate_params]
            )
            gas_limit = (int(str(gas_hex), 16) * 12) // 10
        except V2AdapterError as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_GAS_ESTIMATE_REVERTED",
                "errorMessage": f"eth_estimateGas reverted (negRisk Safe wrap): {exc}",
                "condition_id": condition_id,
            }
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_estimateGas failed (negRisk Safe wrap): {exc}",
                "condition_id": condition_id,
            }

        outer_tx = {
            "to": safe_address,
            "data": exec_calldata,
            "value": 0,
            "chainId": int(self.chain_id),
            "nonce": eoa_nonce,
            "gas": gas_limit,
            "gasPrice": gas_price,
        }

        try:
            from eth_account import Account

            signed = Account.sign_transaction(outer_tx, self.signer_key)
            raw_hex = "0x" + signed.raw_transaction.hex().removeprefix("0x")
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_SIGN_FAILED",
                "errorMessage": f"sign_transaction (outer EOA tx) failed (negRisk path): {exc}",
                "condition_id": condition_id,
            }

        # ── Dry-run gate ──────────────────────────────────────────────────────
        if dry_run:
            # SECURITY (codereview-may19 P0-2): never log or return the signed
            # raw_tx_hex (broadcastable payload). Log only metadata and a short
            # SHA-256 fingerprint, matching the EOA + Safe dry-run patterns.
            import hashlib as _hashlib
            _dry_run_fingerprint = _hashlib.sha256(raw_hex.encode()).hexdigest()[:16]
            logger.warning(
                "REDEEM_DRY_RUN_LOGGED safe_address=%s safe_nonce=%d "
                "condition_id=%s neg_risk=True raw_tx_hex_len=%d "
                "dry_run_fingerprint=%s tx_type=NEGRISK_SAFE",
                safe_address, safe_nonce, condition_id,
                len(raw_hex), _dry_run_fingerprint,
            )
            return {
                "success": False,
                "errorCode": "REDEEM_DRY_RUN_LOGGED",
                "errorMessage": "dry-run mode: raw tx built+signed but not broadcast (negRisk path)",
                "condition_id": condition_id,
                "dry_run_fingerprint": _dry_run_fingerprint,
                "safe_nonce": safe_nonce,
                "neg_risk": True,
            }

        # ── Broadcast ─────────────────────────────────────────────────────────
        try:
            tx_hash = self._rpc_call(
                self.polygon_rpc_url,
                "eth_sendRawTransaction",
                [raw_hex],
            )
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "REDEEM_BROADCAST_FAILED",
                "errorMessage": f"eth_sendRawTransaction failed (negRisk Safe wrap): {exc}",
                "condition_id": condition_id,
            }

        import re as _re
        tx_hash_str = str(tx_hash) if tx_hash is not None else None
        if not tx_hash_str or not _re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash_str):
            return {
                "success": False,
                "errorCode": "REDEEM_INVALID_TX_HASH",
                "errorMessage": (
                    f"eth_sendRawTransaction returned non-hash result: {tx_hash!r}"
                ),
                "condition_id": condition_id,
            }

        return {
            "success": True,
            "tx_hash": tx_hash_str,
            "condition_id": condition_id,
            "index_sets": list(index_sets),
            "neg_risk": True,
            "safe_nonce": safe_nonce,
            "eoa_nonce": eoa_nonce,
            "gas_price": gas_price,
            "gas_limit": gas_limit,
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
        _allow_compat_for_test: bool = False,
    ) -> SubmitResult:
        """Compatibility helper for legacy PolymarketClient.place_limit_order.

        This path exists until U1 wires executable market snapshots into the
        executor. It still produces a VenueSubmissionEnvelope before SDK contact,
        but its market identity fields are compatibility placeholders rather than
        U1-certified snapshot facts. The canonical request hash is computed from
        the final side/size values; the envelope is never mutated post-hash.
        """

        # T1F-COMPAT-SUBMIT-LIMIT-ORDER-REJECTS-OR-FAKE: block this path in live
        # mode (Q1 egress evidence present) unless the caller has explicitly opted
        # in via _allow_compat_for_test.  When evidence is absent, preflight will
        # reject with Q1_EGRESS_EVIDENCE_ABSENT as before — no double-gate needed.
        _evidence_present = (
            self.q1_egress_evidence_path is not None
            and Path(self.q1_egress_evidence_path).exists()
        )
        if _evidence_present and not _allow_compat_for_test:
            _cnt_inc("compat_submit_rejected_total")
            logger.warning(
                "telemetry_counter event=compat_submit_rejected_total path=submit_limit_order"
            )
            return self._compat_rejected_submit_result(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=order_type,
                error_code="COMPAT_SUBMIT_NOT_PERMITTED_IN_LIVE",
                error_message=(
                    "submit_limit_order is a compatibility shim; it must not be "
                    "called in live mode.  Wire U1 executable market snapshots into "
                    "the executor and call submit() with a live-bound envelope."
                ),
            )

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
            allow_unavailable_fee_details=True,
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
        allow_unavailable_fee_details: bool = False,
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
            fee_details=_canonical_fee_details_for_envelope(
                sdk_snapshot.fee_details,
                allow_unavailable=allow_unavailable_fee_details,
            ),
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
        if not callable(fee_rate_fn):
            raise V2AdapterError("SDK client does not expose get_fee_rate_bps for legacy submit compatibility")
        fee_rate_bps = fee_rate_fn(token_id) if callable(fee_rate_fn) else None
        if fee_rate_bps is None:
            raise V2AdapterError("SDK client returned no fee_rate_bps for legacy submit compatibility")
        fee_details = {
            "bps": int(fee_rate_bps),
            "builder_fee_bps": 0,
            "source": "py-clob-client-v2",
            "token_id": token_id,
        }
        fee_details = canonicalize_fee_details(fee_details)
        return SimpleNamespace(
            tick_size=Decimal(str(tick_size_fn(token_id))),
            min_order_size=Decimal("0.01"),
            neg_risk=bool(neg_risk_fn(token_id)),
            fee_details=fee_details,
        )


def _validate_q1_egress_evidence(path: Path | str) -> PreflightResult:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return PreflightResult(
            ok=False,
            error_code="Q1_EGRESS_EVIDENCE_ABSENT",
            message=f"missing Q1 egress evidence: {evidence_path}",
        )
    if not evidence_path.is_file():
        return PreflightResult(
            ok=False,
            error_code="Q1_EGRESS_EVIDENCE_INVALID",
            message=f"Q1 egress evidence is not a file: {evidence_path}",
        )
    normalized_path = evidence_path.as_posix()
    for fragment in Q1_EGRESS_REJECTED_PATH_FRAGMENTS:
        if fragment in normalized_path:
            return PreflightResult(
                ok=False,
                error_code="Q1_EGRESS_EVIDENCE_INVALID",
                message=f"stale Q1 egress evidence path is not current authority: {evidence_path}",
            )
    try:
        text = evidence_path.read_text(encoding="utf-8")
    except Exception as exc:
        return PreflightResult(
            ok=False,
            error_code="Q1_EGRESS_EVIDENCE_INVALID",
            message=f"cannot read Q1 egress evidence {evidence_path}: {exc}",
        )
    missing = [marker for marker in Q1_EGRESS_REQUIRED_MARKERS if marker not in text]
    if missing:
        return PreflightResult(
            ok=False,
            error_code="Q1_EGRESS_EVIDENCE_INVALID",
            message=(
                f"Q1 egress evidence {evidence_path} is missing required marker(s): "
                + ", ".join(missing)
            ),
        )
    return PreflightResult(ok=True)


def _canonical_fee_details_for_envelope(value: Any, *, allow_unavailable: bool = False) -> dict[str, Any]:
    details = dict(value or {})
    if not details:
        if not allow_unavailable:
            return canonicalize_fee_details(details)
        return {}
    try:
        return canonicalize_fee_details(details)
    except MarketSnapshotMismatchError:
        fee_fields = (
            "fee_rate_fraction",
            "feeRate",
            "fee_rate",
            "fee_rate_bps",
            "feeRateBps",
            "base_fee",
            "baseFee",
            "bps",
        )
        if not allow_unavailable or any(details.get(field) is not None for field in fee_fields):
            raise
        return details


def _sdk_version() -> str:
    try:
        return importlib.metadata.version("py-clob-client-v2")
    except importlib.metadata.PackageNotFoundError:
        return "uninstalled"


def _api_creds_from_env() -> Any | None:
    api_key = os.environ.get("POLYMARKET_API_KEY")
    api_secret = os.environ.get("POLYMARKET_API_SECRET")
    api_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE")
    if not (api_key and api_secret and api_passphrase):
        return None
    from py_clob_client_v2.clob_types import ApiCreds

    return ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )


def _normalize_signature_type(value: Any) -> int:
    try:
        signature_type = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"signature_type must be an integer, got {value!r}") from exc
    if signature_type not in {0, 1, 2, 3}:
        raise ValueError(f"unsupported CLOB V2 signature_type={signature_type}")
    return signature_type


def _collateral_allowance_contracts(chain_id: int) -> tuple[str, tuple[str, str]]:
    if int(chain_id) == 137:
        return (
            POLYGON_PUSD_ADDRESS,
            (POLYGON_EXCHANGE_V2_ADDRESS, POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS),
        )
    try:
        from py_clob_client_v2.config import get_contract_config

        config = get_contract_config(chain_id)
        return (
            str(config.collateral),
            (str(config.exchange_v2), str(config.neg_risk_exchange_v2)),
        )
    except Exception as exc:
        raise V2AdapterError(f"unsupported chain_id for allowance fallback: {chain_id}") from exc


def _abi_address(address: str) -> str:
    normalized = str(address).lower().removeprefix("0x")
    if len(normalized) != 40:
        raise ValueError(f"invalid EVM address {address!r}")
    int(normalized, 16)
    return normalized.rjust(64, "0")


def _eth_call_uint(
    rpc_url: str,
    rpc_call: Callable[[str, str, list[Any]], Any],
    *,
    to: str,
    data: str,
) -> int:
    raw = rpc_call(rpc_url, "eth_call", [{"to": to, "data": data}, "latest"])
    return int(str(raw or "0x0"), 16)


def _json_rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={
            "content-type": "application/json",
            "user-agent": "zeus-readonly/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        decoded = json.loads(response.read())
    if "error" in decoded:
        raise V2AdapterError(f"polygon rpc error: {decoded['error']}")
    return decoded.get("result")


def _normalize_condition_id_bytes32(condition_id: str) -> bytes:
    """Coerce a hex condition_id to canonical bytes32. PR-I.5.c.

    Settlement_commands stores condition_id as the hex string Polymarket
    returns (with or without 0x prefix). The CTF ABI expects raw bytes32.
    """
    raw = condition_id.removeprefix("0x").removeprefix("0X")
    if len(raw) != 64:
        raise ValueError(
            f"condition_id must be a 32-byte hex (got {len(raw)} chars): {condition_id!r}"
        )
    try:
        return bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(
            f"condition_id is not valid hex: {condition_id!r}"
        ) from exc


def _build_redeem_calldata(condition_id: str, index_sets: list[int]) -> str:
    """ABI-encode CTF redeemPositions calldata. PR-I.5.c.

    ``redeemPositions(address collateralToken, bytes32 parentCollectionId,
                      bytes32 conditionId, uint256[] indexSets)``

    parentCollectionId is the zero word for top-level positions (no nested
    conditions). collateralToken is the pUSD Polygon address.

    Returns hex-encoded calldata starting with the selector (0x01b7037c).
    """
    from eth_abi import encode as _abi_encode

    if not isinstance(index_sets, (list, tuple)) or not index_sets:
        raise ValueError(f"index_sets must be a non-empty list, got {index_sets!r}")
    coerced: list[int] = []
    for entry in index_sets:
        coerced.append(int(entry))
        if int(entry) <= 0:
            raise ValueError(f"index_sets entries must be positive uint256, got {entry!r}")

    collateral = bytes.fromhex(POLYGON_PUSD_ADDRESS.removeprefix("0x"))
    if len(collateral) != 20:
        raise ValueError("POLYGON_PUSD_ADDRESS is not a valid 20-byte address")
    parent_collection_id = b"\x00" * 32
    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    encoded_args = _abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            "0x" + collateral.hex(),
            parent_collection_id,
            condition_bytes,
            coerced,
        ],
    )
    return CTF_REDEEM_POSITIONS_SELECTOR + encoded_args.hex()


def _build_negrisk_redeem_calldata(
    condition_id: str, index_sets: list[int], amount: int
) -> str:
    """ABI-encode NegRiskCtfAdapter redeemPositions calldata.

    ``redeemPositions(bytes32 conditionId, uint256[] amounts)``

    amounts is always length-2 for binary YES/NO markets. The slot mapping
    is verified on-chain 2026-05-19 via Karachi eth_call probes:

      indexSet=2 (YES won) → amounts=[amount, 0]   (slot 0 = YES)
      indexSet=1 (NO won)  → amounts=[0, amount]   (slot 1 = NO)

    amount: token balance in micro-units (e.g. 1587297 for 1.587297 tokens).
    Derived from settlement_commands.token_amounts_json by submit_redeem().

    Reference tx:
      0x4ce58f2683bd81f7f49b7dd19e35cc2db4fc137c2f841712876ace23afe39448

    Karachi test vector (condition_id=c5faddf4...44ae, amount=1587297,
    indexSet=2):
      0xdbeccb23 + conditionId + 0x40 + 0x02 + 0x183861 + 0x00
    This is byte-for-byte verified against the on-chain successful call.

    Raises ValueError for non-binary index_sets (not 1 or 2).
    """
    from eth_abi import encode as _abi_encode

    if not isinstance(index_sets, (list, tuple)) or not index_sets:
        raise ValueError(f"index_sets must be a non-empty list, got {index_sets!r}")
    if len(index_sets) != 1:
        raise ValueError(
            f"negRisk redeem requires exactly one index_set entry (binary market); "
            f"got {len(index_sets)} entries: {index_sets!r}"
        )
    entry = int(index_sets[0])
    if entry not in (1, 2):
        raise ValueError(
            f"negRisk binary markets only support indexSet=1 (NO) or indexSet=2 (YES); "
            f"got indexSet={entry!r}"
        )
    amount_int = int(amount)
    if amount_int <= 0:
        raise ValueError(f"amount must be positive, got {amount!r}")

    if entry == 2:
        # YES won: YES token at slot 0
        amounts_array: list[int] = [amount_int, 0]
    else:
        # NO won: NO token at slot 1
        amounts_array = [0, amount_int]

    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    encoded_args = _abi_encode(
        ["bytes32", "uint256[]"],
        [condition_bytes, amounts_array],
    )
    selector = bytes.fromhex(NEGRISK_REDEEM_POSITIONS_SELECTOR.removeprefix("0x"))
    return "0x" + (selector + encoded_args).hex()


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


def _order_payload(order_id: str) -> Any:
    try:
        from py_clob_client_v2.clob_types import OrderPayload

        return OrderPayload(orderID=order_id)
    except Exception:
        return SimpleNamespace(orderID=order_id)


def _cancel_result_from_response(order_id: str, raw: Any) -> CancelResult:
    if isinstance(raw, str) and raw.strip():
        normalized_order_id = raw.strip()
        return CancelResult(
            status="CANCELED",
            order_id=normalized_order_id,
            raw_response_json=_canonical_json({"orderID": normalized_order_id, "status": "CANCELED"}),
        )
    raw_dict = dict(raw or {}) if isinstance(raw, dict) else {"raw": _to_jsonish(raw)}
    error_code, error_message = _response_error(raw_dict)
    not_canceled = raw_dict.get("not_canceled", raw_dict.get("not_cancelled"))
    if error_code or error_message or _nonempty(not_canceled) or raw_dict.get("success") is False:
        return CancelResult(
            status="NOT_CANCELED",
            order_id=order_id,
            raw_response_json=_canonical_json(raw_dict),
            error_code=error_code,
            error_message=error_message or _reason_from(not_canceled, "cancel_not_canceled"),
        )
    canceled = raw_dict.get("canceled", raw_dict.get("cancelled"))
    status = str(raw_dict.get("status") or raw_dict.get("state") or "").upper()
    if _nonempty(canceled) or status in {"CANCELED", "CANCELLED", "CANCEL_CONFIRMED"} or raw_dict.get("success") is True:
        return CancelResult(
            status="CANCELED",
            order_id=_extract_order_id(raw_dict) or order_id,
            raw_response_json=_canonical_json(raw_dict),
        )
    return CancelResult(
        status="UNKNOWN",
        order_id=order_id,
        raw_response_json=_canonical_json(raw_dict),
        error_message="unrecognized_cancel_response",
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


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return bool(value)
    if isinstance(value, dict):
        return bool(value)
    try:
        return bool(list(value))
    except TypeError:
        return bool(value)


def _reason_from(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, dict):
        parts = [f"{key}: {item}" for key, item in value.items()]
        return "; ".join(parts) if parts else fallback
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or fallback
    return str(value) or fallback


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


def _string_sequence_from_value(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return (text,) if text else ()
    if isinstance(value, dict):
        for key in ("id", "trade_id", "tradeID", "tradeId", "hash", "tx_hash", "transactionHash"):
            item = value.get(key)
            if item not in (None, ""):
                text = str(item).strip()
                return (text,) if text else ()
        return ()
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            items.extend(_string_sequence_from_value(item))
        return tuple(items)
    return ()


def _extract_string_sequence(raw: Any, *keys: str) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        return ()
    for key in keys:
        values = _string_sequence_from_value(raw.get(key))
        if values:
            return values
    return ()


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
        trade_ids=_extract_string_sequence(
            raw_response,
            "tradeIDs",
            "tradeIds",
            "trade_ids",
            "associate_trades",
            "trades",
        ),
        transaction_hashes=_extract_string_sequence(
            raw_response,
            "transactionsHashes",
            "transactionHashes",
            "transaction_hashes",
            "txHashes",
            "tx_hashes",
        ),
    )
    return SubmitResult(status="accepted", envelope=updated)


def _ctf_balance_units(value: Any) -> int:
    try:
        return int((Decimal(str(value or "0")) * Decimal("1000000")).to_integral_value(rounding=ROUND_FLOOR))
    except Exception:
        return 0


def _micro_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(Decimal(str(value))))
    except Exception:
        return None
