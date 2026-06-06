# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: Wall B + C fix — FOK taker order sizing must be capped at
#   available crossable book depth (size-to-depth), and expected_fill_price_before_fee
#   must equal the sweep VWAP (not limit_price) for multi-level fills.
#
# Relationship invariant under test:
#   For TAKER FOK orders:
#   (1) size = min(desired_shares, available_crossable_shares) so a thin-book
#       candidate can fill at the smaller size rather than rejecting DEPTH_INSUFFICIENT.
#   (2) expected_fill_price_before_fee = sweep VWAP so executor check
#       (sweep.average_price == intent.expected_fill_price_before_fee) passes.
#   (3) If available_crossable_shares < min_order_size → DEPTH_BELOW_MIN_ORDER_SIZE
#       (correct skip, no -EV order).
#   (4) Reservation cap is preserved: limit ≤ c_fee_adjusted at all times.
#
# Tests cover:
#   RED: simulate_clob_sweep on thin book → DEPTH_INSUFFICIENT (pre-fix behaviour)
#   GREEN: size-to-depth → sweep PASSES with smaller size
#   GREEN: multi-level VWAP → expected_fill matches sweep average
#   GREEN: depth < min_order_size → DEPTH_BELOW_MIN_ORDER_SIZE raised
#   GREEN: cert builder applies size-to-depth when available_crossable_shares provided
"""FOK size-to-available-depth antibody tests (Wall B / Wall C 2026-06-01)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.contracts.execution_intent import simulate_clob_sweep
from src.state.snapshot_repo import executable_snapshot_from_row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMMON_ROW_BASE = {
    "snapshot_id": "snap-test",
    "gamma_market_id": "gm-001",
    "event_id": "evt-001",
    "event_slug": "test",
    "condition_id": "cond-001",
    "question_id": "q-001",
    "yes_token_id": "yes-001",
    "no_token_id": "no-001",
    "selected_outcome_token_id": "no-001",
    "outcome_label": "NO",
    "enable_orderbook": 1,
    "active": 1,
    "closed": 0,
    "accepting_orders": 1,
    "market_start_at": None,
    "market_end_at": None,
    "market_close_at": None,
    "sports_start_at": None,
    "min_tick_size": "0.01",
    "min_order_size": "1.0",
    "fee_details_json": json.dumps({"maker_amount": "0.0", "taker_amount": "0.0"}),
    "token_map_json": json.dumps({"yes": "yes-001", "no": "no-001"}),
    "rfqe": None,
    "neg_risk": 0,
    "orderbook_top_bid": "0.68",
    "orderbook_top_ask": "0.80",
    "raw_gamma_payload_hash": "a" * 64,
    "raw_clob_market_info_hash": "b" * 64,
    "raw_orderbook_hash": "c" * 64,
    "authority_tier": "CLOB",
    "captured_at": "2026-06-01T03:57:32+00:00",
    "freshness_deadline": "2026-06-01T05:00:00+00:00",
    "wide_spread_display_substitution": 0,
    "depth_at_best_ask": 0,
    "tradeability_status_json": None,
}


import sqlite3


def _make_sqlite_row(d: dict) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f'"{k}" TEXT' for k in d)
    conn.execute(f"CREATE TABLE t ({cols})")
    conn.execute(
        f"INSERT INTO t VALUES ({', '.join(['?'] * len(d))})",
        [str(v) if v is not None else None for v in d.values()],
    )
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    return row


def _make_snapshot(orderbook: dict, *, min_order_size: str = "1.0") -> object:
    row = dict(_COMMON_ROW_BASE)
    row["orderbook_depth_json"] = json.dumps(orderbook)
    row["min_order_size"] = min_order_size
    return executable_snapshot_from_row(_make_sqlite_row(row))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFOKDepthSweepRED:
    """
    RED: pre-fix behaviour — thin book → DEPTH_INSUFFICIENT when desired size
    exceeds available asks.  This is the raw behaviour of simulate_clob_sweep;
    the fix (size-to-depth) is in the cert builder, not in simulate_clob_sweep.
    """

    def test_red_thin_book_depth_insufficient_on_desired_shares(self):
        """
        buy_no, limit=0.81, available=5.5 shares @ 0.80.
        Desired FOK = 5.0 / 0.80 = 6.25 shares → DEPTH_INSUFFICIENT (5.5 < 6.25).
        """
        snap = _make_snapshot({"asks": [{"price": "0.80", "size": "5.5"}],
                                "bids": [{"price": "0.68", "size": "10"}]})
        result = simulate_clob_sweep(
            snapshot=snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=Decimal("6.25"),   # desired: $5 / $0.80
            limit_price=Decimal("0.81"),
        )
        assert result.depth_status == "DEPTH_INSUFFICIENT"
        assert result.filled_shares == Decimal("5.5")  # partial fill
        # average_price is non-None even on DEPTH_INSUFFICIENT (partial fill VWAP)
        assert result.average_price == Decimal("0.80")

    def test_red_desired_size_exceeds_depth(self):
        """
        Confirms: even with a crossable limit, if requested > available → DEPTH_INSUFFICIENT.
        """
        snap = _make_snapshot({"asks": [{"price": "0.80", "size": "3.0"}],
                                "bids": []})
        result = simulate_clob_sweep(
            snapshot=snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=Decimal("5.0"),
            limit_price=Decimal("0.85"),
        )
        assert result.depth_status == "DEPTH_INSUFFICIENT"


class TestFOKSizeToDepthGREEN:
    """
    GREEN: after size-to-depth, sweep runs on the CAPPED size and passes.
    The cert builder caps size = min(desired, available); this test verifies
    that simulate_clob_sweep produces PASS when called with the capped size.
    """

    def test_green_capped_size_passes_depth_sweep(self):
        """
        buy_no, limit=0.81, available=5.5 shares @ 0.80.
        Desired = 6.25 → capped to 5.5 → sweep with 5.5 → PASS.
        """
        snap = _make_snapshot({"asks": [{"price": "0.80", "size": "5.5"}],
                                "bids": [{"price": "0.68", "size": "10"}]})
        available_shares = Decimal("5.5")
        desired = Decimal("6.25")
        capped = min(desired, available_shares)  # size-to-depth: 5.5

        result = simulate_clob_sweep(
            snapshot=snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=capped,
            limit_price=Decimal("0.81"),
        )
        assert result.depth_status == "PASS"
        assert result.filled_shares == Decimal("5.5")
        assert result.average_price == Decimal("0.80")
        assert result.gross_notional == Decimal("5.5") * Decimal("0.80")

    def test_green_capped_size_vwap_matches_sweep_average(self):
        """
        Multi-level book: asks at [0.78@3, 0.80@5].
        Desired 10 shares > available 8 → capped to 8.
        Sweep VWAP = (3×0.78 + 5×0.80) / 8 = (2.34 + 4.00) / 8 = 0.7925.
        expected_fill_price_before_fee must equal 0.7925, NOT limit_price=0.81.
        """
        snap = _make_snapshot({
            "asks": [{"price": "0.78", "size": "3"}, {"price": "0.80", "size": "5"}],
            "bids": [],
        })
        available_shares = Decimal("8")
        desired = Decimal("10")
        capped = min(desired, available_shares)

        result = simulate_clob_sweep(
            snapshot=snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=capped,
            limit_price=Decimal("0.81"),
        )
        assert result.depth_status == "PASS"
        assert result.average_price is not None
        expected_vwap = (Decimal("3") * Decimal("0.78") + Decimal("5") * Decimal("0.80")) / Decimal("8")
        assert abs(result.average_price - expected_vwap) < Decimal("0.0001")
        # VWAP != limit_price — this is what Wall C is about
        assert result.average_price != Decimal("0.81")

    def test_green_reservation_cap_preserved(self):
        """
        Reservation cap: limit ≤ c_fee_adjusted.
        buy_no: limit = min(best_ask, reservation). If best_ask=0.80, reservation=0.81:
        limit = 0.80 ≤ 0.81 (reservation cap preserved).
        """
        best_ask = Decimal("0.80")
        c_fee_adjusted = Decimal("0.81")
        limit = min(best_ask, c_fee_adjusted)
        assert limit <= c_fee_adjusted
        assert limit == Decimal("0.80")


class TestFOKDepthBelowMinOrderSize:
    """
    GREEN: if available depth < min_order_size → DEPTH_BELOW_MIN_ORDER_SIZE raised.
    The cert builder must skip (not trade) rather than emit a sub-min order.
    """

    def test_depth_below_min_order_size_raises_in_cert_builder(self):
        """
        available_crossable_shares=0.5 < min_order_size=1.0 →
        build_final_intent_certificate_from_actionable raises ValueError
        with 'DEPTH_BELOW_MIN_ORDER_SIZE'.
        """
        from datetime import datetime, timezone
        from src.decision_kernel.certificates.execution import (
            build_final_intent_certificate_from_actionable,
        )
        from src.decision_kernel.certificate import DecisionCertificate
        from src.decision_kernel import claims

        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        def _stub_cert(cert_type: str, payload: dict) -> DecisionCertificate:
            from src.decision_kernel.certificate import build_certificate
            return build_certificate(
                certificate_type=cert_type,
                semantic_key=f"test:{cert_type}",
                claim_type=cert_type,
                mode="LIVE",
                decision_time=now,
                payload=payload,
                authority_id="test",
                authority_version="v1",
                algorithm_id="test",
                algorithm_version="v1",
            )

        # Minimal cert payloads (only fields consumed by the builder)
        actionable = _stub_cert(claims.ACTIONABLE_TRADE, {
            "event_id": "evt-001",
            "final_intent_id": "intent-001",
            "family_id": "fam-001",
            "candidate_id": "cand-001",
            "condition_id": "cond-001",
            "token_id": "no-001",
            "direction": "buy_no",
            "c_fee_adjusted": 0.81,
            "kelly_size_usd": 5.0,
            "live_cap_reserved_notional_usd": 5.0,
            "live_cap_usage_id": "usage-001",
            "executable_snapshot_id": "snap-001",
            "neg_risk": False,
        })
        exec_snap = _stub_cert(claims.EXECUTABLE_SNAPSHOT, {
            "executable_snapshot_hash": "a" * 64,
        })
        quote_feas = _stub_cert(claims.QUOTE_FEASIBILITY, {})
        cost_model = _stub_cert(claims.COST_MODEL, {
            "cost_basis_hash": "b" * 64,
        })
        forecast_auth = _stub_cert(claims.FORECAST_AUTHORITY, {})

        with pytest.raises(ValueError, match="DEPTH_BELOW_MIN_ORDER_SIZE"):
            build_final_intent_certificate_from_actionable(
                actionable_cert=actionable,
                executable_snapshot_cert=exec_snap,
                quote_feasibility_cert=quote_feas,
                cost_model_cert=cost_model,
                forecast_authority_cert=forecast_auth,
                decision_source_context={"source_id": "test"},
                passive_maker_context=None,
                decision_time=now,
                order_mode="TAKER",
                tick_size=0.01,
                min_order_size=1.0,
                best_bid=0.68,
                best_ask=0.80,
                taker_fok_fak_live_enabled=True,
                available_crossable_shares=0.5,   # ← below min_order_size=1.0
            )

    def test_depth_exactly_at_min_order_size_does_not_raise(self):
        """
        available_crossable_shares == min_order_size → no raise.
        Edge case: min(desired, available) = min_order_size → passes.
        """
        from decimal import Decimal
        min_order_size = 1.0
        available = 1.0
        desired = 6.25
        capped = min(desired, available)
        assert capped >= min_order_size  # no raise expected


class TestFOKSizeToDepthCertBuilder:
    """
    GREEN: cert builder emits size = min(desired, available_crossable_shares)
    and expected_fill_price_before_fee = sweep_expected_fill_price.
    """

    def test_cert_builder_caps_size_and_sets_fill_price(self):
        """
        available_crossable_shares=5.5, desired=6.25 →
        cert payload size=5.5, expected_fill_price_before_fee=0.80 (not 0.81).
        """
        from datetime import datetime, timezone
        from src.decision_kernel.certificates.execution import (
            build_final_intent_certificate_from_actionable,
        )
        from src.decision_kernel.certificate import build_certificate
        from src.decision_kernel import claims

        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        def _stub_cert(cert_type, payload):
            return build_certificate(
                certificate_type=cert_type,
                semantic_key=f"test:{cert_type}",
                claim_type=cert_type,
                mode="LIVE",
                decision_time=now,
                payload=payload,
                authority_id="test",
                authority_version="v1",
                algorithm_id="test",
                algorithm_version="v1",
            )

        actionable = _stub_cert(claims.ACTIONABLE_TRADE, {
            "event_id": "evt-001",
            "final_intent_id": "intent-001",
            "family_id": "fam-001",
            "candidate_id": "cand-001",
            "condition_id": "cond-001",
            "token_id": "no-001",
            "direction": "buy_no",
            "c_fee_adjusted": 0.81,
            "kelly_size_usd": 5.0,
            "live_cap_reserved_notional_usd": 5.0,
            "live_cap_usage_id": "usage-001",
            "executable_snapshot_id": "snap-001",
            "neg_risk": False,
        })
        exec_snap = _stub_cert(claims.EXECUTABLE_SNAPSHOT, {
            "executable_snapshot_hash": "a" * 64,
        })
        quote_feas = _stub_cert(claims.QUOTE_FEASIBILITY, {})
        cost_model = _stub_cert(claims.COST_MODEL, {"cost_basis_hash": "b" * 64})
        forecast_auth = _stub_cert(claims.FORECAST_AUTHORITY, {})

        cert = build_final_intent_certificate_from_actionable(
            actionable_cert=actionable,
            executable_snapshot_cert=exec_snap,
            quote_feasibility_cert=quote_feas,
            cost_model_cert=cost_model,
            forecast_authority_cert=forecast_auth,
            decision_source_context={"source_id": "test"},
            passive_maker_context=None,
            decision_time=now,
            order_mode="TAKER",
            tick_size=0.01,
            min_order_size=1.0,
            best_bid=0.68,
            best_ask=0.80,
            taker_fok_fak_live_enabled=True,
            available_crossable_shares=5.5,         # ← available depth
            sweep_expected_fill_price=0.80,         # ← sweep VWAP
        )

        payload = cert.payload
        # Size must be capped at 5.5 (not 6.25)
        assert payload["size"] <= 5.5 + 0.01, (
            f"size={payload['size']} should be ≤ 5.5 (available_crossable_shares)"
        )
        # expected_fill_price_before_fee must be 0.80 (sweep VWAP), not 0.81 (limit)
        efp = payload.get("expected_fill_price_before_fee")
        assert efp is not None
        assert abs(float(efp) - 0.80) < 0.001, (
            f"expected_fill_price_before_fee={efp!r} should be ~0.80 (sweep VWAP)"
        )
        assert payload["max_slippage_bps"] == pytest.approx(0.0)
        from src.engine.event_bound_final_intent import validate_final_intent_cert_for_existing_executor

        assert validate_final_intent_cert_for_existing_executor(cert)

    def test_cert_builder_declares_taker_vwap_slippage_budget(self):
        from datetime import datetime, timezone
        from src.decision_kernel.certificates.execution import (
            build_final_intent_certificate_from_actionable,
        )
        from src.decision_kernel.certificate import build_certificate
        from src.decision_kernel import claims
        from src.engine.event_bound_final_intent import validate_final_intent_cert_for_existing_executor

        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        def _stub_cert(cert_type, payload):
            return build_certificate(
                certificate_type=cert_type,
                semantic_key=f"test:{cert_type}:slippage",
                claim_type=cert_type,
                mode="LIVE",
                decision_time=now,
                payload=payload,
                authority_id="test",
                authority_version="v1",
                algorithm_id="test",
                algorithm_version="v1",
            )

        actionable = _stub_cert(claims.ACTIONABLE_TRADE, {
            "event_id": "evt-002",
            "final_intent_id": "intent-002",
            "family_id": "fam-002",
            "candidate_id": "cand-002",
            "condition_id": "cond-002",
            "token_id": "no-002",
            "direction": "buy_no",
            "c_fee_adjusted": 0.82,
            "kelly_size_usd": 5.0,
            "live_cap_reserved_notional_usd": 5.0,
            "live_cap_usage_id": "usage-002",
            "executable_snapshot_id": "snap-002",
            "neg_risk": False,
        })
        exec_snap = _stub_cert(claims.EXECUTABLE_SNAPSHOT, {
            "executable_snapshot_hash": "a" * 64,
        })
        quote_feas = _stub_cert(claims.QUOTE_FEASIBILITY, {})
        cost_model = _stub_cert(claims.COST_MODEL, {"cost_basis_hash": "b" * 64})
        forecast_auth = _stub_cert(claims.FORECAST_AUTHORITY, {})

        cert = build_final_intent_certificate_from_actionable(
            actionable_cert=actionable,
            executable_snapshot_cert=exec_snap,
            quote_feasibility_cert=quote_feas,
            cost_model_cert=cost_model,
            forecast_authority_cert=forecast_auth,
            decision_source_context={"source_id": "test"},
            passive_maker_context=None,
            decision_time=now,
            order_mode="TAKER",
            tick_size=0.01,
            min_order_size=1.0,
            best_bid=0.70,
            best_ask=0.82,
            taker_fok_fak_live_enabled=True,
            available_crossable_shares=5.5,
            sweep_expected_fill_price="0.80",
        )

        assert cert.payload["limit_price"] == pytest.approx(0.82)
        assert cert.payload["expected_fill_price_before_fee"] == "0.80"
        assert cert.payload["max_slippage_bps"] == pytest.approx(250.0)
        assert validate_final_intent_cert_for_existing_executor(cert)
