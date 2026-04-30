# Lifecycle: created=2026-04-27; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: R3 Z2 Polymarket V2 adapter and submission envelope antibodies.
# Reuse: Run when V2 SDK adapter, envelope provenance, or Q1 preflight behavior changes.
# Created: 2026-04-27
# Last reused/audited: 2026-04-29
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
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
    evidence.write_text("daemon host probe ok\n")
    fake_client = fake_client or FakeOneStepClient()
    return PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=evidence,
        client_factory=lambda **kwargs: fake_client,
    ), fake_client


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

    result = adapter.submit_limit_order(token_id="yes-token", price=0.5, size=10.0, side="BUY")

    assert result.status == "rejected"
    assert result.error_code == "V2_PRE_SUBMIT_EXCEPTION"
    assert "get_neg_risk" in (result.error_message or "")
    assert result.envelope.order_id is None
    assert fake.calls == [("get_ok",)]


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

    result = adapter.submit_limit_order(token_id="yes-token", price=0.5, size=10.0, side="BUY")

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

    result = adapter.submit_limit_order(token_id="yes-token", price=0.5, size=10.0, side="BUY")

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
    fake = FakeTwoStepClient()
    adapter, _ = _adapter(tmp_path, fake)
    adapter.submit_limit_order(token_id="yes-token", price=0.5, size=3.25, side="SELL")

    create_call = next(call for call in fake.calls if call[0] == "create_order")
    order_args = create_call[1]
    assert getattr(order_args, "side") == "SELL"
    assert getattr(order_args, "size") == 3.25

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
    assert envelope.canonical_pre_sign_payload_hash != buy_envelope.canonical_pre_sign_payload_hash


def test_polymarket_client_live_submit_delegates_to_v2_adapter(tmp_path):
    from src.data.polymarket_client import PolymarketClient

    adapter, _ = _adapter(tmp_path, FakeOneStepClient(response={"orderID": "ord-v2", "status": "LIVE"}))
    submit = adapter.submit(adapter.create_submission_envelope(_intent(), FakeSnapshot(), order_type="GTC"))

    class FakeAdapter:
        def __init__(self):
            self.calls = []

        def preflight(self):
            from src.venue.polymarket_v2_adapter import PreflightResult

            return PreflightResult(ok=True)

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
            return submit

    client = PolymarketClient()
    fake_adapter = FakeAdapter()
    client._v2_adapter = fake_adapter

    with pytest.warns(DeprecationWarning, match="compatibility wrapper"):
        result = client.place_limit_order(token_id="yes-token", price=0.5, size=20.0, side="BUY")

    assert fake_adapter.calls == [
        {
            "token_id": "yes-token",
            "price": 0.5,
            "size": 20.0,
            "side": "BUY",
            "order_type": "GTC",
        }
    ]
    assert result["orderID"] == "ord-v2"
    assert result["success"] is True
    assert result["_venue_submission_envelope"]["sdk_package"] == "py-clob-client-v2"


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


def test_polymarket_client_wrapper_fails_closed_when_v2_preflight_rejects():
    from src.data.polymarket_client import PolymarketClient
    from src.venue.polymarket_v2_adapter import PreflightResult

    class FakeAdapter:
        def __init__(self):
            self.submit_called = False

        def preflight(self):
            return PreflightResult(
                ok=False,
                error_code="Q1_EGRESS_EVIDENCE_ABSENT",
                message="missing Q1 evidence",
            )

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
        "errorCode": "Q1_EGRESS_EVIDENCE_ABSENT",
        "errorMessage": "missing Q1 evidence",
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
