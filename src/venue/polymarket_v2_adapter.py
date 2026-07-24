# Created: 2026-04-27
# Last reused/audited: 2026-07-23
# Authority basis (2026-06-12): operator law 2026-06-10 ABSOLUTE — redeem submission
#   FORBIDDEN. redeem() now raises REDEEM_SUBMISSION_FORBIDDEN unconditionally; the
#   autonomous web3 broadcast body (eth_sendRawTransaction EOA path) was DELETED.
#   External deep-review finding 2026-06-12 (residual override + broadcast path).
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#                  + 2026-06-04 M5 mutex-IO antibody: venue read entrypoints
#                    (get_order/get_open_orders/get_trades/get_positions) and the
#                    on-chain _json_rpc_call assert assert_no_world_mutex_held_for_io
#                    so blocking I/O under the world write lock fails loud, not wedges.
#                  + 2026-07-23 pre-POST deterministic signed-order identity callback.
"""Polymarket CLOB V2 adapter.

This module is the only R3 Z2 surface that may import py_clob_client_v2. It
pins provenance in VenueSubmissionEnvelope while tolerating one-step and
two-step SDK order submission shapes.
"""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from src.contracts.execution_intent import ExecutionIntent
from src.contracts.semantic_types import Direction
from src.contracts.executable_market_snapshot import (
    MarketSnapshotMismatchError,
    canonicalize_fee_details,
)
from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
from src.contracts.freshness_registry import FreshnessLevel, registry as _freshness_registry
from src.venue.response_contracts import (
    VenueOrderNotFound,
    VenueResponseShapeError,
    extract_order_id as _extract_order_id,
    extract_response_error as _response_error,
    parse_cancel_outcome,
    parse_order_status,
)
from src.observability.counters import increment as _cnt_inc
from src.state.db import assert_no_world_mutex_held_for_io as _assert_no_world_mutex_held_for_io
from src.venue.batch_submit import (
    CANCEL_ECHO_CANDIDATE_FIELDS,
    MAX_ORDERS_PER_BATCH,
    SUBMIT_ECHO_CANDIDATE_FIELDS,
    map_batch_items,
    map_cancel_envelope,
)

logger = logging.getLogger(__name__)

_DERIVED_API_CREDS_CACHE: dict[tuple[str, int, str, int, str], Any] = {}
_ABSOLUTE_LIVE_PRICE_MIN = Decimal("0.05")
_ABSOLUTE_LIVE_PRICE_MAX = Decimal("0.95")


def _assert_absolute_live_price_before_sdk(price: Decimal | str | float) -> Decimal:
    """Independent final SDK-boundary guard; no live order may bypass it."""

    try:
        value = price if isinstance(price, Decimal) else Decimal(str(price))
    except Exception as exc:
        raise ValueError(f"live SDK order price must be decimal, got {price!r}") from exc
    if not value.is_finite():
        raise ValueError(f"live SDK order price must be finite, got {price!r}")
    if not _ABSOLUTE_LIVE_PRICE_MIN <= value <= _ABSOLUTE_LIVE_PRICE_MAX:
        raise ValueError(
            "live SDK order price outside absolute inclusive [0.05, 0.95] submit band: "
            f"price={value}"
        )
    return value

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
# USDC.e ERC-20 on Polygon mainnet.
# Source: Polygon bridge canonical address; on-chain bytecode verified 2026-05-19.
POLYGON_USDCE_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
# V2 CollateralOnramp on Polygon mainnet (post-2026-04-28 architecture).
# Owner = 0x47ebfac3353314c788b96cdcbf41daadfe03629c (same as pUSD owner — confirms legitimacy).
# wrap(address _asset, address _to, uint256 _amount) selector 0x62355638.
# VERIFIED on-chain: tx 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a
#   block 87167823, 1.587297 USDC.e → 1.587297 pUSD at Safe. arg layout confirmed.
POLYGON_COLLATERAL_ONRAMP_ADDRESS = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
# Deprecated V1 WCOL wrapper — owner-locked to NegRiskAdapter; direct user calls revert
# with custom error 0x5fc483c5 (GS013 on every estimateGas). Left for back-compat only.
# Reference tx proving V1 broken: every estimateGas → GS013 (2026-05-20).
POLYGON_PUSD_WRAPPER_ADDRESS = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"  # Deprecated V1 — use POLYGON_COLLATERAL_ONRAMP_ADDRESS
# ERC-20 approve(spender,amount) selector.
ERC20_APPROVE_SELECTOR = "0x095ea7b3"
# CollateralOnramp wrap(address _asset, address _to, uint256 _amount) selector.
# VERIFIED on-chain 2026-05-20 via tx 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a.
COLLATERAL_ONRAMP_WRAP_SELECTOR = "0x62355638"
# Deprecated V1 selector — kept to avoid breaking any stale import references.
PUSD_WRAP_SELECTOR = "0xbf376c7a"  # Deprecated V1 — use COLLATERAL_ONRAMP_WRAP_SELECTOR
# Kill switch for autonomous wrap. Default OFF (empty string → dry_run=False = live).
# Set ZEUS_AUTONOMOUS_WRAP_DRY_RUN=1 to enable dry-run (build+sign, skip broadcast).
AUTONOMOUS_WRAP_DRY_RUN_ENV = "ZEUS_AUTONOMOUS_WRAP_DRY_RUN"

# W2.4: CTF split/merge/convert selectors. Verified locally via eth_utils.keccak
# against each function's canonical source signature, and CROSS-VALIDATED by
# recomputing CTF_REDEEM_POSITIONS_SELECTOR / NEGRISK_REDEEM_POSITIONS_SELECTOR
# above with the identical methodology — both reproduce the already
# on-chain-verified pinned values byte-for-byte, confirming the methodology
# before it is trusted for these new selectors. On-chain bytecode verification
# of split/merge/convert on THIS deployment is deferred to operator dry-run
# before first live use — the same posture already declared for
# POLYGON_CTF_ADDRESS/POLYGON_NEGRISK_ADAPTER_ADDRESS themselves (lines above).
#
# splitPosition(address collateralToken, bytes32 parentCollectionId,
#               bytes32 conditionId, uint256[] partition, uint256 amount)
# keccak256(...)[:4]. Source: gnosis/conditional-tokens-contracts
# ConditionalTokens.sol (github.com/gnosis/conditional-tokens-contracts/
# blob/master/contracts/ConditionalTokens.sol) — the standard CTF ABI already
# used by CTF_REDEEM_POSITIONS_SELECTOR above.
CTF_SPLIT_POSITION_SELECTOR = "0x72ce4275"
# mergePositions(address,bytes32,bytes32,uint256[],uint256) — same signature
# shape as splitPosition, same source. This selector is IDENTICAL on standard
# CTF (POLYGON_CTF_ADDRESS) and NegRiskAdapter (POLYGON_NEGRISK_ADAPTER_ADDRESS):
# both contracts expose this exact 5-arg mergePositions signature. Independently
# cross-checked against the NegRiskAdapter Go contract bindings (pkg.go.dev/
# github.com/ivanzzeth/polymarket-go-contracts/contracts/neg-risk-adapter),
# which report method 0x9e7212ad for this same signature.
CTF_MERGE_POSITIONS_SELECTOR = "0x9e7212ad"
# NegRiskAdapter convenience splitPosition(bytes32 conditionId, uint256 amount) —
# splits directly against NegRiskAdapter's own wcol collateral for a single
# negRisk-routed condition (no explicit collateral/parent/partition args).
# Source: Polymarket/neg-risk-ctf-adapter src/NegRiskAdapter.sol
# (github.com/Polymarket/neg-risk-ctf-adapter). Selector computed locally via
# keccak256('splitPosition(bytes32,uint256)')[:4]; no third-party binding
# exposes this overload's selector directly (only the 5-arg form and
# mergePositions' 2-arg overload are visible in the Go bindings — see
# NEGRISK_MERGE_POSITIONS_SELECTOR), so this one relies on the source-signature
# cross-check alone until operator dry-run confirms it on-chain.
NEGRISK_SPLIT_POSITION_SELECTOR = "0xa3d7da1d"
# NegRiskAdapter convenience mergePositions(bytes32 conditionId, uint256 amount).
# Same source as NEGRISK_SPLIT_POSITION_SELECTOR. Cross-checked against Go
# bindings "MergePositions0", method 0xb10c5c17 for signature
# mergePositions(bytes32,uint256).
NEGRISK_MERGE_POSITIONS_SELECTOR = "0xb10c5c17"
# NegRiskAdapter convertPositions(bytes32 marketId, uint256 indexSet, uint256 amount).
#
# CONVENTION ANTIBODY (2026-07-02): indexSet here is a MULTI-MARKET bitfield —
# NatSpec: "the least significant bit is the first question (index zero)";
# bit SET = NO position, bit UNSET = YES position. This is a COMPLETELY
# DIFFERENT convention from the per-condition CTF outcome-slot bitmask used by
# redeemPositions/splitPosition/mergePositions elsewhere in this file (see
# _zeus_index_set_to_ctf_bitmask) — do NOT reuse that translation for
# convert's indexSet; it would silently select the wrong sibling markets.
#
# marketId is NegRiskIdLib.getMarketId(questionId) — NOT the standard
# conditionId used everywhere else in this file. This module does not derive
# marketId from questionId (that replicates NegRiskIdLib.sol's hashing scheme,
# out of scope for this inert packet); callers supply it explicitly.
#
# Source: Polymarket/neg-risk-ctf-adapter src/NegRiskAdapter.sol. Cross-checked
# against Go contract bindings method 0xc64748c4 for signature
# convertPositions(bytes32,uint256,uint256).
NEGRISK_CONVERT_POSITIONS_SELECTOR = "0xc64748c4"
# Kill switch for autonomous CTF split/merge/convert (all three — brand-new,
# never-before-existing capability; INERT this packet, no production caller).
# Default OFF returns CTF_CONVERSION_DISABLED without any chain I/O, mirroring
# AUTONOMOUS_REDEEM_ENABLED_ENV's posture for a not-yet-trusted autonomous
# chain-write path (unlike AUTONOMOUS_WRAP_DRY_RUN_ENV, which defaults to live
# because wrap was already promoted to production use).
AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV = "ZEUS_AUTONOMOUS_CTF_CONVERSION_ENABLED"
# Second-layer dry-run gate (build+sign, skip broadcast) — same shape as
# AUTONOMOUS_WRAP_DRY_RUN_ENV. Only consulted once the ENABLED switch above is on.
AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV = "ZEUS_AUTONOMOUS_CTF_CONVERSION_DRY_RUN"


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
class SignedIdentityPersistenceReceipt:
    """Canonical read-back returned only after the signed identity commit."""

    command_id: str
    envelope_id: str
    order_id: str
    signed_order_hash: str
    canonical_pre_sign_payload_hash: str
    raw_request_hash: str


_SIGNED_IDENTITY_RECEIPT_LOCK = threading.Lock()
_SIGNED_IDENTITY_RECEIPTS: dict[int, SignedIdentityPersistenceReceipt] = {}


def _issue_signed_identity_persistence_receipt(
    conn,
    *,
    command_id: str,
    envelope_id: str,
) -> SignedIdentityPersistenceReceipt:
    """Issue a one-shot POST capability only from a canonical SQLite read-back."""

    row = conn.execute(
        """
        SELECT command.command_id,
               signed.envelope_id,
               signed.order_id,
               signed.signed_order_hash,
               signed.canonical_pre_sign_payload_hash,
               signed.raw_request_hash
          FROM venue_commands command
          JOIN venue_submission_envelopes signed
            ON signed.envelope_id = ?
           AND signed.order_id = command.venue_order_id
         WHERE command.command_id = ?
           AND command.state = 'SUBMITTING'
        """,
        (envelope_id, command_id),
    ).fetchone()
    if row is None:
        raise V2AdapterError(
            "committed signed identity failed canonical read-back"
        )
    receipt = SignedIdentityPersistenceReceipt(
        command_id=str(row[0] or ""),
        envelope_id=str(row[1] or ""),
        order_id=str(row[2] or ""),
        signed_order_hash=str(row[3] or ""),
        canonical_pre_sign_payload_hash=str(row[4] or ""),
        raw_request_hash=str(row[5] or ""),
    )
    with _SIGNED_IDENTITY_RECEIPT_LOCK:
        _SIGNED_IDENTITY_RECEIPTS[id(receipt)] = receipt
    return receipt


def _consume_signed_identity_persistence_receipt(
    receipt: object,
) -> SignedIdentityPersistenceReceipt:
    """Atomically consume an issuer-registered receipt; public copies are inert."""

    with _SIGNED_IDENTITY_RECEIPT_LOCK:
        issued = _SIGNED_IDENTITY_RECEIPTS.pop(id(receipt), None)
    if issued is not receipt:
        raise V2AdapterError(
            "signed identity receipt was not issued by canonical read-back gateway"
        )
    return issued


