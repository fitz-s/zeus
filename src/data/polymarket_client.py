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

    Returns dict with 'private_key' and 'funder_address'.
    Raises RuntimeError if keychain resolution fails.
    """
    try:
        result = subprocess.run(
            ["python3", "-c",
             "from bin.keychain_resolver import resolve_polymarket; "
             "import json; print(json.dumps(resolve_polymarket()))"],
            capture_output=True, text=True, timeout=10,
            cwd="/Users/leofitz/.openclaw",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Keychain resolution failed: {result.stderr}")
        return json.loads(result.stdout)
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

    def get_balance(self) -> float:
        """Get USDC balance."""
        if self.paper_mode:
            return 0.0
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        resp = self._clob_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return int(resp["balance"]) / 1e6
