# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Authority basis: offline scheduled physical-exit acceptance task.
"""Offline venue transport, preserving real client, adapter and SDK signing.

Only upstream responses, SDK salt/time entropy and transport are controlled.
This helper never writes commands, envelopes, fills, or lifecycle projections.
The executor must durably bind the real signed identity before fake POST.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import patch

import httpx

from src.data.polymarket_client import HeldOrderbookReadResult, PolymarketClient
from src.venue.polymarket_v2_adapter import (
    PolymarketV2Adapter,
    _deterministic_v2_order_id,
    _signed_order_bytes,
)


# Public, universally known test scalar 1; never sourced from any credential.
_PUBLIC_TEST_KEY = "0x" + "0" * 63 + "1"
_SIGNING_LOCK = threading.Lock()


class _ReplayClient(PolymarketClient):
    """Replace HTTP transport only; inherit real placement and response parsing."""

    def __init__(self, venue, **kwargs):
        super().__init__(**kwargs)
        self.venue = venue
        self._v2_adapter = venue.adapter

    def _public_get(self, path, *, params=None, **kwargs):
        return self.venue.http_response("GET", path, params=params)

    def _public_post(self, path, *, json_body, **kwargs):
        return self.venue.http_response("POST", path, body=json_body)

    def get_held_orderbook_snapshots_hard_deadline(self, token_ids, *, timeout_seconds):
        """Compose fake process I/O with real batch parsing and result type.

        Process spawning/termination is outside this acceptance proof: a spawned
        Python process cannot inherit the scenario transport. All quote facts
        still come through the normal batch HTTP-response parser.
        """
        if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) < 0.01:
            raise TimeoutError("held orderbook batch has insufficient remaining deadline")
        books = self.get_orderbook_snapshots(token_ids, timeout=timeout_seconds)
        return HeldOrderbookReadResult(
            books, terminal_reason="complete", captured_at=self.venue.now(),
            attempted_token_ids=token_ids,
            captured_at_by_token={token: self.venue.now() for token in books},
        )


class _ReplaySdk:
    """Real SDK signer/order builder, with fake exchange transport."""

    def __init__(self, venue):
        from py_clob_client_v2.clob_types import ApiCreds
        from py_clob_client_v2.order_builder.builder import OrderBuilder
        from py_clob_client_v2.signer import Signer

        self.venue = venue
        self.host = "https://scheduled-exit.invalid"
        self.signer = Signer(_PUBLIC_TEST_KEY, 137)
        self.builder = OrderBuilder(self.signer, funder=self.signer.address())
        # Public fake values used solely by real read-header serialization.
        self.creds = ApiCreds("offline-test-key", "dGVzdA==", "offline-test")
        self.use_server_time = True
        self.sign_count = 0

    def assert_level_2_auth(self):
        assert self.signer is not None and self.creds is not None

    def get_ok(self):
        self.venue.record("get_ok")
        return {"ok": True}

    def post_heartbeat(self, heartbeat_id=None):
        self.venue.record("post_heartbeat")
        return {"heartbeat_id": heartbeat_id or "offline-scheduled-exit-heartbeat"}

    def get_neg_risk(self, token_id):
        self.venue.check_token(token_id)
        return False

    def get_tick_size(self, token_id):
        self.venue.check_token(token_id)
        return "0.01"

    def get_fee_rate_bps(self, token_id):
        self.venue.check_token(token_id)
        return self.venue.fee_rate_bps

    def get_order_book(self, token_id):
        return self.venue.book(token_id)

    def get_clob_market_info(self, condition_id):
        return self.venue.market(condition_id)

    def create_order(self, order_args, options=None):
        import py_clob_client_v2.order_builder.builder as builder_module
        from py_clob_client_v2.order_utils import ExchangeOrderBuilderV2

        self.venue.check_token(order_args.token_id)
        self.sign_count += 1
        salt = self.sign_count
        timestamp_ns = int(self.venue.now().timestamp() * 1_000_000) * 1_000

        def exchange_builder(contract, chain_id, signer):
            return ExchangeOrderBuilderV2(
                contract, chain_id, signer, generate_salt=lambda: salt
            )

        # Freeze only the SDK's entropy inputs, retaining its exact V2 encoder,
        # typed-data hash and ECDSA signature implementation.
        with _SIGNING_LOCK, patch.object(
            builder_module, "time", SimpleNamespace(time_ns=lambda: timestamp_ns)
        ), patch.object(builder_module, "ExchangeOrderBuilderV2", exchange_builder):
            signed = self.builder.build_order(order_args, options, version=2)
        self.venue.record("create_order", token_id=str(order_args.token_id))
        return signed

    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        order_id = _deterministic_v2_order_id(
            self, order, chain_id=137, neg_risk=False
        )
        signed_bytes = _signed_order_bytes(order)
        signed_hash = hashlib.sha256(signed_bytes).hexdigest()
        # Independent, fresh read-only connection proves commit-before-POST.
        with sqlite3.connect(
            self.venue.trade_db_path.as_uri() + "?mode=ro", uri=True
        ) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT command_id, state, envelope_id FROM venue_commands "
                "WHERE venue_order_id = ?", (order_id,)
            ).fetchall()
            assert len(rows) == 1, "POST requires one durable command identity"
            command = rows[0]
            assert command["state"] == "SUBMITTING", "POST requires SUBMITTING"
            persisted = conn.execute(
                "SELECT signed_order_blob, signed_order_hash, "
                "canonical_pre_sign_payload_hash, raw_request_hash "
                "FROM venue_submission_envelopes WHERE order_id = ? "
                "AND signed_order_hash = ?", (order_id, signed_hash)
            ).fetchone()
            assert persisted is not None, "POST requires durable signed envelope"
            assert bytes(persisted["signed_order_blob"]) == signed_bytes
            original = conn.execute(
                "SELECT canonical_pre_sign_payload_hash, raw_request_hash "
                "FROM venue_submission_envelopes WHERE envelope_id = ?",
                (command["envelope_id"],),
            ).fetchone()
            assert original is not None
            assert tuple(original) == tuple(persisted)[2:]
        side = "SELL" if int(order.side) == 1 else "BUY"
        size = Decimal(order.makerAmount if side == "SELL" else order.takerAmount) / 1_000_000
        price = Decimal(order.takerAmount if side == "SELL" else order.makerAmount) / 1_000_000 / size
        matched = min(size, self.venue.fill_size)
        assert matched > 0 and matched <= self.venue.liquidity
        assert str(order.tokenId) == self.venue.held_token, "signed token is not held"
        assert size <= self.venue.held_shares, "signed SELL exceeds external inventory"
        assert self.venue.window_open(), "executable window expired before POST"
        assert self.venue.bid >= price, "SELL limit does not cross current bid"
        assert Decimal("0.05") <= self.venue.bid <= Decimal("0.95")
        assert not post_only and str(order_type) == "FAK", "physical exit must use FAK"
        assert side == "SELL", "this replay transport accepts only held SELL"
        row = {
            "id": order_id, "orderID": order_id, "status": "MATCHED",
            "created_at": self.venue.now().isoformat(),
            "market": self.venue.condition_id, "asset_id": str(order.tokenId),
            "side": side, "price": str(price), "original_size": str(size),
            "size_matched": str(matched), "associate_trades": [],
        }
        self.venue.orders[order_id] = row
        self.venue.fill_prices[order_id] = self.venue.bid
        self.venue.record(
            "post_order", order_id=order_id, command_id=command["command_id"],
            signed_order_hash=signed_hash, size=str(size), filled_size=str(matched),
            price=str(price), fill_price=str(self.venue.bid),
            order_type=str(order_type), durable_before_post=True,
        )
        # No trade IDs or authority envelopes: ACK cannot become CONFIRMED.
        return {"success": True, "orderID": order_id, "status": "MATCHED",
                "makingAmount": str(matched), "takingAmount": str(matched * self.venue.bid)}

    def get_order(self, order_id):
        self.venue.record("get_order", order_id=order_id)
        return deepcopy(self.venue.orders.get(order_id))

    def get_open_orders(self, **kwargs):
        self.venue.record("get_open_orders")
        return [deepcopy(row) for row in self.venue.orders.values()
                if row["status"] == "LIVE"]

    def get_trades(self, **kwargs):
        self.venue.record("get_trades")
        return deepcopy(self.venue.confirmed_trades)

    def get_positions(self):
        return self.venue.positions_payload()

    def update_balance_allowance(self, params):
        self.venue.record("update_balance_allowance")
        return {}

    def get_balance_allowance(self, params):
        token = str(getattr(params, "token_id", "") or "")
        self.venue.record("get_balance_allowance", token_id=token)
        balance = (int(self.venue.held_shares * 1_000_000)
                   if token == self.venue.held_token else int(self.venue.cash * 1_000_000))
        if token and token != self.venue.held_token:
            self.venue.check_token(token)
            balance = 0
        return {"balance": str(balance), "allowance": str(balance)}


class ScheduledExitVenue:
    """Synthetic responses and evidence for one held binary outcome."""

    def __init__(self, *, trade_db_path, event_clock, condition_id, yes_token_id,
                 no_token_id, held_token, held_shares=5, bid="0.10", fill_size=2,
                 window_end=None, liquidity=100, fee_rate_bps=0, cash=100,
                 min_order_size=1, market_end=None, average_entry_price="0.12", restored_window=None):
        self.trade_db_path = Path(trade_db_path).resolve()
        if not self.trade_db_path.is_relative_to(Path("/tmp")):
            raise ValueError("replay database must be synthetic and under /tmp")
        self.event_clock = event_clock
        self.condition_id = str(condition_id)
        self.yes_token_id, self.no_token_id = str(yes_token_id), str(no_token_id)
        self.held_token = str(held_token)
        assert self.yes_token_id != self.no_token_id
        assert all(token.isdecimal() for token in (self.yes_token_id, self.no_token_id))
        self.check_token(self.held_token)
        self.held_shares = Decimal(str(held_shares))
        self.bid = Decimal(str(bid))
        self.fill_size = Decimal(str(fill_size))
        self.liquidity = Decimal(str(liquidity))
        self.window_end = window_end
        self.restored_window = restored_window
        self.market_end = market_end
        self.fee_rate_bps = int(fee_rate_bps)
        self.cash = Decimal(str(cash))
        self.min_order_size = Decimal(str(min_order_size))
        self.average_entry_price = Decimal(str(average_entry_price))
        self._applied_trade_ids = set()
        self.calls, self.confirmed_trades = [], []
        self.orders = {}
        self.fill_prices = {}
        self.sdk = _ReplaySdk(self)
        self.funder_address = self.sdk.signer.address()
        evidence = self.trade_db_path.parent / "offline-q1-egress-test.txt"
        evidence.write_text(
            "Q1 Zeus egress evidence sentinel\nauthority_basis: test\n"
            "operator_attestation: test current egress accepted\n"
            "live_side_effects: none; HTTPS GET probes only\n"
            "raw_secrets_or_signed_payloads: none\nprobe_results:\n"
            '[{"effective_url":"https://clob.polymarket.com/ok","status_code":200}]\n'
        )
        self.adapter = PolymarketV2Adapter(
            host=self.sdk.host, funder_address=self.funder_address,
            signer_key=_PUBLIC_TEST_KEY, chain_id=137, signature_type=0,
            q1_egress_evidence_path=evidence, client_factory=lambda **kw: self.sdk,
            rpc_call=self._forbid_rpc,
        )
        venue = self

        class BoundReplayClient(_ReplayClient):
            def __init__(self, **kwargs):
                super().__init__(venue, **kwargs)

        self.client_class = BoundReplayClient
        self.client = self.make_client()

    def make_client(self, **kwargs):
        return self.client_class(**kwargs)

    @staticmethod
    def _forbid_rpc(*args, **kwargs):
        raise AssertionError("offline replay has no RPC transport")

    def now(self):
        value = self.event_clock()
        assert isinstance(value, datetime) and value.tzinfo is not None
        return value.astimezone(timezone.utc)

    def record(self, operation, **details):
        self.calls.append({"operation": operation, "at": self.now().isoformat(),
                           "wall_monotonic": time.perf_counter(), **details})

    def check_token(self, token_id):
        assert str(token_id) in {self.yes_token_id, self.no_token_id}

    def window_open(self):
        if self.restored_window is not None:
            start, end = self.restored_window
            if start <= self.now() < end:
                return True
        if self.window_end is None:
            return True
        end = self.window_end
        if isinstance(end, str):
            end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        assert isinstance(end, datetime) and end.tzinfo is not None
        return self.now() < end

    @property
    def posts(self):
        return [call for call in self.calls if call["operation"] == "post_order"]

    @property
    def post_calls(self):
        return self.posts

    def book(self, token_id):
        self.check_token(token_id)
        self.record("book", token_id=str(token_id))
        bid = self.bid if str(token_id) == self.held_token else Decimal("0.50")
        return {"asset_id": str(token_id), "market": self.condition_id,
                "timestamp": str(int(self.now().timestamp() * 1000)),
                "bids": [{"price": str(bid), "size": str(self.liquidity)}]
                if self.liquidity > 0 and self.window_open() else [],
                "asks": [{"price": str(bid + Decimal("0.01")), "size": "100"}],
                "tick_size": "0.01", "min_order_size": str(self.min_order_size), "neg_risk": False}

    def market(self, condition_id):
        assert str(condition_id) == self.condition_id
        self.record("market", condition_id=self.condition_id)
        end = self.market_end.isoformat() if isinstance(self.market_end, datetime) else self.market_end
        return {"condition_id": self.condition_id, "question_id": "scheduled-exit-question",
                "active": True, "closed": False, "archived": False,
                "accepting_orders": True, "enable_order_book": True,
                "minimum_tick_size": "0.01", "minimum_order_size": str(self.min_order_size),
                "neg_risk": False, "end_date_iso": end, "endDate": end,
                "tokens": [{"token_id": self.yes_token_id, "outcome": "Yes"},
                           {"token_id": self.no_token_id, "outcome": "No"}]}

    def positions_payload(self):
        """Raw external Data API holdings, including confirmed partial exits."""
        self.record("positions", token_id=self.held_token, shares=str(self.held_shares))
        if self.held_shares < Decimal("0.01"):
            return []
        return [{"asset": self.held_token, "size": str(self.held_shares),
                 "conditionId": self.condition_id,
                 "avgPrice": str(self.average_entry_price),
                 "initialValue": str(self.held_shares * self.average_entry_price),
                 "currentValue": str(self.held_shares * self.bid),
                 "curPrice": str(self.bid), "redeemable": False,
                 "outcome": "Yes" if self.held_token == self.yes_token_id else "No"}]

    def http_response(self, method, path, *, params=None, body=None):
        path = urlparse(path).path
        status = 200
        if method == "GET" and path == "/book":
            payload = self.book(params["token_id"])
        elif method == "POST" and path == "/books":
            payload = [self.book(item["token_id"]) for item in body]
        elif method == "GET" and path.startswith("/markets/"):
            payload = self.market(path.rsplit("/", 1)[1])
        elif method == "GET" and path == "/fee-rate":
            payload = {"base_fee": self.fee_rate_bps}
        elif method == "GET" and path == "/time":
            payload = int(self.now().timestamp())
        elif method == "POST" and path == "/v1/heartbeats":
            payload = self.sdk.post_heartbeat((body or {}).get("heartbeat_id"))
        elif method == "GET" and path.startswith("/data/order/"):
            payload = self.sdk.get_order(path.rsplit("/", 1)[1])
            if payload is None:
                status, payload = 404, {"error": "not found"}
        elif method == "GET" and path in {"/data/orders", "/data/trades"}:
            from py_clob_client_v2.constants import END_CURSOR
            payload = {"data": self.sdk.get_trades() if path.endswith("trades")
                       else self.sdk.get_open_orders(), "next_cursor": END_CURSOR}
        else:
            raise AssertionError(f"unexpected offline transport: {method} {path}")
        return httpx.Response(status, json=payload,
                              request=httpx.Request(method, self.sdk.host + path))

    def install_httpx_transport(self, monkeypatch):
        """Keep real authenticated deadline readers; replace their HTTP only."""
        sync_client, async_client = httpx.Client, httpx.AsyncClient

        def handler(request):
            assert request.url.host == urlparse(self.sdk.host).hostname
            body = json.loads(request.content) if request.content else None
            return self.http_response(request.method, str(request.url),
                                      params=dict(request.url.params), body=body)

        def sync_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return sync_client(*args, **kwargs)

        def async_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", sync_factory)
        monkeypatch.setattr(httpx, "AsyncClient", async_factory)

    def cash_rpc_batch(self, rpc_url, calls, *, timeout_seconds):
        """External RPC returns unavailable receipts, never invented cash proof.

        The registered cash collector, ABI decoder and repository remain real.
        Its spawned HTTP process is an external-I/O seam excluded from replay.
        """
        assert float(timeout_seconds) > 0
        result = []
        known_txs = {trade["transaction_hash"] for trade in self.confirmed_trades}
        for method, params in calls:
            self.record("cash_rpc", method=method)
            if method == "eth_chainId":
                result.append("0x89")
            elif method == "eth_getBlockByNumber" and params == ["finalized", False]:
                result.append({"number": "0x1000", "hash": "0x" + hashlib.sha256(
                    b"offline-scheduled-exit-finalized-block").hexdigest()})
            elif method == "eth_getTransactionReceipt":
                assert len(params) == 1 and params[0] in known_txs
                result.append(None)
            else:
                raise AssertionError(f"unexpected offline cash RPC: {method}")
        return result

    def install_chain_transports(self, monkeypatch):
        """Bind only external identity/Data API/RPC reads for scheduled owners."""
        import src.data.polymarket_client as client_module
        import src.ingest.fill_cash_observer as cash_observer

        # The real Data API parser asks this resolver for its wallet address.
        # All material here is the same public test identity used by signing.
        monkeypatch.setattr(client_module, "_resolve_credentials", lambda: {
            "funder_address": self.funder_address, "private_key": _PUBLIC_TEST_KEY,
            "api_creds": self.sdk.creds,
        })

        def positions_get(url, *, params=None, **kwargs):
            parsed = urlparse(url)
            assert parsed.netloc == "data-api.polymarket.com" and parsed.path == "/positions"
            assert (params or {}).get("user") == self.funder_address
            assert int((params or {}).get("offset", "0")) == 0
            return httpx.Response(200, json=self.positions_payload(),
                                  request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", positions_get)
        monkeypatch.setattr(cash_observer, "_json_rpc_batch_call_hard_deadline",
                            self.cash_rpc_batch)

    def confirmed_trade_payload(self, *, order_id=None, trade_id=None,
                                fee_paid_micro=None, size=None, price=None):
        """Build exchange trade data; the real ingestor alone creates facts."""
        order_id = order_id or self.posts[-1]["order_id"]
        row = self.orders[order_id]
        size = Decimal(str(size if size is not None else row["size_matched"]))
        price = Decimal(str(price if price is not None else self.fill_prices[order_id]))
        if fee_paid_micro is None:
            fee_paid_micro = int((size * Decimal(self.fee_rate_bps) / 10_000
                                  * price * (1 - price) * 1_000_000)
                                 .to_integral_value(rounding=ROUND_HALF_UP))
        trade_id = trade_id or "offline-confirmed-" + order_id[2:18]
        return {"event_type": "trade", "id": trade_id, "status": "CONFIRMED",
                "market": self.condition_id, "asset_id": self.held_token,
                "side": "SELL", "trader_side": "TAKER", "taker_order_id": order_id,
                "size": str(size), "price": str(price),
                "fee_rate_bps": self.fee_rate_bps, "fee_paid_micro": int(fee_paid_micro),
                "match_time": row["created_at"], "timestamp": str(int(self.now().timestamp())),
                "transaction_hash": "0x" + hashlib.sha256(trade_id.encode()).hexdigest(),
                "maker_orders": [{"order_id": "offline-maker-" + trade_id,
                                  "asset_id": self.held_token, "side": "BUY",
                                  "matched_amount": str(size), "price": str(price)}]}

    def confirm_trade(self, **kwargs):
        payload = self.confirmed_trade_payload(**kwargs)
        self.confirmed_trades.append(deepcopy(payload))
        order = self.orders[payload["taker_order_id"]]
        order["status"] = "MATCHED" if Decimal(order["size_matched"]) == Decimal(order["original_size"]) else "CANCELED"
        if payload["id"] not in self._applied_trade_ids:
            self._applied_trade_ids.add(payload["id"])
            self.held_shares -= Decimal(payload["size"])
            assert self.held_shares >= 0, "fake CONFIRMED cannot oversell held inventory"
            self.cash += (Decimal(payload["size"]) * Decimal(payload["price"])
                          - Decimal(payload["fee_paid_micro"]) / 1_000_000)
        return payload

    confirm = confirm_trade


def make_scheduled_exit_venue(**kwargs):
    return ScheduledExitVenue(**kwargs)