class AmbiguousSubmitError(RuntimeError):
    """POST crossed the venue boundary without a trustworthy acknowledgement."""

    def __init__(self, message: str, *, envelope: VenueSubmissionEnvelope) -> None:
        super().__init__(message)
        self.envelope = envelope


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

    def submit(
        self,
        envelope: VenueSubmissionEnvelope,
        *,
        before_post: Callable[
            [VenueSubmissionEnvelope], SignedIdentityPersistenceReceipt
        ]
        | None = None,
    ) -> SubmitResult: ...

    def cancel(self, order_id: str) -> CancelResult: ...

    def submit_batch(self, envelopes: list[VenueSubmissionEnvelope]) -> list[SubmitResult]: ...

    def cancel_batch(self, order_ids: list[str]) -> list[CancelResult]: ...

    def get_order(self, order_id: str) -> OrderState: ...

    def get_open_orders(self, filter: OpenOrdersFilter | None = None) -> list[OrderState]: ...

    def get_trades(self, since: Optional[str] = None) -> list[TradeFact]: ...

    def get_positions(self) -> list[PositionFact]: ...

    def get_pusd_balance_micro(self) -> int: ...

    def get_collateral_payload(self) -> dict[str, Any]: ...

    def get_ctf_collateral_payload(self, *, token_ids: list[str]) -> dict[str, Any]: ...

    def get_balance(self, conn=None) -> Any: ...

    def redeem(
        self,
        condition_id: str,
        *,
        index_sets: list[int] | None = None,
    ) -> dict[str, Any]: ...

    def split_positions(
        self,
        condition_id: str,
        amount_micro: int,
        *,
        safe_address: str,
        signer_eoa: str,
        neg_risk: bool = False,
    ) -> dict[str, Any]: ...

    def merge_positions(
        self,
        condition_id: str,
        amount_micro: int,
        *,
        safe_address: str,
        signer_eoa: str,
        neg_risk: bool = False,
    ) -> dict[str, Any]: ...

    def convert_positions(
        self,
        market_id: str,
        index_set: int,
        amount_micro: int,
        *,
        safe_address: str,
        signer_eoa: str,
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
        network_timeout_seconds: float | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.funder_address = funder_address
        self.signer_key = signer_key
        self.api_creds = api_creds
        self.chain_id = chain_id
        self.signature_type = _normalize_signature_type(signature_type)
        self.polygon_rpc_url = polygon_rpc_url
        self.network_timeout_seconds = (
            float(network_timeout_seconds)
            if network_timeout_seconds is not None and float(network_timeout_seconds) > 0
            else None
        )
        if rpc_call is None:
            self._rpc_call = lambda rpc_url, method, params: _json_rpc_call(
                rpc_url,
                method,
                params,
                timeout_seconds=self._network_timeout(20.0),
            )
        else:
            self._rpc_call = rpc_call
        self.builder_code = builder_code
        self.q1_egress_evidence_path = q1_egress_evidence_path
        self._client_factory = client_factory or self._default_client_factory
        self._client = None
        self.sdk_version = sdk_version or _sdk_version()

    def _network_timeout(self, default: float) -> float:
        if self.network_timeout_seconds is None:
            return default
        return max(0.01, float(self.network_timeout_seconds))

    def _default_client_factory(self, **kwargs: Any) -> Any:
        from py_clob_client_v2.client import ClobClient

        # 2026-05-31: bound py_clob_client_v2's process-wide httpx client.
        # faulthandler proof: get_order()/balance reads hung 15+ min on an SSL
        # read (http2 half-open stall) — the library default
        # `httpx.Client(http2=True)` did NOT enforce its timeout on a stalled
        # read, freezing exchange_reconcile.refresh_unresolved_reconcile_findings
        # and starving the EDLI reactor's per-cycle _refresh_pending_family_snapshots
        # (→ 0 receipts). A bounded HTTP/1.1 client makes a stalled venue read
        # RAISE within ~15 s instead of hanging the scheduler cycle indefinitely.
        # Idempotent: re-assigning the module global is safe (helpers.request()
        # looks it up per call); non-fatal if the library layout changes.
        try:
            import httpx as _httpx
            from py_clob_client_v2.http_helpers import helpers as _pcc_helpers

            sdk_timeout = kwargs.get("network_timeout_seconds")
            if sdk_timeout is not None:
                sdk_timeout = max(0.01, float(sdk_timeout))
                timeout = _httpx.Timeout(sdk_timeout, connect=sdk_timeout)
            else:
                timeout = _httpx.Timeout(15.0, connect=8.0)
            _pcc_helpers._http_client = _httpx.Client(http2=False, timeout=timeout)
        except Exception:  # noqa: BLE001 - non-fatal; library default retained
            pass

        explicit_api_creds = kwargs.get("api_creds")
        effective_api_creds = explicit_api_creds
        if effective_api_creds is None:
            effective_api_creds = _cached_derived_api_creds(
                host=kwargs["host"],
                chain_id=kwargs["chain_id"],
                signer_key=kwargs.get("signer_key"),
                signature_type=kwargs.get("signature_type", DEFAULT_SIGNATURE_TYPE),
                funder_address=kwargs.get("funder_address"),
            )
        if effective_api_creds is None:
            effective_api_creds = _api_creds_from_runtime()

        client = ClobClient(
            kwargs["host"],
            kwargs["chain_id"],
            key=kwargs.get("signer_key"),
            creds=effective_api_creds,
            signature_type=kwargs.get("signature_type", DEFAULT_SIGNATURE_TYPE),
            funder=kwargs.get("funder_address"),
            use_server_time=True,
        )
        # CLOB v2 L2 endpoints (balance/order/user-channel auth) require L2 API
        # creds bound to the active signer. Use runtime creds first: Keychain is
        # the operator-owned source of truth, env is a fallback for non-Keychain
        # runtimes. Only derive when neither runtime surface has complete creds.
        # Prefer the SDK's pure derive endpoint when derivation is needed:
        # create_or_derive
        # attempts POST /auth/api-key before deriving, which logs a persistent
        # upstream 400 for accounts that already have signer-bound credentials.
        if explicit_api_creds is None and effective_api_creds is None:
            try:
                derived_api_creds = _derive_l2_api_creds(client)
                client.set_api_creds(derived_api_creds)
                _store_derived_api_creds(
                    host=kwargs["host"],
                    chain_id=kwargs["chain_id"],
                    signer_key=kwargs.get("signer_key"),
                    signature_type=kwargs.get("signature_type", DEFAULT_SIGNATURE_TYPE),
                    funder_address=kwargs.get("funder_address"),
                    api_creds=derived_api_creds,
                )
                logger.warning(
                    "VENUE_AUTH_FALLBACK_TRIGGERED: signer-bound L2 API creds derived "
                    "(no cached signer-bound L2 creds); L2 calls proceeding via derived creds",
                )
            except Exception as exc:  # pragma: no cover - upstream SDK behaviour
                runtime_api_creds = _api_creds_from_runtime()
                if runtime_api_creds is None:
                    logger.warning(
                        "signer-bound L2 API credential derivation failed; L2-authenticated calls will "
                        "fail until creds are provided: %s", exc,
                    )
                else:
                    client.set_api_creds(runtime_api_creds)
                    logger.warning(
                        "VENUE_AUTH_STATIC_FALLBACK_TRIGGERED: signer-bound L2 API credential derivation "
                        "failed; using runtime CLOB creds and deferring validity "
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
                network_timeout_seconds=self.network_timeout_seconds,
            )
        return self._client

    def _refresh_signer_bound_l2_api_creds(self, client: Any) -> None:
        set_api_creds = getattr(client, "set_api_creds", None)
        if not callable(set_api_creds):
            raise V2AdapterError("SDK client does not expose set_api_creds")
        api_creds = _derive_l2_api_creds(client)
        set_api_creds(api_creds)
        _store_derived_api_creds(
            host=self.host,
            chain_id=self.chain_id,
            signer_key=self.signer_key,
            signature_type=self.signature_type,
            funder_address=self.funder_address,
            api_creds=api_creds,
        )
        logger.warning(
            "VENUE_AUTH_RUNTIME_CREDS_REFRESHED: runtime L2 creds failed auth; "
            "re-derived signer-bound L2 creds and retried once",
        )

    def preflight(self) -> PreflightResult:
        if self.q1_egress_evidence_path is not None:
            evidence_result = _validate_q1_egress_evidence(self.q1_egress_evidence_path)
            if not evidence_result.ok:
                return evidence_result
        max_attempts = max(
            1,
            int(float(os.environ.get("ZEUS_V2_PREFLIGHT_MAX_ATTEMPTS", "2") or "2")),
        )
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                client = self._sdk_client()
                get_ok = getattr(client, "get_ok", None)
                if callable(get_ok):
                    get_ok()
                if attempt > 1:
                    logger.warning(
                        "VENUE_PREFLIGHT_RECOVERED_AFTER_RETRY: attempt=%s max_attempts=%s",
                        attempt,
                        max_attempts,
                    )
                return PreflightResult(ok=True)
            except Exception as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    break
                time.sleep(min(0.25 * attempt, 1.0))
        return PreflightResult(
            ok=False,
            error_code="V2_PREFLIGHT_FAILED",
            message=str(last_exc) if last_exc is not None else "unknown preflight failure",
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

    def _bind_runtime_submission_envelope(
        self,
        envelope: VenueSubmissionEnvelope,
    ) -> VenueSubmissionEnvelope:
        envelope_funder = str(envelope.funder_address or "").strip()
        adapter_funder = str(self.funder_address or "").strip()
        if envelope_funder in {"", "UNRESOLVED_PRE_SUBMIT_FUNDER"}:
            raise ValueError("submission envelope missing pre-bound funder_address")
        if envelope_funder.lower() != adapter_funder.lower():
            raise ValueError(
                "submission envelope funder_address does not match adapter funder_address"
            )
        return envelope.with_updates(
            sdk_version=self.sdk_version,
            host=self.host,
            chain_id=self.chain_id,
        )

    def submit(
        self,
        envelope: VenueSubmissionEnvelope,
        *,
        before_post: Callable[
            [VenueSubmissionEnvelope], SignedIdentityPersistenceReceipt
        ]
        | None = None,
    ) -> SubmitResult:
        # T1F-ADAPTER-ASSERTS-LIVE-BOUND-BEFORE-SDK: reject placeholder envelopes
        # before any SDK call.  Mirror: src/data/polymarket_client.py:407-424.
        try:
            _assert_absolute_live_price_before_sdk(envelope.price)
            envelope = self._bind_runtime_submission_envelope(envelope)
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
        if not callable(before_post):
            return _rejected_submit_result(
                envelope,
                error_code="SIGNED_IDENTITY_PERSISTER_REQUIRED",
                error_message=(
                    "live submission requires durable signed order identity "
                    "persistence before venue POST"
                ),
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
        expected_order_id = None
        post_started = False

        def _submit_once(active_client: Any) -> Any:
            nonlocal signed_order, signed_hash, expected_order_id, post_started
            signed_order = None
            signed_hash = None
            expected_order_id = None
            post_started = False
            create_order = getattr(active_client, "create_order", None)
            post_order = getattr(active_client, "post_order", None)
            if callable(create_order) and callable(post_order):
                local_signed_order = create_order(order_args, options=options)
                signed_bytes = _signed_order_bytes(local_signed_order)
                signed_hash = hashlib.sha256(signed_bytes).hexdigest()
                signed_order = signed_bytes
                expected_order_id = _deterministic_v2_order_id(
                    active_client,
                    local_signed_order,
                    chain_id=self.chain_id,
                    neg_risk=envelope.neg_risk,
                )
                _assert_final_fok_depth_bound(active_client, envelope)
                if not expected_order_id:
                    raise V2AdapterError(
                        "signed order has no deterministic venue order id"
                    )
                signed_envelope = envelope.with_updates(
                    signed_order=signed_order,
                    signed_order_hash=signed_hash,
                    order_id=expected_order_id,
                    captured_at=datetime.now(timezone.utc).isoformat(),
                )
                receipt = before_post(signed_envelope)
                if not isinstance(receipt, SignedIdentityPersistenceReceipt):
                    raise V2AdapterError(
                        "signed identity persister returned no canonical read-back receipt"
                    )
                receipt = _consume_signed_identity_persistence_receipt(receipt)
                receipt_values = (
                    receipt.command_id,
                    receipt.envelope_id,
                    receipt.order_id,
                    receipt.signed_order_hash,
                    receipt.canonical_pre_sign_payload_hash,
                    receipt.raw_request_hash,
                )
                if not all(str(value or "").strip() for value in receipt_values):
                    raise V2AdapterError(
                        "signed identity persistence receipt is incomplete"
                    )
                if (
                    receipt.order_id != signed_envelope.order_id
                    or receipt.signed_order_hash
                    != signed_envelope.signed_order_hash
                    or receipt.canonical_pre_sign_payload_hash
                    != signed_envelope.canonical_pre_sign_payload_hash
                    or receipt.raw_request_hash != signed_envelope.raw_request_hash
                ):
                    raise V2AdapterError(
                        "signed identity persistence receipt does not match signed order"
                    )
                post_started = True
                return post_order(
                    local_signed_order,
                    order_type=envelope.order_type,
                    post_only=envelope.post_only,
                    defer_exec=False,
                )
            create_and_post = getattr(active_client, "create_and_post_order", None)
            if callable(create_and_post):
                raise V2AdapterError(
                    "pre-POST signed identity persistence requires two-step SDK submit"
                )
            raise V2AdapterError(
                "SDK client exposes neither two-step nor one-step order submission"
            )

        if not (
            callable(getattr(client, "create_order", None))
            and callable(getattr(client, "post_order", None))
        ) and not callable(getattr(client, "create_and_post_order", None)):
            return _rejected_submit_result(
                envelope,
                error_code="V2_SUBMIT_UNSUPPORTED",
                error_message="SDK client exposes neither one-step nor two-step order submission",
            )

        try:
            raw_response = _submit_once(client)
        except Exception as exc:
            if post_started and _is_polymarket_geoblock_403_error(exc):
                return _rejected_submit_result(
                    envelope,
                    error_code="venue_rejected_geoblock_403",
                    error_message=str(exc),
                    signed_order=signed_order,
                    signed_order_hash=signed_hash,
                )
            if post_started and _is_polymarket_invalid_safe_signature_error(exc):
                logger.error(
                    "VENUE_ORDER_SIGNATURE_REJECTED: deterministic invalid Safe "
                    "order signature; not retrying through L2 credential refresh"
                )
                return _rejected_submit_result(
                    envelope,
                    error_code="venue_auth_invalid_signature_400",
                    error_message=str(exc),
                    signed_order=signed_order,
                    signed_order_hash=signed_hash,
                )
            if (
                post_started
                and envelope.order_type == "FOK"
                and _is_polymarket_fok_killed_error(exc)
            ):
                rejected = envelope.with_updates(
                    signed_order=signed_order,
                    signed_order_hash=signed_hash,
                    order_id=expected_order_id,
                    error_code="venue_fok_not_fully_filled_400",
                    error_message=str(exc),
                )
                return SubmitResult(
                    status="rejected",
                    envelope=rejected,
                    error_code=rejected.error_code,
                    error_message=rejected.error_message,
                )
            if (
                post_started
                and str(envelope.order_type).upper() == "FAK"
                and _is_polymarket_fak_no_match_error(exc)
            ):
                rejected = envelope.with_updates(
                    signed_order=signed_order,
                    signed_order_hash=signed_hash,
                    order_id=expected_order_id,
                    error_code="venue_fak_no_match_400",
                    error_message=str(exc),
                )
                return SubmitResult(
                    status="rejected",
                    envelope=rejected,
                    error_code=rejected.error_code,
                    error_message=rejected.error_message,
                )
            if not post_started:
                error_code = (
                    "SUBMIT_ABORTED_PRICE_MOVED"
                    if str(exc).startswith("SUBMIT_ABORTED_PRICE_MOVED:")
                    else "V2_PRE_SUBMIT_EXCEPTION"
                )
                return _rejected_submit_result(
                    envelope,
                    error_code=error_code,
                    error_message=str(exc),
                    signed_order=signed_order,
                    signed_order_hash=signed_hash,
                )
            ambiguous = envelope.with_updates(
                signed_order=signed_order,
                signed_order_hash=signed_hash,
                order_id=expected_order_id,
                error_code="V2_POST_SUBMIT_AMBIGUOUS",
                error_message=str(exc),
            )
            raise AmbiguousSubmitError(str(exc), envelope=ambiguous) from exc

        response_order_id = _extract_order_id(raw_response)
        explicit_rejection = isinstance(raw_response, dict) and raw_response.get("success") is False
        if expected_order_id and not explicit_rejection:
            if not response_order_id or str(response_order_id).lower() != expected_order_id.lower():
                detail = (
                    "submit response missing deterministic order id"
                    if not response_order_id
                    else "submit response order id does not match locally signed order"
                )
                ambiguous = envelope.with_updates(
                    signed_order=signed_order,
                    signed_order_hash=signed_hash,
                    raw_response_json=_canonical_json(raw_response or {}),
                    order_id=expected_order_id,
                    error_code="V2_ORDER_ID_ACK_MISMATCH",
                    error_message=detail,
                )
                raise AmbiguousSubmitError(detail, envelope=ambiguous)
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
        return _cancel_result_from_response(order_id, raw, check_envelope=True)

    def submit_batch(self, envelopes: list[VenueSubmissionEnvelope]) -> list[SubmitResult]:
        """Submit up to MAX_ORDERS_PER_BATCH orders in ONE SDK post_orders call.

        W2.1 (inert, no production call site). Chunking beyond
        MAX_ORDERS_PER_BATCH is the CALLER's job (src.venue.batch_submit.
        chunk_orders / src.execution.batch_order_submission) -- this method
        refuses an oversized call rather than silently splitting it, so
        callers cannot lose the INV-28 persist-before-side-effect pairing
        that must happen per chunk.

        Mirrors submit()'s pre-flight shape: envelopes are bound + asserted
        live-authority first (no SDK contact on a placeholder envelope). A
        signing failure (create_order) for ANY envelope aborts the WHOLE
        call before any network contact -- no partial submission of an
        unsigned order. If the post_orders() HTTP call itself raises AFTER
        all orders are signed, this method does NOT catch it (mirrors
        submit()'s _submit_once: the exception propagates so the caller can
        record the AMBIGUOUS side effect -- signing succeeded, the network
        outcome is unknown -- as SUBMIT_TIMEOUT_UNKNOWN, matching the
        single-order executor.py:4697-4759 pattern).
        """
        if not envelopes:
            return []
        if len(envelopes) > MAX_ORDERS_PER_BATCH:
            raise ValueError(
                f"submit_batch: {len(envelopes)} orders exceeds MAX_ORDERS_PER_BATCH="
                f"{MAX_ORDERS_PER_BATCH}; caller must chunk "
                f"(see src.venue.batch_submit.chunk_orders)"
            )

        bound: list[VenueSubmissionEnvelope] = []
        for envelope in envelopes:
            try:
                _assert_absolute_live_price_before_sdk(envelope.price)
                bound_envelope = self._bind_runtime_submission_envelope(envelope)
                bound_envelope.assert_live_submit_bound()
            except ValueError as exc:
                _cnt_inc("placeholder_envelope_blocked_total")
                logger.warning(
                    "telemetry_counter event=placeholder_envelope_blocked_total path=submit_batch"
                )
                return [
                    _rejected_submit_result(
                        e,
                        error_code="BOUND_ENVELOPE_NOT_LIVE_AUTHORITY",
                        error_message=str(exc),
                    )
                    for e in envelopes
                ]
            bound.append(bound_envelope)

        post_only_values = {bool(e.post_only) for e in bound}
        if len(post_only_values) > 1:
            # post_orders() takes ONE post_only flag for the whole call
            # (py_clob_client_v2/client.py:840) -- envelopes with mixed
            # post_only cannot share a batch. Fail closed rather than
            # silently applying one envelope's flag to all.
            return [
                _rejected_submit_result(
                    e,
                    error_code="BATCH_POST_ONLY_MISMATCH",
                    error_message="submit_batch requires all envelopes to share one post_only value",
                )
                for e in envelopes
            ]
        batch_post_only = next(iter(post_only_values), False)

        try:
            preflight = self.preflight()
        except Exception as exc:
            return [
                _rejected_submit_result(e, error_code="V2_PREFLIGHT_EXCEPTION", error_message=str(exc))
                for e in envelopes
            ]
        if not preflight.ok:
            return [
                _rejected_submit_result(
                    e,
                    error_code=preflight.error_code or "V2_PREFLIGHT_FAILED",
                    error_message=preflight.message,
                )
                for e in envelopes
            ]

        client = self._sdk_client()
        if not callable(getattr(client, "create_order", None)) or not callable(
            getattr(client, "post_orders", None)
        ):
            return [
                _rejected_submit_result(
                    e,
                    error_code="V2_BATCH_SUBMIT_UNSUPPORTED",
                    error_message="SDK client exposes neither create_order nor post_orders",
                )
                for e in envelopes
            ]

        signed_orders: list[Optional[bytes]] = [None] * len(bound)
        signed_hashes: list[Optional[str]] = [None] * len(bound)
        post_orders_args: list[Any] = []
        try:
            from py_clob_client_v2.clob_types import PostOrdersV2Args

            for i, envelope in enumerate(bound):
                order_args = _order_args_from_envelope(envelope)
                options = SimpleNamespace(tick_size=str(envelope.tick_size), neg_risk=envelope.neg_risk)
                local_signed_order = client.create_order(order_args, options=options)
                signed_bytes = _signed_order_bytes(local_signed_order)
                signed_hashes[i] = hashlib.sha256(signed_bytes).hexdigest()
                signed_orders[i] = signed_bytes
                post_orders_args.append(PostOrdersV2Args(order=local_signed_order, orderType=envelope.order_type))
        except Exception as exc:
            # Pre-network signing failure -- no side effect crossed for
            # ANY envelope in this call. Reject the whole batch (matches
            # single-order submit()'s V2_PRE_SUBMIT_EXCEPTION when
            # signed_order is None).
            return [
                _rejected_submit_result(
                    e,
                    error_code="V2_PRE_SUBMIT_EXCEPTION",
                    error_message=str(exc),
                    signed_order=signed_orders[i] if i < len(signed_orders) else None,
                    signed_order_hash=signed_hashes[i] if i < len(signed_hashes) else None,
                )
                for i, e in enumerate(envelopes)
            ]

        try:
            for envelope in bound:
                _assert_final_fok_depth_bound(client, envelope)
        except Exception as exc:
            error_code = (
                "SUBMIT_ABORTED_PRICE_MOVED"
                if str(exc).startswith("SUBMIT_ABORTED_PRICE_MOVED:")
                else "V2_PRE_SUBMIT_EXCEPTION"
            )
            return [
                _rejected_submit_result(
                    e,
                    error_code=error_code,
                    error_message=str(exc),
                    signed_order=signed_orders[i],
                    signed_order_hash=signed_hashes[i],
                )
                for i, e in enumerate(envelopes)
            ]

        # Deliberately NOT wrapped in try/except: a post-signing exception
        # here is an AMBIGUOUS side effect (venue may have received the
        # request). Propagate so the caller records SUBMIT_TIMEOUT_UNKNOWN
        # for every command in this chunk, mirroring executor.py's
        # single-order post-submit exception handling.
        raw_response = client.post_orders(post_orders_args, post_only=batch_post_only, defer_exec=False)

        mapped = map_batch_items(
            raw_response,
            echo_keys=signed_hashes,
            echo_candidate_fields=SUBMIT_ECHO_CANDIDATE_FIELDS,
        )
        results: list[SubmitResult] = []
        for i, envelope in enumerate(bound):
            item = mapped[i]
            if item.source == "unmapped":
                results.append(
                    _unmapped_submit_result(
                        envelope,
                        signed_order=signed_orders[i],
                        signed_order_hash=signed_hashes[i],
                        raw_response=raw_response,
                    )
                )
            else:
                results.append(
                    _submit_result_from_response(
                        envelope,
                        item.raw_item,
                        signed_order=signed_orders[i],
                        signed_order_hash=signed_hashes[i],
                    )
                )
        return results

    def cancel_batch(self, order_ids: list[str]) -> list[CancelResult]:
        """Cancel up to MAX_ORDERS_PER_BATCH orders in ONE SDK cancel_orders call.

        W2.1 (inert, no production call site). Same chunking contract as
        submit_batch: refuses an oversized call, caller chunks.
        """
        if not order_ids:
            return []
        if len(order_ids) > MAX_ORDERS_PER_BATCH:
            raise ValueError(
                f"cancel_batch: {len(order_ids)} orders exceeds MAX_ORDERS_PER_BATCH="
                f"{MAX_ORDERS_PER_BATCH}; caller must chunk "
                f"(see src.venue.batch_submit.chunk_orders)"
            )
        client = self._sdk_client()
        cancel_orders = getattr(client, "cancel_orders", None)
        if not callable(cancel_orders):
            return [
                _unmapped_cancel_result(order_id, error_code="CANCEL_BATCH_UNSUPPORTED")
                for order_id in order_ids
            ]
        # Deliberately NOT wrapped in try/except -- mirrors submit_batch:
        # a post-call exception is an ambiguous side effect the caller must
        # record, not swallow into a false "rejected".
        raw_response = cancel_orders(list(order_ids))
        # Live-verified envelope shape first (2026-07-05): DELETE /orders
        # returns one {"canceled": [...], "not_canceled": {...}} dict for
        # the whole batch, not a per-item array. Fall through to the
        # per-item-array mapper only when the envelope shape is absent.
        mapped = map_cancel_envelope(raw_response, list(order_ids))
        if mapped is None:
            mapped = map_batch_items(
                raw_response,
                echo_keys=list(order_ids),
                echo_candidate_fields=CANCEL_ECHO_CANDIDATE_FIELDS,
            )
        results: list[CancelResult] = []
        for i, order_id in enumerate(order_ids):
            item = mapped[i]
            if item.source == "unmapped":
                results.append(_unmapped_cancel_result(order_id, raw_response=raw_response))
            else:
                results.append(_cancel_result_from_response(order_id, item.raw_item))
        return results

    def get_order(self, order_id: str) -> OrderState:
        _assert_no_world_mutex_held_for_io("venue.get_order")
        raw = self._sdk_client().get_order(order_id)
        if raw is None or raw == {}:
            raise VenueOrderNotFound(order_id)
        raw_dict = _normalize_v2_amount_response(
            dict(raw or {}),
            endpoint="get_order",
        )
        outcome = parse_order_status(raw_dict, fallback_order_id=order_id, endpoint="get_order")
        raw_dict["_v2_wire_status"] = str(
            raw_dict.get("status") or raw_dict.get("state") or ""
        )
        raw_dict["status"] = outcome.status
        raw_dict["_venue_order_status"] = outcome.status
        return OrderState(order_id=outcome.order_id, status=outcome.status, raw=raw_dict)

    def get_open_orders(self, filter: OpenOrdersFilter | None = None) -> list[OrderState]:
        _assert_no_world_mutex_held_for_io("venue.get_open_orders")
        client = self._sdk_client()
        get_orders = getattr(client, "get_open_orders", None)
        if not callable(get_orders):
            get_orders = getattr(client, "get_orders", None)
        if not callable(get_orders):
            raise V2ReadUnavailable(
                "SDK client exposes neither get_open_orders nor get_orders; "
                "open-order absence is unknown"
            )
        try:
            raw = get_orders(only_first_page=False)
        except TypeError:
            raw = get_orders()
        if isinstance(raw, dict):
            raw = raw.get("data", []) or []
        states: list[OrderState] = []
        for item in raw:
            item_dict = _normalize_v2_amount_response(
                dict(item),
                endpoint="get_open_orders",
            )
            outcome = parse_order_status(item_dict, fallback_order_id="", endpoint="get_open_orders")
            item_dict["_v2_wire_status"] = str(
                item_dict.get("status") or item_dict.get("state") or ""
            )
            item_dict["status"] = outcome.status
            item_dict["_venue_order_status"] = outcome.status
            states.append(OrderState(order_id=outcome.order_id, status=outcome.status, raw=item_dict))
        return states

    def get_trades(self, since: Optional[str] = None) -> list[TradeFact]:
        _assert_no_world_mutex_held_for_io("venue.get_trades")
        get_trades = getattr(self._sdk_client(), "get_trades", None)
        if not callable(get_trades):
            raise V2ReadUnavailable("SDK client does not expose get_trades; trade absence is unknown")
        try:
            raw = get_trades(only_first_page=False) or []
        except TypeError:
            raw = get_trades() or []
        return [TradeFact(raw=dict(item)) for item in raw]

    def get_positions(self) -> list[PositionFact]:
        _assert_no_world_mutex_held_for_io("venue.get_positions")
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
            with urllib.request.urlopen(request, timeout=self._network_timeout(15.0)) as response:
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

    def _pusd_collateral_payload_from_raw(
        self,
        raw: dict[str, Any],
        *,
        allow_chain_allowance_fallback: bool = True,
    ) -> dict[str, Any]:
        pusd_allowance_raw = raw.get("allowance")
        allowance_int = _micro_int_or_none(pusd_allowance_raw)
        authority_tier = "VENUE"
        allowance_source = "clob_balance_allowance"
        chain_allowance = (
            self._chain_collateral_allowance_micro()
            if allow_chain_allowance_fallback
            else None
        )
        if chain_allowance is not None:
            # The CLOB balance/allowance response is a cache and can briefly
            # retain the pre-fill remainder after a successful BUY. Direct
            # ERC20 allowance across every venue spender is current executable
            # truth, including when the stale cache is non-zero.
            pusd_allowance_raw = chain_allowance
            authority_tier = "CHAIN"
            allowance_source = "chain_erc20_allowance"
        elif allowance_int is None:
            pusd_allowance_raw = None
            authority_tier = "DEGRADED"
            allowance_source = "missing"
        elif allowance_int == 0:
            pusd_allowance_raw = allowance_int
            authority_tier = "DEGRADED"
            allowance_source = "chain_erc20_unavailable_clob_zero"

        return {
            "pusd_balance_micro": raw.get("balance", 0),
            "pusd_allowance_micro": pusd_allowance_raw if pusd_allowance_raw is not None else 0,
            "usdc_e_legacy_balance_micro": 0,
            "ctf_token_balances_units": {},
            "ctf_token_allowances_units": {},
            "authority_tier": authority_tier,
            "signature_type": self.signature_type,
            "pusd_allowance_source": allowance_source,
        }

    def get_pusd_collateral_payload(
        self,
        *,
        refresh_allowance: bool = True,
        allow_chain_allowance_fallback: bool | None = None,
    ) -> dict[str, Any]:
        """Return pUSD balance/allowance facts without CTF position enumeration.

        ``refresh_allowance`` controls the optional CLOB cache-update request.
        Chain allowance is independent current truth, so callers may skip that
        request while retaining the direct ERC20 allowance fallback.
        """

        raw = self._collateral_balance_allowance_raw(refresh_allowance=refresh_allowance)
        if allow_chain_allowance_fallback is None:
            allow_chain_allowance_fallback = refresh_allowance
        return self._pusd_collateral_payload_from_raw(
            raw,
            allow_chain_allowance_fallback=allow_chain_allowance_fallback,
        )

    def get_collateral_payload(self) -> dict[str, Any]:
        """Return SDK-derived collateral facts for CollateralLedger.refresh().

        All py_clob_client_v2 imports stay confined to this adapter. The state
        ledger receives plain dictionaries and never depends on SDK types.
        """

        raw = self._collateral_balance_allowance_raw(refresh_allowance=True)
        payload = self._pusd_collateral_payload_from_raw(raw)
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

        payload["ctf_token_balances_units"] = balances
        payload["ctf_token_allowances_units"] = allowances
        return payload

    def get_ctf_collateral_payload(self, *, token_ids: list[str]) -> dict[str, Any]:
        """Return pUSD facts plus CTF balance/allowance only for requested tokens.

        Exit submit needs inventory proof for the token being sold, not a full
        wallet fanout across every weather position. Full enumeration can exceed
        the submit deadline and then leave the sell path reading a pUSD-only
        snapshot. This targeted surface keeps sell preflight exact while making
        its runtime proportional to the order being submitted.
        """

        raw = self._collateral_balance_allowance_raw(refresh_allowance=True)
        payload = self._pusd_collateral_payload_from_raw(raw)
        balances: dict[str, int] = {}
        allowances: dict[str, int] = {}
        for token_id in dict.fromkeys(str(t or "").strip() for t in token_ids):
            if not token_id:
                continue
            conditional_raw = self._conditional_balance_allowance_raw(token_id)
            conditional_balance_units = _micro_int_or_none(conditional_raw.get("balance"))
            balance_units = conditional_balance_units if conditional_balance_units is not None else 0
            balances[token_id] = balance_units
            allowance_raw = conditional_raw.get("allowance")
            allowance_units: int
            if allowance_raw is not None:
                allowance_micro = _micro_int_or_none(allowance_raw)
                allowance_units = (
                    allowance_micro
                    if allowance_micro is not None
                    else _ctf_balance_units(allowance_raw)
                )
            elif conditional_balance_units is not None:
                allowance_units = conditional_balance_units
            else:
                allowance_units = 0
            allowances[token_id] = allowance_units
        payload["ctf_token_balances_units"] = balances
        payload["ctf_token_allowances_units"] = allowances
        payload["ctf_token_scope"] = "targeted"
        return payload

    def _collateral_balance_allowance_raw(self, *, refresh_allowance: bool = True) -> dict[str, Any]:
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

        def _read_once() -> Any:
            if refresh_allowance and callable(update_balance_allowance):
                update_balance_allowance(params)
            return get_balance_allowance(params)

        try:
            raw = _read_once()
        except Exception as exc:
            if not _is_l2_auth_error(exc):
                raise
            self._refresh_signer_bound_l2_api_creds(client)
            raw = _read_once()
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
        """FORBIDDEN — Zeus never submits redeem transactions (operator law 2026-06-10).

        This method UNCONDITIONALLY raises ``RedeemSubmissionAbandonedError``
        (a RuntimeError) before constructing anything. Redemption is EXTERNAL:
        third-party auto-redeem owns the shared wallet; Zeus keeps only the
        ACCOUNTING surfaces (reconcile_pending_redeems chain-receipt
        classification, EXTERNAL_REDEMPTION booking, USDC.e->pUSD wrap of
        proceeds). The former autonomous web3 broadcast body (calldata build,
        signing, eth_sendRawTransaction) was DELETED — the redeem broadcast call
        no longer exists in this entry point and cannot be re-armed by any env
        var, flag, or override.

        The kw-only signature (condition_id, index_sets, neg_risk,
        amount_per_slot) is retained so callers and tests still bind correctly
        before the raise fires.
        """

        # REDEEM SUBMISSION FORBIDDEN (operator law 2026-06-10, ABSOLUTE): the
        # redeem-transaction broadcast path is UNCONSTRUCTABLE from Zeus. This
        # raise is the first and only statement — no env, flag, or override
        # reaches calldata construction, signing, or eth_sendRawTransaction.
        # Redemption is EXTERNAL (third-party auto-redeem owns the shared
        # wallet); Zeus keeps only ACCOUNTING (reconcile_pending_redeems,
        # EXTERNAL_REDEMPTION booking, the wrap/sweep of proceeds).
        from src.execution.settlement_commands import (  # noqa: PLC0415 — lazy, avoids cycle
            assert_redeem_submission_allowed,
        )

        assert_redeem_submission_allowed("polymarket_v2_adapter.redeem")
        # Unreachable past this point: assert_redeem_submission_allowed raises
        # unconditionally. The legacy autonomous-broadcast body (calldata build,
        # Safe-wrap routing, eth_sendRawTransaction) was deleted so the broadcast
        # call no longer exists in this entry point. Retained helper methods
        # (_redeem_via_safe / _redeem_via_negrisk_safe) are dead production code,
        # kept only for their dry-run no-raw-tx-leak source-text antibody tests.
        raise AssertionError(  # pragma: no cover — defense in depth
            "unreachable: assert_redeem_submission_allowed must raise first"
        )

    def get_negrisk_winning_position_balance(
        self,
        condition_id: str,
        index_set: int,
        *,
        holder: str | None = None,
    ) -> dict[str, Any]:
        """Read the Safe's live ERC1155 balance of the winning negRisk position.

        Chain-truth provenance (2026-06-09, operator redeem directive): the
        recorded ``token_amounts_json`` amount is a SNAPSHOT taken at settlement
        time, not chain truth at submit time. The redeem amount/inputs MUST come
        from chain truth — a position that has already been redeemed (or never
        materialised under the recorded id) carries a stale recorded amount that
        exceeds the live balance, so the inner negRisk ``redeemPositions`` burn
        reverts → Safe execTransaction GS013.

        index_set uses the ZEUS convention (the same one stored in
        settlement_commands.winning_index_set and consumed by
        _build_negrisk_redeem_calldata): 2 = YES (slot 0), 1 = NO (slot 1).

        CONVENTION ANTIBODY (2026-06-09): the CTF contract's indexSet is a
        BITMASK over outcome slots — indexSet = 1 << slot, i.e. slot0/YES → 1,
        slot1/NO → 2 — the EXACT INVERSE of the Zeus binary labels. Passing the
        Zeus number straight into getCollectionId derives the OPPOSITE
        outcome's position. Verified on-chain 2026-06-09: the Safe's 7 real
        unredeemed winners (data-api positions) matched the derived positionId
        ONLY under ctf_index_set = 1 << outcome_slot; the pass-through version
        derived the losing token (balance 0) for every one of them. The mapping
        below is explicit and must never be "simplified" into a pass-through.

        Derivation (no web3 lib — uses self._rpc_call, the same urllib JSON-RPC
        seam every other on-chain read in this adapter uses):
          1. wcol = NegRiskAdapter.wcol()  (wrapped collateral; negRisk position
             ERC1155 ids derive from WCOL, NOT pUSD).
          2. ctf_index_set = 1 if Zeus index_set==2 (YES, slot0) else 2.
          3. collectionId = CTF.getCollectionId(0x00..00, conditionId, ctf_index_set).
          4. positionId   = CTF.getPositionId(wcol, collectionId).
          5. balance      = CTF.balanceOf(holder_safe, positionId).

        Returns a dict:
          {"ok": True, "balance_micro": int, "position_id": int,
           "wcol": str, "holder": str}
        on success, or {"ok": False, "errorCode": ..., "errorMessage": ...}
        on any RPC/derivation failure (caller fails closed: does NOT submit
        when balance cannot be established).
        """
        holder_addr = holder or self.funder_address
        if not self.polygon_rpc_url:
            return {
                "ok": False,
                "errorCode": "REDEEM_RPC_URL_MISSING",
                "errorMessage": "polygon_rpc_url required for chain-truth balance probe",
            }
        try:
            condition_bytes = _normalize_condition_id_bytes32(condition_id)
        except ValueError as exc:
            return {
                "ok": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": f"invalid condition_id for balance probe: {exc}",
            }
        if int(index_set) not in (1, 2):
            return {
                "ok": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": (
                    f"negRisk binary balance probe supports indexSet 1 (NO) or "
                    f"2 (YES); got {index_set!r}"
                ),
            }
        try:
            # 1. wcol()
            wcol_raw = _eth_call_uint(
                self.polygon_rpc_url,
                self._rpc_call,
                to=POLYGON_NEGRISK_ADAPTER_ADDRESS,
                data=NEGRISK_WCOL_SELECTOR,
            )
            wcol_addr = "0x" + format(wcol_raw, "040x")[-40:]
            # 2. Zeus label -> CTF bitmask: 2 (YES, slot0) -> 1<<0 = 1;
            #    1 (NO, slot1) -> 1<<1 = 2. See CONVENTION ANTIBODY above.
            ctf_index_set = 1 if int(index_set) == 2 else 2
            # 3. getCollectionId(parentCollectionId=0, conditionId, ctf_index_set)
            collection_data = (
                CTF_GET_COLLECTION_ID_SELECTOR
                + ("00" * 32)
                + condition_bytes.hex()
                + format(ctf_index_set, "064x")
            )
            collection_raw = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": POLYGON_CTF_ADDRESS, "data": collection_data}, "latest"],
            )
            collection_hex = str(collection_raw or "0x").removeprefix("0x").rjust(64, "0")[:64]
            # 3. getPositionId(collateralToken=wcol, collectionId)
            position_data = (
                CTF_GET_POSITION_ID_SELECTOR
                + _abi_address(wcol_addr)
                + collection_hex
            )
            position_id = _eth_call_uint(
                self.polygon_rpc_url,
                self._rpc_call,
                to=POLYGON_CTF_ADDRESS,
                data=position_data,
            )
            # 4. balanceOf(holder, positionId)
            balance_data = (
                ERC1155_BALANCE_OF_SELECTOR
                + _abi_address(holder_addr)
                + format(int(position_id), "064x")
            )
            balance_micro = _eth_call_uint(
                self.polygon_rpc_url,
                self._rpc_call,
                to=POLYGON_CTF_ADDRESS,
                data=balance_data,
            )
        except Exception as exc:  # noqa: BLE001 — any RPC failure → fail-closed
            return {
                "ok": False,
                "errorCode": "REDEEM_BALANCE_PROBE_FAILED",
                "errorMessage": f"chain-truth balance probe failed: {exc}",
                "condition_id": condition_id,
            }
        return {
            "ok": True,
            "balance_micro": int(balance_micro),
            "position_id": int(position_id),
            "wcol": wcol_addr,
            "holder": holder_addr,
            "zeus_index_set": int(index_set),
            "ctf_index_set": ctf_index_set,
        }

    def get_standard_ctf_winning_position_balance(
        self,
        condition_id: str,
        index_set: int,
        *,
        holder: str | None = None,
    ) -> dict[str, Any]:
        """Read the Safe's live ERC1155 balance of a winning STANDARD-CTF position.

        Standard-CTF analogue of get_negrisk_winning_position_balance, for
        non-negRisk markets (operator redeem directive 2026-06-10 — $19 stuck on
        a standard-CTF NO winner the negRisk-only sweep skipped forever).

        Difference from the negRisk probe: the position ERC1155 id derives from
        USDC.e collateral DIRECTLY (no NegRiskAdapter.wcol() indirection). For a
        standard-CTF position the data-api `asset` id IS this positionId, so the
        caller's chain-truth veto is `position_id == data-api asset AND balance>0`.

        index_set uses the SAME Zeus convention as the negRisk probe and
        settlement_commands.winning_index_set: 2 = YES (slot 0), 1 = NO (slot 1).
        The CTF indexSet bitmask is 1<<slot (Zeus 2 -> 1, Zeus 1 -> 2); see the
        CONVENTION ANTIBODY on _zeus_index_set_to_ctf_bitmask. Verified on-chain
        2026-06-10: the stuck NO winner (Zeus index 1) matched ONLY at CTF
        bitmask 2.

        Derivation (no web3 lib — uses self._rpc_call, the same urllib JSON-RPC
        seam every other on-chain read in this adapter uses):
          1. ctf_index_set = 1<<slot derived from the Zeus label.
          2. collectionId = CTF.getCollectionId(0x00..00, conditionId, ctf_index_set).
          3. positionId   = CTF.getPositionId(USDC.e, collectionId).
          4. balance      = CTF.balanceOf(holder_safe, positionId).

        Returns the same dict shape as the negRisk probe on success, or
        {"ok": False, "errorCode": ..., "errorMessage": ...} on any RPC/derivation
        failure (caller fails closed: does NOT submit when balance/identity
        cannot be established).
        """
        holder_addr = holder or self.funder_address
        if not self.polygon_rpc_url:
            return {
                "ok": False,
                "errorCode": "REDEEM_RPC_URL_MISSING",
                "errorMessage": "polygon_rpc_url required for chain-truth balance probe",
            }
        try:
            condition_bytes = _normalize_condition_id_bytes32(condition_id)
        except ValueError as exc:
            return {
                "ok": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": f"invalid condition_id for balance probe: {exc}",
            }
        try:
            # Zeus label -> CTF bitmask (1<<slot). Reuses the single source of
            # truth so the probe and the redeem calldata can never diverge.
            ctf_index_set = _zeus_index_set_to_ctf_bitmask(int(index_set))
        except ValueError as exc:
            return {
                "ok": False,
                "errorCode": "REDEEM_CALLDATA_BUILD_FAILED",
                "errorMessage": str(exc),
            }
        try:
            # 1. getCollectionId(parentCollectionId=0, conditionId, ctf_index_set)
            collection_data = (
                CTF_GET_COLLECTION_ID_SELECTOR
                + ("00" * 32)
                + condition_bytes.hex()
                + format(ctf_index_set, "064x")
            )
            collection_raw = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": POLYGON_CTF_ADDRESS, "data": collection_data}, "latest"],
            )
            collection_hex = str(collection_raw or "0x").removeprefix("0x").rjust(64, "0")[:64]
            # 2. getPositionId(collateralToken=USDC.e, collectionId)
            position_data = (
                CTF_GET_POSITION_ID_SELECTOR
                + _abi_address(POLYGON_USDCE_ADDRESS)
                + collection_hex
            )
            position_id = _eth_call_uint(
                self.polygon_rpc_url,
                self._rpc_call,
                to=POLYGON_CTF_ADDRESS,
                data=position_data,
            )
            # 3. balanceOf(holder, positionId)
            balance_data = (
                ERC1155_BALANCE_OF_SELECTOR
                + _abi_address(holder_addr)
                + format(int(position_id), "064x")
            )
            balance_micro = _eth_call_uint(
                self.polygon_rpc_url,
                self._rpc_call,
                to=POLYGON_CTF_ADDRESS,
                data=balance_data,
            )
        except Exception as exc:  # noqa: BLE001 — any RPC failure → fail-closed
            return {
                "ok": False,
                "errorCode": "REDEEM_BALANCE_PROBE_FAILED",
                "errorMessage": f"chain-truth balance probe failed: {exc}",
                "condition_id": condition_id,
            }
        return {
            "ok": True,
            "balance_micro": int(balance_micro),
            "position_id": int(position_id),
            "collateral": POLYGON_USDCE_ADDRESS,
            "holder": holder_addr,
            "zeus_index_set": int(index_set),
            "ctf_index_set": ctf_index_set,
        }

    # _redeem_via_safe / _redeem_via_negrisk_safe DELETED 2026-07-08 (R6-a):
    # dead redeem-submission broadcast machinery (zero production callers --
    # redeem() above raises RedeemSubmissionAbandonedError before either could
    # ever be reached). Both still built real eth_sendRawTransaction calldata
    # for a live CTF/negRisk redeemPositions broadcast, which the operator law
    # of 2026-06-10 (Zeus never submits redeem tx) forbids -- keeping
    # constructable-but-unreachable code was the actual residual risk this
    # packet closes. See src/execution/settlement_commands.py's
    # assert_redeem_submission_allowed for the permanent enforcement point.

    def _wrap_via_safe(
        self,
        safe_address: str,
        amount_micro: int,
        tx_kind: str,  # "APPROVE" or "WRAP"
        signer_eoa: str,
    ) -> dict[str, Any]:
        """Safe v1.3.0 execTransaction for USDC.e → pUSD two-step wrapping.

        tx_kind="APPROVE": calls USDC.e.approve(POLYGON_COLLATERAL_ONRAMP_ADDRESS, amount_micro)
        tx_kind="WRAP":    calls CollateralOnramp.wrap(USDCE, safe_address, amount_micro)

        VERIFIED on-chain 2026-05-20: wrap(address _asset, address _to, uint256 _amount)
        arg layout confirmed via tx 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a
        (block 87167823). pUSD landed at safe_address. V1 WCOL path (0x3A3BD7bb) deprecated.

        Returns dict with:
          success=True, tx_hash=<str>                       (live broadcast)
          success=False, errorCode="WRAP_DRY_RUN_LOGGED",
            dry_run_fingerprint=<16-hex>                   (dry-run mode)
          success=False, errorCode=<WRAP_*>                (error)
        """
        import hashlib as _hashlib
        import logging
        import os

        from src.venue.safe_exec import (
            SAFE_V1_3_VERSION,
            build_exec_transaction_calldata,
            build_safe_tx_hash,
            sign_safe_tx,
        )

        _logger = logging.getLogger(__name__)
        dry_run = os.environ.get(AUTONOMOUS_WRAP_DRY_RUN_ENV, "").lower() in (
            "1", "true", "yes", "on",
        )

        if tx_kind not in ("APPROVE", "WRAP"):
            return {
                "success": False,
                "errorCode": "WRAP_INVALID_TX_KIND",
                "errorMessage": f"tx_kind must be APPROVE or WRAP, got {tx_kind!r}",
                "tx_kind": tx_kind,
            }

        # ── Pre-flight 1: Safe VERSION ────────────────────────────────────────
        try:
            import eth_abi
            raw_version = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": "0xffa1ad74"}, "latest"],
            )
            version_str = eth_abi.decode(
                ["string"], bytes.fromhex(str(raw_version).removeprefix("0x"))
            )[0]
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_RPC_PRECHECK_FAILED",
                "errorMessage": f"VERSION() eth_call failed: {exc}",
                "tx_kind": tx_kind,
            }
        if version_str != SAFE_V1_3_VERSION:
            return {
                "success": False,
                "errorCode": "WRAP_SAFE_VERSION_UNSUPPORTED",
                "errorMessage": (
                    f"Safe at {safe_address} reports VERSION={version_str!r}; "
                    f"expected {SAFE_V1_3_VERSION!r}"
                ),
                "tx_kind": tx_kind,
            }

        # ── Pre-flight 2: getOwners ───────────────────────────────────────────
        try:
            raw_owners = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": "0xa0e67e2b"}, "latest"],
            )
            owners_list = eth_abi.decode(
                ["address[]"], bytes.fromhex(str(raw_owners).removeprefix("0x"))
            )[0]
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_RPC_PRECHECK_FAILED",
                "errorMessage": f"getOwners() eth_call failed: {exc}",
                "tx_kind": tx_kind,
            }
        if signer_eoa.lower() not in [o.lower() for o in owners_list]:
            return {
                "success": False,
                "errorCode": "WRAP_SAFE_OWNER_MISMATCH",
                "errorMessage": f"signer EOA {signer_eoa} not in Safe.getOwners() {owners_list}",
                "tx_kind": tx_kind,
            }

        # ── Pre-flight 3: Safe nonce ──────────────────────────────────────────
        try:
            raw_nonce = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": "0xaffed0e0"}, "latest"],
            )
            safe_nonce = int(str(raw_nonce), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_RPC_PRECHECK_FAILED",
                "errorMessage": f"Safe nonce() eth_call failed: {exc}",
                "tx_kind": tx_kind,
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
                "errorCode": "WRAP_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_getBalance failed for signer EOA: {exc}",
                "tx_kind": tx_kind,
            }
        if eoa_balance_wei < _MATIC_FLOOR_WEI:
            return {
                "success": False,
                "errorCode": "WRAP_EOA_MATIC_INSUFFICIENT",
                "errorMessage": (
                    f"signer EOA {signer_eoa} has {eoa_balance_wei} wei MATIC; "
                    f"need >= {_MATIC_FLOOR_WEI} wei (0.05 MATIC)"
                ),
                "tx_kind": tx_kind,
            }

        # ── Build inner calldata ──────────────────────────────────────────────
        try:
            inner_calldata_hex = _build_wrap_calldata(tx_kind, safe_address, amount_micro)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_CALLDATA_BUILD_FAILED",
                "errorMessage": f"inner calldata build failed: {exc}",
                "tx_kind": tx_kind,
            }
        inner_data = bytes.fromhex(inner_calldata_hex.removeprefix("0x"))
        inner_to = (
            POLYGON_USDCE_ADDRESS if tx_kind == "APPROVE" else POLYGON_COLLATERAL_ONRAMP_ADDRESS
        )

        # ── Build Safe tx hash + sign ─────────────────────────────────────────
        try:
            safe_tx_hash_bytes = build_safe_tx_hash(
                safe_address=safe_address,
                chain_id=int(self.chain_id),
                to=inner_to,
                value=0,
                data=inner_data,
                operation=0,  # CALL
                nonce=safe_nonce,
            )
            signature = sign_safe_tx(safe_tx_hash_bytes, self.signer_key)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_SIGN_FAILED",
                "errorMessage": f"Safe sign failed: {exc}",
                "tx_kind": tx_kind,
            }

        # ── Build outer execTransaction calldata ─────────────────────────────
        try:
            exec_calldata = build_exec_transaction_calldata(
                to=inner_to,
                value=0,
                data=inner_data,
                operation=0,
                signatures=signature,
            )
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_CALLDATA_BUILD_FAILED",
                "errorMessage": f"execTransaction calldata build failed: {exc}",
                "tx_kind": tx_kind,
            }

        # ── EOA nonce + gas ───────────────────────────────────────────────────
        try:
            eoa_nonce_hex = self._rpc_call(
                self.polygon_rpc_url,
                "eth_getTransactionCount",
                [signer_eoa, "pending"],
            )
            eoa_nonce = int(str(eoa_nonce_hex), 16)
            gas_price_hex = self._rpc_call(self.polygon_rpc_url, "eth_gasPrice", [])
            gas_price = int(str(gas_price_hex), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_RPC_PRECHECK_FAILED",
                "errorMessage": f"EOA nonce/gasPrice fetch failed: {exc}",
                "tx_kind": tx_kind,
            }

        try:
            gas_hex = self._rpc_call(
                self.polygon_rpc_url,
                "eth_estimateGas",
                [{"from": signer_eoa, "to": safe_address, "data": exec_calldata}],
            )
            gas_limit = (int(str(gas_hex), 16) * 12) // 10
        except V2AdapterError as exc:
            return {
                "success": False,
                "errorCode": "WRAP_GAS_ESTIMATE_REVERTED",
                "errorMessage": f"eth_estimateGas reverted: {exc}",
                "tx_kind": tx_kind,
            }
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "WRAP_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_estimateGas failed: {exc}",
                "tx_kind": tx_kind,
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
                "errorCode": "WRAP_SIGN_FAILED",
                "errorMessage": f"sign_transaction (outer EOA tx) failed: {exc}",
                "tx_kind": tx_kind,
            }

        # ── Dry-run gate ──────────────────────────────────────────────────────
        if dry_run:
            # SECURITY: never log or return the signed raw_tx_hex.
            # A signed raw transaction is a broadcastable payload; any observer can
            # replay it and bypass the no-side-effect gate. Log only non-sensitive
            # metadata (fingerprint of calldata, NOT of signed tx).
            _dry_run_fingerprint = _hashlib.sha256(exec_calldata.encode()).hexdigest()[:16]
            _logger.warning(
                "WRAP_DRY_RUN_LOGGED safe_address=%s safe_nonce=%d "
                "tx_kind=%s amount_micro=%d raw_tx_hex_len=%d "
                "dry_run_fingerprint=%s",
                safe_address, safe_nonce, tx_kind, amount_micro,
                len(raw_hex), _dry_run_fingerprint,
            )
            return {
                "success": False,
                "errorCode": "WRAP_DRY_RUN_LOGGED",
                "errorMessage": "dry-run mode: raw tx built+signed but not broadcast",
                "tx_kind": tx_kind,
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
                "errorCode": "WRAP_BROADCAST_FAILED",
                "errorMessage": f"eth_sendRawTransaction failed: {exc}",
                "tx_kind": tx_kind,
            }

        import re as _re
        tx_hash_str = str(tx_hash) if tx_hash is not None else None
        if not tx_hash_str or not _re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash_str):
            return {
                "success": False,
                "errorCode": "WRAP_INVALID_TX_HASH",
                "errorMessage": f"eth_sendRawTransaction returned non-hash: {tx_hash!r}",
                "tx_kind": tx_kind,
            }

        return {
            "success": True,
            "tx_hash": tx_hash_str,
            "tx_kind": tx_kind,
            "safe_nonce": safe_nonce,
            "eoa_nonce": eoa_nonce,
            "gas_price": gas_price,
            "gas_limit": gas_limit,
            "amount_micro": amount_micro,
        }

    def _broadcast_ctf_operation_via_safe(
        self,
        operation: str,
        inner_to: str,
        inner_data_hex: str,
        safe_address: str,
        signer_eoa: str,
    ) -> dict[str, Any]:
        """Safe v1.3.0 execTransaction broadcast for a CTF split/merge/convert
        inner call. Shared by split_positions/merge_positions/convert_positions.

        Mirrors _wrap_via_safe's preflight+build+sign+broadcast shape exactly
        (same four preflight checks, same dry-run/broadcast gating), generalized
        from a fixed WRAP/APPROVE inner-call pair to caller-supplied inner
        calldata for an arbitrary CTF/NegRiskAdapter target.

        Two-layer safety gate, both default OFF (brand-new capability, INERT
        this packet — nothing calls split_positions/merge_positions/
        convert_positions in production yet, unlike wrap which was already
        promoted live):
          AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV — hard kill switch. OFF returns
            CTF_CONVERSION_DISABLED without any chain I/O.
          AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV — once enabled, build+sign but
            skip broadcast when set.

        Returns dict with:
          success=True, tx_hash=<str>                        (live broadcast)
          success=False, errorCode="CTF_CONVERSION_DISABLED"  (kill switch off)
          success=False, errorCode="CTF_CONVERSION_DRY_RUN_LOGGED",
            dry_run_fingerprint=<16-hex>                      (dry-run mode)
          success=False, errorCode=<CTF_CONVERSION_*>          (error)
        """
        import hashlib as _hashlib
        import logging
        import os

        from src.venue.safe_exec import (
            SAFE_V1_3_VERSION,
            build_exec_transaction_calldata,
            build_safe_tx_hash,
            sign_safe_tx,
        )

        _logger = logging.getLogger(__name__)

        enabled = os.environ.get(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "").lower() in (
            "1", "true", "yes", "on",
        )
        if not enabled:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_DISABLED",
                "errorMessage": (
                    f"{AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV} is not set; CTF "
                    "split/merge/convert broadcast is disabled by default"
                ),
                "operation": operation,
            }

        dry_run = os.environ.get(AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV, "").lower() in (
            "1", "true", "yes", "on",
        )

        inner_data = bytes.fromhex(inner_data_hex.removeprefix("0x"))

        # ── Pre-flight 1: Safe VERSION ────────────────────────────────────────
        try:
            import eth_abi
            raw_version = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": "0xffa1ad74"}, "latest"],
            )
            version_str = eth_abi.decode(
                ["string"], bytes.fromhex(str(raw_version).removeprefix("0x"))
            )[0]
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_RPC_PRECHECK_FAILED",
                "errorMessage": f"VERSION() eth_call failed: {exc}",
                "operation": operation,
            }
        if version_str != SAFE_V1_3_VERSION:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_SAFE_VERSION_UNSUPPORTED",
                "errorMessage": (
                    f"Safe at {safe_address} reports VERSION={version_str!r}; "
                    f"expected {SAFE_V1_3_VERSION!r}"
                ),
                "operation": operation,
            }

        # ── Pre-flight 2: getOwners ───────────────────────────────────────────
        try:
            raw_owners = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": "0xa0e67e2b"}, "latest"],
            )
            owners_list = eth_abi.decode(
                ["address[]"], bytes.fromhex(str(raw_owners).removeprefix("0x"))
            )[0]
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_RPC_PRECHECK_FAILED",
                "errorMessage": f"getOwners() eth_call failed: {exc}",
                "operation": operation,
            }
        if signer_eoa.lower() not in [o.lower() for o in owners_list]:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_SAFE_OWNER_MISMATCH",
                "errorMessage": f"signer EOA {signer_eoa} not in Safe.getOwners() {owners_list}",
                "operation": operation,
            }

        # ── Pre-flight 3: Safe nonce ──────────────────────────────────────────
        try:
            raw_nonce = self._rpc_call(
                self.polygon_rpc_url,
                "eth_call",
                [{"to": safe_address, "data": "0xaffed0e0"}, "latest"],
            )
            safe_nonce = int(str(raw_nonce), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_RPC_PRECHECK_FAILED",
                "errorMessage": f"Safe nonce() eth_call failed: {exc}",
                "operation": operation,
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
                "errorCode": "CTF_CONVERSION_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_getBalance failed for signer EOA: {exc}",
                "operation": operation,
            }
        if eoa_balance_wei < _MATIC_FLOOR_WEI:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_EOA_MATIC_INSUFFICIENT",
                "errorMessage": (
                    f"signer EOA {signer_eoa} has {eoa_balance_wei} wei MATIC; "
                    f"need >= {_MATIC_FLOOR_WEI} wei (0.05 MATIC)"
                ),
                "operation": operation,
            }

        # ── Build Safe tx hash + sign ─────────────────────────────────────────
        try:
            safe_tx_hash_bytes = build_safe_tx_hash(
                safe_address=safe_address,
                chain_id=int(self.chain_id),
                to=inner_to,
                value=0,
                data=inner_data,
                operation=0,  # CALL
                nonce=safe_nonce,
            )
            signature = sign_safe_tx(safe_tx_hash_bytes, self.signer_key)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_SIGN_FAILED",
                "errorMessage": f"Safe sign failed: {exc}",
                "operation": operation,
            }

        # ── Build outer execTransaction calldata ─────────────────────────────
        try:
            exec_calldata = build_exec_transaction_calldata(
                to=inner_to,
                value=0,
                data=inner_data,
                operation=0,
                signatures=signature,
            )
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_CALLDATA_BUILD_FAILED",
                "errorMessage": f"execTransaction calldata build failed: {exc}",
                "operation": operation,
            }

        # ── EOA nonce + gas ───────────────────────────────────────────────────
        try:
            eoa_nonce_hex = self._rpc_call(
                self.polygon_rpc_url,
                "eth_getTransactionCount",
                [signer_eoa, "pending"],
            )
            eoa_nonce = int(str(eoa_nonce_hex), 16)
            gas_price_hex = self._rpc_call(self.polygon_rpc_url, "eth_gasPrice", [])
            gas_price = int(str(gas_price_hex), 16)
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_RPC_PRECHECK_FAILED",
                "errorMessage": f"EOA nonce/gasPrice fetch failed: {exc}",
                "operation": operation,
            }

        try:
            gas_hex = self._rpc_call(
                self.polygon_rpc_url,
                "eth_estimateGas",
                [{"from": signer_eoa, "to": safe_address, "data": exec_calldata}],
            )
            gas_limit = (int(str(gas_hex), 16) * 12) // 10
        except V2AdapterError as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_GAS_ESTIMATE_REVERTED",
                "errorMessage": f"eth_estimateGas reverted: {exc}",
                "operation": operation,
            }
        except Exception as exc:
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_RPC_PRECHECK_FAILED",
                "errorMessage": f"eth_estimateGas failed: {exc}",
                "operation": operation,
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
                "errorCode": "CTF_CONVERSION_SIGN_FAILED",
                "errorMessage": f"sign_transaction (outer EOA tx) failed: {exc}",
                "operation": operation,
            }

        # ── Dry-run gate ──────────────────────────────────────────────────────
        if dry_run:
            # SECURITY: never log or return the signed raw_tx_hex (see
            # _wrap_via_safe's identical rationale — a signed raw transaction
            # is a broadcastable payload).
            _dry_run_fingerprint = _hashlib.sha256(exec_calldata.encode()).hexdigest()[:16]
            _logger.warning(
                "CTF_CONVERSION_DRY_RUN_LOGGED operation=%s safe_address=%s "
                "safe_nonce=%d raw_tx_hex_len=%d dry_run_fingerprint=%s",
                operation, safe_address, safe_nonce, len(raw_hex), _dry_run_fingerprint,
            )
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_DRY_RUN_LOGGED",
                "errorMessage": "dry-run mode: raw tx built+signed but not broadcast",
                "operation": operation,
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
                "errorCode": "CTF_CONVERSION_BROADCAST_FAILED",
                "errorMessage": f"eth_sendRawTransaction failed: {exc}",
                "operation": operation,
            }

        import re as _re
        tx_hash_str = str(tx_hash) if tx_hash is not None else None
        if not tx_hash_str or not _re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash_str):
            return {
                "success": False,
                "errorCode": "CTF_CONVERSION_INVALID_TX_HASH",
                "errorMessage": f"eth_sendRawTransaction returned non-hash: {tx_hash!r}",
                "operation": operation,
            }

        return {
            "success": True,
            "tx_hash": tx_hash_str,
            "operation": operation,
            "safe_nonce": safe_nonce,
            "eoa_nonce": eoa_nonce,
            "gas_price": gas_price,
            "gas_limit": gas_limit,
        }

    def split_positions(
        self,
        condition_id: str,
        amount_micro: int,
        *,
        safe_address: str,
        signer_eoa: str,
        neg_risk: bool = False,
    ) -> dict[str, Any]:
        """Split collateral into a full YES+NO position set for condition_id.

        neg_risk=False targets standard CTF (POLYGON_CTF_ADDRESS, USDC.e
        collateral, 5-arg splitPosition). neg_risk=True targets NegRiskAdapter
        (POLYGON_NEGRISK_ADAPTER_ADDRESS, wcol collateral, 2-arg convenience
        splitPosition(bytes32,uint256)) — matching redeem()'s existing
        neg_risk kw-only routing convention.

        INERT this packet: no production caller. See
        _broadcast_ctf_operation_via_safe for the two-layer safety gate.
        """
        if neg_risk:
            inner_data = _build_negrisk_split_calldata(condition_id, amount_micro)
            inner_to = POLYGON_NEGRISK_ADAPTER_ADDRESS
        else:
            inner_data = _build_split_calldata(condition_id, amount_micro)
            inner_to = POLYGON_CTF_ADDRESS
        return self._broadcast_ctf_operation_via_safe(
            "SPLIT", inner_to, inner_data, safe_address, signer_eoa,
        )

    def merge_positions(
        self,
        condition_id: str,
        amount_micro: int,
        *,
        safe_address: str,
        signer_eoa: str,
        neg_risk: bool = False,
    ) -> dict[str, Any]:
        """Merge a full YES+NO position set back into collateral for condition_id.

        Same neg_risk routing convention as split_positions. INERT this
        packet: no production caller.
        """
        if neg_risk:
            inner_data = _build_negrisk_merge_calldata(condition_id, amount_micro)
            inner_to = POLYGON_NEGRISK_ADAPTER_ADDRESS
        else:
            inner_data = _build_merge_calldata(condition_id, amount_micro)
            inner_to = POLYGON_CTF_ADDRESS
        return self._broadcast_ctf_operation_via_safe(
            "MERGE", inner_to, inner_data, safe_address, signer_eoa,
        )

    def convert_positions(
        self,
        market_id: str,
        index_set: int,
        amount_micro: int,
        *,
        safe_address: str,
        signer_eoa: str,
    ) -> dict[str, Any]:
        """Convert a set of NO positions to the complementary YES + collateral.

        NegRiskAdapter-only (there is no standard-CTF equivalent). market_id
        and index_set use NegRiskAdapter's own conventions — see
        NEGRISK_CONVERT_POSITIONS_SELECTOR module comment; do NOT pass a
        standard conditionId or a CTF outcome-slot bitmask here.

        INERT this packet: no production caller.
        """
        inner_data = _build_negrisk_convert_calldata(market_id, index_set, amount_micro)
        return self._broadcast_ctf_operation_via_safe(
            "CONVERT", POLYGON_NEGRISK_ADAPTER_ADDRESS, inner_data, safe_address, signer_eoa,
        )

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


