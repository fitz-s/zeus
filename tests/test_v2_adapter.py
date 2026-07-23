# Lifecycle: created=2026-04-27; last_reviewed=2026-07-23; last_reused=2026-07-23
# Purpose: R3 Z2 Polymarket V2 adapter and submission envelope antibodies.
# Reuse: Run when V2 SDK adapter, envelope provenance, or Q1 preflight behavior changes.
# Created: 2026-04-27
# Last reused/audited: 2026-07-18
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
#                  + 2026-05-17 public CLOB HTTP reuse for live opening_hunt backpressure.
"""R3 Z2 Polymarket V2 adapter antibodies."""

from __future__ import annotations

import hashlib
import importlib
import sqlite3
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

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


def test_target_ctf_collateral_payload_does_not_enumerate_all_positions(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class FakeClientWithTargetCtf(FakeBalanceAllowanceClient):
        def get_positions(self):
            raise AssertionError("target exit CTF proof must not enumerate every position")

        def get_balance_allowance(self, params):
            self.calls.append(("get_balance_allowance", params))
            asset_type = str(getattr(params, "asset_type", "")).upper()
            if "CONDITIONAL" in asset_type:
                assert getattr(params, "token_id") == "exit-token"
                return {"balance": "21427700"}
            return {"balance": "100000000", "allowance": "50000000"}

    fake = FakeClientWithTargetCtf()
    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        signature_type=3,
        q1_egress_evidence_path=tmp_path / "unused.txt",
        client_factory=lambda **kwargs: fake,
    )

    payload = adapter.get_ctf_collateral_payload(token_ids=["exit-token"])

    assert payload["ctf_token_scope"] == "targeted"
    assert payload["ctf_token_balances_units"] == {"exit-token": 21427700}
    assert payload["ctf_token_allowances_units"] == {"exit-token": 21427700}
    call_asset_types = [
        str(getattr(call[1], "asset_type", "")).upper()
        for call in fake.calls
        if call[0] == "get_balance_allowance"
    ]
    assert any("COLLATERAL" in asset for asset in call_asset_types)
    assert any("CONDITIONAL" in asset for asset in call_asset_types)


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


def test_polymarket_client_defaults_to_current_keychain_funder_signature_type(monkeypatch):
    from src.data import polymarket_client as pm

    monkeypatch.setattr(
        pm,
        "_resolve_credentials",
        lambda: {"private_key": "0xabc", "funder_address": "0xfunder"},
    )
    monkeypatch.delenv("POLYMARKET_CLOB_V2_SIGNATURE_TYPE", raising=False)
    monkeypatch.setattr(pm, "_real_order_submit_enabled", lambda: False)

    adapter = pm.PolymarketClient()._ensure_v2_adapter()

    assert adapter.signature_type == 2
    assert adapter.polygon_rpc_url


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


def test_polymarket_client_requires_explicit_signature_type_when_submit_armed(monkeypatch):
    from src.data import polymarket_client as pm

    monkeypatch.setattr(
        pm,
        "_resolve_credentials",
        lambda: {"private_key": "0xabc", "funder_address": "0xfunder"},
    )
    monkeypatch.delenv("POLYMARKET_CLOB_V2_SIGNATURE_TYPE", raising=False)
    monkeypatch.setattr(pm, "_real_order_submit_enabled", lambda: True)

    with pytest.raises(RuntimeError, match="POLYMARKET_CLOB_V2_SIGNATURE_TYPE is required"):
        pm.PolymarketClient()._ensure_v2_adapter()


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
        text = path.read_text()
        if "py_clob_client_v2" in text and path.as_posix() != "src/venue/polymarket_v2_adapter.py":
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
        _intent(), FakeSnapshot(), order_type="FAK"
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


def test_fok_killed_400_is_definitive_rejection(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeFokKilledClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="FOK")
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_fok_not_fully_filled_400"
    assert result.envelope.order_id == "0xexpected-order-id"
    assert result.envelope.signed_order == fake.signed_order


def test_fak_no_match_400_is_definitive_zero_fill_rejection(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeFakNoMatchClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="FAK")
    monkeypatch.setattr(
        adapter_mod,
        "_deterministic_v2_order_id",
        lambda *args, **kwargs: "0xexpected-order-id",
    )

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "venue_fak_no_match_400"
    assert result.envelope.order_id == "0xexpected-order-id"
    assert result.envelope.signed_order == fake.signed_order

    from src.data.polymarket_client import _legacy_order_result_from_submit

    payload = _legacy_order_result_from_submit(result)
    assert payload["success"] is False
    assert payload["errorCode"] == "venue_fak_no_match_400"


