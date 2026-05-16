# Lifecycle: created=2026-04-27; last_reviewed=2026-05-15; last_reused=2026-05-15
# Purpose: R3 Z2 Polymarket V2 adapter and submission envelope antibodies.
# Reuse: Run when V2 SDK adapter, envelope provenance, or Q1 preflight behavior changes.
# Created: 2026-04-27
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
#                  + docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md
#                  + docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
"""R3 Z2 Polymarket V2 adapter antibodies."""

from __future__ import annotations

import hashlib
import importlib
import sys
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


class FakeCreateOrderFailureClient(FakeTwoStepClient):
    def create_order(self, order_args, options=None):
        self.calls.append(("create_order", order_args, options))
        raise RuntimeError("local signing failed")


class FakePostOrderFailureClient(FakeTwoStepClient):
    def post_order(self, order, order_type=None, post_only=False, defer_exec=False):
        self.calls.append(("post_order", order, order_type, post_only, defer_exec))
        raise TimeoutError("post timed out")


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


class FakeOpenOrdersClient:
    def __init__(self):
        self.calls = []

    def get_open_orders(self):
        self.calls.append(("get_open_orders",))
        return [{"orderID": "ord-open", "status": "LIVE"}]


class FakeLegacyGetOrdersClient:
    def __init__(self):
        self.calls = []

    def get_orders(self):
        self.calls.append(("get_orders",))
        return {"data": [{"id": "ord-legacy", "state": "LIVE"}]}


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
    assert payload["authority_tier"] == "CHAIN"
    assert payload["pusd_allowance_source"] == "missing"


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

    adapter = pm.PolymarketClient()._ensure_v2_adapter()

    assert adapter.signature_type == 2
    assert adapter.polygon_rpc_url


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

    result = adapter.submit(envelope)

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

    assert fake.calls == [("get_open_orders",)]
    assert len(orders) == 1
    assert orders[0].order_id == "ord-open"
    assert orders[0].status == "LIVE"


def test_get_open_orders_keeps_legacy_get_orders_fallback(tmp_path):
    adapter, fake = _adapter(tmp_path, FakeLegacyGetOrdersClient())

    orders = adapter.get_open_orders()

    assert fake.calls == [("get_orders",)]
    assert len(orders) == 1
    assert orders[0].order_id == "ord-legacy"
    assert orders[0].status == "LIVE"


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

    result = adapter.submit(envelope)

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "local signing failed" in (result.error_message or "")
    assert not any(call[0] == "post_order" for call in fake.calls)


def test_post_order_exception_still_bubbles_as_possible_unknown_side_effect(tmp_path):
    fake = FakePostOrderFailureClient()
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    with pytest.raises(TimeoutError, match="post timed out"):
        adapter.submit(envelope)

    assert any(call[0] == "post_order" for call in fake.calls)


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


def test_one_step_sdk_path_still_produces_envelope_with_provenance(tmp_path):
    fake = FakeOneStepClient(response={"orderID": "ord-one", "status": "matched"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    result = adapter.submit(envelope)

    assert result.status == "accepted"
    assert result.error_code is None
    assert result.envelope.order_id == "ord-one"
    assert result.envelope.signed_order is None
    assert result.envelope.signed_order_hash is None
    assert result.envelope.raw_request_hash == envelope.raw_request_hash
    assert '"orderID":"ord-one"' in (result.envelope.raw_response_json or "")
    assert any(call[0] == "create_and_post_order" for call in fake.calls)


def test_two_step_sdk_path_produces_envelope_with_signed_order_hash(tmp_path):
    signed = b"fake-signed-order"
    fake = FakeTwoStepClient(post_response={"orderID": "ord-two", "status": "live"}, signed_order=signed)
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    result = adapter.submit(envelope)

    assert [call[0] for call in fake.calls if call[0] in {"create_order", "post_order"}] == [
        "create_order",
        "post_order",
    ]
    assert result.status == "accepted"
    assert result.envelope.order_id == "ord-two"
    assert result.envelope.signed_order == signed
    assert result.envelope.signed_order_hash == hashlib.sha256(signed).hexdigest()


def test_missing_order_id_does_not_produce_submit_acked(tmp_path):
    fake = FakeOneStepClient(response={"success": True, "status": "LIVE"})
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    result = adapter.submit(envelope)

    assert result.status == "rejected"
    assert result.error_code == "MISSING_ORDER_ID"
    assert result.envelope.order_id is None
    assert result.envelope.error_code == "MISSING_ORDER_ID"


def test_success_false_response_returns_typed_rejection_with_error_code(tmp_path):
    fake = FakeOneStepClient(
        response={"success": False, "errorCode": "INSUFFICIENT_BALANCE", "errorMessage": "not enough funds"}
    )
    adapter, _ = _adapter(tmp_path, fake)
    envelope = adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC")

    result = adapter.submit(envelope)

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

    result = adapter.submit(envelope)

    create_call = next(call for call in fake.calls if call[0] == "create_order")
    options = create_call[2]
    assert envelope.neg_risk is True
    assert getattr(options, "neg_risk") is True
    assert result.envelope.neg_risk is True


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

        def submit(self, bound_envelope):
            self.submit_calls.append(bound_envelope)
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


def test_polymarket_client_fee_rate_accepts_current_base_fee_shape(monkeypatch):
    from src.data import polymarket_client as pm

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"base_fee": 30}

    monkeypatch.setattr(pm.httpx, "get", lambda *args, **kwargs: Response())

    client = pm.PolymarketClient()
    assert client.get_fee_rate("token-1") == pytest.approx(0.003)
    assert client.get_fee_rate_details("token-1")["fee_rate_bps"] == pytest.approx(30.0)


def test_polymarket_client_fee_rate_rejects_malformed_shape(monkeypatch):
    from src.data import polymarket_client as pm

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"feeSchedule": {"feesEnabled": True}}

    monkeypatch.setattr(pm.httpx, "get", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError, match="base_fee"):
        pm.PolymarketClient().get_fee_rate("token-1")


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