def _derived_api_creds_cache_key(
    *,
    host: str,
    chain_id: int,
    signer_key: Any,
    signature_type: int,
    funder_address: Any,
) -> tuple[str, int, str, int, str]:
    signer_digest = hashlib.sha256(str(signer_key or "").encode()).hexdigest()
    return (
        str(host).rstrip("/"),
        int(chain_id),
        signer_digest,
        _normalize_signature_type(signature_type),
        str(funder_address or "").lower(),
    )


def _cached_derived_api_creds(
    *,
    host: str,
    chain_id: int,
    signer_key: Any,
    signature_type: int,
    funder_address: Any,
) -> Any | None:
    return _DERIVED_API_CREDS_CACHE.get(
        _derived_api_creds_cache_key(
            host=host,
            chain_id=chain_id,
            signer_key=signer_key,
            signature_type=signature_type,
            funder_address=funder_address,
        )
    )


def _store_derived_api_creds(
    *,
    host: str,
    chain_id: int,
    signer_key: Any,
    signature_type: int,
    funder_address: Any,
    api_creds: Any,
) -> None:
    _DERIVED_API_CREDS_CACHE[
        _derived_api_creds_cache_key(
            host=host,
            chain_id=chain_id,
            signer_key=signer_key,
            signature_type=signature_type,
            funder_address=funder_address,
        )
    ] = api_creds


