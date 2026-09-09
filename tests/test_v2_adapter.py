# Lifecycle: created=2026-04-27; last_reviewed=2026-09-02; last_reused=2026-09-02
# Purpose: R3 Z2 Polymarket V2 adapter and submission envelope antibodies.
# Reuse: Run when V2 SDK adapter, envelope provenance, or Q1 preflight behavior changes.
# Created: 2026-04-27
# Last reused/audited: 2026-09-02
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
#                  + 2026-05-17 public CLOB HTTP reuse for live opening_hunt backpressure.
#                  + 2026-08-10 certified taker/maker mode matrix and adapter-owned heartbeat transport.
"""R3 Z2 Polymarket V2 adapter antibodies."""

from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib
import json
import sqlite3
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps


@dataclass(frozen=True)
class FakeSnapshot:
    condition_id: str = "cond-123"
    question_id: str = "question-123"
    yes_token_id: str = "yes-token"
    no_token_id: str = "no-token"
    tick_size: Decimal = Decimal("0.01")
    min_order_size: Decimal = Decimal("5")
    neg_risk: bool = True
    fee_details: dict = None
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    freshness_window_seconds: int = 300

    def __post_init__(self):
        if self.fee_details is None:
            object.__setattr__(self, "fee_details", {"bps": 0, "builder_fee_bps": 0})


class FakeOneStepClient:
    def __init__(self, response=None):
        self.response = response or {"orderID": "ord-one-step", "status": "LIVE"}
        self.calls = []

    def get_ok(self):
        self.calls.append(("get_ok",))
        return {"ok": True}

    def get_neg_risk(self, token_id):
        self.calls.append(("get_neg_risk", token_id))
        return True

    def get_tick_size(self, token_id):
        self.calls.append(("get_tick_size", token_id))
        return "0.01"

    def get_fee_rate_bps(self, token_id):
        self.calls.append(("get_fee_rate_bps", token_id))
        return 0

    def create_and_post_order(self, order_args, options=None, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("create_and_post_order", order_args, options, order_type, post_only, defer_exec))
        return self.response


class FakeTwoStepClient:
    def __init__(self, post_response=None, signed_order=b"fake-signed-order"):
        self.post_response = post_response or {"orderID": "ord-two-step", "status": "LIVE"}
        self.signed_order = signed_order
        self.calls = []

    def get_ok(self):
        self.calls.append(("get_ok",))
        return {"ok": True}

    def get_neg_risk(self, token_id):
        self.calls.append(("get_neg_risk", token_id))
        return True

    def get_tick_size(self, token_id):
        self.calls.append(("get_tick_size", token_id))
        return "0.01"

    def get_fee_rate_bps(self, token_id):
        self.calls.append(("get_fee_rate_bps", token_id))
        return 0

    def create_order(self, order_args, options=None):
        self.calls.append(("create_order", order_args, options))
        return self.signed_order

    def get_order_book(self, token_id):
        self.calls.append(("get_order_book", token_id))
        return {
            "asset_id": token_id,
            "bids": [{"price": "0.49", "size": "100"}],
            "asks": [{"price": "0.50", "size": "100"}],
        }

    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        return self.post_response


class FakePreflightOnlyClient:
    """Preflight-capable client that cannot provide local submit snapshot facts."""

    def __init__(self):
        self.calls = []

    def get_ok(self):
        self.calls.append(("get_ok",))
        return {"ok": True}


class FakeFlakyPreflightClient:
    def __init__(self):
        self.calls = []

    def get_ok(self):
        self.calls.append(("get_ok",))
        if len(self.calls) == 1:
            raise RuntimeError("transient preflight transport")
        return {"ok": True}


class FakeCreateOrderFailureClient(FakeTwoStepClient):
    def create_order(self, order_args, options=None):
        self.calls.append(("create_order", order_args, options))
        raise RuntimeError("local signing failed")


class FakeCreateOrderTransportFailureClient(FakeTwoStepClient):
    def create_order(self, order_args, options=None):
        self.calls.append(("create_order", order_args, options))
        from py_clob_client_v2.exceptions import PolyApiException

        raise PolyApiException(error_msg="Request exception!")


