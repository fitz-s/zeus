"""Polymarket CLOB API client. Spec §6.4.

Limit orders ONLY. Auth via macOS Keychain.
All numeric fields from API are STRINGS — always float() before use.
"""

import json
import logging
import os
import subprocess
import warnings
from decimal import Decimal
from typing import Any, Optional

import httpx

from src.contracts.executable_market_snapshot_v2 import (
    MarketSnapshotMismatchError,
    canonicalize_fee_details,
    fee_rate_fraction_from_details,
)

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"


class V2PreflightError(RuntimeError):
    """Raised when the V2 endpoint preflight check fails (INV-25).

    A V2PreflightError means the CLOB endpoint is unreachable or returned an
    unexpected response. Callers (executor._live_order) must treat this as a
    hard rejection — no place_limit_order call may proceed in the same cycle.
    """


def _resolve_credentials() -> dict:
    """Resolve Polymarket credentials from macOS Keychain.

    Uses OpenClaw's keychain_resolver stdin/stdout protocol directly.
    Returns dict with 'private_key' and 'funder_address'.
    """
    try:
        # Resolve OpenClaw root: OPENCLAW_HOME → ~/.openclaw
        openclaw_root = os.environ.get("OPENCLAW_HOME", os.path.expanduser("~/.openclaw"))
        # Read credentials via OpenClaw keychain resolver protocol
        result = subprocess.run(
            ["python3", "-c",
             f"import json, sys; sys.path.insert(0, {openclaw_root!r}); "
             "from bin.keychain_resolver import read_keychain; "
             "pk = read_keychain('openclaw-metamask-private-key'); "
             "fa = read_keychain('openclaw-polymarket-funder-address'); "
             "print(json.dumps({'private_key': pk, 'funder_address': fa}))"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Keychain resolution failed: {result.stderr}")
        creds = json.loads(result.stdout)
        if "private_key" not in creds or "funder_address" not in creds:
            raise RuntimeError("Missing private_key or funder_address from Keychain")
        return creds
    except Exception as e:
        raise RuntimeError(f"Cannot resolve Polymarket credentials: {e}") from e


class PolymarketClient:
    """CLOB client for order placement and orderbook queries."""

    def __init__(self):
        self._clob_client = None
        self._v2_adapter = None
        self._pending_submission_envelope = None

    def _ensure_client(self):
        """Deprecated compatibility alias for the V2 adapter boundary."""
        warnings.warn(
            "PolymarketClient._ensure_client() is deprecated; live venue I/O routes "
            "through src.venue.polymarket_v2_adapter.PolymarketV2Adapter.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._ensure_v2_adapter()

    def _ensure_v2_adapter(self):
        """Lazy init: connect live CLOB I/O through the strict V2 adapter."""
        adapter = getattr(self, "_v2_adapter", None)
        if adapter is not None:
            return adapter

        from src.venue.polymarket_v2_adapter import DEFAULT_V2_HOST, PolymarketV2Adapter

        creds = _resolve_credentials()
        adapter = PolymarketV2Adapter(
            host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
            funder_address=creds["funder_address"],
            signer_key=creds["private_key"],
            chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
            api_creds=creds.get("api_creds"),
        )
        self._v2_adapter = adapter
        logger.info("Polymarket CLOB V2 adapter initialized (live mode)")
        return adapter

    def v2_preflight(self) -> None:
        """Verify V2 endpoint reachability before any order placement (INV-25).

        Calls self._clob_client.get_ok() — a V2-only SDK health-check method.
        Any failure (network error, unexpected response, AttributeError if the
        SDK does not expose get_ok) raises V2PreflightError.

        This is a reachability-only check today. Full V2 endpoint-identity
        verification (signature challenge, API version header assertion) requires
        operator-confirmed endpoint signature and is deferred to a follow-up slice
        per decisions.md §O3-b.

        INV-25: When this method raises, _live_order must return a rejected
        OrderResult without calling place_limit_order.
        """
        legacy_client = getattr(self, "_clob_client", None)
        if legacy_client is not None and getattr(self, "_v2_adapter", None) is None:
            warnings.warn(
                "Injected legacy CLOB client preflight is deprecated and retained "
                "only for compatibility tests; live preflight uses PolymarketV2Adapter.",
                DeprecationWarning,
                stacklevel=2,
            )
            if not hasattr(legacy_client, "get_ok"):
                raise V2PreflightError(
                    "SDK lacks get_ok preflight method; preflight cannot verify endpoint identity. "
                    "Use py-clob-client-v2 through PolymarketV2Adapter to satisfy INV-25."
                )
            try:
                legacy_client.get_ok()
            except Exception as exc:
                raise V2PreflightError(f"V2 endpoint preflight failed: {exc!r}") from exc
            return

        result = self._ensure_v2_adapter().preflight()
        if not result.ok:
            raise V2PreflightError(
                f"{result.error_code or 'V2_PREFLIGHT_FAILED'}: {result.message}"
            )

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch orderbook for a token. Public endpoint, no auth.

        Returns: {"bids": [{"price": float, "size": float}...],
                  "asks": [{"price": float, "size": float}...]}
        """
        data = self.get_orderbook_snapshot(token_id)

        # Normalize: API returns string numerics
        for side in ("bids", "asks"):
            if side in data:
                for entry in data[side]:
                    entry["price"] = float(entry["price"])
                    entry["size"] = float(entry["size"])

        return data

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        """Fetch raw CLOB orderbook facts for executable snapshot capture."""

        resp = httpx.get(f"{CLOB_BASE}/book", params={"token_id": token_id}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"CLOB orderbook response for {token_id} is not an object")
        return data

    def get_clob_market_info(self, condition_id: str) -> dict:
        """Fetch raw CLOB market facts for executable snapshot capture."""

        adapter = getattr(self, "_v2_adapter", None)
        if adapter is not None:
            getter = getattr(adapter, "get_clob_market_info", None)
            if callable(getter):
                info = getter(condition_id)
                raw = getattr(info, "raw", info)
                if isinstance(raw, dict):
                    return dict(raw)

        legacy_client = getattr(self, "_clob_client", None)
        if legacy_client is not None:
            getter = getattr(legacy_client, "get_market", None)
            if callable(getter):
                raw = getter(condition_id)
                if isinstance(raw, dict):
                    return dict(raw)
                if raw is not None and hasattr(raw, "__dict__"):
                    return dict(raw.__dict__)

        resp = httpx.get(f"{CLOB_BASE}/markets/{condition_id}", timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"CLOB market response for {condition_id} is not an object")
        return data

    def get_best_bid_ask(self, token_id: str) -> tuple[float, float, float, float]:
        """Get best bid/ask with sizes for VWMP calculation.

        Returns: (best_bid, best_ask, bid_size, ask_size)
        """
        from src.contracts.exceptions import EmptyOrderbookError
        from src.data.market_scanner import _top_book_level_decimal

        book = self.get_orderbook(token_id)
        try:
            best_bid, bid_size = _top_book_level_decimal(book, "bids")
            best_ask, ask_size = _top_book_level_decimal(book, "asks")
        except Exception as exc:
            raise EmptyOrderbookError(f"No executable top book for {token_id}: {exc}") from exc
        if best_bid >= best_ask:
            raise EmptyOrderbookError(f"Crossed orderbook for {token_id}")

        return float(best_bid), float(best_ask), float(bid_size), float(ask_size)

    def get_fee_rate(self, token_id: str) -> float:
        """Fetch the token-specific Polymarket taker fee as a fraction."""

        return fee_rate_fraction_from_details(self.get_fee_rate_details(token_id))

    def get_fee_rate_details(self, token_id: str) -> dict[str, Any]:
        """Fetch token-specific fee metadata with explicit fraction/bps units."""

        resp = httpx.get(f"{CLOB_BASE}/fee-rate", params={"token_id": token_id}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        schedule = data.get("feeSchedule") if isinstance(data, dict) else None
        if not isinstance(schedule, dict):
            schedule = data if isinstance(data, dict) else {}
        try:
            return canonicalize_fee_details(
                schedule,
                source="clob_fee_rate",
                token_id=token_id,
            )
        except MarketSnapshotMismatchError as exc:
            raise RuntimeError(f"Fee-rate response missing base_fee/feeRate for {token_id}: {exc}") from exc

    def bind_submission_envelope(self, envelope: Any) -> None:
        """Bind the next limit-order submit to executable snapshot provenance."""

        self._pending_submission_envelope = envelope

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
    ) -> Optional[dict]:
        """Place a limit order. Spec §6.4: limit orders ONLY.

        Args:
            token_id: YES or NO token ID
            price: limit price [0.01, 0.99]
            size: number of shares
            side: "BUY" or "SELL"
            order_type: concrete CLOB limit-order type ("GTC", "FOK", "FAK", ...)

        Returns: order result dict or None on failure
        """
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"place_limit_order requires side='BUY' or 'SELL', got {side!r}")

        pending_envelope = getattr(self, "_pending_submission_envelope", None)
        if pending_envelope is not None:
            self._pending_submission_envelope = None
            mismatch = _submission_envelope_mismatch(
                pending_envelope,
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=order_type,
            )
            if mismatch:
                return {
                    "success": False,
                    "status": "rejected",
                    "errorCode": "BOUND_ENVELOPE_MISMATCH",
                    "errorMessage": mismatch,
                    "_venue_submission_envelope": pending_envelope.to_dict(),
                }
            try:
                adapter = self._ensure_v2_adapter()
            except Exception as exc:
                rejected_envelope = pending_envelope.with_updates(
                    error_code="V2_PREFLIGHT_EXCEPTION",
                    error_message=str(exc),
                )
                return {
                    "success": False,
                    "status": "rejected",
                    "errorCode": rejected_envelope.error_code,
                    "errorMessage": rejected_envelope.error_message,
                    "_venue_submission_envelope": rejected_envelope.to_dict(),
                }
            submit = adapter.submit(pending_envelope)
            result = _legacy_order_result_from_submit(submit)
            logger.info(
                "V2 bound-envelope submit result: %s %s @ %.3f x %.1f → %s",
                side,
                token_id[:12],
                price,
                size,
                result.get("status"),
            )
            return result

        warnings.warn(
            "PolymarketClient.place_limit_order() is a compatibility wrapper; "
            "live placement routes through PolymarketV2Adapter.",
            DeprecationWarning,
            stacklevel=2,
        )

        try:
            adapter = self._ensure_v2_adapter()
            preflight = adapter.preflight()
        except Exception as exc:
            # M2: this compatibility wrapper lazily initializes credentials /
            # adapter and runs a preflight before adapter.submit_limit_order().
            # Failures here are before the venue submit side-effect boundary,
            # so executor should receive a typed rejection payload rather than
            # misclassifying the exception as SUBMIT_UNKNOWN_SIDE_EFFECT.
            return {
                "success": False,
                "status": "rejected",
                "errorCode": "V2_PREFLIGHT_EXCEPTION",
                "errorMessage": str(exc),
            }
        if not preflight.ok:
            return {
                "success": False,
                "status": "rejected",
                "errorCode": preflight.error_code or "V2_PREFLIGHT_FAILED",
                "errorMessage": preflight.message,
            }

        submit = adapter.submit_limit_order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            order_type=order_type,
        )
        result = _legacy_order_result_from_submit(submit)
        logger.info(
            "V2 order submit result: %s %s @ %.3f x %.1f → %s",
            side,
            token_id[:12],
            price,
            size,
            result.get("status"),
        )
        return result

    def get_order(self, order_id: str) -> Optional[dict]:
        """Fetch a single order by venue order ID. Returns None if not found.

        Wraps SDK's get_order. Normalizes response to at least
        {"orderID": str, "status": str} so the recovery loop is stable
        against SDK response shape changes.

        Returns None when the venue returns 404 or similar "not found" signal.
        Other exceptions (network error, auth failure) propagate — the
        recovery loop catches and logs them so a single bad lookup does not
        kill the loop.
        """
        try:
            state = self._ensure_v2_adapter().get_order(order_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        except Exception as exc:
            # Some SDK versions raise a plain exception on 404; treat any
            # message containing "not found" (case-insensitive) as None.
            if "not found" in str(exc).lower() or "404" in str(exc):
                return None
            raise

        result = dict(state.raw)

        # Guarantee the two load-bearing keys downstream code reads.
        if "orderID" not in result:
            result.setdefault("orderID", state.order_id or result.get("id") or result.get("order_id") or order_id)
        if "status" not in result:
            result.setdefault("status", state.status or result.get("state") or result.get("order_status") or "UNKNOWN")

        return result

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel a pending order."""
        from src.control.cutover_guard import CutoverPending, gate_for_intent
        from src.execution.command_bus import IntentKind

        decision = gate_for_intent(IntentKind.CANCEL)
        if not decision.allow_cancel:
            raise CutoverPending(decision.block_reason or decision.state.value)
        result = self._ensure_v2_adapter().cancel(order_id)
        payload = {
            "orderID": result.order_id,
            "status": result.status,
            "errorCode": result.error_code,
            "errorMessage": result.error_message,
            "raw_response_json": result.raw_response_json,
        }
        logger.info("Order cancel result: %s → %s", order_id, result.status)
        return payload

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Fetch a live order's latest exchange status."""
        try:
            result = self.get_order(order_id)
            if result is None:
                return {"status": "NOT_FOUND"}
            logger.info("Order status: %s → %s", order_id, result.get("status"))
            return result
        except Exception as exc:
            logger.warning("Order status fetch failed for %s: %s", order_id, exc)
            return {"status": "FETCH_ERROR", "reason": str(exc)}

    def get_open_orders(self) -> list[dict]:
        """Return all currently open exchange orders for the funded wallet."""
        legacy_client = getattr(self, "_clob_client", None)
        if legacy_client is not None and getattr(self, "_v2_adapter", None) is None:
            warnings.warn(
                "Injected legacy CLOB client get_open_orders is deprecated and retained "
                "only for compatibility tests; live order queries use PolymarketV2Adapter.",
                DeprecationWarning,
                stacklevel=2,
            )
            return list(legacy_client.get_orders())
        states = self._ensure_v2_adapter().get_open_orders()
        result = []
        for state in states:
            raw = dict(state.raw)
            raw.setdefault("orderID", state.order_id)
            raw.setdefault("status", state.status)
            result.append(raw)
        return result

    def get_positions_from_api(self) -> Optional[list[dict]]:
        """Fetch authoritative live positions from Polymarket's data API."""
        creds = _resolve_credentials()
        address = creds.get("funder_address", "")
        if not address:
            raise RuntimeError("Missing funder_address for position fetch")

        resp = httpx.get(
            f"{DATA_API_BASE}/positions",
            params={"user": address, "sizeThreshold": "0.01"},
            timeout=15.0,
        )
        resp.raise_for_status()
        raw = resp.json()
        if isinstance(raw, dict):
            raw = raw.get("data", []) or []

        positions: list[dict] = []
        for item in raw:
            token_id = item.get("asset", "") or item.get("token_id", "")
            if not token_id:
                continue
            try:
                size = float(item.get("size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if size < 0.01:
                continue

            try:
                avg_price = float(item.get("avgPrice", 0) or item.get("avg_price", 0) or 0)
                initial_value = float(item.get("initialValue", 0) or 0)
                current_value = float(item.get("currentValue", 0) or 0)
                cash_pnl = float(item.get("cashPnl", 0) or 0)
                cur_price = float(item.get("curPrice", 0) or 0)
            except (TypeError, ValueError) as e:
                logger.warning("Quarantining token %s due to malformed metrics: %s", token_id, e)
                continue

            positions.append({
                "token_id": token_id,
                "condition_id": item.get("conditionId", "") or item.get("condition_id", ""),
                "size": round(size, 4),
                "avg_price": round(avg_price, 6),
                "cost": round(initial_value, 4) if initial_value > 0 else round(size * avg_price, 4),
                "side": item.get("outcome", "") or item.get("side", ""),
                "current_value": round(current_value, 4),
                "cash_pnl": round(cash_pnl, 4),
                "cur_price": round(cur_price, 6),
                "redeemable": bool(item.get("redeemable", False)),
                "title": item.get("title", ""),
                "end_date": item.get("endDate", ""),
            })
        return positions

    def get_balance(self) -> float:
        """Get pUSD balance through the Z4 CollateralLedger."""
        warnings.warn(
            "PolymarketClient.get_balance() is a compatibility wrapper; "
            "live balance queries route through CollateralLedger.",
            DeprecationWarning,
            stacklevel=2,
        )
        from src.state.collateral_ledger import CollateralLedger, configure_global_ledger
        from src.state.db import get_trade_connection_with_world

        conn = get_trade_connection_with_world()
        ledger = CollateralLedger(conn)
        snapshot = ledger.refresh(self._ensure_v2_adapter())
        conn.commit()
        configure_global_ledger(ledger)
        return snapshot.pusd_balance_micro / 1_000_000

    def redeem(self, condition_id: str) -> Optional[dict]:
        """Redeem winning shares for USDC after settlement.

        Not urgent (USDC stays claimable indefinitely) but without it,
        winning capital sits on-chain instead of being available for new trades.
        """
        warnings.warn(
            "PolymarketClient.redeem() is a compatibility wrapper; "
            "redeem attempts route through PolymarketV2Adapter when supported.",
            DeprecationWarning,
            stacklevel=2,
        )
        from src.state.collateral_ledger import require_pusd_redemption_allowed

        require_pusd_redemption_allowed()
        logger.warning(
            "Redeem deferred for condition %s: R1 settlement command ledger is not implemented",
            condition_id,
        )
        return {
            "success": False,
            "errorCode": "REDEEM_DEFERRED_TO_R1",
            "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
            "condition_id": condition_id,
        }


def _submission_envelope_mismatch(
    envelope: Any,
    *,
    token_id: str,
    price: float,
    size: float,
    side: str,
    order_type: str,
) -> str:
    if str(getattr(envelope, "selected_outcome_token_id", "")) != str(token_id):
        return "bound envelope token_id does not match submit token_id"
    if str(getattr(envelope, "side", "")) != str(side):
        return "bound envelope side does not match submit side"
    if str(getattr(envelope, "order_type", "")) != str(order_type):
        return "bound envelope order_type does not match submit order_type"
    if Decimal(str(getattr(envelope, "price", ""))) != Decimal(str(price)):
        return "bound envelope price does not match submit price"
    if Decimal(str(getattr(envelope, "size", ""))) != Decimal(str(size)):
        return "bound envelope size does not match submit size"
    return ""


def _legacy_order_result_from_submit(submit: Any) -> dict:
    envelope = submit.envelope
    payload = {
        "success": submit.status == "accepted",
        "status": submit.status,
        "errorCode": submit.error_code,
        "errorMessage": submit.error_message,
        "_venue_submission_envelope": envelope.to_dict(),
    }
    if envelope.order_id:
        payload.update(
            {
                "orderID": envelope.order_id,
                "orderId": envelope.order_id,
                "id": envelope.order_id,
            }
        )
    return payload