def _derive_l2_api_creds(client: Any) -> Any:
    derive_api_key = getattr(client, "derive_api_key", None)
    if callable(derive_api_key):
        return derive_api_key()
    return client.create_or_derive_api_key()


def _is_l2_auth_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}:{exc}".lower()
    return "unauthorized" in text or "invalid api key" in text or "status_code=401" in text


def _is_polymarket_invalid_safe_signature_error(exc: BaseException) -> bool:
    text = " ".join(f"{type(exc).__name__}:{exc}".split())
    return "status_code=400" in text and "invalid POLY_GNOSIS_SAFE signature" in text


def _is_polymarket_geoblock_403_error(exc: BaseException) -> bool:
    """Recognize the venue's synchronous, definitive geographic rejection."""

    text = " ".join(f"{type(exc).__name__}:{exc}".split()).lower()
    return (
        "status_code=403" in text
        and "trading restricted in your region" in text
        and "geoblock" in text
    )


def _is_polymarket_fok_killed_error(exc: BaseException) -> bool:
    text = " ".join(f"{type(exc).__name__}:{exc}".split()).lower()
    return (
        "status_code=400" in text
        and "order couldn't be fully filled" in text
        and "fok orders are fully filled or killed" in text
    )


def _is_polymarket_fak_no_match_error(exc: BaseException) -> bool:
    """Recognize the venue's definitive zero-fill FAK terminal response."""

    text = " ".join(f"{type(exc).__name__}:{exc}".split()).lower()
    return (
        "status_code=400" in text
        and "no orders found to match with fak order" in text
        and "fak orders are partially filled or killed if no match is found" in text
    )