class FakePostOrderFailureClient(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        raise TimeoutError("post timed out")


class FakeGeoblockClient(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        raise RuntimeError(
            "PolyApiException[status_code=403, error_message={'error': 'Trading "
            "restricted in your region, please refer to available regions - "
            "https://docs.polymarket.com/developers/CLOB/geoblock'}]"
        )


class FakeOrderManagerNotReady425Client(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        from py_clob_client_v2.exceptions import PolyApiException

        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        response = SimpleNamespace(
            status_code=425,
            json=lambda: {"error": "order manager not ready, please retry"},
        )
        raise PolyApiException(response)


class FakeTradingDisabled503Client(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        from py_clob_client_v2.exceptions import PolyApiException

        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        response = SimpleNamespace(
            status_code=503,
            json=lambda: {"error": "trading is disabled"},
        )
        raise PolyApiException(response)


class FakePostOnlyCross400Client(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        from py_clob_client_v2.exceptions import PolyApiException

        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        response = SimpleNamespace(
            status_code=400,
            json=lambda: {"error": "invalid post-only order: order crosses book"},
        )
        raise PolyApiException(response)


class FakeFokKilledClient(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        raise RuntimeError(
            "PolyApiException[status_code=400, error_message={'error': \"order couldn't "
            "be fully filled. FOK orders are fully filled or killed.\", "
            "'orderID': '0xexpected-order-id'}]"
        )


class FakeFakNoMatchClient(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        raise RuntimeError(
            "PolyApiException[status_code=400, error_message={'error': 'no orders "
            "found to match with FAK order. FAK orders are partially filled or "
            "killed if no match is found.', 'orderID': '0xexpected-order-id'}]"
        )


class FakeInvalidSafeSignatureTwoStepClient(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        raise RuntimeError(
            "PolyApiException[status_code=400, "
            "error_message={'error':'invalid POLY_GNOSIS_SAFE signature'}]"
        )


class FakeInvalidSafeSignatureOneStepClient(FakeOneStepClient):
    def __init__(self):
        super().__init__(response={"orderID": "ord-recovered", "status": "LIVE"})
        self._refreshed = False
        self.derived_creds = FakeApiCreds(
            "derived-submit-key",
            "derived-submit-secret",
            "derived-submit-passphrase",
        )

    def derive_api_key(self):
        self.calls.append(("derive_api_key",))
        return self.derived_creds

    def set_api_creds(self, creds):
        self.calls.append(("set_api_creds", creds))
        self._refreshed = True

    def create_and_post_order(self, order_args, options=None, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("create_and_post_order", order_args, options, order_type, post_only, defer_exec))
        if not self._refreshed:
            raise RuntimeError(
                "PolyApiException[status_code=400, "
                "error_message={'error':'invalid POLY_GNOSIS_SAFE signature'}]"
            )
        return self.response


class FakeBalanceAllowanceClient:
    def __init__(self, response=None):
        self.response = response or {"balance": "100000000", "allowance": "50000000"}
        self.calls = []

    def get_balance_allowance(self, params):
        self.calls.append(("get_balance_allowance", params))
        return dict(self.response)

    def update_balance_allowance(self, params):
        self.calls.append(("update_balance_allowance", params))
        return {}


class FakeStaleL2CredsBalanceClient(FakeBalanceAllowanceClient):
    def __init__(self):
        super().__init__()
        self._refreshed = False
        self.derived_creds = FakeApiCreds("derived-key", "derived-secret", "derived-passphrase")

    def derive_api_key(self):
        self.calls.append(("derive_api_key",))
        return self.derived_creds

    def set_api_creds(self, creds):
        self.calls.append(("set_api_creds", creds))
        self._refreshed = True

    def update_balance_allowance(self, params):
        self.calls.append(("update_balance_allowance", params))
        if not self._refreshed:
            raise RuntimeError("PolyApiException[status_code=401, error_message={'error':'Unauthorized/Invalid api key'}]")
        return {}


class FakeOpenOrdersClient:
    def __init__(self):
        self.calls = []

    def get_open_orders(self, **kwargs):
        self.calls.append(("get_open_orders", kwargs))
        return [{
            "orderID": "ord-open",
            "status": "LIVE",
            "original_size": "10000000",
            "size_matched": "0",
        }]


class FakeLegacyGetOrdersClient:
    def __init__(self):
        self.calls = []

    def get_orders(self, **kwargs):
        self.calls.append(("get_orders", kwargs))
        return {"data": [{
            "id": "ord-legacy",
            "state": "LIVE",
            "original_size": "10000000",
            "size_matched": "0",
        }]}


class FakeTradesClient:
    def __init__(self):
        self.calls = []

    def get_trades(self, **kwargs):
        self.calls.append(("get_trades", kwargs))
        return [{"id": "trade-open", "status": "MATCHED"}]


class FakeCancelOrderClient:
    def __init__(self, response=None):
        self.response = response or {"canceled": ["ord-cancel"], "not_canceled": []}
        self.calls = []

    def cancel_order(self, payload):
        self.calls.append(("cancel_order", payload))
        return self.response


class FakeAuthClient:
    derive_response = None
    derive_error = None
    instances = []

    def __init__(
        self,
        host,
        chain_id,
        *,
        key=None,
        creds=None,
        signature_type=None,
        funder=None,
        use_server_time=False,
    ):
        self.host = host
        self.chain_id = chain_id
        self.key = key
        self.creds = creds
        self.signature_type = signature_type
        self.funder = funder
        self.use_server_time = use_server_time
        self.calls = []
        type(self).instances.append(self)

    def create_or_derive_api_key(self):
        self.calls.append(("create_or_derive_api_key",))
        if self.derive_error is not None:
            raise self.derive_error
        return self.derive_response

    def derive_api_key(self):
        self.calls.append(("derive_api_key",))
        if self.derive_error is not None:
            raise self.derive_error
        return self.derive_response

    def set_api_creds(self, creds):
        self.calls.append(("set_api_creds", creds))
        self.creds = creds


@dataclass(frozen=True)
class FakeApiCreds:
    api_key: str
    api_secret: str
    api_passphrase: str


def _install_fake_py_clob_client_v2(monkeypatch):
    package = types.ModuleType("py_clob_client_v2")
    client_module = types.ModuleType("py_clob_client_v2.client")
    clob_types_module = types.ModuleType("py_clob_client_v2.clob_types")
    client_module.ClobClient = FakeAuthClient
    clob_types_module.ApiCreds = FakeApiCreds
    package.client = client_module
    package.clob_types = clob_types_module
    monkeypatch.setitem(sys.modules, "py_clob_client_v2", package)
    monkeypatch.setitem(sys.modules, "py_clob_client_v2.client", client_module)
    monkeypatch.setitem(sys.modules, "py_clob_client_v2.clob_types", clob_types_module)
    return FakeApiCreds


def _intent(direction: Direction = Direction("buy_yes"), token_id: str = "yes-token") -> ExecutionIntent:
    return ExecutionIntent(
        direction=direction,
        target_size_usd=10.0,
        limit_price=0.50,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="market-123",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.10,
    )


def _adapter(tmp_path: Path, fake_client=None):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    evidence = tmp_path / "q1_zeus_egress_2026-04-27.txt"
    _write_valid_q1_evidence(evidence)
    fake_client = fake_client or FakeOneStepClient()
    return PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=evidence,
        client_factory=lambda **kwargs: fake_client,
    ), fake_client


def _submit(adapter, envelope, *, before_post=None):
    """Submit with a test persister that represents a successful durable write."""

    persister = before_post or _test_identity_receipt
    return adapter.submit(envelope, before_post=persister)


def _test_identity_receipt(signed_envelope, **overrides):
    from src.venue.polymarket_v2_adapter import (
        _issue_signed_identity_persistence_receipt,
    )

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                venue_order_id TEXT
            );
            CREATE TABLE venue_submission_envelopes (
                envelope_id TEXT PRIMARY KEY,
                order_id TEXT,
                signed_order_hash TEXT,
                canonical_pre_sign_payload_hash TEXT,
                raw_request_hash TEXT
            );
            """
        )
        identity = {
            "order_id": signed_envelope.order_id,
            "signed_order_hash": signed_envelope.signed_order_hash,
            "canonical_pre_sign_payload_hash": (
                signed_envelope.canonical_pre_sign_payload_hash
            ),
            "raw_request_hash": signed_envelope.raw_request_hash,
        }
        identity.update(overrides)
        conn.execute(
            "INSERT INTO venue_commands VALUES (?, 'SUBMITTING', ?)",
            ("test-command", identity["order_id"]),
        )
        conn.execute(
            "INSERT INTO venue_submission_envelopes VALUES (?, ?, ?, ?, ?)",
            (
                "test-persisted-envelope",
                identity["order_id"],
                identity["signed_order_hash"],
                identity["canonical_pre_sign_payload_hash"],
                identity["raw_request_hash"],
            ),
        )
        conn.commit()
        return _issue_signed_identity_persistence_receipt(
            conn,
            command_id="test-command",
            envelope_id="test-persisted-envelope",
        )
    finally:
        conn.close()


def test_default_client_factory_prefers_keychain_creds_over_env_and_derivation(monkeypatch):
    ApiCreds = _install_fake_py_clob_client_v2(monkeypatch)
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    keychain_creds = ApiCreds(
        api_key="keychain-key",
        api_secret="keychain-secret",
        api_passphrase="keychain-passphrase",
    )
    monkeypatch.setattr(adapter_mod, "_api_creds_from_keychain", lambda: keychain_creds)
    FakeAuthClient.instances = []
    FakeAuthClient.derive_error = AssertionError("derive should not run when keychain creds exist")
    FakeAuthClient.derive_response = None
    monkeypatch.setenv("POLYMARKET_API_KEY", "env-key")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "env-secret")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "env-passphrase")

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        q1_egress_evidence_path=None,
    )

    client = adapter._sdk_client()

    assert client.creds is keychain_creds
    assert client.calls == []


def test_default_client_factory_reuses_cached_derived_creds(monkeypatch):
    ApiCreds = _install_fake_py_clob_client_v2(monkeypatch)
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    cached_creds = ApiCreds(
        api_key="cached-derived-key",
        api_secret="cached-derived-secret",
        api_passphrase="cached-derived-passphrase",
    )
    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    adapter_mod._store_derived_api_creds(
        host="https://clob.polymarket.com",
        chain_id=137,
        signer_key="test-key",
        signature_type=2,
        funder_address="0xfunder",
        api_creds=cached_creds,
    )
    FakeAuthClient.instances = []
    FakeAuthClient.derive_error = AssertionError("derive should not be called when cached creds exist")
    FakeAuthClient.derive_response = None
    monkeypatch.setattr(adapter_mod, "_api_creds_from_keychain", lambda: None)
    monkeypatch.setenv("POLYMARKET_API_KEY", "env-key")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "env-secret")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "env-passphrase")

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        q1_egress_evidence_path=None,
    )

    client = adapter._sdk_client()

    assert client.creds is cached_creds
    assert client.calls == []


def test_default_client_factory_uses_env_creds_when_keychain_absent(monkeypatch):
    ApiCreds = _install_fake_py_clob_client_v2(monkeypatch)
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    FakeAuthClient.instances = []
    FakeAuthClient.derive_error = AssertionError("derive should not run when env creds exist")
    FakeAuthClient.derive_response = None
    monkeypatch.setattr(adapter_mod, "_api_creds_from_keychain", lambda: None)
    monkeypatch.setenv("POLYMARKET_API_KEY", "env-key")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "env-secret")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "env-passphrase")

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        q1_egress_evidence_path=None,
    )

    client = adapter._sdk_client()

    assert client.creds.api_key == "env-key"
    assert client.creds.api_secret == "env-secret"
    assert client.creds.api_passphrase == "env-passphrase"
    assert client.calls == []


def test_default_client_factory_signs_l1_auth_with_venue_time(monkeypatch):
    _install_fake_py_clob_client_v2(monkeypatch)
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    FakeAuthClient.instances = []
    FakeAuthClient.derive_error = AssertionError("derive should not run when env creds exist")
    FakeAuthClient.derive_response = None
    monkeypatch.setattr(adapter_mod, "_api_creds_from_keychain", lambda: None)
    monkeypatch.setenv("POLYMARKET_API_KEY", "env-key")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "env-secret")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "env-passphrase")

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        q1_egress_evidence_path=None,
    )

    client = adapter._sdk_client()

    assert client.use_server_time is True


def test_default_client_factory_does_not_create_api_key_when_derive_supported(monkeypatch):
    ApiCreds = _install_fake_py_clob_client_v2(monkeypatch)
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    FakeAuthClient.instances = []
    FakeAuthClient.derive_error = None
    FakeAuthClient.derive_response = ApiCreds(
        api_key="derived-key",
        api_secret="derived-secret",
        api_passphrase="derived-passphrase",
    )
    monkeypatch.setattr(adapter_mod, "_api_creds_from_keychain", lambda: None)
    monkeypatch.delenv("POLYMARKET_API_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_API_SECRET", raising=False)
    monkeypatch.delenv("POLYMARKET_API_PASSPHRASE", raising=False)

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        q1_egress_evidence_path=None,
    )

    client = adapter._sdk_client()

    assert client.creds.api_key == "derived-key"
    assert "derive_api_key" in [call[0] for call in client.calls]
    assert "create_or_derive_api_key" not in [call[0] for call in client.calls]


def test_default_client_factory_does_not_override_explicit_api_creds(monkeypatch):
    ApiCreds = _install_fake_py_clob_client_v2(monkeypatch)
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    provided = ApiCreds(
        api_key="provided-key",
        api_secret="provided-secret",
        api_passphrase="provided-passphrase",
    )
    FakeAuthClient.instances = []
    FakeAuthClient.derive_response = None
    FakeAuthClient.derive_error = AssertionError("derive should not be called")
    monkeypatch.setattr(adapter_mod, "_api_creds_from_keychain", lambda: None)
    monkeypatch.setenv("POLYMARKET_API_KEY", "env-key")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "env-secret")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "env-passphrase")

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        api_creds=provided,
        q1_egress_evidence_path=None,
    )

    client = adapter._sdk_client()

    assert client.creds is provided
    assert client.calls == []


def test_default_client_factory_preserves_shared_sdk_transport(monkeypatch):
    _install_fake_py_clob_client_v2(monkeypatch)
    from py_clob_client_v2.http_helpers import helpers
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    original_transport = helpers._http_client
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        api_creds=SimpleNamespace(
            api_key="provided-key",
            api_secret="provided-secret",
            api_passphrase="provided-passphrase",
        ),
        q1_egress_evidence_path=None,
    )

    adapter._sdk_client()

    assert helpers._http_client is original_transport


def test_adapter_threads_configured_signature_type_to_client_factory(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeOneStepClient()

    evidence = tmp_path / "q1_zeus_egress_2026-05-15.txt"
    _write_valid_q1_evidence(evidence)
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=evidence,
        client_factory=factory,
    )

    assert adapter._sdk_client() is not None
    assert captured["signature_type"] == 3
    assert captured["funder_address"] == "0xfunder"


def test_adapter_rejects_unknown_signature_type(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    with pytest.raises(ValueError, match="unsupported CLOB V2 signature_type"):
        PolymarketV2Adapter(
            host="https://clob.polymarket.com",
            funder_address="0xfunder",
            signer_key="test-key",
            signature_type=9,
            q1_egress_evidence_path=tmp_path / "unused.txt",
            client_factory=lambda **kwargs: FakeOneStepClient(),
        )


def test_collateral_payload_syncs_and_reads_with_configured_signature_type(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient()
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == "50000000"
    assert payload["signature_type"] == 3
    assert [call[0] for call in fake.calls[:2]] == [
        "update_balance_allowance",
        "get_balance_allowance",
    ]
    for _name, params in fake.calls[:2]:
        assert getattr(params, "asset_type") == "COLLATERAL"
        assert getattr(params, "signature_type") == 3


def test_v2_adapter_passes_configured_network_timeout_to_sdk_factory(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeBalanceAllowanceClient()

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=factory,
        network_timeout_seconds=0.75,
    )

    assert adapter._sdk_client() is not None
    assert captured["network_timeout_seconds"] == 0.75


def test_v2_data_api_position_fallback_reads_all_pages(tmp_path, monkeypatch):
    import json
    import urllib.parse

    from src.venue import polymarket_v2_adapter as adapter_module

    offsets = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def urlopen(request, **_kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        offset = int(query["offset"][0])
        offsets.append(offset)
        page = [
            {"asset": f"token-{index}", "size": 5}
            for index in range(offset, offset + 500)
        ]
        if offset:
            page = [{"asset": "held-token-after-default-page", "size": 5}]
        return Response(page)

    monkeypatch.setattr(adapter_module.urllib.request, "urlopen", urlopen)
    adapter = adapter_module.PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: object(),
    )

    positions = adapter._get_positions_from_data_api()

    assert len(positions) == 501
    assert positions[-1].raw["asset"] == "held-token-after-default-page"
    assert offsets == [0, 500]


def test_pusd_collateral_payload_does_not_enumerate_ctf_positions(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class FakeClientWithForbiddenPositions(FakeBalanceAllowanceClient):
        def get_positions(self):
            raise AssertionError("BUY pUSD proof must not enumerate CTF positions")

    fake = FakeClientWithForbiddenPositions()
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_pusd_collateral_payload()

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == "50000000"
    assert payload["ctf_token_balances_units"] == {}
    assert payload["ctf_token_allowances_units"] == {}
    assert [call[0] for call in fake.calls[:2]] == [
        "update_balance_allowance",
        "get_balance_allowance",
    ]


def test_target_ctf_collateral_payload_does_not_enumerate_all_positions(
    monkeypatch,
    tmp_path,
):
    from src.venue import polymarket_v2_adapter as adapter_module

    token_id = "21427700"
    calls = []

    def batch_call(_url, requested, *, timeout_seconds):
        calls.extend(requested)
        assert timeout_seconds == 8.0
        return [
            f"0x{100_000_000:064x}",
            f"0x{50_000_000:064x}",
            f"0x{40_000_000:064x}",
            f"0x{21_427_700:064x}",
            f"0x{1:064x}",
            f"0x{1:064x}",
        ]

    monkeypatch.setattr(
        adapter_module,
        "_json_rpc_batch_call_hard_deadline",
        batch_call,
    )
    adapter = adapter_module.PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        polygon_rpc_url="https://polygon.invalid",
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: pytest.fail(
            "targeted submit proof must not create a CLOB client"
        ),
    )

    payload = adapter.get_ctf_collateral_payload(
        token_ids=["", token_id, token_id]
    )

    assert payload["ctf_token_scope"] == "targeted"
    assert payload["ctf_token_balances_units"] == {token_id: 21427700}
    assert payload["ctf_token_allowances_units"] == {token_id: 21427700}
    assert payload["authority_tier"] == "CHAIN"
    assert len(calls) == 6


def test_target_ctf_chain_batch_finishes_inside_submit_guard(
    monkeypatch,
    tmp_path,
):
    from src.venue import polymarket_v2_adapter as adapter_module

    observed = {}

    token_one = "123456789"
    token_two = "987654321"

    def batch_call(_url, requested, *, timeout_seconds):
        observed["calls"] = requested
        observed["timeout_seconds"] = timeout_seconds
        return [
            f"0x{100_000_000:064x}",
            f"0x{90_000_000:064x}",
            f"0x{80_000_000:064x}",
            f"0x{13_000_000:064x}",
            f"0x{1:064x}",
            f"0x{1:064x}",
            f"0x{17_000_000:064x}",
            f"0x{1:064x}",
            f"0x{0:064x}",
        ]

    monkeypatch.setattr(
        adapter_module,
        "_json_rpc_batch_call_hard_deadline",
        batch_call,
    )
    adapter = adapter_module.PolymarketV2Adapter(
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        polygon_rpc_url="https://polygon.invalid",
        network_timeout_seconds=30.0,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: pytest.fail("CLOB is not submit authority"),
    )

    payload = adapter.get_ctf_collateral_payload(
        token_ids=["", token_one, token_one, token_two]
    )

    assert observed["timeout_seconds"] == 10.0
    calls = observed["calls"]
    assert len(calls) == 9
    collateral, spenders = adapter_module._collateral_allowance_contracts(137)
    funder_word = adapter_module._abi_address(adapter.funder_address)
    assert calls[0] == (
        "eth_call",
        [
            {
                "to": collateral,
                "data": "0x70a08231" + funder_word,
            },
            "latest",
        ],
    )
    for index, spender in enumerate(spenders, start=1):
        assert calls[index] == (
            "eth_call",
            [
                {
                    "to": collateral,
                    "data": (
                        "0xdd62ed3e"
                        + funder_word
                        + adapter_module._abi_address(spender)
                    ),
                },
                "latest",
            ],
        )
    for token_offset, token_id in enumerate((token_one, token_two)):
        base = 3 + token_offset * 3
        assert calls[base] == (
            "eth_call",
            [
                {
                    "to": adapter_module.POLYGON_CTF_ADDRESS,
                    "data": (
                        adapter_module.ERC1155_BALANCE_OF_SELECTOR
                        + funder_word
                        + format(int(token_id), "064x")
                    ),
                },
                "latest",
            ],
        )
        for approval_offset, spender in enumerate(spenders, start=1):
            assert calls[base + approval_offset] == (
                "eth_call",
                [
                    {
                        "to": adapter_module.POLYGON_CTF_ADDRESS,
                        "data": (
                            adapter_module.ERC1155_IS_APPROVED_FOR_ALL_SELECTOR
                            + funder_word
                            + adapter_module._abi_address(spender)
                        ),
                    },
                    "latest",
                ],
            )
    assert payload["pusd_allowance_micro"] == 80_000_000
    assert payload["ctf_token_balances_units"] == {
        token_one: 13_000_000,
        token_two: 17_000_000,
    }
    assert payload["ctf_token_allowances_units"] == {
        token_one: 13_000_000,
        token_two: 0,
    }


@pytest.mark.parametrize("token_id", ["not-a-token", "-1", str(2**256)])
def test_target_ctf_chain_batch_rejects_invalid_uint256_token(
    monkeypatch,
    tmp_path,
    token_id,
):
    from src.venue import polymarket_v2_adapter as adapter_module

    monkeypatch.setattr(
        adapter_module,
        "_json_rpc_batch_call_hard_deadline",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid token must fail before Polygon I/O"
        ),
    )
    adapter = adapter_module.PolymarketV2Adapter(
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        polygon_rpc_url="https://polygon.invalid",
        q1_egress_evidence_path=tmp_path / "unused.txt",
    )

    with pytest.raises(
        adapter_module.V2AdapterError,
        match="invalid targeted|outside uint256",
    ):
        adapter.get_ctf_collateral_payload(token_ids=[token_id])


def test_target_ctf_chain_batch_rejects_noncanonical_approval_bool(
    monkeypatch,
    tmp_path,
):
    from src.venue import polymarket_v2_adapter as adapter_module

    monkeypatch.setattr(
        adapter_module,
        "_json_rpc_batch_call_hard_deadline",
        lambda *_args, **_kwargs: [
            f"0x{100_000_000:064x}",
            f"0x{90_000_000:064x}",
            f"0x{80_000_000:064x}",
            f"0x{13_000_000:064x}",
            f"0x{2:064x}",
            f"0x{1:064x}",
        ],
    )
    adapter = adapter_module.PolymarketV2Adapter(
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        polygon_rpc_url="https://polygon.invalid",
        q1_egress_evidence_path=tmp_path / "unused.txt",
    )

    with pytest.raises(
        adapter_module.V2AdapterError,
        match="canonical bools",
    ):
        adapter.get_ctf_collateral_payload(token_ids=["123456789"])


def test_target_ctf_chain_batch_hard_deadline_kills_slow_drip_worker():
    import multiprocessing
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from src.venue import polymarket_v2_adapter as adapter_module

    body = json.dumps(
        [{"jsonrpc": "2.0", "id": 1, "result": f"0x{1:064x}"}]
    ).encode()

    class SlowDripHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowDripHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with pytest.raises(TimeoutError, match="hard deadline"):
            adapter_module._json_rpc_batch_call_hard_deadline(
                f"http://127.0.0.1:{server.server_port}",
                [("eth_call", [{"to": "0x1"}, "latest"])],
                timeout_seconds=0.25,
            )
        assert all(
            child.name != "zeus-targeted-collateral-rpc"
            for child in multiprocessing.active_children()
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1.0)


def test_target_ctf_chain_batch_subprocess_rejects_partial_rpc_truth():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from src.venue import polymarket_v2_adapter as adapter_module

    body = json.dumps(
        [{"jsonrpc": "2.0", "id": 1, "result": f"0x{1:064x}"}]
    ).encode()

    class PartialBatchHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), PartialBatchHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with pytest.raises(
            adapter_module.V2ReadUnavailable,
            match="incomplete",
        ):
            adapter_module._json_rpc_batch_call_hard_deadline(
                f"http://127.0.0.1:{server.server_port}",
                [
                    ("eth_call", [{"to": "0x1"}, "latest"]),
                    ("eth_call", [{"to": "0x2"}, "latest"]),
                ],
                timeout_seconds=2.0,
            )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1.0)


def test_target_ctf_collateral_falls_back_to_chain_without_inventing_zero(tmp_path):
    from src.venue.polymarket_v2_adapter import (
        ERC1155_BALANCE_OF_SELECTOR,
        ERC1155_IS_APPROVED_FOR_ALL_SELECTOR,
        PolymarketV2Adapter,
    )

    token_id = "123456789"

    class ConditionalReadUnavailable(FakeBalanceAllowanceClient):
        def get_balance_allowance(self, params):
            asset_type = str(getattr(params, "asset_type", "")).upper()
            if "CONDITIONAL" in asset_type:
                return {"balance": "0"}
            return {"balance": "100000000", "allowance": "50000000"}

    rpc_calls = []

    def rpc_call(_url, method, params):
        assert method == "eth_call"
        data = params[0]["data"]
        rpc_calls.append(data)
        if data.startswith(ERC1155_BALANCE_OF_SELECTOR):
            return hex(13_000_000)
        if data.startswith(ERC1155_IS_APPROVED_FOR_ALL_SELECTOR):
            return hex(1)
        raise AssertionError(data)

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        polygon_rpc_url="https://polygon.invalid",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: ConditionalReadUnavailable(),
    )

    payload = adapter.get_ctf_collateral_payload(token_ids=[token_id])

    assert payload["ctf_token_balances_units"] == {token_id: 13_000_000}
    assert payload["ctf_token_allowances_units"] == {token_id: 13_000_000}
    assert sum(data.startswith(ERC1155_BALANCE_OF_SELECTOR) for data in rpc_calls) == 1
    assert (
        sum(
            data.startswith(ERC1155_IS_APPROVED_FOR_ALL_SELECTOR)
            for data in rpc_calls
        )
        == 2
    )


def test_target_ctf_chain_proof_reads_balance_and_approvals_concurrently(tmp_path):
    from src.venue.polymarket_v2_adapter import (
        ERC1155_BALANCE_OF_SELECTOR,
        ERC1155_IS_APPROVED_FOR_ALL_SELECTOR,
        PolymarketV2Adapter,
    )

    token_id = "123456789"
    started = threading.Barrier(3)
    callers: set[int] = set()
    callers_lock = threading.Lock()

    def rpc_call(_url, method, params):
        assert method == "eth_call"
        data = params[0]["data"]
        with callers_lock:
            callers.add(threading.get_ident())
        started.wait(timeout=1.0)
        if data.startswith(ERC1155_BALANCE_OF_SELECTOR):
            return hex(13_000_000)
        if data.startswith(ERC1155_IS_APPROVED_FOR_ALL_SELECTOR):
            return hex(1)
        raise AssertionError(data)

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        polygon_rpc_url="https://polygon.invalid",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: object(),
    )

    proof = adapter._chain_conditional_balance_allowance_raw(token_id)

    assert proof["balance"] == "13000000"
    assert proof["allowance"] == "13000000"
    assert len(callers) == 3


def test_parallel_chain_collateral_guards_fail_loud_under_world_mutex(tmp_path):
    from src.state.db import WorldMutexIOViolation, world_write_mutex
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        polygon_rpc_url="https://polygon.invalid",
        rpc_call=lambda *_args: (_ for _ in ()).throw(
            AssertionError("world-mutex guard must preempt RPC")
        ),
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: object(),
    )

    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation, match="onchain.ctf_parallel"):
            adapter._chain_conditional_balance_allowance_raw("123456789")
        with pytest.raises(
            WorldMutexIOViolation,
            match="onchain.pusd_allowance_parallel",
        ):
            adapter._chain_collateral_allowance_micro()
        production_adapter = PolymarketV2Adapter(
            host="https://clob.polymarket.com",
            funder_address="0x0000000000000000000000000000000000000001",
            signer_key="test-key",
            chain_id=137,
            signature_type=3,
            polygon_rpc_url="https://polygon.invalid",
            q1_egress_evidence_path=tmp_path / "unused.txt",
        )
        with pytest.raises(
            WorldMutexIOViolation,
            match="onchain.batch_hard_deadline",
        ):
            production_adapter.get_ctf_collateral_payload(
                token_ids=["123456789"]
            )
    finally:
        mutex.release()


def test_target_ctf_collateral_keeps_dual_read_failure_unknown(tmp_path):
    from src.venue.polymarket_v2_adapter import (
        PolymarketV2Adapter,
        V2ReadUnavailable,
    )

    class ConditionalReadUnavailable(FakeBalanceAllowanceClient):
        def get_balance_allowance(self, params):
            asset_type = str(getattr(params, "asset_type", "")).upper()
            if "CONDITIONAL" in asset_type:
                raise TimeoutError("CLOB conditional read unavailable")
            return {"balance": "100000000", "allowance": "50000000"}

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        polygon_rpc_url="https://polygon.invalid",
        rpc_call=lambda *_args: (_ for _ in ()).throw(TimeoutError("RPC unavailable")),
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: ConditionalReadUnavailable(),
    )

    with pytest.raises(V2ReadUnavailable, match="both CLOB and chain"):
        adapter.get_ctf_collateral_payload(token_ids=["123456789"])


@pytest.mark.parametrize(
    "client_mode",
    ("timeout", "missing_method", "construction_failure"),
)
def test_target_ctf_collateral_uses_chain_when_entire_clob_read_is_unavailable(
    tmp_path,
    monkeypatch,
    client_mode,
):
    from src.venue import polymarket_v2_adapter as adapter_module

    token_id = "123456789"

    class FullClobTimeout(FakeBalanceAllowanceClient):
        def get_balance_allowance(self, _params):
            raise TimeoutError("CLOB balance endpoint unavailable")

    client = FullClobTimeout() if client_mode == "timeout" else object()

    def client_factory(**_kwargs):
        if client_mode == "construction_failure":
            raise RuntimeError("SDK construction failed")
        return client
    monkeypatch.setattr(
        adapter_module,
        "_json_rpc_batch_call",
        lambda *_args, **_kwargs: [
            "0x" + format(100_000_000, "064x"),
            "0x" + format(90_000_000, "064x"),
            "0x" + format(80_000_000, "064x"),
        ],
    )
    rpc_calls = []

    def rpc_call(_url, method, params):
        assert method == "eth_call"
        data = params[0]["data"]
        rpc_calls.append(data)
        if data.startswith(adapter_module.ERC1155_BALANCE_OF_SELECTOR):
            return hex(13_000_000)
        if data.startswith(adapter_module.ERC1155_IS_APPROVED_FOR_ALL_SELECTOR):
            return hex(1)
        raise AssertionError(data)

    adapter = adapter_module.PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x0000000000000000000000000000000000000001",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        polygon_rpc_url="https://polygon.invalid",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=client_factory,
    )

    payload = adapter.get_ctf_collateral_payload(token_ids=[token_id])

    assert payload["authority_tier"] == "CHAIN"
    assert payload["pusd_balance_micro"] == 100_000_000
    assert payload["pusd_allowance_micro"] == 80_000_000
    assert payload["ctf_token_balances_units"] == {token_id: 13_000_000}
    assert payload["ctf_token_allowances_units"] == {token_id: 13_000_000}
    assert rpc_calls


def test_pusd_collateral_payload_can_skip_allowance_update_for_heartbeat(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient()
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_pusd_collateral_payload(refresh_allowance=False)

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == "50000000"
    assert [call[0] for call in fake.calls] == ["get_balance_allowance"]


def test_pusd_collateral_payload_skips_chain_allowance_fallback_for_heartbeat(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(response={"balance": "100000000"})
    rpc_calls = []

    def rpc_call(_url, method, params):
        rpc_calls.append((method, params))
        raise AssertionError("pUSD heartbeat must not call chain allowance fallback")

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_pusd_collateral_payload(refresh_allowance=False)

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == 0
    assert payload["authority_tier"] == "DEGRADED"
    assert payload["pusd_allowance_source"] == "missing"
    assert rpc_calls == []


def test_pusd_collateral_payload_can_skip_clob_update_and_use_chain_allowance(tmp_path):
    """Current balance + direct chain allowance must fit the sidecar fast path."""
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(response={"balance": "100000000"})
    rpc_calls = []

    def rpc_call(_url, method, params):
        rpc_calls.append((method, params))
        return hex((2**256) - 1)

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_pusd_collateral_payload(
        refresh_allowance=False,
        allow_chain_allowance_fallback=True,
    )

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == (2**256) - 1
    assert payload["authority_tier"] == "CHAIN"
    assert payload["pusd_allowance_source"] == "chain_erc20_allowance"
    assert [call[0] for call in fake.calls] == ["get_balance_allowance"]
    assert len(rpc_calls) == 2


def test_chain_pusd_collateral_payload_batches_balance_and_both_allowances(
    monkeypatch,
    tmp_path,
):
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    observed = {}

    def batch_call(url, calls, *, timeout_seconds):
        observed.update(url=url, calls=calls, timeout_seconds=timeout_seconds)
        return [
            f"0x{123:064x}",
            f"0x{((2**256) - 1):064x}",
            f"0x{((2**256) - 2):064x}",
        ]

    monkeypatch.setattr(adapter_mod, "_json_rpc_batch_call", batch_call)
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **_kwargs: pytest.fail("chain batch must not create a CLOB client"),
        network_timeout_seconds=17,
    )

    payload = adapter.get_chain_pusd_collateral_payload()

    assert payload["pusd_balance_micro"] == 123
    assert payload["pusd_allowance_micro"] == (2**256) - 2
    assert payload["authority_tier"] == "CHAIN"
    assert payload["pusd_balance_source"] == "CHAIN"
    assert payload["pusd_allowance_source"] == "chain_erc20_batch"
    assert observed["url"] == "https://rpc.test"
    assert observed["timeout_seconds"] == 17
    assert len(observed["calls"]) == 3
    assert {method for method, _params in observed["calls"]} == {"eth_call"}


def test_json_rpc_batch_rejects_partial_chain_truth(monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                [{"jsonrpc": "2.0", "id": 1, "result": f"0x{1:064x}"}]
            ).encode()

    monkeypatch.setattr(adapter_mod.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    with pytest.raises(adapter_mod.V2AdapterError, match="batch incomplete"):
        adapter_mod._json_rpc_batch_call(
            "https://rpc.test",
            [
                ("eth_call", [{"to": "0x1"}, "latest"]),
                ("eth_call", [{"to": "0x2"}, "latest"]),
            ],
        )


def test_json_rpc_batch_accepts_strict_finalized_header_with_eth_call(monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps([
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"number": "0x2a", "hash": "0x" + "ab" * 32},
                },
                {"jsonrpc": "2.0", "id": 2, "result": "0x" + "01" * 32},
            ]).encode()

    monkeypatch.setattr(
        adapter_mod.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(),
    )
    result = adapter_mod._json_rpc_batch_call(
        "https://rpc.test",
        [
            ("eth_getBlockByNumber", ["finalized", False]),
            ("eth_call", [{"to": "0x1"}, "0x2a"]),
        ],
    )
    assert result[0]["number"] == "0x2a"
    assert result[0]["hash"] == "0x" + "ab" * 32
    assert result[1] == "0x" + "01" * 32


@pytest.mark.parametrize(
    "header",
    [
        {"number": "0x2a", "hash": "0x12"},
        {"number": "0xwat", "hash": "0x" + "ab" * 32},
        {"number": "0x2a", "hash": "0x" + "zz" * 32},
    ],
)
def test_json_rpc_batch_rejects_invalid_finalized_header(monkeypatch, header):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps([{"jsonrpc": "2.0", "id": 1, "result": header}]).encode()

    monkeypatch.setattr(
        adapter_mod.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(),
    )
    with pytest.raises(adapter_mod.V2AdapterError, match="invalid block header"):
        adapter_mod._json_rpc_batch_call(
            "https://rpc.test",
            [("eth_getBlockByNumber", ["finalized", False])],
        )


@pytest.mark.parametrize(
    "item",
    [
        {"jsonrpc": "2.0", "id": True, "result": "0x1"},
        {"jsonrpc": "2.0", "id": 1, "result": None},
        {"jsonrpc": "2.0", "id": 1, "result": ""},
        {"jsonrpc": "2.0", "id": 1, "result": "0xwat0"},
        {"id": 1, "result": "0x1"},
    ],
)
def test_json_rpc_batch_rejects_invalid_identity_or_quantity(monkeypatch, item):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps([item]).encode()

    monkeypatch.setattr(adapter_mod.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    with pytest.raises(adapter_mod.V2AdapterError):
        adapter_mod._json_rpc_batch_call(
            "https://rpc.test",
            [("eth_call", [{"to": "0x1"}, "latest"])],
        )


@pytest.mark.parametrize("value", [None, "", "0xwat0", "0x00"])
def test_chain_pusd_collateral_payload_rejects_invalid_batch_quantity(
    monkeypatch,
    tmp_path,
    value,
):
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    monkeypatch.setattr(
        adapter_mod,
        "_json_rpc_batch_call",
        lambda *_args, **_kwargs: [value, "0x1", "0x1"],
    )
    adapter = PolymarketV2Adapter(
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        polygon_rpc_url="https://rpc.test",
        q1_egress_evidence_path=tmp_path / "unused.txt",
    )

    with pytest.raises(
        adapter_mod.V2AdapterError,
        match="invalid hex data|expected 32-byte uint256",
    ):
        adapter.get_chain_pusd_collateral_payload()


def test_collateral_payload_rederives_once_when_runtime_l2_creds_are_stale(tmp_path):
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    fake = FakeStaleL2CredsBalanceClient()
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_balance_micro"] == "100000000"
    call_names = [call[0] for call in fake.calls]
    assert call_names == [
        "update_balance_allowance",
        "derive_api_key",
        "set_api_creds",
        "update_balance_allowance",
        "get_balance_allowance",
    ]
    assert adapter_mod._cached_derived_api_creds(
        host="https://clob.polymarket.com",
        chain_id=137,
        signer_key="test-key",
        signature_type=2,
        funder_address="0xfunder",
    ) is fake.derived_creds


def test_collateral_payload_missing_allowance_remains_fail_closed_zero(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(response={"balance": "100000000"})
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == 0
    assert payload["authority_tier"] == "DEGRADED"
    assert payload["pusd_allowance_source"] == "missing"


def test_default_chain_rpc_uses_configured_network_timeout(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(response={"balance": "100000000"})
    rpc_timeouts: list[float] = []

    def fake_json_rpc_call(_url, method, params, *, timeout_seconds=20.0):
        rpc_timeouts.append(timeout_seconds)
        assert method == "eth_call"
        assert params
        return hex(25_000_000)

    monkeypatch.setattr(adapter_mod, "_json_rpc_call", fake_json_rpc_call)
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
        network_timeout_seconds=0.75,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_allowance_micro"] == 25_000_000
    assert rpc_timeouts == [0.75, 0.75]


def test_collateral_payload_uses_chain_allowance_when_clob_omits_allowance(tmp_path):
    from src.venue.polymarket_v2_adapter import (
        POLYGON_EXCHANGE_V2_ADDRESS,
        POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS,
        PolymarketV2Adapter,
    )

    fake = FakeBalanceAllowanceClient(response={"balance": "100000000"})
    rpc_calls = []
    allowances = [25_000_000, 9_000_000]

    def rpc_call(_url, method, params):
        rpc_calls.append((method, params))
        return hex(allowances[len(rpc_calls) - 1])

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == 9_000_000
    assert payload["pusd_allowance_source"] == "chain_erc20_allowance"
    assert len(rpc_calls) == 2
    expected_spenders = {
        POLYGON_EXCHANGE_V2_ADDRESS.lower().removeprefix("0x").rjust(64, "0"),
        POLYGON_NEG_RISK_EXCHANGE_V2_ADDRESS.lower().removeprefix("0x").rjust(64, "0"),
    }
    actual_spenders = {params[0]["data"][-64:] for _method, params in rpc_calls}
    assert actual_spenders == expected_spenders
    for method, params in rpc_calls:
        assert method == "eth_call"
        data = params[0]["data"]
        assert data.startswith("0xdd62ed3e")
        assert "1111111111111111111111111111111111111111" in data


def test_collateral_payload_rechecks_chain_when_clob_reports_zero_allowance(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(
        response={"balance": "100000000", "allowance": "0"}
    )
    rpc_calls = []

    def rpc_call(_url, method, params):
        rpc_calls.append((method, params))
        return hex((2**256) - 1)

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == (2**256) - 1
    assert payload["pusd_allowance_source"] == "chain_erc20_allowance"
    assert len(rpc_calls) == 2


def test_collateral_payload_chain_truth_overrides_stale_nonzero_clob_allowance(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(
        response={"balance": "100000000", "allowance": "1000000"}
    )
    rpc_calls = []

    def rpc_call(_url, method, params):
        rpc_calls.append((method, params))
        return hex((2**256) - 1)

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_allowance_micro"] == (2**256) - 1
    assert payload["pusd_allowance_source"] == "chain_erc20_allowance"
    assert payload["authority_tier"] == "CHAIN"
    assert len(rpc_calls) == 2


def test_collateral_payload_degrades_when_clob_zero_and_chain_unavailable(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(
        response={"balance": "100000000", "allowance": "0"}
    )

    def rpc_call(_url, _method, _params):
        raise RuntimeError("rpc unavailable")

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        rpc_call=rpc_call,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_balance_micro"] == "100000000"
    assert payload["pusd_allowance_micro"] == 0
    assert payload["pusd_allowance_source"] == "chain_erc20_unavailable_clob_zero"
    assert payload["authority_tier"] == "DEGRADED"


def test_collateral_payload_does_not_label_clob_cache_as_chain_when_rpc_unavailable(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(
        response={"balance": "100000000", "allowance": "1000000"}
    )

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0x1111111111111111111111111111111111111111",
        signer_key="test-key",
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://rpc.test",
        rpc_call=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("rpc unavailable")
        ),
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_allowance_micro"] == "1000000"
    assert payload["pusd_allowance_source"] == "clob_balance_allowance"
    assert payload["authority_tier"] == "VENUE"


def test_collateral_payload_pusd_allowance_not_overwritten_by_ctf_positions(tmp_path):
    """Regression: CTF positions loop must not clobber the pUSD allowance variable.

    When a wallet holds CTF outcome tokens, the loop body assigns a local
    ``allowance_raw`` for each position.  Before the fix this shadowed the
    outer ``pusd_allowance_raw``, so ``pusd_allowance_micro`` ended up as
    the last position's token allowance (or 0 when absent) rather than the
    actual pUSD/CLOB allowance.  The return payload must always report the
    initial pUSD allowance regardless of CTF position count.
    """
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    # Client with pUSD allowance + two CTF positions carrying different allowances.
    class FakeClientWithPositions:
        def get_balance_allowance(self, params):
            return {"balance": "200000000", "allowance": "99000000"}

        def update_balance_allowance(self, params):
            return {}

        def get_positions(self):
            return [
                {"asset": "token-A", "size": "10", "allowance": "1111"},
                {"asset": "token-B", "size": "5"},  # no allowance field → 0
            ]

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: FakeClientWithPositions(),
    )

    payload = adapter.get_collateral_payload()

    # pUSD allowance must be the CLOB value, not any CTF position's allowance.
    assert payload["pusd_allowance_micro"] == "99000000", (
        f"pusd_allowance_micro was {payload['pusd_allowance_micro']!r}; "
        "CTF position loop must not overwrite the pUSD allowance variable"
    )
    # CTF maps must still reflect the positions correctly.
    assert "token-A" in payload["ctf_token_balances_units"]
    assert "token-B" in payload["ctf_token_balances_units"]


def test_collateral_payload_pusd_allowance_preserved_with_zero_ctf_positions(tmp_path):
    """Baseline: pUSD allowance correct when no CTF positions exist."""
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeBalanceAllowanceClient(response={"balance": "100000000", "allowance": "77000000"})
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_collateral_payload()

    assert payload["pusd_allowance_micro"] == "77000000"
    assert payload["ctf_token_balances_units"] == {}



def test_polymarket_client_translates_split_http_timeout_for_v2_reads(monkeypatch):
    """Authenticated monitor reads receive scalar seconds, never httpx.Timeout."""
    from src.data import polymarket_client as pm

    monkeypatch.setattr(
        pm,
        "_resolve_credentials",
        lambda: {"private_key": "0xabc", "funder_address": "0xfunder"},
    )
    monkeypatch.setenv("POLYMARKET_CLOB_V2_SIGNATURE_TYPE", "2")
    timeout = pm.httpx.Timeout(connect=1.8, read=2.0, write=0.25, pool=0.1)

    adapter = pm.PolymarketClient(
        public_http_timeout=timeout,
    )._ensure_v2_adapter()

    assert adapter.network_timeout_seconds == pytest.approx(2.0)



def test_default_q1_egress_evidence_uses_current_live_control_surface():
    from src.venue.polymarket_v2_adapter import DEFAULT_Q1_EGRESS_EVIDENCE

    default_path = str(DEFAULT_Q1_EGRESS_EVIDENCE)

    assert "task_2026-04-26_polymarket_clob_v2_migration" not in default_path
    assert default_path == "docs/operations/live_egress/q1_zeus_egress_current.txt"
    assert DEFAULT_Q1_EGRESS_EVIDENCE.exists()


def test_polymarket_client_honors_signature_type_env(monkeypatch):
    from src.data import polymarket_client as pm

    monkeypatch.setattr(
        pm,
        "_resolve_credentials",
        lambda: {"private_key": "0xabc", "funder_address": "0xfunder"},
    )
    monkeypatch.setenv("POLYMARKET_CLOB_V2_SIGNATURE_TYPE", "1")

    adapter = pm.PolymarketClient()._ensure_v2_adapter()

    assert adapter.signature_type == 1


def test_polymarket_client_honors_q1_egress_evidence_env(monkeypatch, tmp_path):
    from src.data import polymarket_client as pm

    evidence = tmp_path / "q1_egress_current.txt"
    _write_valid_q1_evidence(evidence)
    monkeypatch.setattr(
        pm,
        "_resolve_credentials",
        lambda: {"private_key": "0xabc", "funder_address": "0xfunder"},
    )
    monkeypatch.setenv("POLYMARKET_CLOB_V2_Q1_EGRESS_EVIDENCE", str(evidence))
    monkeypatch.setenv("POLYMARKET_CLOB_V2_SIGNATURE_TYPE", "2")

    adapter = pm.PolymarketClient()._ensure_v2_adapter()

    assert adapter.q1_egress_evidence_path == evidence


def _write_valid_q1_evidence(path: Path) -> None:
    path.write_text(
        "Q1 Zeus egress evidence sentinel\n"
        "authority_basis: test\n"
        "operator_attestation: test current egress accepted\n"
        "live_side_effects: none; HTTPS GET probes only\n"
        "raw_secrets_or_signed_payloads: none\n"
        "probe_results:\n"
        "[{\"effective_url\":\"https://clob.polymarket.com/ok\",\"status_code\":200}]\n",
        encoding="utf-8",
    )


def test_adapter_module_imports_without_py_clob_client_v2_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "py_clob_client_v2", None)
    module = importlib.import_module("src.venue.polymarket_v2_adapter")
    assert hasattr(module, "PolymarketV2Adapter")


def test_py_clob_client_v2_import_is_confined_to_venue_adapter():
    offenders = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=path.as_posix())
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        if any(
            module == "py_clob_client_v2" or module.startswith("py_clob_client_v2.")
            for module in imported_modules
        ) and path.as_posix() != "src/venue/polymarket_v2_adapter.py":
            offenders.append(path.as_posix())
    assert offenders == []


def test_preflight_fails_closed_when_q1_egress_evidence_absent(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeOneStepClient()
    adapter = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=tmp_path / "missing.txt",
        client_factory=lambda **kwargs: fake,
    )

    result = adapter.preflight()

    assert result.ok is False
    assert result.error_code == "Q1_EGRESS_EVIDENCE_ABSENT"
    assert fake.calls == []


def test_preflight_rejects_arbitrary_existing_q1_egress_file_without_sdk_contact(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    evidence = tmp_path / "any_existing_file.txt"
    evidence.write_text("not current q1 egress evidence\n")
    fake = FakeOneStepClient()
    adapter = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=evidence,
        client_factory=lambda **kwargs: fake,
    )

    result = adapter.preflight()

    assert result.ok is False
    assert result.error_code == "Q1_EGRESS_EVIDENCE_INVALID"
    assert fake.calls == []


def test_preflight_rejects_archived_april_q1_egress_path_without_sdk_contact(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    evidence = (
        tmp_path
        / "docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/q1_zeus_egress_2026-04-26.txt"
    )
    evidence.parent.mkdir(parents=True)
    _write_valid_q1_evidence(evidence)
    fake = FakeOneStepClient()
    adapter = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=evidence,
        client_factory=lambda **kwargs: fake,
    )

    result = adapter.preflight()

    assert result.ok is False
    assert result.error_code == "Q1_EGRESS_EVIDENCE_INVALID"
    assert fake.calls == []


def test_preflight_retries_transient_get_ok_without_submit_side_effect(tmp_path, monkeypatch):
    adapter, fake = _adapter(tmp_path, FakeFlakyPreflightClient())
    monkeypatch.setenv("ZEUS_V2_PREFLIGHT_MAX_ATTEMPTS", "2")

    result = adapter.preflight()

    assert result.ok is True
    assert fake.calls == [("get_ok",), ("get_ok",)]


def test_submit_fails_closed_when_q1_egress_evidence_absent(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeOneStepClient()
    adapter = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=tmp_path / "missing.txt",
        client_factory=lambda **kwargs: fake,
    )
    envelope = adapter.create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="GTC",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "Q1_EGRESS_EVIDENCE_ABSENT"
    assert result.envelope.error_code == "Q1_EGRESS_EVIDENCE_ABSENT"
    assert result.envelope.order_id is None
    assert fake.calls == []


def test_submit_limit_order_fails_closed_when_q1_egress_evidence_absent(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake = FakeOneStepClient()
    adapter = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=tmp_path / "missing.txt",
        client_factory=lambda **kwargs: fake,
    )

    result = adapter.submit_limit_order(token_id="yes-token", price=0.5, size=10.0, side="BUY")

    assert result.status == "rejected"
    assert result.error_code == "Q1_EGRESS_EVIDENCE_ABSENT"
    assert result.envelope.error_code == "Q1_EGRESS_EVIDENCE_ABSENT"
    assert result.envelope.order_id is None
    assert fake.calls == []


def test_submit_limit_order_snapshot_failure_is_typed_pre_submit_rejection(tmp_path):
    adapter, fake = _adapter(tmp_path, FakePreflightOnlyClient())

    result = adapter.submit_limit_order(token_id="yes-token", price=0.5, size=10.0, side="BUY", _allow_compat_for_test=True)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "get_neg_risk" in (result.error_message or "")
    assert result.envelope.order_id is None
    assert fake.calls == [("get_ok",)]


def test_get_open_orders_uses_sdk_get_open_orders_surface(tmp_path):
    adapter, fake = _adapter(tmp_path, FakeOpenOrdersClient())

    orders = adapter.get_open_orders()

    assert fake.calls == [("get_open_orders", {"only_first_page": False})]
    assert len(orders) == 1
    assert orders[0].order_id == "ord-open"
    assert orders[0].status == "LIVE"


def test_get_open_orders_keeps_legacy_get_orders_fallback(tmp_path):
    adapter, fake = _adapter(tmp_path, FakeLegacyGetOrdersClient())

    orders = adapter.get_open_orders()

    assert fake.calls == [("get_orders", {"only_first_page": False})]
    assert len(orders) == 1
    assert orders[0].order_id == "ord-legacy"
    assert orders[0].status == "LIVE"


def test_get_trades_requests_all_pages_from_sdk(tmp_path):
    adapter, fake = _adapter(tmp_path, FakeTradesClient())

    trades = adapter.get_trades()

    assert fake.calls == [("get_trades", {"only_first_page": False})]
    assert len(trades) == 1
    assert trades[0].raw["id"] == "trade-open"


def test_account_truth_reads_time_and_each_account_surface_with_one_deadline(tmp_path, monkeypatch):
    import httpx
    import py_clob_client_v2.headers.headers as headers_mod
    from py_clob_client_v2.constants import END_CURSOR

    class FakeAccountClient:
        host = "https://clob-v2.polymarket.com"
        signer = object()
        creds = object()

    adapter, _ = _adapter(tmp_path, FakeAccountClient())
    calls = []
    header_paths = []
    http_clients = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    def fake_async_client(**kwargs):
        client = FakeAsyncClient()
        http_clients.append((client, kwargs))
        return client

    monkeypatch.setattr(
        headers_mod,
        "create_level_2_headers",
        lambda _signer, _creds, args, timestamp: (
            header_paths.append(args.request_path) or {"x-time": str(timestamp)}
        ),
    )
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    async def fake_get(_http, endpoint, *, headers, params, deadline_monotonic):
        calls.append((endpoint, headers, params, deadline_monotonic))
        if endpoint.endswith("/time"):
            return 1_700_000_000
        if endpoint.endswith("/data/orders"):
            if len([call for call in calls if call[0].endswith("/data/orders")]) == 1:
                return {
                    "data": [{
                        "orderID": "ord-account",
                        "status": "LIVE",
                        "original_size": "10000000",
                        "size_matched": "0",
                    }],
                    "next_cursor": "orders-page-2",
                }
            return {
                "data": [{
                    "orderID": "ord-account-2",
                    "status": "LIVE",
                    "original_size": "10000000",
                    "size_matched": "0",
                }],
                "next_cursor": END_CURSOR,
            }
        if endpoint.endswith("/data/trades"):
            return {
                "data": [{"id": "trade-account", "status": "MATCHED"}],
                "next_cursor": END_CURSOR,
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(adapter, "_account_truth_json_get_async", fake_get)
    deadline = time.monotonic() + 5.0
    truth = adapter.get_account_truth(deadline_monotonic=deadline)

    assert [endpoint.rsplit("/", 1)[-1] for endpoint, *_rest in calls] == [
        "time",
        "orders",
        "orders",
        "trades",
    ]
    assert all(call[-1] == deadline for call in calls)
    assert header_paths == ["/data/orders", "/data/trades"]
    assert len(http_clients) == 1
    assert http_clients[0][1] == {"http2": False, "timeout": None}
    assert [state.order_id for state in truth.open_orders] == ["ord-account", "ord-account-2"]
    assert [trade.raw["id"] for trade in truth.trades] == ["trade-account"]


def test_account_truth_deadline_and_page_limit_fail_closed_with_bounded_calls(tmp_path, monkeypatch):
    from src.venue.polymarket_v2_adapter import IncompleteAccountTruthError

    class FakeAccountClient:
        host = "https://clob-v2.polymarket.com"

    adapter, _ = _adapter(tmp_path, FakeAccountClient())
    calls = []

    async def forbidden_get(*_args, **_kwargs):
        calls.append("network")
        raise AssertionError("expired deadline must prevent every network call")

    monkeypatch.setattr(adapter, "_account_truth_json_get_async", forbidden_get)
    with pytest.raises(IncompleteAccountTruthError, match="INCOMPLETE_ACCOUNT_TRUTH"):
        adapter.get_account_truth(deadline_monotonic=time.monotonic() - 0.01)
    assert calls == []

    async def fake_headers(*_args, **_kwargs):
        return {}, 1_700_000_000

    async def incomplete_page(*_args, **_kwargs):
        calls.append("page")
        return {"data": [], "next_cursor": "more-pages"}

    monkeypatch.setattr(adapter, "_account_truth_headers_async", fake_headers)
    monkeypatch.setattr(
        adapter,
        "_account_truth_json_get_async",
        incomplete_page,
    )
    with pytest.raises(IncompleteAccountTruthError, match="bounded page limit"):
        adapter.get_account_truth(
            deadline_monotonic=time.monotonic() + 5.0,
            max_pages=1,
        )
    assert calls == ["page"]


def test_account_truth_transport_deadline_covers_combined_request_phases(tmp_path):
    from src.venue.polymarket_v2_adapter import IncompleteAccountTruthError

    adapter, _ = _adapter(tmp_path, object())

    class SlowTransport:
        async def get(self, *_args, **_kwargs):
            # Simulate two independent transport phases; a per-phase timeout
            # could admit both waits even though their aggregate exceeds budget.
            await asyncio.sleep(0.008)
            await asyncio.sleep(0.008)
            raise AssertionError("combined phases should have been cancelled")

    started = time.monotonic()
    with pytest.raises(IncompleteAccountTruthError, match="deadline elapsed"):
        asyncio.run(
            adapter._account_truth_json_get_async(
                SlowTransport(),
                "https://clob-v2.polymarket.com/data/orders",
                headers=None,
                params=None,
                deadline_monotonic=started + 0.010,
            )
        )
    assert time.monotonic() - started < 0.10


def test_account_truth_shared_deadline_interrupts_cross_phase_wait(tmp_path, monkeypatch):
    from src.venue.polymarket_v2_adapter import IncompleteAccountTruthError
    import py_clob_client_v2.headers.headers as headers_mod
    from py_clob_client_v2.constants import END_CURSOR

    class FakeAccountClient:
        host = "https://clob-v2.polymarket.com"
        signer = object()
        creds = object()

    adapter, _ = _adapter(tmp_path, FakeAccountClient())
    calls = []
    monkeypatch.setattr(
        headers_mod,
        "create_level_2_headers",
        lambda _signer, _creds, _args, timestamp: {"x-time": str(timestamp)},
    )

    async def slow_request(_http, endpoint, **_kwargs):
        surface = endpoint.rsplit("/", 1)[-1]
        calls.append(surface)
        if surface == "time":
            return 1_700_000_000
        if surface == "orders":
            await asyncio.sleep(0.008)
            return {"data": [], "next_cursor": END_CURSOR}
        if surface == "trades":
            await asyncio.sleep(0.05)
            return {"data": [], "next_cursor": END_CURSOR}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(adapter, "_account_truth_json_get_async", slow_request)
    started = time.monotonic()
    with pytest.raises(IncompleteAccountTruthError, match="deadline elapsed"):
        adapter.get_account_truth(deadline_monotonic=started + 0.025)

    assert calls == ["time", "orders", "trades"]
    assert time.monotonic() - started < 0.10


def test_submit_limit_order_rejects_before_sdk_submit_when_fee_bps_missing(tmp_path):
    class MissingFeeClient:
        def __init__(self):
            self.calls = []

        def get_ok(self):
            self.calls.append(("get_ok",))
            return {"ok": True}

        def get_neg_risk(self, token_id):
            self.calls.append(("get_neg_risk", token_id))
            return True

        def get_tick_size(self, token_id):
            self.calls.append(("get_tick_size", token_id))
            return "0.01"

        def create_order(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("create_order must not run without fee-rate proof")

        def post_order(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("post_order must not run without fee-rate proof")

    fake = MissingFeeClient()
    adapter, _ = _adapter(tmp_path, fake)

    result = adapter.submit_limit_order(token_id="yes-token", price=0.5, size=10.0, side="BUY", _allow_compat_for_test=True)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "get_fee_rate_bps" in (result.error_message or "")
    assert not any(call[0] in {"create_order", "post_order", "create_and_post_order"} for call in fake.calls)


def test_submit_limit_order_rejects_before_sdk_submit_when_fee_bps_none(tmp_path):
    class NoneFeeClient(FakeTwoStepClient):
        def get_fee_rate_bps(self, token_id):
            self.calls.append(("get_fee_rate_bps", token_id))
            return None

    fake = NoneFeeClient()
    adapter, _ = _adapter(tmp_path, fake)

    result = adapter.submit_limit_order(token_id="yes-token", price=0.5, size=10.0, side="BUY", _allow_compat_for_test=True)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "fee_rate_bps" in (result.error_message or "")
    assert not any(call[0] in {"create_order", "post_order", "create_and_post_order"} for call in fake.calls)


def test_two_step_signing_failure_is_typed_pre_submit_rejection(tmp_path):
    fake = FakeCreateOrderFailureClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "local signing failed" in (result.error_message or "")
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_two_step_pre_submit_transport_failure_is_retryable_typed_rejection(tmp_path):
    fake = FakeCreateOrderTransportFailureClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="FOK", post_only=False
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_TRANSPORT_EXCEPTION"
    assert result.envelope.order_id is None
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_post_order_exception_carries_deterministic_order_identity(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakePostOrderFailureClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    with pytest.raises(adapter_mod.AmbiguousSubmitError, match="post timed out") as caught:
        _submit(adapter, envelope)

    assert caught.value.envelope.order_id == "0xexpected-order-id"
    assert caught.value.envelope.signed_order == fake.signed_order
    assert caught.value.envelope.error_code == "V2_POST_SUBMIT_AMBIGUOUS"
    assert any(call[0] == "post_order" for call in fake.calls)


def test_order_manager_not_ready_425_is_deterministic_rejection(
    tmp_path,
    monkeypatch,
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeOrderManagerNotReady425Client()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="GTC",
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_order_manager_not_ready_425"
    assert result.envelope.error_code == "venue_order_manager_not_ready_425"
    assert result.envelope.order_id is None
    assert result.envelope.signed_order == fake.signed_order
    assert any(call[0] == "post_order" for call in fake.calls)
    from src.data.polymarket_client import _legacy_order_result_from_submit

    legacy = _legacy_order_result_from_submit(result)
    assert legacy["success"] is False
    assert legacy["status"] == "rejected"
    assert legacy["errorCode"] == "venue_order_manager_not_ready_425"


def test_trading_disabled_503_is_deterministic_rejection(
    tmp_path,
    monkeypatch,
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTradingDisabled503Client()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="FAK",
        post_only=False,
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_trading_disabled_503"
    assert result.envelope.order_id is None
    assert result.envelope.signed_order == fake.signed_order
    assert any(call[0] == "post_order" for call in fake.calls)


def test_post_only_cross_400_is_deterministic_rejection(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakePostOnlyCross400Client()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="GTC",
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_rejected_400"
    assert result.envelope.error_code == "venue_rejected_400"
    assert result.envelope.order_id is None
    assert result.envelope.signed_order == fake.signed_order
    assert any(call[0] == "post_order" for call in fake.calls)


def test_other_425_response_remains_ambiguous(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class Other425Client(FakeTwoStepClient):
        def post_order(
            self,
            order,
            order_type=None,
            post_only=False,
            defer_exec=False,
        ):
            self.calls.append(
                ("post_order", order, order_type, post_only, defer_exec)
            )
            from py_clob_client_v2.exceptions import PolyApiException

            response = SimpleNamespace(
                status_code=425,
                json=lambda: {"error": "request state unknown"},
            )
            raise PolyApiException(response)

    fake = Other425Client()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="GTC",
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    with pytest.raises(adapter_mod.AmbiguousSubmitError):
        _submit(adapter, envelope)
    from py_clob_client_v2.exceptions import PolyApiException

    transport = PolyApiException(
        error_msg={"error": "order manager not ready, please retry"}
    )
    assert transport.status_code is None
    assert not adapter_mod._is_polymarket_order_manager_not_ready_425_error(
        transport
    )


def test_runtime_error_cannot_impersonate_order_manager_425(
    tmp_path,
    monkeypatch,
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class Impostor425Client(FakeTwoStepClient):
        def post_order(
            self,
            order,
            order_type=None,
            post_only=False,
            defer_exec=False,
        ):
            self.calls.append(
                ("post_order", order, order_type, post_only, defer_exec)
            )
            raise RuntimeError(
                "PolyApiException[status_code=425, error_message={'error': "
                "'order manager not ready, please retry'}]"
            )

    fake = Impostor425Client()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="GTC",
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    with pytest.raises(adapter_mod.AmbiguousSubmitError):
        _submit(adapter, envelope)


def test_signed_identity_callback_runs_before_post(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    expected = "0xexpected-order-id"
    fake = FakeTwoStepClient(
        post_response={"orderID": expected, "status": "LIVE"}
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: expected,
    )
    persisted = []

    def _persist(signed_envelope):
        persisted.append(signed_envelope)
        fake.calls.append(("identity_persisted", signed_envelope.order_id))
        return _test_identity_receipt(signed_envelope)

    result = _submit(adapter, envelope, before_post=_persist)

    assert result.status == "accepted"
    assert persisted[0].order_id == expected
    assert persisted[0].signed_order == fake.signed_order
    assert persisted[0].signed_order_hash == hashlib.sha256(fake.signed_order).hexdigest()
    names = [call[0] for call in fake.calls]
    assert names.index("create_order") < names.index("identity_persisted") < names.index("post_order")


def test_signed_identity_persistence_failure_prevents_post(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    def _fail(_signed_envelope):
        raise RuntimeError("signed identity journal unavailable")

    result = _submit(adapter, envelope, before_post=_fail)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "signed identity journal unavailable" in (result.error_message or "")
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_noop_signed_identity_callback_prevents_post(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    result = adapter.submit(envelope, before_post=lambda signed_envelope: None)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "no canonical read-back receipt" in (result.error_message or "")
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_publicly_constructed_identity_receipt_cannot_authorize_post(
    tmp_path, monkeypatch
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    def _forged(signed_envelope):
        return adapter_mod.SignedIdentityPersistenceReceipt(
            command_id="forged-command",
            envelope_id="nonexistent-envelope",
            order_id=signed_envelope.order_id,
            signed_order_hash=signed_envelope.signed_order_hash,
            canonical_pre_sign_payload_hash=(
                signed_envelope.canonical_pre_sign_payload_hash
            ),
            raw_request_hash=signed_envelope.raw_request_hash,
        )

    result = adapter.submit(envelope, before_post=_forged)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "not issued by canonical read-back gateway" in (result.error_message or "")
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_signed_identity_receipt_is_one_shot(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    expected = "0xexpected-order-id"
    fake = FakeTwoStepClient(
        post_response={"orderID": expected, "status": "LIVE"}
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: expected,
    )
    issued = []

    def _replay(signed_envelope):
        if not issued:
            issued.append(_test_identity_receipt(signed_envelope))
        return issued[0]

    first = adapter.submit(envelope, before_post=_replay)
    second = adapter.submit(envelope, before_post=_replay)

    assert first.status == "accepted"
    assert second.status == "rejected"
    assert second.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "not issued by canonical read-back gateway" in (
        second.error_message or ""
    )
    assert sum(call[0] == "post_order" for call in fake.calls) == 1


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("order_id", "0xwrong-order"),
        ("signed_order_hash", "f" * 64),
        ("canonical_pre_sign_payload_hash", "e" * 64),
        ("raw_request_hash", "d" * 64),
    ],
)
def test_issued_identity_receipt_mismatch_cannot_authorize_post(
    tmp_path, monkeypatch, field, wrong_value
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    expected = "0xexpected-order-id"
    fake = FakeTwoStepClient(
        post_response={"orderID": expected, "status": "LIVE"}
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: expected,
    )

    result = adapter.submit(
        envelope,
        before_post=lambda signed_envelope: _test_identity_receipt(
            signed_envelope,
            **{field: wrong_value},
        ),
    )

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "does not match signed order" in (result.error_message or "")
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_signed_identity_receipt_issuer_has_one_runtime_gateway():
    symbol = "_issue_signed_identity_persistence_receipt"
    callers = []
    for path in Path("src").rglob("*.py"):
        if path.as_posix() == "src/venue/polymarket_v2_adapter.py":
            continue
        if symbol in path.read_text():
            callers.append(path.as_posix())

    assert callers == ["src/execution/executor.py"]


def test_missing_signed_identity_persister_prevents_all_post(tmp_path):
    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )

    result = adapter.submit(envelope)

    assert result.status == "rejected"
    assert result.error_code == "SIGNED_IDENTITY_PERSISTER_REQUIRED"
    assert fake.calls == []


def test_geoblock_403_is_definitive_rejection_without_venue_identity(
    tmp_path, monkeypatch
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeGeoblockClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xclient-derived-not-venue-identity",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_rejected_geoblock_403"
    assert result.envelope.order_id is None
    assert result.envelope.signed_order == fake.signed_order
    assert result.envelope.signed_order_hash
    assert result.envelope.raw_response_json is None
    assert any(call[0] == "post_order" for call in fake.calls)


def test_fok_killed_400_classifier_remains_available_for_recovery():
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeFokKilledClient()
    with pytest.raises(RuntimeError) as caught:
        fake.post_order(fake.signed_order, order_type="FOK")

    assert adapter_mod._is_polymarket_fok_killed_error(caught.value)


def test_fak_no_match_400_classifier_remains_available_for_recovery():
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeFakNoMatchClient()
    with pytest.raises(RuntimeError) as caught:
        fake.post_order(fake.signed_order, order_type="FAK")

    assert adapter_mod._is_polymarket_fak_no_match_error(caught.value)


@pytest.mark.parametrize("order_type", ["FAK", "FOK"])
def test_buy_taker_capable_order_crosses_all_sdk_boundaries(
    tmp_path, monkeypatch, order_type
):
    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type=order_type, post_only=False
    )
    monkeypatch.setattr(
        "src.venue.polymarket_v2_adapter._deterministic_v2_order_id",
        lambda *args, **kwargs: "ord-two-step",
    )

    result = _submit(adapter, envelope)

    assert result.status == "accepted"
    assert result.envelope.order_id == "ord-two-step"
    assert any(call[0] == "create_order" for call in fake.calls)
    assert any(
        call[0] == "post_order" and call[2] == order_type and call[3] is False
        for call in fake.calls
    )


def test_final_sdk_boundary_independently_rejects_non_maker_before_post(
    tmp_path, monkeypatch
):
    import src.venue.polymarket_v2_adapter as adapter_mod
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC", post_only=False
    )
    monkeypatch.setattr(
        VenueSubmissionEnvelope,
        "assert_live_fill_price_bound",
        lambda self: None,
    )
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "LIVE_FILL_PRICE_UNBOUNDED:FINAL_SDK_BOUNDARY" in (result.error_message or "")
    assert any(call[0] == "create_order" for call in fake.calls)
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_final_sdk_boundary_independently_rejects_size_below_minimum_before_post(
    tmp_path, monkeypatch
):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC", post_only=True
    ).with_updates(size=Decimal("0.5"), min_order_size=Decimal("1"))
    monkeypatch.setattr(
        "src.venue.polymarket_v2_adapter._deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected",
    )
    monkeypatch.setattr(
        VenueSubmissionEnvelope,
        "assert_live_fill_price_bound",
        lambda self: None,
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "LIVE_ORDER_SIZE_INVALID:FINAL_SDK_BOUNDARY" in (
        result.error_message or ""
    )
    assert any(call[0] == "create_order" for call in fake.calls)
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_final_sdk_boundary_independently_rejects_off_tick_before_post(
    tmp_path, monkeypatch
):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC", post_only=True
    ).with_updates(price=Decimal("0.505"), tick_size=Decimal("0.01"))
    monkeypatch.setattr(
        "src.venue.polymarket_v2_adapter._deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected",
    )
    monkeypatch.setattr(
        VenueSubmissionEnvelope,
        "assert_live_fill_price_bound",
        lambda self: None,
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "LIVE_ORDER_TICK_INVALID:FINAL_SDK_BOUNDARY" in (
        result.error_message or ""
    )
    assert any(call[0] == "create_order" for call in fake.calls)
    assert not any(call[0] == "post_order" for call in fake.calls)


@pytest.mark.parametrize(
    ("size", "minimum"),
    (("NaN", "1"), ("1", "NaN"), ("0", "1"), ("1", "0")),
)
def test_live_envelope_rejects_invalid_size_authority_before_sdk(
    tmp_path, size, minimum
):
    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC", post_only=True
    ).with_updates(size=Decimal(size), min_order_size=Decimal(minimum))

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    assert "live order size is below venue minimum or invalid" in (
        result.error_message or ""
    )
    assert fake.calls == []


@pytest.mark.parametrize(
    ("side", "order_type"),
    [("BUY", "GTC"), ("BUY", "GTD"), ("SELL", "FOK")],
)
def test_illegal_side_order_type_tuple_fails_closed_before_sdk(
    tmp_path, side, order_type
):
    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type=order_type, post_only=False
    ).with_updates(side=side)

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "LIVE_FILL_PRICE_UNBOUNDED"
    assert fake.calls == []


def test_final_sdk_boundary_allows_fak_sell_with_limit_price_floor(
    tmp_path,
    monkeypatch,
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient(
        post_response={"orderID": "0xexpected", "status": "LIVE"}
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    ).with_updates(side="SELL", order_type="FAK", post_only=False)
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected",
    )

    result = _submit(adapter, envelope)

    assert result.status == "accepted"
    assert any(
        call[0] == "post_order"
        and call[2] == "FAK"
        and call[3] is False
        for call in fake.calls
    )


def test_fok_one_step_only_client_fails_closed_before_submit(tmp_path):
    fake = FakeOneStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="FOK", post_only=False
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "pre-POST signed identity persistence" in (result.error_message or "")
    assert not any(call[0] == "create_and_post_order" for call in fake.calls)


@pytest.mark.parametrize(
    ("side", "bad_level_side", "bad_level"),
    [
        ("BUY", "asks", {"price": "1", "size": "20"}),
        ("SELL", "bids", {"price": "1.01", "size": "20"}),
        ("BUY", "asks", {"price": "0.50", "size": "0"}),
    ],
)
def test_fok_final_depth_rejects_levels_outside_probability_domain(
    tmp_path, side, bad_level_side, bad_level
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class MalformedFinalBookClient(FakeTwoStepClient):
        def get_order_book(self, token_id):
            self.calls.append(("get_order_book", token_id))
            return {
                "asset_id": token_id,
                "bids": [{"price": "0.99", "size": "100"}],
                "asks": [{"price": "0.50", "size": "100"}],
                bad_level_side: [bad_level],
            }

    fake = MalformedFinalBookClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="FOK"
    ).with_updates(side=side)

    with pytest.raises(ValueError, match="FOK_FINAL_DEPTH_MALFORMED"):
        adapter_mod._assert_final_fok_depth_bound(fake, envelope)


def test_response_order_id_mismatch_is_ambiguous(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient(post_response={"orderID": "0xwrong", "status": "LIVE"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    with pytest.raises(adapter_mod.AmbiguousSubmitError) as caught:
        _submit(adapter, envelope)

    assert caught.value.envelope.order_id == "0xexpected-order-id"
    assert caught.value.envelope.error_code == "V2_ORDER_ID_ACK_MISMATCH"
    assert '"orderID":"0xwrong"' in (caught.value.envelope.raw_response_json or "")


def test_invalid_safe_signature_is_deterministic_rejection_not_l2_credential_retry(
    tmp_path, monkeypatch
):
    import src.venue.polymarket_v2_adapter as adapter_mod

    adapter_mod._DERIVED_API_CREDS_CACHE.clear()
    fake = FakeInvalidSafeSignatureTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod, "_deterministic_v2_order_id", lambda *args, **kwargs: "ord-safe"
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_auth_invalid_signature_400"
    assert "invalid POLY_GNOSIS_SAFE signature" in (result.error_message or "")
    assert [call[0] for call in fake.calls] == [
        "get_ok",
        "create_order",
        "post_order",
    ]
    assert adapter_mod._cached_derived_api_creds(
        host="https://clob-v2.polymarket.com",
        chain_id=137,
        signer_key="test-key",
        signature_type=2,
        funder_address="0xfunder",
    ) is None


def test_two_step_invalid_safe_signature_preserves_signed_order_hash(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    signed = b"signed-safe-order"
    fake = FakeInvalidSafeSignatureTwoStepClient(signed_order=signed)
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod, "_deterministic_v2_order_id", lambda *args, **kwargs: "ord-safe"
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_auth_invalid_signature_400"
    assert result.envelope.signed_order == signed
    assert result.envelope.signed_order_hash == hashlib.sha256(signed).hexdigest()
    assert [call[0] for call in fake.calls] == [
        "get_ok",
        "create_order",
        "post_order",
    ]


def test_create_submission_envelope_captures_all_provenance_fields(tmp_path):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    adapter, _fake = _adapter(tmp_path)
    envelope = adapter.create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="GTC",
        post_only=False,
    )

    assert isinstance(envelope, VenueSubmissionEnvelope)
    assert envelope.sdk_package == "py-clob-client-v2"
    assert envelope.sdk_version
    assert envelope.host == "https://clob-v2.polymarket.com"
    assert envelope.chain_id == 137
    assert envelope.funder_address == "0xfunder"
    assert envelope.condition_id == "cond-123"
    assert envelope.question_id == "question-123"
    assert envelope.yes_token_id == "yes-token"
    assert envelope.no_token_id == "no-token"
    assert envelope.selected_outcome_token_id == "yes-token"
    assert envelope.outcome_label == "YES"
    assert envelope.order_type == "GTC"
    assert envelope.post_only is False
    assert envelope.tick_size == Decimal("0.01")
    assert envelope.min_order_size == Decimal("5")
    assert envelope.neg_risk is True
    assert envelope.fee_details == {
        "bps": 0,
        "builder_fee_bps": 0,
        "fee_rate_fraction": 0.0,
        "fee_rate_bps": 0.0,
        "fee_rate_source_field": "bps",
        "fee_rate_raw_unit": "bps",
    }
    assert len(envelope.canonical_pre_sign_payload_hash) == 64
    assert len(envelope.raw_request_hash) == 64
    assert envelope.raw_response_json is None
    assert envelope.order_id is None
    assert envelope.error_code is None


def test_one_step_sdk_path_fails_closed_before_side_effect(tmp_path):
    fake = FakeOneStepClient(
        response={
            "orderID": "ord-one",
            "status": "matched",
            "makingAmount": "1.7",
            "takingAmount": "5",
            "transactionsHashes": ["0xhash-one"],
        }
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "pre-POST signed identity persistence requires two-step SDK submit" in (
        result.error_message or ""
    )
    assert result.envelope.order_id is None
    assert result.envelope.signed_order is None
    assert result.envelope.signed_order_hash is None
    assert result.envelope.raw_request_hash == envelope.raw_request_hash
    assert result.envelope.raw_response_json is None
    assert not any(call[0] == "create_and_post_order" for call in fake.calls)


@pytest.mark.parametrize("price", [0.05, 0.95])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_live_submit_unit_price_band_is_inclusive(tmp_path, price, side, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient(post_response={"orderID": "ord-boundary", "status": "live"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _priced_intent(price),
        FakeSnapshot(tick_size=Decimal("0.01")),
        order_type="GTC",
    ).with_updates(side=side)
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "ord-boundary",
    )

    result = _submit(adapter, envelope)

    assert result.status == "accepted"
    assert result.envelope.price == Decimal(str(price))
    assert result.envelope.tick_size == Decimal("0.01")
    assert any(call[0] == "post_order" for call in fake.calls)


@pytest.mark.parametrize(
    ("price", "error_fragment"),
    [
        (0.0, "outside absolute inclusive [0.05, 0.95]"),
        (0.0499, "outside absolute inclusive [0.05, 0.95]"),
        (0.9501, "outside absolute inclusive [0.05, 0.95]"),
        (0.998, "outside absolute inclusive [0.05, 0.95]"),
        (1.0, "outside absolute inclusive [0.05, 0.95]"),
        ("NaN", "must be finite"),
        ("Infinity", "must be finite"),
        ("-Infinity", "must be finite"),
    ],
)
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_live_submit_rejects_out_of_band_price_before_sdk_contact(
    tmp_path, price, error_fragment, side
):
    fake = FakeOneStepClient(response={"orderID": "must-not-submit", "status": "live"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _priced_intent(0.50), FakeSnapshot(), order_type="GTC"
    ).with_updates(price=Decimal(str(price)), side=side)

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert error_fragment in str(result.error_message)
    assert not fake.calls


def test_adapter_sdk_boundary_rejects_even_if_envelope_guard_is_bypassed(
    tmp_path, monkeypatch
):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    fake = FakeOneStepClient(response={"orderID": "must-not-submit", "status": "live"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(
        _priced_intent(0.50), FakeSnapshot(), order_type="GTC"
    ).with_updates(price=Decimal("0.998"))
    monkeypatch.setattr(VenueSubmissionEnvelope, "assert_live_submit_bound", lambda self: None)

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert "outside absolute inclusive [0.05, 0.95]" in str(result.error_message)
    assert not fake.calls


def test_legacy_order_result_preserves_matched_submit_truth(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient(
        post_response={
            "orderID": "ord-one",
            "status": "matched",
            "makingAmount": "1.7",
            "takingAmount": "5",
            "transactionsHashes": ["0xhash-one"],
        }
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod, "_deterministic_v2_order_id", lambda *args, **kwargs: "ord-one"
    )

    submit = _submit(adapter, envelope)

    from src.data.polymarket_client import _legacy_order_result_from_submit

    payload = _legacy_order_result_from_submit(submit)
    assert payload["success"] is True
    assert payload["status"] == "matched"
    assert payload["orderID"] == "ord-one"
    assert payload["makingAmount"] == "1.7"
    assert payload["takingAmount"] == "5"
    assert payload["_venue_response_contract"] == "POLYMARKET_CLOB_V2_HUMAN_SUBMIT_AMOUNTS"
    assert payload["_v2_making_amount"] == "1.7"
    assert payload["_v2_taking_amount"] == "5"
    assert payload["_v2_matched_size"] == "5"
    assert payload["_v2_fill_price"] == "0.34"
    assert payload["transactionsHashes"] == ["0xhash-one"]


def test_point_order_fixed_6_sizes_are_typed_as_human_shares(tmp_path):
    class PointOrderClient(FakeOneStepClient):
        def get_order(self, order_id):
            return {
                "id": order_id,
                "status": "ORDER_STATUS_MATCHED",
                "side": "BUY",
                "original_size": "10000000",
                "size_matched": "3250000",
                "price": "0.34",
            }

    adapter, _ = _adapter(tmp_path, PointOrderClient())

    order = adapter.get_order("ord-fixed-6")
    payload = order.raw

    assert order.status == "MATCHED"
    assert payload["original_size"] == "10"
    assert payload["size_matched"] == "3.25"
    assert payload["status"] == "MATCHED"
    assert payload["_venue_response_contract"] == "POLYMARKET_CLOB_V2_FIXED_6_POINT_ORDER"
    assert payload["_v2_original_size"] == "10"
    assert payload["_v2_matched_size"] == "3.25"
    assert payload["_v2_wire_original_size"] == "10000000"
    assert payload["_v2_wire_size_matched"] == "3250000"
    assert payload["_v2_wire_status"] == "ORDER_STATUS_MATCHED"
    assert payload["_venue_order_status"] == "MATCHED"


def test_point_order_human_decimal_sizes_preserve_live_share_units(tmp_path):
    class PointOrderClient(FakeOneStepClient):
        def get_order(self, order_id):
            return {
                "id": order_id,
                "status": "MATCHED",
                "side": "BUY",
                "original_size": "31.6",
                "size_matched": "31.6",
                "price": "0.6",
            }

    adapter, _ = _adapter(tmp_path, PointOrderClient())
    payload = adapter.get_order("ord-human-point").raw

    assert payload["original_size"] == "31.6"
    assert payload["size_matched"] == "31.6"
    assert payload["_venue_response_contract"] == "POLYMARKET_CLOB_V2_HUMAN_POINT_ORDER"
    assert payload["_v2_original_size"] == "31.6"
    assert payload["_v2_matched_size"] == "31.6"


def test_point_order_ingress_provides_one_human_contract_to_live_consumers(tmp_path):
    class PointOrderClient(FakeOneStepClient):
        def get_order(self, order_id):
            return {
                "id": order_id,
                "status": "ORDER_STATUS_LIVE",
                "side": "BUY",
                "asset_id": "tok-1",
                "maker_address": "0xfunder",
                "originalSize": "10000000",
                "sizeMatched": "3250000",
                "price": "0.34",
            }

    adapter, _ = _adapter(tmp_path, PointOrderClient())
    payload = adapter.get_order("ord-live-fixed-6").raw

    from src.execution.edli_resting_absorbed_resolver import _our_live_resting_order
    from src.execution.exchange_reconcile import _order_matched_size
    from src.execution.exit_lifecycle import _venue_open_order_remaining_size
    from src.execution.fill_tracker import _extract_filled_shares, _normalize_status

    assert _normalize_status(payload) == "PARTIALLY_MATCHED"
    assert payload["originalSize"] == "10"
    assert payload["sizeMatched"] == "3.25"
    assert _extract_filled_shares(
        payload,
        allow_order_size_fallback=False,
    ) == pytest.approx(3.25)
    assert _venue_open_order_remaining_size(payload) == Decimal("6.75")
    assert _order_matched_size(payload) == Decimal("3.25")
    assert _our_live_resting_order(
        [payload],
        token_id="tok-1",
        funder_address="0xfunder",
        limit_price=0.34,
        order_size=10.0,
    ) is payload


def test_two_step_sdk_path_produces_envelope_with_signed_order_hash(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    signed = b"fake-signed-order"
    fake = FakeTwoStepClient(post_response={"orderID": "ord-two", "status": "live"}, signed_order=signed)
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod, "_deterministic_v2_order_id", lambda *args, **kwargs: "ord-two"
    )

    result = _submit(adapter, envelope)

    assert [call[0] for call in fake.calls if call[0] in {"create_order", "post_order"}] == [
        "create_order",
        "post_order",
    ]
    assert result.status == "accepted"
    assert result.envelope.order_id == "ord-two"
    assert result.envelope.signed_order == signed
    assert result.envelope.signed_order_hash == hashlib.sha256(signed).hexdigest()


def test_missing_order_id_response_is_ambiguous_with_signed_identity(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient(post_response={"success": True, "status": "LIVE"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod, "_deterministic_v2_order_id", lambda *args, **kwargs: "ord-expected"
    )

    with pytest.raises(adapter_mod.AmbiguousSubmitError) as caught:
        _submit(adapter, envelope)

    assert caught.value.envelope.order_id == "ord-expected"
    assert caught.value.envelope.error_code == "V2_ORDER_ID_ACK_MISMATCH"
    assert "missing deterministic order id" in (caught.value.envelope.error_message or "")


def test_success_false_response_returns_typed_rejection_with_error_code(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient(
        post_response={
            "success": False,
            "errorCode": "INSUFFICIENT_BALANCE",
            "errorMessage": "not enough funds",
        }
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    monkeypatch.setattr(
        adapter_mod, "_deterministic_v2_order_id", lambda *args, **kwargs: "ord-rejected"
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "INSUFFICIENT_BALANCE"
    assert result.envelope.error_code == "INSUFFICIENT_BALANCE"
    assert result.envelope.error_message == "not enough funds"
    assert "INSUFFICIENT_BALANCE" in (result.envelope.raw_response_json or "")


def test_envelope_schema_version_is_pinned_and_roundtrips(tmp_path):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    adapter, _ = _adapter(tmp_path)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    assert VenueSubmissionEnvelope.SCHEMA_VERSION == 1
    payload = envelope.to_json()
    assert '"schema_version":1' in payload
    restored = VenueSubmissionEnvelope.from_json(payload)
    assert restored == envelope
    assert isinstance(restored.tick_size, Decimal)
    assert restored.tick_size == Decimal("0.01")


def test_envelope_rejects_unknown_outcome_label(tmp_path):
    adapter, _ = _adapter(tmp_path)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    with pytest.raises(ValueError, match="outcome_label must be YES or NO"):
        envelope.with_updates(outcome_label="UNKNOWN")


def test_stale_snapshot_raises_before_envelope_creation(tmp_path):
    from src.venue.polymarket_v2_adapter import StaleMarketSnapshotError

    adapter, _ = _adapter(tmp_path)
    stale_snapshot = FakeSnapshot(
        captured_at="2000-01-01T00:00:00+00:00",
        freshness_window_seconds=1,
    )

    with pytest.raises(StaleMarketSnapshotError, match="outside freshness window"):
        adapter.create_submission_envelope(_intent(), stale_snapshot, order_type="GTC")


def test_neg_risk_passthrough_v2_preserves_snapshot_value(tmp_path):
    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(neg_risk=True), order_type="GTC")

    result = _submit(adapter, envelope)

    create_call = next(call for call in fake.calls if call[0] == "create_order")
    options = create_call[2]
    assert envelope.neg_risk is True
    assert getattr(options, "neg_risk") is True
    assert result.envelope.neg_risk is True


def test_submit_rejects_unbound_pre_submit_funder_before_sdk_contact(tmp_path):
    fake = FakeOneStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    pre_submit_placeholder = envelope.with_updates(funder_address="UNRESOLVED_PRE_SUBMIT_FUNDER")

    result = _submit(adapter, pre_submit_placeholder)

    assert result.status == "rejected"
    assert result.envelope.error_code == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    assert "missing pre-bound funder_address" in str(result.envelope.error_message)
    assert not fake.calls


def test_submit_rejects_mismatched_pre_submit_funder_before_sdk_contact(tmp_path):
    fake = FakeOneStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")
    mismatched = envelope.with_updates(funder_address="0xotherfunder")

    result = _submit(adapter, mismatched)

    assert result.status == "rejected"
    assert result.envelope.error_code == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    assert "does not match adapter funder_address" in str(result.envelope.error_message)
    assert not fake.calls


def test_legacy_sell_compatibility_hashes_final_side_and_size(tmp_path):
    # AMD-T1F-2: T1F-ADAPTER-ASSERTS-LIVE-BOUND-BEFORE-SDK makes placeholder→SDK
    # contact impossible by design. This test now inspects the envelope's hash
    # fields directly rather than asserting SDK call_count.
    adapter, _ = _adapter(tmp_path, FakeTwoStepClient())

    envelope = adapter._create_compat_submission_envelope(
        token_id="yes-token",
        price=Decimal("0.5"),
        size=Decimal("3.25"),
        side="SELL",
        order_type="GTC",
        sdk_snapshot=adapter._compat_snapshot_for_token("yes-token"),
    )
    buy_envelope = adapter._create_compat_submission_envelope(
        token_id="yes-token",
        price=Decimal("0.5"),
        size=Decimal("3.25"),
        side="BUY",
        order_type="GTC",
        sdk_snapshot=adapter._compat_snapshot_for_token("yes-token"),
    )
    assert envelope.side == "SELL"
    assert envelope.is_compatibility_placeholder is True
    with pytest.raises(ValueError, match="compatibility submission envelope"):
        envelope.assert_live_submit_bound()
    assert envelope.canonical_pre_sign_payload_hash != buy_envelope.canonical_pre_sign_payload_hash


def test_polymarket_client_bound_compatibility_envelope_rejects_before_adapter_submit(tmp_path):
    from src.data.polymarket_client import PolymarketClient

    adapter, _ = _adapter(tmp_path, FakeTwoStepClient())
    envelope = adapter._create_compat_submission_envelope(
        token_id="yes-token",
        price=Decimal("0.5"),
        size=Decimal("3.25"),
        side="BUY",
        order_type="GTC",
        sdk_snapshot=adapter._compat_snapshot_for_token("yes-token"),
    )

    class FakeAdapter:
        def submit(self, bound_envelope):  # pragma: no cover - tripwire
            raise AssertionError("compatibility envelope must reject before adapter submit")

    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()
    client.bind_submission_envelope(envelope)

    result = client.place_limit_order(
        token_id="yes-token",
        price=0.5,
        size=3.25,
        side="BUY",
        order_type="GTC",
    )

    assert result["success"] is False
    assert result["errorCode"] == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    assert "compatibility submission envelope" in result["errorMessage"]
    assert result["_venue_submission_envelope"]["condition_id"].startswith("legacy:")
    assert (
        result["_venue_submission_envelope"]["error_code"]
        == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    )


def test_polymarket_client_unbound_place_limit_order_fails_closed_without_submit():
    from src.data.polymarket_client import PolymarketClient

    class FakeAdapter:
        def __init__(self):
            self.calls = []

        def preflight(self):  # pragma: no cover - tripwire
            raise AssertionError("unbound wrapper must fail before v2 preflight")

        def submit_limit_order(self, *, token_id, price, size, side, order_type):
            self.calls.append(
                {
                    "token_id": token_id,
                    "price": price,
                    "size": size,
                    "side": side,
                    "order_type": order_type,
                }
            )
            raise AssertionError("unbound wrapper must not call compatibility submit")

    client = PolymarketClient()
    fake_adapter = FakeAdapter()
    client._v2_adapter = fake_adapter

    with pytest.warns(DeprecationWarning, match="compatibility wrapper"):
        result = client.place_limit_order(token_id="yes-token", price=0.5, size=20.0, side="BUY")

    assert fake_adapter.calls == []
    assert result == {
        "success": False,
        "status": "rejected",
        "errorCode": "BOUND_ENVELOPE_REQUIRED",
        "errorMessage": "live placement requires bind_submission_envelope() before place_limit_order()",
    }


def test_polymarket_client_bound_envelope_bypasses_legacy_compat_submit(tmp_path):
    from src.data.polymarket_client import PolymarketClient

    envelope = _adapter(tmp_path, FakeOneStepClient())[0].create_submission_envelope(
        _intent(),
        FakeSnapshot(condition_id="cond-bound", question_id="q-bound"),
        order_type="GTC",
    )

    class FakeAdapter:
        def __init__(self):
            self.submit_calls = []
            self.compat_calls = []

        def submit(self, bound_envelope, *, before_post=None):
            self.submit_calls.append(bound_envelope)
            assert before_post is identity_persister
            from src.venue.polymarket_v2_adapter import SubmitResult

            return SubmitResult(
                status="accepted",
                envelope=bound_envelope.with_updates(order_id="ord-bound"),
            )

        def submit_limit_order(self, **kwargs):  # pragma: no cover - tripwire
            self.compat_calls.append(kwargs)
            raise AssertionError("bound live submit must not use compatibility envelope path")

    client = PolymarketClient()
    fake_adapter = FakeAdapter()
    client._v2_adapter = fake_adapter
    client.bind_submission_envelope(envelope)
    identity_persister = lambda signed_envelope: None
    client.bind_signed_submission_identity_persister(identity_persister)

    result = client.place_limit_order(token_id="yes-token", price=0.5, size=20.0, side="BUY")

    assert fake_adapter.submit_calls == [envelope]
    assert fake_adapter.compat_calls == []
    assert result["orderID"] == "ord-bound"
    assert result["_venue_submission_envelope"]["condition_id"] == "cond-bound"
    assert not result["_venue_submission_envelope"]["condition_id"].startswith("legacy:")


def test_polymarket_client_bound_envelope_rejects_submit_shape_mismatch(tmp_path):
    from src.data.polymarket_client import PolymarketClient

    envelope = _adapter(tmp_path, FakeOneStepClient())[0].create_submission_envelope(
        _intent(),
        FakeSnapshot(),
        order_type="GTC",
    )

    class FakeAdapter:
        def submit(self, bound_envelope):  # pragma: no cover - tripwire
            raise AssertionError("mismatched bound envelope must fail before adapter submit")

    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()
    client.bind_submission_envelope(envelope)

    result = client.place_limit_order(token_id="wrong-token", price=0.5, size=20.0, side="BUY")

    assert result["success"] is False
    assert result["errorCode"] == "BOUND_ENVELOPE_MISMATCH"
    assert result["_venue_submission_envelope"]["condition_id"] == "cond-123"


def test_polymarket_client_requires_pre_post_identity_persister(tmp_path):
    from src.data.polymarket_client import PolymarketClient

    envelope = _adapter(tmp_path, FakeOneStepClient())[0].create_submission_envelope(
        _intent(), FakeSnapshot(), order_type="GTC"
    )

    class FakeAdapter:
        def submit(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("missing identity persister must fail before submit")

    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()
    client.bind_submission_envelope(envelope)

    result = client.place_limit_order(
        token_id="yes-token", price=0.5, size=20.0, side="BUY"
    )

    assert result["success"] is False
    assert result["errorCode"] == "SIGNED_IDENTITY_PERSISTER_REQUIRED"


def test_polymarket_client_fee_rate_accepts_current_base_fee_shape(monkeypatch):
    from src.data import polymarket_client as pm

    pm._FEE_RATE_CACHE.clear()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"base_fee": 30}

    client = pm.PolymarketClient()
    calls = []

    class PublicClient:
        def get(self, url, *, params=None):
            calls.append((url, params))
            return Response()

    client._public_http_client = PublicClient()

    assert client.get_fee_rate("token-1") == pytest.approx(0.003)
    assert client.get_fee_rate_details("token-1")["fee_rate_bps"] == pytest.approx(30.0)
    assert calls == [(f"{pm.CLOB_BASE}/fee-rate", {"token_id": "token-1"})]


def test_polymarket_client_fee_rate_rejects_malformed_shape(monkeypatch):
    from src.data import polymarket_client as pm

    pm._FEE_RATE_CACHE.clear()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"feeSchedule": {"feesEnabled": True}}

    client = pm.PolymarketClient()

    class PublicClient:
        def get(self, url, *, params=None):
            return Response()

    client._public_http_client = PublicClient()

    with pytest.raises(RuntimeError, match="base_fee"):
        client.get_fee_rate("token-1")


def test_polymarket_client_reuses_public_http_client_for_clob_reads(monkeypatch):
    from src.data import polymarket_client as pm

    pm._FEE_RATE_CACHE.clear()

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class PublicClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
            self.closed = False

        def get(self, url, *, params=None):
            self.calls.append((url, params))
            if url.endswith("/markets/condition-1"):
                return Response({"condition_id": "condition-1", "tokens": []})
            if url.endswith("/book"):
                return Response({"bids": [], "asks": []})
            if url.endswith("/fee-rate"):
                return Response({"base_fee": 30})
            raise AssertionError(f"unexpected URL: {url}")

        def close(self):
            self.closed = True

    clients = []

    def client_factory(*args, **kwargs):
        client = PublicClient(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(pm.httpx, "Client", client_factory)

    client = pm.PolymarketClient()
    client._v2_adapter = type(
        "AdapterTripwire",
        (),
        {
            "get_clob_market_info": lambda self, condition_id: (_ for _ in ()).throw(
                AssertionError("public CLOB market facts must not use the V2 SDK adapter")
            )
        },
    )()
    assert client.get_clob_market_info("condition-1") == {"condition_id": "condition-1", "tokens": []}
    assert client.get_orderbook_snapshot("token-1") == {"bids": [], "asks": []}
    assert client.get_fee_rate_details("token-1")["fee_rate_bps"] == pytest.approx(30.0)

    assert len(clients) == 1
    assert clients[0].calls == [
        (f"{pm.CLOB_BASE}/markets/condition-1", None),
        (f"{pm.CLOB_BASE}/book", {"token_id": "token-1"}),
        (f"{pm.CLOB_BASE}/fee-rate", {"token_id": "token-1"}),
    ]

    client.close()
    assert clients[0].closed is True
    assert client._public_http_client is None


def test_polymarket_client_cancel_blocks_before_adapter_when_cutover_disallows(monkeypatch):
    from src.control.cutover_guard import CutoverDecision, CutoverPending, CutoverState
    from src.data.polymarket_client import PolymarketClient

    class FakeAdapter:
        def cancel(self, _order_id, *, deadline_monotonic=None):  # pragma: no cover - tripwire
            raise AssertionError("adapter.cancel must not run when CutoverGuard blocks")

    monkeypatch.setattr(
        "src.control.cutover_guard.gate_for_intent",
        lambda _intent_kind: CutoverDecision(False, False, False, "BLOCKED:CANCEL", CutoverState.BLOCKED),
    )
    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()

    with pytest.raises(CutoverPending, match="BLOCKED:CANCEL"):
        client.cancel_order("ord-cancel")


def test_v2_cancel_order_method_uses_order_payload(tmp_path):
    fake = FakeCancelOrderClient()
    adapter, _ = _adapter(tmp_path, fake)

    result = adapter.cancel("ord-cancel")

    assert result.status == "CANCELED"
    assert result.order_id == "ord-cancel"
    assert fake.calls[0][0] == "cancel_order"
    assert fake.calls[0][1].orderID == "ord-cancel"
    assert '"canceled":["ord-cancel"]' in (result.raw_response_json or "")


def test_v2_cancel_enforces_remaining_deadline(tmp_path, monkeypatch):
    from src.venue import polymarket_v2_adapter as adapter_module

    observed = {}

    def bounded_cancel(_client, order_id, deadline_monotonic):
        observed.update(order_id=order_id, deadline=deadline_monotonic)
        return {"canceled": [order_id]}

    monkeypatch.setattr(adapter_module, "_bounded_cancel_request", bounded_cancel)
    adapter, _ = _adapter(tmp_path, FakeCancelOrderClient())
    deadline = time.monotonic() + 0.2
    result = adapter.cancel("ord-cancel", deadline_monotonic=deadline)
    assert result.status == "CANCELED"
    assert observed["order_id"] == "ord-cancel"
    assert observed["deadline"] == deadline

    from src.venue.polymarket_v2_adapter import IncompleteOrderTruthError

    with pytest.raises(IncompleteOrderTruthError, match="deadline elapsed"):
        adapter.cancel("ord-cancel", deadline_monotonic=time.monotonic() - 0.001)


def test_bounded_cancel_uses_private_native_transport_without_global_sdk_mutation(monkeypatch):
    import httpx
    from py_clob_client_v2.http_helpers import helpers
    from src.venue.polymarket_v2_adapter import _bounded_cancel_request

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    class RecordingClient:
        instances = []

        def __init__(self, *, timeout, **_kwargs):
            self.timeout = timeout
            self.calls = []
            self.__class__.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, **kwargs):
            self.calls.append(kwargs)
            return httpx.Response(200, json={"canceled": ["ord-cancel"]})

    monkeypatch.setattr(httpx, "Client", RecordingClient)
    original = helpers._http_client
    client = SimpleNamespace(
        host="https://clob.example",
        use_server_time=False,
        signer=Signer(),
        creds=Creds(),
        assert_level_2_auth=lambda: None,
    )
    deadline = time.monotonic() + 0.2
    result = _bounded_cancel_request(client, "ord-cancel", deadline)
    assert result == {"canceled": ["ord-cancel"]}
    assert helpers._http_client is original
    assert RecordingClient.instances[0].calls[0]["method"] == "DELETE"
    assert RecordingClient.instances[0].calls[0]["timeout"].read <= 0.2


def test_bounded_cancel_native_timeout_has_no_late_second_call(monkeypatch):
    import httpx
    from src.venue.polymarket_v2_adapter import _bounded_cancel_request

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    calls = []

    class TimeoutClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, **_kwargs):
            calls.append(1)
            raise httpx.ReadTimeout("native timeout")

    monkeypatch.setattr(httpx, "Client", TimeoutClient)
    with pytest.raises(httpx.ReadTimeout):
        _bounded_cancel_request(
            SimpleNamespace(
                host="https://clob.example",
                use_server_time=False,
                signer=Signer(),
                creds=Creds(),
                assert_level_2_auth=lambda: None,
            ),
            "ord-cancel",
            time.monotonic() + 0.2,
        )
    assert calls == [1]


def test_internal_heartbeat_uses_private_bounded_transport_without_global_sdk_mutation(monkeypatch):
    import httpx
    from py_clob_client_v2.http_helpers import helpers
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    class RecordingTransport:
        instances = []

        def __init__(self, *, timeout, **_kwargs):
            self.timeout = timeout
            self.calls = []
            self.__class__.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, **kwargs):
            self.calls.append(kwargs)
            return httpx.Response(200, json={"heartbeat_id": "next-heartbeat"})

    monkeypatch.setattr(httpx, "Client", RecordingTransport)
    sdk_client = SimpleNamespace(
        host="https://clob.example",
        signer=Signer(),
        creds=Creds(),
        assert_level_2_auth=lambda: None,
    )
    adapter = PolymarketV2Adapter(
        host="https://clob.example",
        funder_address="0xfunder",
        signer_key="test-key",
        api_creds=Creds(),
        q1_egress_evidence_path=None,
        client_factory=lambda **_kwargs: sdk_client,
        network_timeout_seconds=0.25,
    )
    original_transport = helpers._http_client

    result = adapter.post_heartbeat("current-heartbeat")

    assert result.raw == {"heartbeat_id": "next-heartbeat"}
    assert helpers._http_client is original_transport
    assert RecordingTransport.instances[0].calls[0]["method"] == "POST"
    assert RecordingTransport.instances[0].calls[0]["timeout"].read <= 0.25


def test_main_internal_heartbeat_owner_uses_adapter_bounded_transport(monkeypatch):
    import httpx

    import src.main as main_module
    import src.data.polymarket_client as client_module
    import src.control.heartbeat_supervisor as heartbeat_module
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    class RecordingTransport:
        calls = []

        def __init__(self, *, timeout, **_kwargs):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, **kwargs):
            self.__class__.calls.append(kwargs)
            return httpx.Response(200, json={"heartbeat_id": "next-heartbeat"})

    monkeypatch.setattr(httpx, "Client", RecordingTransport)
    sdk_client = SimpleNamespace(
        host="https://clob.example",
        signer=Signer(),
        creds=Creds(),
        assert_level_2_auth=lambda: None,
    )
    adapter = PolymarketV2Adapter(
        host="https://clob.example",
        funder_address="0xfunder",
        signer_key="test-key",
        api_creds=Creds(),
        q1_egress_evidence_path=None,
        client_factory=lambda **_kwargs: sdk_client,
        network_timeout_seconds=0.25,
    )

    class Client:
        def _ensure_v2_adapter(self):
            return adapter

    monkeypatch.setattr(client_module, "PolymarketClient", Client)
    monkeypatch.setattr(main_module, "_external_venue_heartbeat_enabled", lambda: False)
    monkeypatch.setattr(heartbeat_module, "heartbeat_cadence_seconds_from_env", lambda: 10)
    monkeypatch.setattr(heartbeat_module, "fresh_heartbeat_id_from_status", lambda: "")
    monkeypatch.setattr(heartbeat_module, "configure_global_supervisor", lambda _supervisor: None)
    monkeypatch.setattr(heartbeat_module, "write_heartbeat_keeper_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "_start_venue_background_maintenance_async", lambda _adapter: None)
    monkeypatch.setattr(main_module, "_venue_heartbeat_supervisor", None)
    monkeypatch.setattr(main_module, "_venue_heartbeat_adapter", None)

    main_module._write_venue_heartbeat()

    assert RecordingTransport.calls[0]["method"] == "POST"
    assert RecordingTransport.calls[0]["url"].endswith("/v1/heartbeats")


def test_production_server_time_heartbeat_signs_post_with_bounded_server_timestamp(monkeypatch):
    import httpx

    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    class RecordingTransport:
        instances = []

        def __init__(self, *, timeout, **_kwargs):
            self.timeout = timeout
            self.calls = []
            self.__class__.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            return httpx.Response(200, json={"timestamp": "1700000000"})

        def request(self, **kwargs):
            self.calls.append((kwargs["method"], kwargs["url"], kwargs))
            return httpx.Response(200, json={"heartbeat_id": "next-heartbeat"})

    monkeypatch.setattr(httpx, "Client", RecordingTransport)
    sdk_client = SimpleNamespace(
        host="https://clob.example",
        use_server_time=True,
        signer=Signer(),
        creds=Creds(),
        assert_level_2_auth=lambda: None,
    )
    adapter = PolymarketV2Adapter(
        host="https://clob.example",
        funder_address="0xfunder",
        signer_key="test-key",
        api_creds=Creds(),
        q1_egress_evidence_path=None,
        client_factory=lambda **_kwargs: sdk_client,
        network_timeout_seconds=0.25,
    )

    result = adapter.post_heartbeat("current-heartbeat")
    calls = RecordingTransport.instances[0].calls

    assert result.raw == {"heartbeat_id": "next-heartbeat"}
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[0][1].endswith("/time")
    assert calls[1][2]["headers"]["POLY_TIMESTAMP"] == "1700000000"
    assert json.loads(calls[1][2]["content"].decode("utf-8")) == {
        "heartbeat_id": "current-heartbeat"
    }


def test_server_time_heartbeat_timeout_never_posts(monkeypatch):
    import httpx

    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class Signer:
        def address(self):
            return "0xabc"

    class Creds:
        api_secret = "c2VjcmV0"
        api_key = "key"
        api_passphrase = "pass"

    class TimeoutTransport:
        post_calls = 0

        def __init__(self, *, timeout, **_kwargs):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _url, **_kwargs):
            raise httpx.ReadTimeout("server time timeout")

        def request(self, **_kwargs):
            self.__class__.post_calls += 1
            raise AssertionError("heartbeat POST must not follow /time timeout")

    monkeypatch.setattr(httpx, "Client", TimeoutTransport)
    sdk_client = SimpleNamespace(
        host="https://clob.example",
        use_server_time=True,
        signer=Signer(),
        creds=Creds(),
        assert_level_2_auth=lambda: None,
    )
    adapter = PolymarketV2Adapter(
        host="https://clob.example",
        funder_address="0xfunder",
        signer_key="test-key",
        api_creds=Creds(),
        q1_egress_evidence_path=None,
        client_factory=lambda **_kwargs: sdk_client,
        network_timeout_seconds=0.25,
    )

    with pytest.raises(httpx.ReadTimeout):
        adapter.post_heartbeat("current-heartbeat")
    assert TimeoutTransport.post_calls == 0


def test_bounded_cancel_requires_typed_l2_auth_before_signed_request():
    from src.venue.polymarket_v2_adapter import IncompleteOrderTruthError, _bounded_cancel_request

    with pytest.raises(IncompleteOrderTruthError, match="L2 cancel authentication unavailable"):
        _bounded_cancel_request(
            SimpleNamespace(
                host="https://clob.example",
                use_server_time=False,
                signer=object(),
                creds=object(),
            ),
            "ord-cancel",
            time.monotonic() + 0.2,
        )


class TestCancelSingleResponseContract:
    """R6-a response-contract layer: single-order cancel() must apply the
    same live-verified (2026-07-05) envelope exact-membership check that
    cancel_batch already applies, closing the #429 false-positive where a
    batch-envelope-shaped response mentioning some OTHER order id was
    silently reported as CANCELED for THIS order."""

    def test_envelope_mentioning_other_order_stays_unknown_fail_closed(self, tmp_path):
        """2026-07-05 live incident class, single-cancel variant: before
        this packet, `_nonempty(raw_dict.get("canceled"))` was truthy
        whenever ANY order id appeared in "canceled", regardless of
        whether it was the order this call asked to cancel."""
        fake = FakeCancelOrderClient(response={"canceled": ["other-ord"], "not_canceled": {}})
        adapter, _ = _adapter(tmp_path, fake)

        result = adapter.cancel("ord-cancel")

        assert result.status == "UNKNOWN"
        assert result.order_id == "ord-cancel"

    def test_envelope_not_canceled_dict_maps_with_reason(self, tmp_path):
        fake = FakeCancelOrderClient(
            response={"canceled": [], "not_canceled": {"ord-cancel": "order not found"}}
        )
        adapter, _ = _adapter(tmp_path, fake)

        result = adapter.cancel("ord-cancel")

        assert result.status == "NOT_CANCELED"
        assert "order not found" in (result.error_message or "")

    def test_legacy_status_shape_still_confirms_cancel(self, tmp_path):
        """Non-envelope legacy per-order shape (status key, no
        canceled/not_canceled) must keep working exactly as before."""
        fake = FakeCancelOrderClient(response={"orderID": "ord-cancel", "status": "CANCELED"})
        adapter, _ = _adapter(tmp_path, fake)

        result = adapter.cancel("ord-cancel")

        assert result.status == "CANCELED"
        assert result.order_id == "ord-cancel"

    def test_unrecognized_shape_raises_venue_response_shape_error(self, tmp_path):
        from src.venue.response_contracts import VenueResponseShapeError

        fake = FakeCancelOrderClient(response={"foo": "bar"})
        adapter, _ = _adapter(tmp_path, fake)

        with pytest.raises(VenueResponseShapeError, match="cancel"):
            adapter.cancel("ord-cancel")


class TestOrderStatusResponseContract:
    """R6-a: get_order/get_open_orders must fail closed (raise) on a
    response item that carries neither 'status' nor 'state', rather than
    silently defaulting to the placeholder status string "UNKNOWN"."""

    def test_get_order_empty_payload_raises_typed_not_found(self, tmp_path):
        from src.venue.response_contracts import VenueOrderNotFound

        class EmptyPointOrderClient:
            def get_order(self, _order_id):
                return {}

        adapter, _ = _adapter(tmp_path, EmptyPointOrderClient())

        with pytest.raises(VenueOrderNotFound, match="ord-missing"):
            adapter.get_order("ord-missing")

    def test_get_order_missing_status_key_raises(self, tmp_path):
        from src.venue.response_contracts import VenueResponseShapeError

        class FakeNoStatusOrderClient:
            def get_order(self, order_id):
                return {"orderID": order_id}

        adapter, _ = _adapter(tmp_path, FakeNoStatusOrderClient())

        with pytest.raises(VenueResponseShapeError, match="get_order"):
            adapter.get_order("ord-1")

    def test_get_open_orders_item_missing_status_key_raises(self, tmp_path):
        from src.venue.response_contracts import VenueResponseShapeError

        class FakeNoStatusOpenOrdersClient:
            def __init__(self):
                self.calls = []

            def get_open_orders(self, **kwargs):
                self.calls.append(("get_open_orders", kwargs))
                return [{"orderID": "ord-open"}]

        adapter, _ = _adapter(tmp_path, FakeNoStatusOpenOrdersClient())

        with pytest.raises(VenueResponseShapeError, match="get_open_orders"):
            adapter.get_open_orders()

    @pytest.mark.parametrize(
        "amounts",
        (
            {"size_matched": "3250000"},
            {"original_size": "10000000"},
            {"original_size": "-1", "size_matched": "0"},
            {"original_size": "not-a-number", "size_matched": "0"},
        ),
    )
    def test_get_order_malformed_fixed_6_amounts_fail_closed(
        self,
        tmp_path,
        amounts,
    ):
        from src.venue.response_contracts import VenueResponseShapeError

        class MalformedPointOrderClient:
            def get_order(self, order_id):
                return {
                    "id": order_id,
                    "status": "ORDER_STATUS_MATCHED",
                    **amounts,
                }

        adapter, _ = _adapter(tmp_path, MalformedPointOrderClient())

        with pytest.raises(VenueResponseShapeError, match="point-order"):
            adapter.get_order("ord-malformed-fixed-6")


def test_polymarket_client_cancel_payload_is_exit_safety_parseable(monkeypatch):
    from src.control.cutover_guard import CutoverDecision, CutoverState
    from src.data.polymarket_client import PolymarketClient
    from src.execution.exit_safety import parse_cancel_response
    from src.venue.polymarket_v2_adapter import CancelResult

    class FakeAdapter:
        def cancel(self, order_id, *, deadline_monotonic=None):
            return CancelResult(
                status="CANCELED",
                order_id=order_id,
                raw_response_json='{"canceled":["ord-cancel"],"not_canceled":[]}',
            )

    monkeypatch.setattr(
        "src.control.cutover_guard.gate_for_intent",
        lambda _intent_kind: CutoverDecision(False, True, False, None, CutoverState.LIVE_ENABLED),
    )
    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()

    payload = client.cancel_order("ord-cancel")

    assert payload["orderID"] == "ord-cancel"
    assert payload["status"] == "CANCELED"
    assert parse_cancel_response(payload).status == "CANCELED"


def test_polymarket_client_cancel_legacy_adapter_call_omits_deadline_keyword(monkeypatch):
    from src.control.cutover_guard import CutoverDecision, CutoverState
    from src.data.polymarket_client import PolymarketClient
    from src.venue.polymarket_v2_adapter import CancelResult

    class LegacyAdapter:
        def __init__(self):
            self.calls = []

        def cancel(self, order_id):
            self.calls.append(order_id)
            return CancelResult(
                status="CANCELED",
                order_id=order_id,
                raw_response_json='{"canceled":["ord-cancel"]}',
            )

    monkeypatch.setattr(
        "src.control.cutover_guard.gate_for_intent",
        lambda _intent_kind: CutoverDecision(False, True, False, None, CutoverState.LIVE_ENABLED),
    )
    client = PolymarketClient()
    adapter = LegacyAdapter()
    client._v2_adapter = adapter

    assert client.cancel_order("ord-cancel")["status"] == "CANCELED"
    assert adapter.calls == ["ord-cancel"]


def test_polymarket_client_maps_typed_point_order_absence_to_none():
    from src.data.polymarket_client import PolymarketClient
    from src.venue.response_contracts import VenueOrderNotFound

    class FakeAdapter:
        def get_order(self, order_id):
            raise VenueOrderNotFound(order_id)

    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()

    assert client.get_order("ord-missing") is None
    assert client.authenticated_point_absence_returns_none is True


@pytest.mark.parametrize(
    "error",
    (
        RuntimeError("order not found after transport reset"),
        RuntimeError("upstream returned 404 without typed venue proof"),
    ),
)
def test_polymarket_client_does_not_infer_point_absence_from_error_text(error):
    from src.data.polymarket_client import PolymarketClient

    class FakeAdapter:
        def get_order(self, _order_id):
            raise error

    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()

    with pytest.raises(RuntimeError, match=str(error)):
        client.get_order("ord-unknown")


def test_polymarket_client_wrapper_fails_closed_before_unbound_v2_preflight():
    from src.data.polymarket_client import PolymarketClient

    class FakeAdapter:
        def __init__(self):
            self.submit_called = False

        def preflight(self):  # pragma: no cover - tripwire
            raise AssertionError("unbound wrapper must fail before v2 preflight")

        def submit_limit_order(self, **_kwargs):
            self.submit_called = True
            raise AssertionError("submit_limit_order must not run after preflight rejection")

    client = PolymarketClient()
    fake_adapter = FakeAdapter()
    client._v2_adapter = fake_adapter

    with pytest.warns(DeprecationWarning, match="compatibility wrapper"):
        result = client.place_limit_order(token_id="yes-token", price=0.5, size=20.0, side="BUY")

    assert result == {
        "success": False,
        "status": "rejected",
        "errorCode": "BOUND_ENVELOPE_REQUIRED",
        "errorMessage": "live placement requires bind_submission_envelope() before place_limit_order()",
    }
    assert fake_adapter.submit_called is False


def test_old_v1_sdk_import_is_removed_from_live_client_paths():
    live_paths = [
        Path("src/data/polymarket_client.py"),
        Path("src/execution/executor.py"),
        Path("src/execution/exit_lifecycle.py"),
    ]
    offenders = []
    for path in live_paths:
        tree = ast.parse(path.read_text(), filename=path.as_posix())
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        if any(
            module == "py_clob_client" or module.startswith("py_clob_client.")
            for module in imported_modules
        ):
            offenders.append(path.as_posix())
    assert offenders == []


# ---------------------------------------------------------------------------
# W2.1: PolymarketV2Adapter.submit_batch / cancel_batch
# ---------------------------------------------------------------------------


class FakeBatchTwoStepClient:
    """Two-step SDK client fake supporting post_orders/cancel_orders.

    create_order returns a signed payload that VARIES per call (keyed on
    token_id+price) so distinct envelopes hash to distinct signed_order
    hashes -- required to exercise echo-id mapping meaningfully.
    """

    def __init__(self, post_orders_response=None, cancel_orders_response=None):
        self.post_orders_response = post_orders_response
        self.cancel_orders_response = cancel_orders_response
        self.calls = []

    def get_ok(self):
        self.calls.append(("get_ok",))
        return {"ok": True}

    def create_order(self, order_args, options=None):
        self.calls.append(("create_order", order_args, options))
        return f"signed:{order_args.token_id}:{order_args.price}".encode()

    def get_order_book(self, token_id):
        self.calls.append(("get_order_book", token_id))
        return {
            "asset_id": token_id,
            "bids": [{"price": "0.49", "size": "100"}],
            "asks": [{"price": "0.50", "size": "100"}],
        }

    def post_orders(self, args, post_only=False, defer_exec=False):
        self.calls.append(("post_orders", args, post_only, defer_exec))
        return self.post_orders_response

    def cancel_orders(self, order_ids):
        self.calls.append(("cancel_orders", order_ids))
        return self.cancel_orders_response


class FakeSigningFailsOnSecondClient(FakeBatchTwoStepClient):
    def create_order(self, order_args, options=None):
        self.calls.append(("create_order", order_args, options))
        if len([c for c in self.calls if c[0] == "create_order"]) == 2:
            raise RuntimeError("local signing failed on second order")
        return f"signed:{order_args.token_id}:{order_args.price}".encode()


class FakePostOrdersExceptionClient(FakeBatchTwoStepClient):
    def post_orders(self, args, post_only=False, defer_exec=False):
        self.calls.append(("post_orders", args, post_only, defer_exec))
        raise TimeoutError("post_orders timed out")


class FakeBatchOrderManagerNotReady425Client(FakeBatchTwoStepClient):
    def post_orders(self, args, post_only=False, defer_exec=False):
        from py_clob_client_v2.exceptions import PolyApiException

        self.calls.append(("post_orders", args, post_only, defer_exec))
        response = SimpleNamespace(
            status_code=425,
            json=lambda: {"error": "order manager not ready, please retry"},
        )
        raise PolyApiException(response)


class FakeCancelOrdersExceptionClient(FakeBatchTwoStepClient):
    def cancel_orders(self, order_ids):
        self.calls.append(("cancel_orders", order_ids))
        raise TimeoutError("cancel_orders timed out")


def _priced_intent(price: float) -> ExecutionIntent:
    from dataclasses import replace

    return replace(_intent(), limit_price=price)


def _batch_envelopes(adapter, n: int, *, post_only: bool = True):
    # FakeSnapshot's yes_token_id is fixed ("yes-token"); vary limit_price
    # per order instead of token_id so create_submission_envelope's
    # assert_live_submit_bound (selected_outcome_token_id must equal the
    # snapshot's yes/no token) stays satisfied while still producing
    # distinct signed_order_hash values per order (FakeBatchTwoStepClient
    # keys signing on token_id+price).
    return [
        adapter.create_submission_envelope(
            _priced_intent(0.50 + i * 0.01), FakeSnapshot(), order_type="GTC", post_only=post_only
        )
        for i in range(n)
    ]


def _signed_hash_for(price: str) -> str:
    return hashlib.sha256(f"signed:yes-token:{price}".encode()).hexdigest()


class TestSubmitBatch:
    def test_empty_envelopes_returns_empty_list(self, tmp_path):
        adapter, _ = _adapter(tmp_path, FakeBatchTwoStepClient())
        assert adapter.submit_batch([]) == []

    def test_oversized_batch_raises_value_error(self, tmp_path):
        from src.venue.batch_submit import MAX_ORDERS_PER_BATCH

        adapter, _ = _adapter(tmp_path, FakeBatchTwoStepClient())
        envelopes = _batch_envelopes(adapter, MAX_ORDERS_PER_BATCH + 1)
        with pytest.raises(ValueError, match="exceeds MAX_ORDERS_PER_BATCH"):
            adapter.submit_batch(envelopes)

    def test_out_of_band_price_rejects_entire_batch_before_sdk_contact(self, tmp_path):
        fake = FakeBatchTwoStepClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 3)
        envelopes[1] = envelopes[1].with_updates(price=Decimal("0.998"))

        results = adapter.submit_batch(envelopes)

        assert [result.status for result in results] == ["rejected"] * 3
        assert all(
            "outside absolute inclusive [0.05, 0.95]" in str(result.error_message)
            for result in results
        )
        assert not fake.calls

    def test_sdk_boundary_rejects_batch_even_if_envelope_guard_is_bypassed(
        self, tmp_path, monkeypatch
    ):
        from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

        fake = FakeBatchTwoStepClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 2)
        envelopes[1] = envelopes[1].with_updates(price=Decimal("0.998"))
        monkeypatch.setattr(VenueSubmissionEnvelope, "assert_live_submit_bound", lambda self: None)

        results = adapter.submit_batch(envelopes)

        assert [result.status for result in results] == ["rejected", "rejected"]
        assert all(
            "outside absolute inclusive [0.05, 0.95]" in str(result.error_message)
            for result in results
        )
        assert not fake.calls

    def test_sdk_boundary_rejects_subminimum_batch_even_if_envelope_guard_is_bypassed(
        self, tmp_path, monkeypatch
    ):
        from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

        fake = FakeBatchTwoStepClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 2)
        envelopes[1] = envelopes[1].with_updates(
            size=Decimal("0.5"),
            min_order_size=Decimal("1"),
        )
        monkeypatch.setattr(
            VenueSubmissionEnvelope,
            "assert_live_fill_price_bound",
            lambda self: None,
        )

        results = adapter.submit_batch(envelopes)

        assert [result.status for result in results] == ["rejected", "rejected"]
        assert all(
            "LIVE_ORDER_SIZE_INVALID:FINAL_SDK_BOUNDARY"
            in str(result.error_message)
            for result in results
        )
        assert not any(call[0] == "post_orders" for call in fake.calls)

    def test_sdk_boundary_rejects_off_tick_batch_even_if_envelope_guard_is_bypassed(
        self, tmp_path, monkeypatch
    ):
        from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

        fake = FakeBatchTwoStepClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 2)
        envelopes[1] = envelopes[1].with_updates(
            price=Decimal("0.505"),
            tick_size=Decimal("0.01"),
        )
        monkeypatch.setattr(
            VenueSubmissionEnvelope,
            "assert_live_fill_price_bound",
            lambda self: None,
        )

        results = adapter.submit_batch(envelopes)

        assert [result.status for result in results] == ["rejected", "rejected"]
        assert all(
            "LIVE_ORDER_TICK_INVALID:FINAL_SDK_BOUNDARY"
            in str(result.error_message)
            for result in results
        )
        assert not any(call[0] == "post_orders" for call in fake.calls)

    def test_index_fallback_maps_results_in_order(self, tmp_path):
        fake = FakeBatchTwoStepClient(
            post_orders_response=[
                {"orderID": "ord-0", "status": "LIVE"},
                {"orderID": "ord-1", "status": "LIVE"},
                {"orderID": "ord-2", "status": "LIVE"},
            ]
        )
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 3)

        results = adapter.submit_batch(envelopes)

        assert [r.status for r in results] == ["accepted", "accepted", "accepted"]
        assert [r.envelope.order_id for r in results] == ["ord-0", "ord-1", "ord-2"]
        post_orders_call = next(c for c in fake.calls if c[0] == "post_orders")
        assert len(post_orders_call[1]) == 3

    def test_echo_id_mapping_survives_out_of_order_response(self, tmp_path):
        prices = [str(0.50 + i * 0.01) for i in range(3)]
        hashes = [_signed_hash_for(p) for p in prices]
        # Response deliberately reversed and echoes signed_order_hash --
        # index mapping would silently mismatch here; echo-id must not.
        response = [
            {"orderHash": hashes[2], "orderID": "ord-for-2", "status": "LIVE"},
            {"orderHash": hashes[1], "orderID": "ord-for-1", "status": "LIVE"},
            {"orderHash": hashes[0], "orderID": "ord-for-0", "status": "LIVE"},
        ]
        fake = FakeBatchTwoStepClient(post_orders_response=response)
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 3)

        results = adapter.submit_batch(envelopes)

        assert [r.envelope.order_id for r in results] == ["ord-for-0", "ord-for-1", "ord-for-2"]

    def test_non_array_response_marks_all_unmapped(self, tmp_path):
        fake = FakeBatchTwoStepClient(post_orders_response={"error": "malformed"})
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 2)

        results = adapter.submit_batch(envelopes)

        assert [r.status for r in results] == ["unmapped", "unmapped"]
        assert all(r.error_code == "BATCH_RESPONSE_UNMAPPED" for r in results)

    def test_length_mismatch_marks_all_unmapped(self, tmp_path):
        fake = FakeBatchTwoStepClient(post_orders_response=[{"orderID": "only-one"}])
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 3)

        results = adapter.submit_batch(envelopes)

        assert len(results) == 3
        assert all(r.status == "unmapped" for r in results)

    def test_non_post_only_rejects_whole_batch_before_signing(self, tmp_path):
        fake = FakeBatchTwoStepClient()
        adapter, _ = _adapter(tmp_path, fake)
        mixed = [
            adapter.create_submission_envelope(_priced_intent(0.50), FakeSnapshot(), order_type="GTC", post_only=False),
            adapter.create_submission_envelope(_priced_intent(0.51), FakeSnapshot(), order_type="GTC", post_only=True),
        ]

        results = adapter.submit_batch(mixed)

        assert all(r.status == "rejected" and r.error_code == "LIVE_FILL_PRICE_UNBOUNDED" for r in results)
        assert not any(c[0] == "create_order" for c in fake.calls)

    def test_signing_failure_for_any_envelope_rejects_whole_batch_before_network(self, tmp_path):
        fake = FakeSigningFailsOnSecondClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 3)

        results = adapter.submit_batch(envelopes)

        assert all(r.status == "rejected" and r.error_code == "V2_PRE_SUBMIT_EXCEPTION" for r in results)
        assert not any(c[0] == "post_orders" for c in fake.calls)

    def test_fok_batch_crosses_final_sdk_boundary(self, tmp_path):
        fake = FakeBatchTwoStepClient(
            post_orders_response=[{"orderID": "ord-0", "status": "LIVE"}]
        )
        adapter, _ = _adapter(tmp_path, fake)
        envelope = adapter.create_submission_envelope(
            _priced_intent(0.50), FakeSnapshot(), order_type="FOK", post_only=False
        )

        results = adapter.submit_batch([envelope])

        assert results[0].status == "accepted"
        assert any(c[0] == "create_order" for c in fake.calls)
        assert any(c[0] == "post_orders" for c in fake.calls)

    def test_fok_batch_does_not_reintroduce_legacy_book_gate(self, tmp_path):
        class ThinBatchClient(FakeBatchTwoStepClient):
            def get_order_book(self, token_id):
                self.calls.append(("get_order_book", token_id))
                return {
                    "asset_id": token_id,
                    "bids": [{"price": "0.49", "size": "100"}],
                    "asks": [{"price": "0.50", "size": "1"}],
                }

        fake = ThinBatchClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelope = adapter.create_submission_envelope(
            _priced_intent(0.50), FakeSnapshot(), order_type="FOK", post_only=False
        )

        results = adapter.submit_batch([envelope])

        assert results[0].status == "unmapped"
        assert results[0].error_code == "BATCH_RESPONSE_UNMAPPED"
        assert any(c[0] == "post_orders" for c in fake.calls)

    def test_post_orders_exception_propagates_as_ambiguous_side_effect(self, tmp_path):
        fake = FakePostOrdersExceptionClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 2)

        with pytest.raises(TimeoutError, match="post_orders timed out"):
            adapter.submit_batch(envelopes)

        assert any(c[0] == "post_orders" for c in fake.calls)

    def test_order_manager_not_ready_425_rejects_entire_batch(self, tmp_path):
        fake = FakeBatchOrderManagerNotReady425Client()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 2)

        results = adapter.submit_batch(envelopes)

        assert [result.status for result in results] == ["rejected", "rejected"]
        assert [result.error_code for result in results] == [
            "venue_order_manager_not_ready_425",
            "venue_order_manager_not_ready_425",
        ]
        assert all(result.envelope.order_id is None for result in results)
        assert any(call[0] == "post_orders" for call in fake.calls)


class TestCancelBatch:
    def test_empty_order_ids_returns_empty_list(self, tmp_path):
        adapter, _ = _adapter(tmp_path, FakeBatchTwoStepClient())
        assert adapter.cancel_batch([]) == []

    def test_oversized_batch_raises_value_error(self, tmp_path):
        from src.venue.batch_submit import MAX_ORDERS_PER_BATCH

        adapter, _ = _adapter(tmp_path, FakeBatchTwoStepClient())
        with pytest.raises(ValueError, match="exceeds MAX_ORDERS_PER_BATCH"):
            adapter.cancel_batch([f"ord-{i}" for i in range(MAX_ORDERS_PER_BATCH + 1)])

    def test_index_fallback_maps_canceled_results_in_order(self, tmp_path):
        fake = FakeBatchTwoStepClient(
            cancel_orders_response=[
                {"canceled": True, "orderID": "ord-0"},
                {"canceled": True, "orderID": "ord-1"},
            ]
        )
        adapter, _ = _adapter(tmp_path, fake)

        results = adapter.cancel_batch(["ord-0", "ord-1"])

        assert [r.status for r in results] == ["CANCELED", "CANCELED"]
        cancel_call = next(c for c in fake.calls if c[0] == "cancel_orders")
        assert cancel_call[1] == ["ord-0", "ord-1"]

    def test_echo_id_mapping_survives_out_of_order_response(self, tmp_path):
        response = [
            {"orderID": "ord-1", "canceled": True},
            {"orderID": "ord-0", "not_canceled": "already open elsewhere"},
        ]
        fake = FakeBatchTwoStepClient(cancel_orders_response=response)
        adapter, _ = _adapter(tmp_path, fake)

        results = adapter.cancel_batch(["ord-0", "ord-1"])

        assert results[0].order_id == "ord-0"
        assert results[0].status == "NOT_CANCELED"
        assert results[1].order_id == "ord-1"
        assert results[1].status == "CANCELED"

    def test_non_array_response_marks_all_unmapped(self, tmp_path):
        fake = FakeBatchTwoStepClient(cancel_orders_response={"error": "malformed"})
        adapter, _ = _adapter(tmp_path, fake)

        results = adapter.cancel_batch(["ord-0", "ord-1"])

        assert all(r.status == "UNKNOWN" for r in results)

    def test_unsupported_client_returns_unmapped_unknown(self, tmp_path):
        class NoCancelOrdersClient(FakeBatchTwoStepClient):
            cancel_orders = None  # type: ignore[assignment]

        adapter, _ = _adapter(tmp_path, NoCancelOrdersClient())

        results = adapter.cancel_batch(["ord-0"])

        assert results[0].status == "UNKNOWN"
        assert results[0].error_code == "CANCEL_BATCH_UNSUPPORTED"

    def test_cancel_orders_exception_propagates_as_ambiguous_side_effect(self, tmp_path):
        fake = FakeCancelOrdersExceptionClient()
        adapter, _ = _adapter(tmp_path, fake)

        with pytest.raises(TimeoutError, match="cancel_orders timed out"):
            adapter.cancel_batch(["ord-0"])

    def test_live_envelope_shape_maps_canceled_order(self, tmp_path):
        """2026-07-05 live incident pin: DELETE /orders returns ONE envelope
        dict for the whole batch. The first live order (0x9df6...) WAS
        canceled by the venue but the per-item-array mapper failed to
        attribute it -> BATCH_RESPONSE_UNMAPPED -> REVIEW_REQUIRED."""
        oid = "0x9df6b4f0b7cd1246f91fec5ba34943c74837284fe5c7c02e53bdc75a4f32939b"
        fake = FakeBatchTwoStepClient(cancel_orders_response={"canceled": [oid]})
        adapter, _ = _adapter(tmp_path, fake)

        results = adapter.cancel_batch([oid])

        assert results[0].status == "CANCELED"
        assert results[0].order_id == oid
        assert results[0].error_code is None

    def test_live_envelope_not_canceled_maps_with_reason(self, tmp_path):
        fake = FakeBatchTwoStepClient(
            cancel_orders_response={
                "canceled": [],
                "not_canceled": {"ord-0": "order not found"},
            }
        )
        adapter, _ = _adapter(tmp_path, fake)

        results = adapter.cancel_batch(["ord-0"])

        assert results[0].status == "NOT_CANCELED"
        assert "order not found" in (results[0].error_message or "")

    def test_live_envelope_missing_id_stays_unknown_fail_closed(self, tmp_path):
        fake = FakeBatchTwoStepClient(cancel_orders_response={"canceled": ["other-ord"]})
        adapter, _ = _adapter(tmp_path, fake)

        results = adapter.cancel_batch(["ord-0"])

        assert results[0].status == "UNKNOWN"
        assert results[0].error_code == "BATCH_RESPONSE_UNMAPPED"


def test_deadline_order_read_cancels_transport_without_sdk_get_order(
    tmp_path,
    monkeypatch,
):
    import httpx

    from src.venue.polymarket_v2_adapter import IncompleteOrderTruthError

    class FakeAuthenticatedClient:
        host = "https://clob-v2.polymarket.com"
        signer = object()
        creds = object()

        def get_order(self, _order_id):
            raise AssertionError("deadline path must not call blocking SDK get_order")

    class SlowHttp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            await asyncio.sleep(1.0)
            raise AssertionError("absolute deadline must cancel the transport")

    adapter, _ = _adapter(tmp_path, FakeAuthenticatedClient())
    adapter._client = FakeAuthenticatedClient()

    async def fake_headers(*_args, **_kwargs):
        return {"x-test": "signed"}, 1_700_000_000

    monkeypatch.setattr(adapter, "_account_truth_headers_async", fake_headers)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: SlowHttp())

    started = time.monotonic()
    with pytest.raises(IncompleteOrderTruthError, match="deadline elapsed"):
        adapter.get_order(
            "ord-never-returns",
            deadline_monotonic=started + 0.02,
        )
    assert time.monotonic() - started < 0.20


def test_deadline_order_read_never_invokes_lazy_client_factory(tmp_path):
    from src.venue.polymarket_v2_adapter import IncompleteOrderTruthError

    factory_calls = []

    def blocking_factory(**_kwargs):
        factory_calls.append("called")
        time.sleep(0.15)
        return object()

    adapter, _ = _adapter(tmp_path, object())
    adapter._client_factory = blocking_factory
    started = time.monotonic()
    with pytest.raises(IncompleteOrderTruthError, match="client was not prepared"):
        adapter.get_order(
            "ord-unprepared",
            deadline_monotonic=started + 0.01,
        )

    assert factory_calls == []
    assert time.monotonic() - started < 0.10


def test_polymarket_client_prepares_same_adapter_used_by_deadline_order_read():
    from src.data.polymarket_client import PolymarketClient
    from src.venue.polymarket_v2_adapter import OrderState

    class FakeAdapter:
        def __init__(self):
            self.prepared = False
            self.deadlines = []

        def prepare_order_truth_reader(self):
            self.prepared = True

        def get_order(self, order_id, *, deadline_monotonic):
            assert self.prepared
            self.deadlines.append(deadline_monotonic)
            return OrderState(
                order_id=order_id,
                status="LIVE",
                raw={"orderID": order_id, "status": "LIVE"},
            )

    client = PolymarketClient()
    adapter = FakeAdapter()
    client._v2_adapter = adapter
    client.prepare_order_truth_reader()
    deadline = time.monotonic() + 1.0

    assert client.get_order("ord-prepared", deadline_monotonic=deadline) == {
        "orderID": "ord-prepared",
        "status": "LIVE",
    }
    assert adapter.deadlines == [deadline]
