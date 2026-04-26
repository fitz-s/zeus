"""Polymarket CLOB API client. Spec §6.4.

Limit orders ONLY. Auth via macOS Keychain.
All numeric fields from API are STRINGS — always float() before use.
"""

import json
import logging
import os
import subprocess
from typing import Optional

import httpx

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

    def _ensure_client(self):
        """Lazy init: connect to CLOB only on first real I/O."""
        if self._clob_client is not None:
            return
        from py_clob_client.client import ClobClient

        creds = _resolve_credentials()
        self._clob_client = ClobClient(
            host=CLOB_BASE,
            key=creds["private_key"],
            chain_id=137,
            signature_type=2,
            funder=creds["funder_address"],
        )
        self._clob_client.set_api_creds(
            self._clob_client.create_or_derive_api_creds()
        )
        logger.info("Polymarket CLOB client initialized (live mode)")

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
        self._ensure_client()
        if not hasattr(self._clob_client, "get_ok"):
            raise V2PreflightError(
                "SDK lacks get_ok preflight method; preflight cannot verify endpoint identity. "
                "Upgrade py-clob-client to >= 0.34 to satisfy INV-25."
            )
        try:
            self._clob_client.get_ok()
        except Exception as exc:
            raise V2PreflightError(f"V2 endpoint preflight failed: {exc!r}") from exc

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch orderbook for a token. Public endpoint, no auth.

        Returns: {"bids": [{"price": float, "size": float}...],
                  "asks": [{"price": float, "size": float}...]}
        """
        resp = httpx.get(f"{CLOB_BASE}/book", params={"token_id": token_id}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()

        # Normalize: API returns string numerics
        for side in ("bids", "asks"):
            if side in data:
                for entry in data[side]:
                    entry["price"] = float(entry["price"])
                    entry["size"] = float(entry["size"])

        return data

    def get_best_bid_ask(self, token_id: str) -> tuple[float, float, float, float]:
        """Get best bid/ask with sizes for VWMP calculation.

        Returns: (best_bid, best_ask, bid_size, ask_size)
        """
        from src.contracts.exceptions import EmptyOrderbookError
        book = self.get_orderbook(token_id)
        if "bids" not in book or not book["bids"]:
            raise EmptyOrderbookError(f"No bids available for {token_id}")
        if "asks" not in book or not book["asks"]:
            raise EmptyOrderbookError(f"No asks available for {token_id}")

        best_bid = book["bids"][0]["price"]
        best_ask = book["asks"][0]["price"]
        bid_size = book["bids"][0]["size"]
        ask_size = book["asks"][0]["size"]
        
        if bid_size <= 0.0 or ask_size <= 0.0:
            raise EmptyOrderbookError(f"Liquidity sizes are 0.0 for token {token_id}")

        return best_bid, best_ask, bid_size, ask_size

    def get_fee_rate(self, token_id: str) -> float:
        """Fetch the token-specific Polymarket taker fee rate."""
        resp = httpx.get(f"{CLOB_BASE}/fee-rate", params={"token_id": token_id}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        schedule = data.get("feeSchedule") if isinstance(data, dict) else None
        if not isinstance(schedule, dict):
            schedule = data if isinstance(data, dict) else {}
        if schedule.get("feesEnabled") is False:
            return 0.0
        for key in ("feeRate", "fee_rate", "takerFeeRate", "taker_fee_rate"):
            if key in schedule and schedule[key] is not None:
                return float(schedule[key])
        raise RuntimeError(f"Fee-rate response missing feeSchedule.feeRate for {token_id}")

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
    ) -> Optional[dict]:
        """Place a limit order. Spec §6.4: limit orders ONLY.

        Args:
            token_id: YES or NO token ID
            price: limit price [0.01, 0.99]
            size: number of shares
            side: "BUY" or "SELL"

        Returns: order result dict or None on failure
        """
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        _SIDE_MAP = {"BUY": BUY, "SELL": SELL}
        if side not in _SIDE_MAP:
            raise ValueError(f"place_limit_order requires side='BUY' or 'SELL', got {side!r}")
        side_const = _SIDE_MAP[side]
        order_args = OrderArgs(
            price=price, size=size, side=side_const, token_id=token_id
        )

        self._ensure_client()
        signed = self._clob_client.create_order(order_args)
        result = self._clob_client.post_order(signed)

        logger.info("Order placed: %s %s @ %.3f x %.1f → %s",
                     side, token_id[:12], price, size, result.get("status"))
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
        self._ensure_client()
        try:
            result = self._clob_client.get_order(order_id)
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

        if result is None:
            return None

        # Normalize to stable shape if SDK returns a non-dict (e.g. a model obj).
        if not isinstance(result, dict):
            try:
                result = dict(result)
            except (TypeError, ValueError):
                result = {"orderID": order_id, "status": str(result)}

        # Guarantee the two load-bearing keys downstream code reads.
        if "orderID" not in result:
            result.setdefault("orderID", result.get("id") or result.get("order_id") or order_id)
        if "status" not in result:
            result.setdefault("status", result.get("state") or result.get("order_status") or "UNKNOWN")

        return result

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel a pending order."""
        self._ensure_client()
        result = self._clob_client.cancel(order_id)
        logger.info("Order cancelled: %s → %s", order_id, result.get("status"))
        return result

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Fetch a live order's latest exchange status."""
        self._ensure_client()
        try:
            if hasattr(self._clob_client, "get_order"):
                result = self._clob_client.get_order(order_id)
            elif hasattr(self._clob_client, "get_orders"):
                orders = self._clob_client.get_orders()
                result = next((o for o in orders if o.get("id") == order_id), None)
            else:
                logger.warning("Live client has no order-status method")
                return {"status": "MISSING_METHOD"}
                
            if result is None:
                return {"status": "NOT_FOUND"}
                
            logger.info("Order status: %s → %s", order_id, result.get("status") if result else "missing")
            return result
        except Exception as exc:
            logger.warning("Order status fetch failed for %s: %s", order_id, exc)
            return {"status": "FETCH_ERROR", "reason": str(exc)}

    def get_open_orders(self) -> list[dict]:
        """Return all currently open exchange orders for the funded wallet."""
        self._ensure_client()
        try:
            from py_clob_client.clob_types import OpenOrderParams

            result = self._clob_client.get_orders(OpenOrderParams()) or []
        except (ImportError, TypeError):
            result = self._clob_client.get_orders() or []

        if isinstance(result, dict):
            result = result.get("data", []) or []
        return list(result)

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
        """Get USDC balance."""
        self._ensure_client()
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        resp = self._clob_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return int(resp["balance"]) / 1e6

    def redeem(self, condition_id: str) -> Optional[dict]:
        """Redeem winning shares for USDC after settlement.

        Not urgent (USDC stays claimable indefinitely) but without it,
        winning capital sits on-chain instead of being available for new trades.
        """
        self._ensure_client()
        try:
            result = self._clob_client.redeem(condition_id)
            logger.info("Redeemed condition %s → %s", condition_id, result)
            return result
        except Exception as exc:
            logger.warning("Redeem failed for condition %s: %s", condition_id, exc)
            return None