def _assert_final_fok_depth_bound(client: Any, envelope: VenueSubmissionEnvelope) -> None:
    """Bind an FOK to executable depth after SDK preparation, immediately pre-POST."""

    if str(envelope.order_type).upper() != "FOK":
        return
    get_order_book = getattr(client, "get_order_book", None)
    if not callable(get_order_book):
        raise ValueError(
            "SUBMIT_ABORTED_PRICE_MOVED:FOK_FINAL_DEPTH_UNAVAILABLE:get_order_book_missing"
        )
    book = get_order_book(envelope.selected_outcome_token_id)
    asset_id = _level_value(book, "asset_id")
    if asset_id not in {None, ""} and str(asset_id) != envelope.selected_outcome_token_id:
        raise ValueError(
            "SUBMIT_ABORTED_PRICE_MOVED:FOK_FINAL_DEPTH_IDENTITY_MISMATCH"
        )

    side = str(envelope.side).upper()
    if side == "BUY":
        level_side = "asks"
        crosses = lambda price: price <= envelope.price
    elif side == "SELL":
        level_side = "bids"
        crosses = lambda price: price >= envelope.price
    else:
        raise ValueError(f"unsupported FOK side {envelope.side!r}")

    levels = _level_value(book, level_side)
    if not isinstance(levels, (list, tuple)):
        raise ValueError(
            f"SUBMIT_ABORTED_PRICE_MOVED:FOK_FINAL_DEPTH_UNAVAILABLE:{level_side}_missing"
        )
    available = Decimal("0")
    for level in levels:
        try:
            price = Decimal(str(_level_value(level, "price")))
            size = Decimal(str(_level_value(level, "size")))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise ValueError(
                "SUBMIT_ABORTED_PRICE_MOVED:FOK_FINAL_DEPTH_MALFORMED"
            ) from exc
        if (
            not price.is_finite()
            or not size.is_finite()
            or price <= 0
            or price >= 1
            or size <= 0
        ):
            raise ValueError(
                "SUBMIT_ABORTED_PRICE_MOVED:FOK_FINAL_DEPTH_MALFORMED"
            )
        if crosses(price):
            available += size
    if available < envelope.size:
        raise ValueError(
            "SUBMIT_ABORTED_PRICE_MOVED:FOK_FINAL_DEPTH_INSUFFICIENT:"
            f"required={envelope.size}:available={available}:limit={envelope.price}"
        )


