# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: U1 antibodies for ExecutableMarketSnapshotV2 persistence and freshness gate.
# Reuse: Run when executable snapshots, venue_commands gating, or V2 market preflight semantics change.
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/U1.yaml
"""U1 executable market snapshot and command freshness gate tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.data.market_scanner import (
    ExecutableSnapshotCaptureError,
    _top_book_level_decimal,
    capture_executable_market_snapshot,
)
from src.data.polymarket_client import PolymarketClient
from src.contracts.executable_market_snapshot_v2 import (
    ExecutableMarketSnapshotV2,
    MarketNotTradableError,
    MarketSnapshotMismatchError,
    StaleMarketSnapshotError,
    canonicalize_fee_details,
    is_fresh,
)
from src.state.db import init_schema
from src.state.snapshot_repo import get_snapshot, insert_snapshot
from src.state.venue_command_repo import insert_command


NOW = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


class FakeClobFacts:
    def __init__(
        self,
        *,
        market_info: dict | None = None,
        orderbook: dict | None = None,
        fee_rate=30,
    ):
        self.market_info = market_info if market_info is not None else {
            "condition_id": "condition-1",
            "tokens": [{"token_id": "yes-token"}, {"token_id": "no-token"}],
            "feesEnabled": True,
        }
        self.orderbook = orderbook if orderbook is not None else {
            "asset_id": "yes-token",
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": False,
            "bids": [{"price": "0.49", "size": "100"}],
            "asks": [{"price": "0.51", "size": "100"}],
        }
        self.fee_rate = fee_rate

    def get_clob_market_info(self, condition_id: str) -> dict:
        assert condition_id == "condition-1"
        return self.market_info

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        assert token_id in {"yes-token", "no-token"}
        return self.orderbook

    def get_fee_rate(self, token_id: str) -> float:
        if isinstance(self.fee_rate, BaseException):
            raise self.fee_rate
        return self.fee_rate


def _market_for_capture(**outcome_overrides) -> dict:
    outcome = {
        "title": "Will NYC high temp be 39-40°F?",
        "token_id": "yes-token",
        "no_token_id": "no-token",
        "price": 0.49,
        "no_price": 0.51,
        "range_low": 39,
        "range_high": 40,
        "market_id": "condition-1",
        "condition_id": "condition-1",
        "question_id": "question-1",
        "gamma_market_id": "gamma-1",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "enable_orderbook": True,
        "market_end_at": (NOW + timedelta(days=1)).isoformat(),
        "token_map_raw": {"YES": "yes-token", "NO": "no-token"},
        "raw_gamma_payload_hash": HASH_A,
        "gamma_market_raw": {
            "id": "gamma-1",
            "conditionId": "condition-1",
            "questionID": "question-1",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
            "clobTokenIds": ["yes-token", "no-token"],
        },
    }
    outcome.update(outcome_overrides)
    return {
        "event_id": "event-1",
        "slug": "weather-nyc-high",
        "outcomes": [outcome],
    }


def _decision_for_capture(direction: str = "buy_yes"):
    return SimpleNamespace(
        tokens={
            "market_id": "condition-1",
            "token_id": "yes-token",
            "no_token_id": "no-token",
        },
        edge=SimpleNamespace(direction=direction),
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _snapshot(snapshot_id: str = "snap-u1", **overrides) -> ExecutableMarketSnapshotV2:
    payload = dict(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-1",
        event_id="event-1",
        event_slug="weather-nyc-high",
        condition_id="condition-1",
        question_id="question-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id="yes-token",
        outcome_label="YES",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=NOW + timedelta(hours=1),
        market_end_at=NOW + timedelta(days=1),
        market_close_at=NOW + timedelta(days=1, hours=1),
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        fee_details={"bps": 0, "source": "test"},
        token_map_raw={"YES": "yes-token", "NO": "no-token"},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.49"),
        orderbook_top_ask=Decimal("0.51"),
        orderbook_depth_jsonb='{"asks":[["0.51","100"]],"bids":[["0.49","100"]]}',
        raw_gamma_payload_hash=HASH_A,
        raw_clob_market_info_hash=HASH_B,
        raw_orderbook_hash=HASH_C,
        authority_tier="CLOB",
        captured_at=NOW,
        freshness_deadline=NOW + timedelta(seconds=30),
    )
    payload.update(overrides)
    return ExecutableMarketSnapshotV2(**payload)


def _ensure_envelope(
    conn,
    *,
    token_id: str = "yes-token",
    envelope_id: str | None = None,
    price: str = "0.50",
    size: str = "10",
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    no_token_id = "no-token" if token_id == "yes-token" else f"{token_id}-no"
    envelope_id = envelope_id or f"env-{token_id}-{price}-{size}"
    if conn.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-1",
            question_id="question-1",
            yes_token_id=token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side="BUY",
            price=Decimal(str(price)),
            size=Decimal(str(size)),
            order_type="GTC",
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={},
            canonical_pre_sign_payload_hash=HASH_A,
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash=HASH_B,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def _insert_command(
    conn,
    *,
    snapshot_id: str = "snap-u1",
    token_id: str = "yes-token",
    price: float = 0.50,
    size: float = 10.0,
    expected_min_tick_size=Decimal("0.01"),
    expected_min_order_size=Decimal("0.01"),
    expected_neg_risk: bool | None = False,
    checked_at: datetime = NOW,
) -> None:
    insert_command(
        conn,
        command_id=f"cmd-{snapshot_id}-{token_id}-{price}-{size}",
        envelope_id=_ensure_envelope(conn, token_id=token_id, price=str(price), size=str(size)),
        snapshot_id=snapshot_id,
        position_id="pos-u1",
        decision_id="dec-u1",
        idempotency_key=(snapshot_id.replace("-", "") + "0" * 32)[:32],
        intent_kind="ENTRY",
        market_id="market-u1",
        token_id=token_id,
        side="BUY",
        size=size,
        price=price,
        created_at=checked_at.isoformat(),
        snapshot_checked_at=checked_at,
        expected_min_tick_size=expected_min_tick_size,
        expected_min_order_size=expected_min_order_size,
        expected_neg_risk=expected_neg_risk,
    )


def test_insert_snapshot_persists_all_fields(conn):
    snap = _snapshot(sports_start_at=NOW + timedelta(minutes=30))
    insert_snapshot(conn, snap)

    loaded = get_snapshot(conn, "snap-u1")

    assert loaded == snap
    assert loaded.sports_start_at == NOW + timedelta(minutes=30)
    assert loaded.fee_details == {"bps": 0, "source": "test"}
    assert loaded.token_map_raw == {"YES": "yes-token", "NO": "no-token"}


def test_capture_executable_snapshot_persists_verified_gamma_and_clob_facts(conn):
    fields = capture_executable_market_snapshot(
        conn,
        market=_market_for_capture(),
        decision=_decision_for_capture(),
        clob=FakeClobFacts(),
        captured_at=NOW,
        scan_authority="VERIFIED",
    )

    loaded = get_snapshot(conn, fields["executable_snapshot_id"])

    assert loaded is not None
    assert loaded.condition_id == "condition-1"
    assert loaded.question_id == "question-1"
    assert loaded.selected_outcome_token_id == "yes-token"
    assert loaded.outcome_label == "YES"
    assert loaded.min_tick_size == Decimal("0.01")
    assert loaded.min_order_size == Decimal("5")
    assert loaded.neg_risk is False
    assert loaded.fee_details == {
        "source": "clob_fee_rate",
        "token_id": "yes-token",
        "fee_rate_fraction": 0.003,
        "fee_rate_bps": 30.0,
        "fee_rate_source_field": "fee_rate_bps",
        "fee_rate_raw_unit": "bps",
        "fee_rate_unit_inferred": "legacy_get_fee_rate_gt_1_bps",
    }
    assert loaded.authority_tier == "CLOB"
    assert fields["executable_snapshot_min_tick_size"] == "0.01"
    assert fields["executable_snapshot_min_order_size"] == "5"
    assert fields["executable_snapshot_neg_risk"] is False


def test_fee_details_canonicalize_base_fee_bps_to_fraction():
    details = canonicalize_fee_details(
        {"base_fee": "30", "source": "clob_fee_rate"},
        token_id="token-1",
    )

    assert details["fee_rate_fraction"] == pytest.approx(0.003)
    assert details["fee_rate_bps"] == pytest.approx(30.0)
    assert details["fee_rate_source_field"] == "base_fee"
    assert details["fee_rate_raw_unit"] == "bps"
    assert details["token_id"] == "token-1"


def test_fee_details_canonicalize_fraction_fee_rate_to_bps():
    details = canonicalize_fee_details({"feeRate": "0.072"})

    assert details["fee_rate_fraction"] == pytest.approx(0.072)
    assert details["fee_rate_bps"] == pytest.approx(720.0)
    assert details["fee_rate_source_field"] == "feeRate"
    assert details["fee_rate_raw_unit"] == "fraction"


def test_fee_details_reject_inconsistent_fraction_and_bps():
    with pytest.raises(MarketSnapshotMismatchError, match="inconsistent"):
        canonicalize_fee_details({"feeRate": "0.072", "base_fee": "30"})


def test_fee_details_reject_conflicting_expected_token_or_source():
    with pytest.raises(MarketSnapshotMismatchError, match="token_id"):
        canonicalize_fee_details(
            {"base_fee": 30, "token_id": "wrong-token"},
            token_id="expected-token",
        )

    with pytest.raises(MarketSnapshotMismatchError, match="source"):
        canonicalize_fee_details(
            {"base_fee": 30, "source": "stale_source"},
            source="clob_fee_rate",
        )


def test_capture_executable_snapshot_selects_no_orderbook_for_buy_no(conn):
    clob = FakeClobFacts(orderbook={
        "asset_id": "no-token",
        "tick_size": "0.01",
        "min_order_size": "5",
        "neg_risk": False,
        "bids": [{"price": "0.48", "size": "100"}],
        "asks": [{"price": "0.52", "size": "100"}],
    })

    fields = capture_executable_market_snapshot(
        conn,
        market=_market_for_capture(),
        decision=_decision_for_capture(direction="buy_no"),
        clob=clob,
        captured_at=NOW,
        scan_authority="VERIFIED",
    )
    loaded = get_snapshot(conn, fields["executable_snapshot_id"])

    assert loaded.selected_outcome_token_id == "no-token"
    assert loaded.outcome_label == "NO"
    assert loaded.orderbook_top_bid == Decimal("0.48")
    assert loaded.orderbook_top_ask == Decimal("0.52")
    assert loaded.raw_orderbook_hash


def test_capture_executable_snapshot_normalizes_unsorted_orderbook(conn):
    clob = FakeClobFacts(orderbook={
        "asset_id": "yes-token",
        "tick_size": "0.01",
        "min_order_size": "5",
        "neg_risk": False,
        "bids": [
            {"price": "0.01", "size": "100"},
            {"price": "0.47", "size": "25"},
            {"price": "0.47", "size": "75"},
        ],
        "asks": [
            {"price": "0.99", "size": "50"},
            {"price": "0.53", "size": "10"},
            {"price": "0.53", "size": "15"},
        ],
    })

    fields = capture_executable_market_snapshot(
        conn,
        market=_market_for_capture(),
        decision=_decision_for_capture(direction="buy_yes"),
        clob=clob,
        captured_at=NOW,
        scan_authority="VERIFIED",
    )
    loaded = get_snapshot(conn, fields["executable_snapshot_id"])

    assert loaded.orderbook_top_bid == Decimal("0.47")
    assert loaded.orderbook_top_ask == Decimal("0.53")
    assert _top_book_level_decimal(clob.orderbook, "bids") == (Decimal("0.47"), Decimal("100"))
    assert _top_book_level_decimal(clob.orderbook, "asks") == (Decimal("0.53"), Decimal("25"))


def test_polymarket_client_best_bid_ask_normalizes_unsorted_orderbook(monkeypatch):
    client = object.__new__(PolymarketClient)

    def fake_orderbook(token_id):
        assert token_id == "yes-token"
        return {
            "bids": [
                {"price": 0.01, "size": 100.0},
                {"price": 0.47, "size": 25.0},
                {"price": 0.47, "size": 75.0},
            ],
            "asks": [
                {"price": 0.99, "size": 50.0},
                {"price": 0.53, "size": 10.0},
                {"price": 0.53, "size": 15.0},
            ],
        }

    monkeypatch.setattr(client, "get_orderbook", fake_orderbook)

    assert client.get_best_bid_ask("yes-token") == (0.47, 0.53, 100.0, 25.0)


@pytest.mark.parametrize(
    "market_info",
    [
        {
            "condition_id": "condition-1",
            "t": [{"t": "yes-token", "o": "Yes"}, {"t": "no-token", "o": "No"}],
        },
        {
            "condition_id": "condition-1",
            "primary_token_id": "yes-token",
            "secondary_token_id": "no-token",
        },
    ],
)
def test_capture_executable_snapshot_accepts_documented_clob_token_shapes(conn, market_info):
    fields = capture_executable_market_snapshot(
        conn,
        market=_market_for_capture(),
        decision=_decision_for_capture(),
        clob=FakeClobFacts(market_info=market_info),
        captured_at=NOW,
        scan_authority="VERIFIED",
    )

    loaded = get_snapshot(conn, fields["executable_snapshot_id"])

    assert loaded is not None
    assert loaded.yes_token_id == "yes-token"
    assert loaded.no_token_id == "no-token"


def test_capture_executable_snapshot_requires_clob_token_proof(conn):
    with pytest.raises(ExecutableSnapshotCaptureError, match="token map"):
        capture_executable_market_snapshot(
            conn,
            market=_market_for_capture(),
            decision=_decision_for_capture(),
            clob=FakeClobFacts(market_info={"condition_id": "condition-1"}),
            captured_at=NOW,
            scan_authority="VERIFIED",
        )


def test_capture_executable_snapshot_uses_market_fact_methods_only(conn):
    class FactOnlyClob(FakeClobFacts):
        def __init__(self):
            super().__init__()
            self.calls = []

        def get_clob_market_info(self, condition_id: str) -> dict:
            self.calls.append("get_clob_market_info")
            return super().get_clob_market_info(condition_id)

        def get_orderbook_snapshot(self, token_id: str) -> dict:
            self.calls.append("get_orderbook_snapshot")
            return super().get_orderbook_snapshot(token_id)

        def get_fee_rate(self, token_id: str) -> float:
            self.calls.append("get_fee_rate")
            return super().get_fee_rate(token_id)

        def cancel(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("snapshot capture must not touch cancel")

        def redeem(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("snapshot capture must not touch redeem")

        def place_limit_order(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("snapshot capture must not touch live submit")

        def v2_preflight(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("snapshot capture must not touch live cutover/preflight")

    clob = FactOnlyClob()

    capture_executable_market_snapshot(
        conn,
        market=_market_for_capture(),
        decision=_decision_for_capture(),
        clob=clob,
        captured_at=NOW,
        scan_authority="VERIFIED",
    )

    assert clob.calls == ["get_clob_market_info", "get_orderbook_snapshot", "get_fee_rate"]


@pytest.mark.parametrize("authority", ["STALE", "EMPTY_FALLBACK", "NEVER_FETCHED"])
def test_capture_executable_snapshot_requires_verified_gamma_authority(conn, authority):
    with pytest.raises(ExecutableSnapshotCaptureError, match="VERIFIED Gamma authority"):
        capture_executable_market_snapshot(
            conn,
            market=_market_for_capture(),
            decision=_decision_for_capture(),
            clob=FakeClobFacts(),
            captured_at=NOW,
            scan_authority=authority,
        )


@pytest.mark.parametrize(
    ("clob", "match"),
    [
        (
            FakeClobFacts(orderbook={
                "asset_id": "yes-token",
                "min_order_size": "5",
                "neg_risk": False,
                "bids": [{"price": "0.49", "size": "100"}],
                "asks": [{"price": "0.51", "size": "100"}],
            }),
            "tick_size",
        ),
        (
            FakeClobFacts(orderbook={
                "asset_id": "yes-token",
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": False,
                "bids": [],
                "asks": [{"price": "0.51", "size": "100"}],
            }),
            "missing bids",
        ),
        (
            FakeClobFacts(orderbook={
                "asset_id": "yes-token",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [{"price": "0.49", "size": "100"}],
                "asks": [{"price": "0.51", "size": "100"}],
            }),
            "neg_risk",
        ),
        (
            FakeClobFacts(fee_rate=RuntimeError("fee endpoint down")),
            "fee endpoint down",
        ),
    ],
)
def test_capture_executable_snapshot_fails_closed_on_missing_clob_facts(conn, clob, match):
    with pytest.raises(ExecutableSnapshotCaptureError, match=match):
        capture_executable_market_snapshot(
            conn,
            market=_market_for_capture(),
            decision=_decision_for_capture(),
            clob=clob,
            captured_at=NOW,
            scan_authority="VERIFIED",
        )


@pytest.mark.parametrize(
    ("market", "clob", "match"),
    [
        (_market_for_capture(closed=True), FakeClobFacts(), "not currently tradable"),
        (
            _market_for_capture(),
            FakeClobFacts(market_info={
                "condition_id": "wrong-condition",
                "tokens": [{"token_id": "yes-token"}, {"token_id": "no-token"}],
            }),
            "condition_id",
        ),
        (
            _market_for_capture(),
            FakeClobFacts(market_info={
                "condition_id": "condition-1",
                "tokens": [{"token_id": "yes-token"}, {"token_id": "wrong-no"}],
            }),
            "token map",
        ),
        (
            _market_for_capture(),
            FakeClobFacts(orderbook={
                "asset_id": "wrong-token",
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": False,
                "bids": [{"price": "0.49", "size": "100"}],
                "asks": [{"price": "0.51", "size": "100"}],
            }),
            "orderbook token_id",
        ),
    ],
)
def test_capture_executable_snapshot_fails_closed_on_gamma_clob_inconsistency(conn, market, clob, match):
    with pytest.raises(ExecutableSnapshotCaptureError, match=match):
        capture_executable_market_snapshot(
            conn,
            market=market,
            decision=_decision_for_capture(),
            clob=clob,
            captured_at=NOW,
            scan_authority="VERIFIED",
        )


def test_update_snapshot_raises_via_trigger(conn):
    insert_snapshot(conn, _snapshot())

    with pytest.raises(sqlite3.IntegrityError, match="APPEND-ONLY"):
        conn.execute(
            "UPDATE executable_market_snapshots SET active = 0 WHERE snapshot_id = ?",
            ("snap-u1",),
        )


def test_delete_snapshot_raises_via_trigger(conn):
    insert_snapshot(conn, _snapshot())

    with pytest.raises(sqlite3.IntegrityError, match="APPEND-ONLY"):
        conn.execute(
            "DELETE FROM executable_market_snapshots WHERE snapshot_id = ?",
            ("snap-u1",),
        )


def test_freshness_check_fails_after_window(conn):
    snap = _snapshot(freshness_deadline=NOW + timedelta(seconds=1))

    assert is_fresh(snap, NOW + timedelta(seconds=1))
    assert not is_fresh(snap, NOW + timedelta(seconds=2))


def test_command_insertion_requires_fresh_snapshot(conn):
    with pytest.raises(StaleMarketSnapshotError, match="snapshot_id"):
        insert_command(
            conn,
            command_id="cmd-missing",
            snapshot_id=None,
            position_id="pos-u1",
            decision_id="dec-u1",
            idempotency_key="f" * 32,
            intent_kind="ENTRY",
            market_id="market-u1",
            token_id="yes-token",
            side="BUY",
            size=10.0,
            price=0.5,
            created_at=NOW.isoformat(),
        )

    insert_snapshot(conn, _snapshot())
    _insert_command(conn)
    row = conn.execute(
        "SELECT snapshot_id FROM venue_commands WHERE command_id LIKE 'cmd-snap-u1%'"
    ).fetchone()
    assert row["snapshot_id"] == "snap-u1"


def test_stale_snapshot_blocks_submit(conn):
    insert_snapshot(
        conn,
        _snapshot(
            snapshot_id="snap-stale",
            captured_at=NOW - timedelta(minutes=5),
            freshness_deadline=NOW - timedelta(minutes=4),
        ),
    )

    with pytest.raises(StaleMarketSnapshotError):
        _insert_command(conn, snapshot_id="snap-stale")


def test_enable_orderbook_false_blocks_submit(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-disabled", enable_orderbook=False))

    with pytest.raises(MarketNotTradableError, match="enable_orderbook=false"):
        _insert_command(conn, snapshot_id="snap-disabled")


def test_active_false_blocks_submit(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-inactive", active=False))

    with pytest.raises(MarketNotTradableError, match="active=false"):
        _insert_command(conn, snapshot_id="snap-inactive")


def test_closed_true_blocks_submit(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-closed", closed=True))

    with pytest.raises(MarketNotTradableError, match="closed=true"):
        _insert_command(conn, snapshot_id="snap-closed")


def test_tick_mismatch_blocks_before_signing(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-tick"))

    with pytest.raises(MarketSnapshotMismatchError, match="min_tick_size"):
        _insert_command(
            conn,
            snapshot_id="snap-tick",
            expected_min_tick_size=Decimal("0.001"),
        )

    with pytest.raises(MarketSnapshotMismatchError, match="not aligned"):
        _insert_command(conn, snapshot_id="snap-tick", price=0.333)


def test_min_order_size_mismatch_blocks_before_signing(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-min-size", min_order_size=Decimal("5")))

    with pytest.raises(MarketSnapshotMismatchError, match="min_order_size"):
        _insert_command(
            conn,
            snapshot_id="snap-min-size",
            expected_min_order_size=Decimal("0.01"),
        )

    with pytest.raises(MarketSnapshotMismatchError, match="below"):
        _insert_command(
            conn,
            snapshot_id="snap-min-size",
            size=1.0,
            expected_min_order_size=Decimal("5"),
        )


def test_sports_market_start_auto_cancel_represented_in_snapshot(conn):
    sports_start = NOW + timedelta(minutes=12)
    insert_snapshot(conn, _snapshot(snapshot_id="snap-sports", sports_start_at=sports_start))

    loaded = get_snapshot(conn, "snap-sports")

    assert loaded.sports_start_at == sports_start


def test_authority_tier_constraint_enforced(conn):
    with pytest.raises(ValueError, match="authority_tier"):
        _snapshot(snapshot_id="snap-bad-tier", authority_tier="BLOG")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
              snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
              question_id, yes_token_id, no_token_id, enable_orderbook,
              active, closed, min_tick_size, min_order_size, fee_details_json,
              token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask,
              orderbook_depth_json, raw_gamma_payload_hash,
              raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
              captured_at, freshness_deadline
            ) VALUES (
              'snap-bad-db', 'g', 'e', 'slug', 'c', 'q', 'y', 'n', 1, 1, 0,
              '0.01', '0.01', '{}', '{}', 0, '0.49', '0.51', '{}',
              ?, ?, ?, 'BLOG', ?, ?
            )
            """,
            (HASH_A, HASH_B, HASH_C, NOW.isoformat(), (NOW + timedelta(seconds=30)).isoformat()),
        )


def test_raw_payload_hashes_persisted_for_replay(conn):
    insert_snapshot(conn, _snapshot(snapshot_id="snap-hashes"))

    row = conn.execute(
        """
        SELECT raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash
        FROM executable_market_snapshots
        WHERE snapshot_id = 'snap-hashes'
        """
    ).fetchone()

    assert row["raw_gamma_payload_hash"] == HASH_A
    assert row["raw_clob_market_info_hash"] == HASH_B
    assert row["raw_orderbook_hash"] == HASH_C


def test_init_schema_migrates_legacy_venue_commands_snapshot_column():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_kind TEXT NOT NULL,
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            price REAL NOT NULL,
            venue_order_id TEXT,
            state TEXT NOT NULL,
            last_event_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            review_required_reason TEXT
        )
        """
    )

    init_schema(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(venue_commands)")}
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(venue_commands)")}
    assert "snapshot_id" in columns
    assert "idx_venue_commands_snapshot" in indexes
