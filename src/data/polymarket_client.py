"""Polymarket CLOB API client. Spec §6.4.

Limit orders ONLY. Auth via macOS Keychain.
All numeric fields from API are STRINGS — always float() before use.
"""

import json
import logging
import subprocess
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
DATA_API_BASE = "https://data-api.polymarket.com"


def _resolve_credentials() -> dict:
    """Resolve Polymarket credentials from macOS Keychain.

    Uses OpenClaw's keychain_resolver stdin/stdout protocol directly.
    Returns dict with 'private_key' and 'funder_address'.
    """
    try:
        # Read credentials via OpenClaw keychain resolver protocol
        result = subprocess.run(
            ["python3", "-c",
             "import json, sys; sys.path.insert(0, '/Users/leofitz/.openclaw'); "
             "from bin.keychain_resolver import read_keychain; "
             "pk = read_keychain('openclaw-metamask-private-key'); "
             "fa = read_keychain('openclaw-polymarket-funder-address'); "
             "print(json.dumps({'private_key': pk, 'funder_address': fa}))"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Keychain resolution failed: {result.stderr}")
        creds = json.loads(result.stdout)
        if not creds.get("private_key") or not creds.get("funder_address"):
            raise RuntimeError("Missing private_key or funder_address from Keychain")
        return creds
    except Exception as e:
        raise RuntimeError(f"Cannot resolve Polymarket credentials: {e}") from e


class PolymarketClient:
    """CLOB client for order placement and orderbook queries."""

    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self._clob_client = None

        if not paper_mode:
            self._init_live_client()

    def _init_live_client(self):
        """Initialize py-clob-client with keychain credentials."""
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
            for entry in data.get(side, []):
                entry["price"] = float(entry["price"])
                entry["size"] = float(entry["size"])

        return data

    def get_best_bid_ask(self, token_id: str) -> tuple[float, float, float, float]:
        """Get best bid/ask with sizes for VWMP calculation.

        Returns: (best_bid, best_ask, bid_size, ask_size)
        """
        book = self.get_orderbook(token_id)
        bids = book.get("bids", [])
        asks = book.get("asks", [])

        best_bid = bids[0]["price"] if bids else 0.0
        best_ask = asks[0]["price"] if asks else 1.0
        bid_size = bids[0]["size"] if bids else 0.0
        ask_size = asks[0]["size"] if asks else 0.0

        return best_bid, best_ask, bid_size, ask_size

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
        if self.paper_mode:
            logger.warning("place_limit_order called in paper mode — no-op")
            return None

        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        side_const = BUY if side == "BUY" else SELL
        order_args = OrderArgs(
            price=price, size=size, side=side_const, token_id=token_id
        )

        signed = self._clob_client.create_order(order_args)
        result = self._clob_client.post_order(signed)

        logger.info("Order placed: %s %s @ %.3f x %.1f → %s",
                     side, token_id[:12], price, size, result.get("status"))
        return result

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel a pending order."""
        if self.paper_mode:
            return None
        result = self._clob_client.cancel(order_id)
        logger.info("Order cancelled: %s → %s", order_id, result.get("status"))
        return result

    def get_order_status(self, order_id: str) -> Optional[dict]:
        """Fetch a live order's latest exchange status."""
        if self.paper_mode:
            return None

        try:
            if hasattr(self._clob_client, "get_order"):
                result = self._clob_client.get_order(order_id)
            elif hasattr(self._clob_client, "get_orders"):
                orders = self._clob_client.get_orders()
                result = next((o for o in orders if o.get("id") == order_id), None)
            else:
                logger.warning("Live client has no order-status method")
                return None
            logger.info("Order status: %s → %s", order_id, result.get("status") if result else "missing")
            return result
        except Exception as exc:
            logger.warning("Order status fetch failed for %s: %s", order_id, exc)
            return None

    def get_open_orders(self) -> list[dict]:
        """Return all currently open exchange orders for the funded wallet."""
        if self.paper_mode:
            return []

        try:
            try:
                from py_clob_client.clob_types import OpenOrderParams

                result = self._clob_client.get_orders(OpenOrderParams()) or []
            except (ImportError, TypeError):
                result = self._clob_client.get_orders() or []

            if isinstance(result, dict):
                result = result.get("data", []) or []
            return list(result)
        except Exception as exc:
            logger.warning("Open-order fetch failed: %s", exc)
            return []

    def get_positions_from_api(self) -> Optional[list[dict]]:
        """Fetch authoritative live positions from Polymarket's data API."""
        if self.paper_mode:
            return []

        try:
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

                avg_price = float(item.get("avgPrice", 0) or item.get("avg_price", 0) or 0)
                initial_value = float(item.get("initialValue", 0) or 0)
                positions.append({
                    "token_id": token_id,
                    "condition_id": item.get("conditionId", "") or item.get("condition_id", ""),
                    "size": round(size, 4),
                    "avg_price": round(avg_price, 6),
                    "cost": round(initial_value, 4) if initial_value > 0 else round(size * avg_price, 4),
                    "side": item.get("outcome", "") or item.get("side", ""),
                    "current_value": round(float(item.get("currentValue", 0) or 0), 4),
                    "cash_pnl": round(float(item.get("cashPnl", 0) or 0), 4),
                    "cur_price": round(float(item.get("curPrice", 0) or 0), 6),
                    "redeemable": bool(item.get("redeemable", False)),
                    "title": item.get("title", ""),
                    "end_date": item.get("endDate", ""),
                })
            return positions
        except Exception as exc:
            logger.warning("Live position fetch failed: %s", exc)
            return None

    def get_balance(self) -> float:
        """Get USDC balance."""
        if self.paper_mode:
            return 0.0
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        resp = self._clob_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return int(resp["balance"]) / 1e6