def _level_value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _api_creds_from_runtime() -> Any | None:
    return _api_creds_from_keychain() or _api_creds_from_env()


def _api_creds_from_keychain() -> Any | None:
    try:
        read_keychain = _import_keychain_reader()
        api_key = read_keychain("openclaw-polymarket-api-key")
        api_secret = read_keychain("openclaw-polymarket-api-secret")
        api_passphrase = read_keychain("openclaw-polymarket-api-passphrase")
    except Exception:
        return None
    return _api_creds_from_values(api_key, api_secret, api_passphrase)


def _import_keychain_reader() -> Callable[[str], str]:
    openclaw_root = str(Path.home() / ".openclaw")
    if openclaw_root not in sys.path:
        sys.path.insert(0, openclaw_root)
    from bin.keychain_resolver import read_keychain  # type: ignore[import-not-found]

    return read_keychain


def _api_creds_from_env() -> Any | None:
    api_key = os.environ.get("POLYMARKET_API_KEY")
    api_secret = os.environ.get("POLYMARKET_API_SECRET")
    api_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE")
    return _api_creds_from_values(api_key, api_secret, api_passphrase)


def _api_creds_from_values(api_key: Any, api_secret: Any, api_passphrase: Any) -> Any | None:
    if not (api_key and api_secret and api_passphrase):
        return None
    from py_clob_client_v2.clob_types import ApiCreds

    return ApiCreds(
        api_key=str(api_key),
        api_secret=str(api_secret),
        api_passphrase=str(api_passphrase),
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


def _json_rpc_call(
    rpc_url: str,
    method: str,
    params: list[Any],
    *,
    timeout_seconds: float = 20.0,
) -> Any:
    # CATEGORY ANTIBODY (2026-06-04): the single on-chain RPC entrypoint. A
    # blocking eth_call here while the world write mutex is held is the M5 /
    # STEP-7 / #95 starvation disease — fail loud and located, never wedge.
    _assert_no_world_mutex_held_for_io(f"onchain.{method}")
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
    with urllib.request.urlopen(request, timeout=max(0.01, float(timeout_seconds))) as response:
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


def _zeus_index_set_to_ctf_bitmask(zeus_index_set: int) -> int:
    """Translate a Zeus binary outcome label to the on-chain CTF indexSet bitmask.

    CONVENTION ANTIBODY (2026-06-10, operator redeem directive — $19 stuck):
    Zeus stores the winning outcome as 2=YES (slot 0) / 1=NO (slot 1) in
    settlement_commands.winning_index_set. The CTF/NegRisk contracts expect the
    on-chain indexSet to be the BITMASK over outcome slots: indexSet = 1 << slot,
    i.e. YES/slot0 -> 1, NO/slot1 -> 2 — the EXACT INVERSE of the Zeus number.

    This is the SAME mapping the negRisk balance probe
    (get_negrisk_winning_position_balance) and amounts builder
    (_build_negrisk_redeem_calldata, which encodes amounts by Zeus label) already
    apply. Centralising it here makes winning_index_set carry ONE consistent
    meaning (the Zeus label) across BOTH the negRisk and standard-CTF lanes, so a
    NO winner can never be encoded as the (losing) YES bitmask.

    Verified on-chain 2026-06-10 against the stuck standard-CTF NO winner
    (condition 0xde5f67…d9c, asset …360377): outcome="No"/outcomeIndex=1, and the
    matching getCollectionId indexSet word is 1<<1 = 2. Zeus label 1 (NO) MUST
    map to CTF bitmask 2.
    """
    if int(zeus_index_set) == 2:  # YES, slot 0
        return 1
    if int(zeus_index_set) == 1:  # NO, slot 1
        return 2
    raise ValueError(
        f"standard-CTF binary redeem only supports Zeus indexSet 1 (NO) or 2 (YES); "
        f"got {zeus_index_set!r}"
    )


def _build_redeem_calldata(condition_id: str, index_sets: list[int]) -> str:
    """ABI-encode standard CTF redeemPositions calldata. PR-I.5.c.

    ``redeemPositions(address collateralToken, bytes32 parentCollectionId,
                      bytes32 conditionId, uint256[] indexSets)``

    parentCollectionId is the zero word for top-level positions (no nested
    conditions). collateralToken is USDC.e: Polymarket standard-CTF positions are
    minted against USDC.e, NOT pUSD (verified on-chain 2026-06-10 — the stuck
    winner's positionId derives from getPositionId(USDC.e, …), not pUSD). Using
    the wrong collateral derives the wrong positionId and the redeem reverts.

    ``index_sets`` carries the Zeus binary label (2=YES, 1=NO), the SAME contract
    as settlement_commands.winning_index_set and the negRisk lane. It is
    translated to the CTF bitmask (1<<slot) here via _zeus_index_set_to_ctf_bitmask
    so a NO winner redeems the NO token (bitmask 2), not the losing YES token.

    Returns hex-encoded calldata starting with the selector (0x01b7037c).
    """
    from eth_abi import encode as _abi_encode

    if not isinstance(index_sets, (list, tuple)) or not index_sets:
        raise ValueError(f"index_sets must be a non-empty list, got {index_sets!r}")
    ctf_bitmasks: list[int] = []
    for entry in index_sets:
        if int(entry) <= 0:
            raise ValueError(f"index_sets entries must be positive uint256, got {entry!r}")
        # Translate Zeus label -> CTF bitmask. Binary markets only (1 or 2).
        ctf_bitmasks.append(_zeus_index_set_to_ctf_bitmask(int(entry)))

    collateral = bytes.fromhex(POLYGON_USDCE_ADDRESS.removeprefix("0x"))
    if len(collateral) != 20:
        raise ValueError("POLYGON_USDCE_ADDRESS is not a valid 20-byte address")
    parent_collection_id = b"\x00" * 32
    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    encoded_args = _abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            "0x" + collateral.hex(),
            parent_collection_id,
            condition_bytes,
            ctf_bitmasks,
        ],
    )
    return CTF_REDEEM_POSITIONS_SELECTOR + encoded_args.hex()