def test_fok_rechecks_full_depth_after_signing_immediately_before_post(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    fake = FakeTwoStepClient(post_response={"orderID": "0xexpected", "status": "LIVE"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="FOK")
    monkeypatch.setattr(adapter_mod, "_deterministic_v2_order_id", lambda *a, **k: "0xexpected")

    result = _submit(adapter, envelope)

    names = [call[0] for call in fake.calls]
    assert result.status == "accepted"
    assert names.index("create_order") < names.index("get_order_book") < names.index("post_order")


def test_fok_depth_loss_after_signing_rejects_without_post(tmp_path, monkeypatch):
    import src.venue.polymarket_v2_adapter as adapter_mod

    class ThinFinalBookClient(FakeTwoStepClient):
        def get_order_book(self, token_id):
            self.calls.append(("get_order_book", token_id))
            return {
                "asset_id": token_id,
                "bids": [{"price": "0.49", "size": "100"}],
                "asks": [{"price": "0.50", "size": "19.99"}],
            }

    fake = ThinFinalBookClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="FOK")
    monkeypatch.setattr(adapter_mod, "_deterministic_v2_order_id", lambda *a, **k: "0xexpected")

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "SUBMIT_ABORTED_PRICE_MOVED"
    assert "FOK_FINAL_DEPTH_INSUFFICIENT" in (result.error_message or "")
    assert result.envelope.signed_order == fake.signed_order
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_fok_one_step_only_client_fails_closed_before_submit(tmp_path):
    fake = FakeOneStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="FOK")

    result = _submit(adapter, envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "pre-POST signed identity persistence requires two-step SDK submit" in (
        result.error_message or ""
    )
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
    assert [call[0] for call in fake.calls] == ["get_ok", "create_order", "post_order"]
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
    assert [call[0] for call in fake.calls] == ["get_ok", "create_order", "post_order"]


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
    assert calls == [
        (f"{pm.CLOB_BASE}/fee-rate", {"token_id": "token-1"}),
        (f"{pm.CLOB_BASE}/fee-rate", {"token_id": "token-1"}),
    ]


def test_polymarket_client_fee_rate_rejects_malformed_shape(monkeypatch):
    from src.data import polymarket_client as pm

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
        def cancel(self, _order_id):  # pragma: no cover - tripwire
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
        def cancel(self, order_id):
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


def test_polymarket_client_maps_typed_point_order_absence_to_none():
    from src.data.polymarket_client import PolymarketClient
    from src.venue.response_contracts import VenueOrderNotFound

    class FakeAdapter:
        def get_order(self, order_id):
            raise VenueOrderNotFound(order_id)

    client = PolymarketClient()
    client._v2_adapter = FakeAdapter()

    assert client.get_order("ord-missing") is None


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
        Path("src/execution/exit_triggers.py"),
    ]
    offenders = [path.as_posix() for path in live_paths if "py_clob_client" in path.read_text()]
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


class FakeCancelOrdersExceptionClient(FakeBatchTwoStepClient):
    def cancel_orders(self, order_ids):
        self.calls.append(("cancel_orders", order_ids))
        raise TimeoutError("cancel_orders timed out")


def _priced_intent(price: float) -> ExecutionIntent:
    from dataclasses import replace

    return replace(_intent(), limit_price=price)


def _batch_envelopes(adapter, n: int, *, post_only: bool = False):
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

    def test_mixed_post_only_rejects_whole_batch_before_signing(self, tmp_path):
        fake = FakeBatchTwoStepClient()
        adapter, _ = _adapter(tmp_path, fake)
        mixed = [
            adapter.create_submission_envelope(_priced_intent(0.50), FakeSnapshot(), order_type="GTC", post_only=False),
            adapter.create_submission_envelope(_priced_intent(0.51), FakeSnapshot(), order_type="GTC", post_only=True),
        ]

        results = adapter.submit_batch(mixed)

        assert all(r.status == "rejected" and r.error_code == "BATCH_POST_ONLY_MISMATCH" for r in results)
        assert not any(c[0] == "create_order" for c in fake.calls)

    def test_signing_failure_for_any_envelope_rejects_whole_batch_before_network(self, tmp_path):
        fake = FakeSigningFailsOnSecondClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 3)

        results = adapter.submit_batch(envelopes)

        assert all(r.status == "rejected" and r.error_code == "V2_PRE_SUBMIT_EXCEPTION" for r in results)
        assert not any(c[0] == "post_orders" for c in fake.calls)

    def test_fok_batch_checks_depth_after_all_signing_before_post(self, tmp_path):
        fake = FakeBatchTwoStepClient(
            post_orders_response=[{"orderID": "ord-0", "status": "LIVE"}]
        )
        adapter, _ = _adapter(tmp_path, fake)
        envelope = adapter.create_submission_envelope(
            _priced_intent(0.50), FakeSnapshot(), order_type="FOK"
        )

        results = adapter.submit_batch([envelope])

        names = [call[0] for call in fake.calls]
        assert results[0].status == "accepted"
        assert names.index("create_order") < names.index("get_order_book") < names.index("post_orders")

    def test_fok_batch_depth_loss_rejects_whole_batch_without_post(self, tmp_path):
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
            _priced_intent(0.50), FakeSnapshot(), order_type="FOK"
        )

        results = adapter.submit_batch([envelope])

        assert results[0].status == "rejected"
        assert results[0].error_code == "SUBMIT_ABORTED_PRICE_MOVED"
        assert "FOK_FINAL_DEPTH_INSUFFICIENT" in (results[0].error_message or "")
        assert not any(call[0] == "post_orders" for call in fake.calls)

    def test_post_orders_exception_propagates_as_ambiguous_side_effect(self, tmp_path):
        fake = FakePostOrdersExceptionClient()
        adapter, _ = _adapter(tmp_path, fake)
        envelopes = _batch_envelopes(adapter, 2)

        with pytest.raises(TimeoutError, match="post_orders timed out"):
            adapter.submit_batch(envelopes)

        assert any(c[0] == "post_orders" for c in fake.calls)


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