# getPositionId(address collateralToken, bytes32 collectionId) selector.
# keccak256('getPositionId(address,bytes32)')[:4] — verified 2026-06-09.
CTF_GET_POSITION_ID_SELECTOR = "0x39dd7530"
# getCollectionId(bytes32 parentCollectionId, bytes32 conditionId, uint256 indexSet) selector.
# keccak256('getCollectionId(bytes32,bytes32,uint256)')[:4] — verified 2026-06-09.
CTF_GET_COLLECTION_ID_SELECTOR = "0x856296f7"
# balanceOf(address account, uint256 id) ERC1155 selector.
# keccak256('balanceOf(address,uint256)')[:4] — verified 2026-06-09.
ERC1155_BALANCE_OF_SELECTOR = "0x00fdd58e"
# wcol() — NegRiskAdapter's wrapped-collateral token used as the CTF collateral
# for ALL negRisk positions. negRisk position ERC1155 ids are derived from WCOL,
# NOT pUSD. keccak256('wcol()')[:4] — verified 2026-06-09.
NEGRISK_WCOL_SELECTOR = "0x7e3b74c3"


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


# Full binary partition for split/merge — the two disjoint CTF outcome-slot
# bitmasks (slot0=1/YES, slot1=2/NO; same convention as
# _zeus_index_set_to_ctf_bitmask's OUTPUT space, not its Zeus-label input
# space). A split of ``amount`` collateral against this partition mints
# ``amount`` of BOTH the YES and NO position, i.e. the full outcome set —
# the only partition W2.4 builds (a partial/sub-partition split is out of
# scope for this packet).
_CTF_FULL_BINARY_PARTITION: tuple[int, int] = (1, 2)


def _build_split_calldata(condition_id: str, amount_micro: int) -> str:
    """ABI-encode standard CTF splitPosition calldata (full YES+NO partition).

    ``splitPosition(address collateralToken, bytes32 parentCollectionId,
                    bytes32 conditionId, uint256[] partition, uint256 amount)``

    Mirrors _build_redeem_calldata's collateral/parentCollectionId choice:
    collateral is USDC.e (Polymarket standard-CTF positions are minted
    against USDC.e, not pUSD), parentCollectionId is the zero word (top-level
    position, no nested conditions).

    Returns hex-encoded calldata starting with CTF_SPLIT_POSITION_SELECTOR.
    """
    from eth_abi import encode as _abi_encode

    amount_int = int(amount_micro)
    if amount_int <= 0:
        raise ValueError(f"amount_micro must be positive, got {amount_micro!r}")

    collateral = bytes.fromhex(POLYGON_USDCE_ADDRESS.removeprefix("0x"))
    if len(collateral) != 20:
        raise ValueError("POLYGON_USDCE_ADDRESS is not a valid 20-byte address")
    parent_collection_id = b"\x00" * 32
    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    encoded_args = _abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
        [
            "0x" + collateral.hex(),
            parent_collection_id,
            condition_bytes,
            list(_CTF_FULL_BINARY_PARTITION),
            amount_int,
        ],
    )
    return CTF_SPLIT_POSITION_SELECTOR + encoded_args.hex()


def _build_merge_calldata(condition_id: str, amount_micro: int) -> str:
    """ABI-encode standard CTF mergePositions calldata (full YES+NO partition).

    Same argument shape as _build_split_calldata — mergePositions and
    splitPosition share an identical ABI signature on the Gnosis CTF
    (github.com/gnosis/conditional-tokens-contracts) — only the selector
    differs.

    Returns hex-encoded calldata starting with CTF_MERGE_POSITIONS_SELECTOR.
    """
    from eth_abi import encode as _abi_encode

    amount_int = int(amount_micro)
    if amount_int <= 0:
        raise ValueError(f"amount_micro must be positive, got {amount_micro!r}")

    collateral = bytes.fromhex(POLYGON_USDCE_ADDRESS.removeprefix("0x"))
    if len(collateral) != 20:
        raise ValueError("POLYGON_USDCE_ADDRESS is not a valid 20-byte address")
    parent_collection_id = b"\x00" * 32
    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    encoded_args = _abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
        [
            "0x" + collateral.hex(),
            parent_collection_id,
            condition_bytes,
            list(_CTF_FULL_BINARY_PARTITION),
            amount_int,
        ],
    )
    return CTF_MERGE_POSITIONS_SELECTOR + encoded_args.hex()


def _build_negrisk_split_calldata(condition_id: str, amount_micro: int) -> str:
    """ABI-encode NegRiskAdapter splitPosition(bytes32,uint256) calldata.

    The NegRiskAdapter convenience overload — splits against the adapter's
    own wcol collateral for a single negRisk-routed condition; no explicit
    collateral/parentCollectionId/partition args (unlike the standard-CTF
    5-arg form built by _build_split_calldata).

    Returns hex-encoded calldata starting with NEGRISK_SPLIT_POSITION_SELECTOR.
    """
    from eth_abi import encode as _abi_encode

    amount_int = int(amount_micro)
    if amount_int <= 0:
        raise ValueError(f"amount_micro must be positive, got {amount_micro!r}")
    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    encoded_args = _abi_encode(["bytes32", "uint256"], [condition_bytes, amount_int])
    return NEGRISK_SPLIT_POSITION_SELECTOR + encoded_args.hex()


def _build_negrisk_merge_calldata(condition_id: str, amount_micro: int) -> str:
    """ABI-encode NegRiskAdapter mergePositions(bytes32,uint256) calldata.

    Mirrors _build_negrisk_split_calldata's argument shape; only the selector
    differs.

    Returns hex-encoded calldata starting with NEGRISK_MERGE_POSITIONS_SELECTOR.
    """
    from eth_abi import encode as _abi_encode

    amount_int = int(amount_micro)
    if amount_int <= 0:
        raise ValueError(f"amount_micro must be positive, got {amount_micro!r}")
    condition_bytes = _normalize_condition_id_bytes32(condition_id)
    encoded_args = _abi_encode(["bytes32", "uint256"], [condition_bytes, amount_int])
    return NEGRISK_MERGE_POSITIONS_SELECTOR + encoded_args.hex()


def _build_negrisk_convert_calldata(market_id: str, index_set: int, amount_micro: int) -> str:
    """ABI-encode NegRiskAdapter convertPositions(bytes32,uint256,uint256) calldata.

    ``convertPositions(bytes32 marketId, uint256 indexSet, uint256 amount)``

    market_id is NegRiskIdLib.getMarketId(questionId) — NOT the standard
    conditionId (see NEGRISK_CONVERT_POSITIONS_SELECTOR module comment).
    index_set is the multi-market NO/YES bitfield convention documented on
    that same selector constant — NOT the per-condition CTF outcome-slot
    bitmask used by the other builders in this file. _normalize_condition_id_bytes32
    is reused here purely as a generic bytes32-hex normalizer (its name is
    condition_id-specific; its behavior is not).

    Returns hex-encoded calldata starting with NEGRISK_CONVERT_POSITIONS_SELECTOR.
    """
    from eth_abi import encode as _abi_encode

    amount_int = int(amount_micro)
    if amount_int <= 0:
        raise ValueError(f"amount_micro must be positive, got {amount_micro!r}")
    index_set_int = int(index_set)
    if index_set_int <= 0:
        raise ValueError(f"index_set must be a positive bitfield, got {index_set!r}")
    market_bytes = _normalize_condition_id_bytes32(market_id)
    encoded_args = _abi_encode(
        ["bytes32", "uint256", "uint256"],
        [market_bytes, index_set_int, amount_int],
    )
    return NEGRISK_CONVERT_POSITIONS_SELECTOR + encoded_args.hex()


def _build_wrap_calldata(tx_kind: str, safe_address: str, amount_micro: int) -> str:
    """Build inner calldata for one step of the USDC.e → pUSD two-step wrap.

    tx_kind="APPROVE": ERC-20 approve(POLYGON_COLLATERAL_ONRAMP_ADDRESS, amount_micro)
      ABI-encoded: approve(address spender, uint256 value)
      selector: 0x095ea7b3
      args: [spender=POLYGON_COLLATERAL_ONRAMP_ADDRESS padded to 32B, value padded to 32B]

    tx_kind="WRAP": CollateralOnramp.wrap(USDCE, safe_address, amount_micro)
      ABI-encoded: wrap(address _asset, address _to, uint256 _amount)
      selector: 0x62355638
      args: [_asset=POLYGON_USDCE_ADDRESS, _to=safe_address, _amount=amount_micro]
      VERIFIED on-chain 2026-05-20: tx 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a
        block 87167823. pUSD landed at safe_address. arg layout confirmed.
    """
    import eth_abi  # type: ignore[import]

    if tx_kind == "APPROVE":
        selector = bytes.fromhex(ERC20_APPROVE_SELECTOR.removeprefix("0x"))
        encoded_args = eth_abi.encode(["address", "uint256"], [POLYGON_COLLATERAL_ONRAMP_ADDRESS, amount_micro])
    elif tx_kind == "WRAP":
        selector = bytes.fromhex(COLLATERAL_ONRAMP_WRAP_SELECTOR.removeprefix("0x"))
        # _asset = USDC.e source token, _to = recipient safe, _amount = micro-units
        encoded_args = eth_abi.encode(["address", "address", "uint256"], [POLYGON_USDCE_ADDRESS, safe_address, amount_micro])
    else:
        raise ValueError(f"tx_kind must be APPROVE or WRAP, got {tx_kind!r}")
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
            raise StaleMarketSnapshotError("ExecutableMarketSnapshot is stale")

    has_captured_at = hasattr(snapshot, "captured_at") or (isinstance(snapshot, dict) and "captured_at" in snapshot)
    has_window = hasattr(snapshot, "freshness_window_seconds") or (
        isinstance(snapshot, dict) and "freshness_window_seconds" in snapshot
    )
    if has_captured_at or has_window:
        if not (has_captured_at and has_window):
            raise StaleMarketSnapshotError("ExecutableMarketSnapshot freshness fields are incomplete")
        captured_at = _parse_snapshot_datetime(_snapshot_attr(snapshot, "captured_at"))
        window_seconds = float(_snapshot_attr(snapshot, "freshness_window_seconds"))
        if window_seconds <= 0:
            raise StaleMarketSnapshotError("ExecutableMarketSnapshot freshness window must be positive")
        age_seconds = (datetime.now(timezone.utc) - captured_at).total_seconds()
        if _freshness_registry.evaluate("executable_snapshot", age_seconds, override_threshold_seconds=window_seconds) >= FreshnessLevel.STALE:
            raise StaleMarketSnapshotError("ExecutableMarketSnapshot is outside freshness window")
        return

    if not has_is_fresh:
        raise StaleMarketSnapshotError("ExecutableMarketSnapshot freshness contract missing")


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


def _cancel_result_from_response(
    order_id: str, raw: Any, *, check_envelope: bool = False
) -> CancelResult:
    """Thin call site into the response-contract layer (src.venue.response_contracts).

    Shared by the single-order ``cancel()`` path (``check_envelope=True``)
    and cancel_batch's per-order index/echo_id fallback (default False --
    those raw_items are already-mapped legacy per-item dicts, not a
    top-level envelope; see response_contracts._looks_like_cancel_envelope
    for why the two must not be conflated). ``check_envelope=True`` closes
    the #429 false-positive this closes (single-cancel used to report
    CANCELED for an order id the envelope never actually mentioned) --
    see response_contracts.py module docstring for the live incident.
    VenueResponseShapeError intentionally propagates uncaught: every
    current caller of cancel()/cancel_order() already wraps the call in
    ``except Exception`` and records an unknown/ambiguous side effect
    (venue_cancel_journal.py:242, fill_tracker.py:1562,
    day0_hard_fact_exit.py:937) -- the same fail-closed handling a raised
    SDK/network exception already gets, never a silently-guessed CANCELED.
    """
    outcome = parse_cancel_outcome(
        order_id, raw, endpoint="cancel", check_envelope=check_envelope
    )
    if isinstance(raw, str) and raw.strip():
        raw_json = _canonical_json({"orderID": outcome.order_id, "status": "CANCELED"})
    else:
        raw_dict = dict(raw) if isinstance(raw, dict) else {"raw": _to_jsonish(raw)}
        raw_json = _canonical_json(raw_dict)
    return CancelResult(
        status=outcome.status,
        order_id=outcome.order_id,
        raw_response_json=raw_json,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
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


# _nonempty/_reason_from/_extract_order_id/_response_error moved to
# src.venue.response_contracts (R6-a response-contract layer); imported at
# module top as _extract_order_id/_response_error aliases.


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


def _deterministic_v2_order_id(
    client: Any,
    signed_order: Any,
    *,
    chain_id: int,
    neg_risk: bool,
) -> str | None:
    """Return the EIP-712 order hash when the SDK exposes a real V2 order."""

    from py_clob_client_v2.config import get_contract_config
    from py_clob_client_v2.order_utils import ExchangeOrderBuilderV2
    from py_clob_client_v2.order_utils.model.order_data_v2 import SignedOrderV2

    if not isinstance(signed_order, SignedOrderV2):
        return None
    signer = getattr(client, "signer", None)
    if signer is None:
        raise ValueError("V2 signed order identity requires SDK signer")
    config = get_contract_config(chain_id)
    contract = config.neg_risk_exchange_v2 if neg_risk else config.exchange_v2
    builder = ExchangeOrderBuilderV2(contract, chain_id, signer)
    typed_data = builder.build_order_typed_data(signed_order)
    return str(builder.build_order_hash(typed_data))


def _unmapped_submit_result(
    envelope: VenueSubmissionEnvelope,
    *,
    signed_order: bytes | None,
    signed_order_hash: str | None,
    raw_response: Any,
) -> SubmitResult:
    """Fail-closed batch-mapping outcome (ruling 1(c)): a response WAS
    received for this call, but this envelope could not be attributed to
    any item in it. NEVER a success -- the caller must record this as
    SUBMIT_UNKNOWN (command_bus.CommandEventType.SUBMIT_UNKNOWN), distinct
    from the post-call-exception SUBMIT_TIMEOUT_UNKNOWN case."""
    updated = envelope.with_updates(
        signed_order=signed_order,
        signed_order_hash=signed_order_hash,
        raw_response_json=_canonical_json(raw_response or {}),
        error_code="BATCH_RESPONSE_UNMAPPED",
        error_message="batch response could not be mapped to this request (unverified response shape)",
    )
    return SubmitResult(
        status="unmapped",
        envelope=updated,
        error_code=updated.error_code,
        error_message=updated.error_message,
    )


def _unmapped_cancel_result(
    order_id: str,
    *,
    error_code: str = "BATCH_RESPONSE_UNMAPPED",
    raw_response: Any = None,
) -> CancelResult:
    """Fail-closed batch-mapping outcome for cancel_batch -- same contract
    as _unmapped_submit_result. status="UNKNOWN" matches the existing
    single-order _cancel_result_from_response UNKNOWN branch."""
    return CancelResult(
        status="UNKNOWN",
        order_id=order_id,
        raw_response_json=_canonical_json(raw_response) if raw_response is not None else None,
        error_code=error_code,
        error_message="batch response could not be mapped to this request (unverified response shape)",
    )


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
    raw_response = _normalize_v2_amount_response(
        raw_response,
        side=envelope.side,
        endpoint="submit",
    )
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


_V2_AMOUNT_SCALE = Decimal("1000000")
_V2_POINT_ORDER_CONTRACT = "POLYMARKET_CLOB_V2_FIXED_6_POINT_ORDER"
_V2_HUMAN_POINT_ORDER_CONTRACT = "POLYMARKET_CLOB_V2_HUMAN_POINT_ORDER"
_V2_SUBMIT_CONTRACT = "POLYMARKET_CLOB_V2_HUMAN_SUBMIT_AMOUNTS"


def _v2_decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _normalize_v2_amount_response(
    raw_response: Any,
    *,
    side: str | None = None,
    endpoint: str = "unknown",
) -> Any:
    """Add typed human-unit fields for the two distinct V2 response shapes."""

    if not isinstance(raw_response, dict):
        return raw_response
    normalized = dict(raw_response)
    original_raw = _level_value(normalized, "original_size")
    if original_raw in (None, ""):
        original_raw = _level_value(normalized, "originalSize")
    matched_raw = _level_value(normalized, "size_matched")
    if matched_raw in (None, ""):
        matched_raw = _level_value(normalized, "sizeMatched")
    point_order_endpoint = endpoint in {"get_order", "get_open_orders"}
    if point_order_endpoint and (
        original_raw in (None, "") or matched_raw in (None, "")
    ):
        raise VenueResponseShapeError(
            endpoint,
            raw_response,
            "point-order response requires original_size and size_matched fields",
        )
    if original_raw not in (None, "") or matched_raw not in (None, ""):
        try:
            original_units = Decimal(str(original_raw))
            matched_units = Decimal(str(matched_raw))
        except (ArithmeticError, TypeError, ValueError):
            if point_order_endpoint:
                raise VenueResponseShapeError(
                    endpoint,
                    raw_response,
                    "point-order amount is non-numeric",
                )
            normalized["_v2_amount_normalization_error"] = "NON_NUMERIC_FIXED_6"
            return normalized
        human_point_order = bool(
            point_order_endpoint
            and (
                original_units != original_units.to_integral_value()
                or matched_units != matched_units.to_integral_value()
                or max(original_units, matched_units) < _V2_AMOUNT_SCALE
            )
        )
        if (
            not original_units.is_finite()
            or not matched_units.is_finite()
            or original_units < 0
            or matched_units < 0
            or (
                not human_point_order
                and (
                    original_units != original_units.to_integral_value()
                    or matched_units != matched_units.to_integral_value()
                )
            )
        ):
            if point_order_endpoint:
                raise VenueResponseShapeError(
                    endpoint,
                    raw_response,
                    "point-order amount is outside the non-negative decimal domain",
                )
            normalized["_v2_amount_normalization_error"] = "INVALID_FIXED_6"
            return normalized
        normalized["_venue_response_contract"] = (
            _V2_HUMAN_POINT_ORDER_CONTRACT
            if human_point_order
            else _V2_POINT_ORDER_CONTRACT
        )
        scale = Decimal("1") if human_point_order else _V2_AMOUNT_SCALE
        normalized["_v2_original_size"] = _v2_decimal_text(original_units / scale)
        normalized["_v2_matched_size"] = _v2_decimal_text(matched_units / scale)
        normalized["_v2_wire_original_size"] = str(original_raw)
        normalized["_v2_wire_size_matched"] = str(matched_raw)
        normalized["original_size"] = normalized["_v2_original_size"]
        normalized["size_matched"] = normalized["_v2_matched_size"]
        normalized["originalSize"] = normalized["_v2_original_size"]
        normalized["sizeMatched"] = normalized["_v2_matched_size"]
        return normalized

    making_raw = _level_value(normalized, "makingAmount")
    if making_raw in (None, ""):
        making_raw = _level_value(normalized, "making_amount")
    taking_raw = _level_value(normalized, "takingAmount")
    if taking_raw in (None, ""):
        taking_raw = _level_value(normalized, "taking_amount")
    if making_raw in (None, "") and taking_raw in (None, ""):
        return normalized
    normalized["_venue_response_contract"] = _V2_SUBMIT_CONTRACT
    try:
        making = Decimal(str(making_raw))
        taking = Decimal(str(taking_raw))
    except (ArithmeticError, TypeError, ValueError):
        normalized["_v2_amount_normalization_error"] = "NON_NUMERIC"
        return normalized
    if (
        not making.is_finite()
        or not taking.is_finite()
        or making < 0
        or taking < 0
    ):
        normalized["_v2_amount_normalization_error"] = "INVALID_HUMAN_AMOUNTS"
        return normalized
    side_value = str(side or normalized.get("side") or "").strip().upper()
    if side_value not in {"BUY", "SELL"}:
        normalized["_v2_amount_normalization_error"] = "SIDE_MISSING"
        return normalized
    shares = taking if side_value == "BUY" else making
    usd = making if side_value == "BUY" else taking
    normalized["_v2_making_amount"] = _v2_decimal_text(making)
    normalized["_v2_taking_amount"] = _v2_decimal_text(taking)
    normalized["_v2_matched_size"] = _v2_decimal_text(shares)
    if shares > 0:
        normalized["_v2_fill_price"] = _v2_decimal_text(usd / shares)
    return normalized


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
