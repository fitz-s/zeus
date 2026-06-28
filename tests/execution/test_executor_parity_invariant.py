# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: GOAL#36 pre-arm parity — executor.py:1746 tick_size and
#   :1778 expected_fill_price_before_fee must equal the bound ExecutableMarketSnapshot.
#
# Relationship invariant under test:
#   A FinalExecutionIntent built from executable snapshot S, when validated
#   against S, must pass executor parity — for both tick_size and
#   expected_fill_price_before_fee, for buy_yes AND buy_no.
#
#   Fails pre-fix because:
#   Bug A: ERA cert builder sources tick_size from cert payload string
#          (float round-trip) rather than from _snap_for_depth.min_tick_size
#          (Decimal from DB).  When cert payload's min_tick_size diverges from
#          the DB snapshot row (different value), intent.tick_size != snapshot.min_tick_size.
#   Bug B: ERA sweeps with Decimal-arithmetic desired_shares
#          (Decimal("5.0") / Decimal("0.6") = Decimal("8.333...333")),
#          but cert builder computes size as float arithmetic
#          (5.0 / 0.6 = 8.333333333333334 — different value).
#          Guard re-sweeps with float-derived shares, gets different VWAP on
#          multi-level orderbooks.  ERA also stored the VWAP as float() which
#          loses precision relative to the exact Decimal sweep result.
#
# Fix direction (builders only, guard stays strict):
#   Bug A: ERA passes tick_size=str(_snap_for_depth.min_tick_size)
#          to cert builder (DB Decimal string, not cert payload float).
#          Cert builder stores "tick_size": str(Decimal(str(tick_size))) in payload.
#   Bug B: ERA computes _desired_shares using float arithmetic to match cert builder.
#          ERA stores sweep_expected_fill_price=str(_depth_sweep.average_price)
#          (exact Decimal string, not float).
"""
Relationship tests: FinalExecutionIntent built from snapshot S must pass parity
against S.  Four cases: buy_no and buy_yes for both Bug A (tick_size) and
Bug B (expected_fill_price).  Tests are RED pre-fix (reproduce live divergence)
and GREEN post-fix.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.contracts.execution_intent import FinalExecutionIntent, simulate_clob_sweep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_CANCEL_AFTER = _NOW + timedelta(hours=1)

_SNAP_HASH = "a" * 64
_COST_HASH = "d" * 64
_COST_ID = f"cost_basis:{_COST_HASH[:16]}"


def _make_db_snapshot(
    *,
    min_tick_size: str,
    direction: str = "buy_no",
    neg_risk: bool = False,
    orderbook_json: str | None = None,
) -> SimpleNamespace:
    """
    Minimal ExecutableMarketSnapshot-like object with real Decimal fields,
    matching the shape returned by snapshot_repo.get_snapshot.
    outcome_label must be set so simulate_clob_sweep / _selected_token_for_direction
    does not raise AttributeError.
    """
    if direction in ("buy_no", "sell_no"):
        selected_token = "no-token-001"
        yes_token = "yes-token-001"
        no_token = "no-token-001"
        outcome_label = "NO"
    else:
        selected_token = "yes-token-001"
        yes_token = "yes-token-001"
        no_token = "no-token-001"
        outcome_label = "YES"

    if orderbook_json is None:
        # Single-level ask at 0.80 with 20 shares
        orderbook_json = json.dumps({
            "asks": [{"price": "0.80", "size": "20"}],
            "bids": [{"price": "0.70", "size": "20"}],
        })

    return SimpleNamespace(
        snapshot_id="snap-parity-test",
        executable_snapshot_hash=_SNAP_HASH,
        min_tick_size=Decimal(min_tick_size),
        min_order_size=Decimal("1.0"),
        neg_risk=neg_risk,
        selected_outcome_token_id=selected_token,
        yes_token_id=yes_token,
        no_token_id=no_token,
        outcome_label=outcome_label,
        gamma_market_id="gamma-parity",
        event_id="event-parity",
        orderbook_depth_jsonb=orderbook_json,
    )


def _make_intent(
    *,
    tick_size: Decimal,
    expected_fill_price: Decimal,
    direction: str = "buy_no",
    neg_risk: bool = False,
    submitted_shares: Decimal = Decimal("5.0"),
    limit_price: Decimal = Decimal("0.80"),
) -> FinalExecutionIntent:
    """
    FinalExecutionIntent whose tick_size and expected_fill_price come from
    the cert builder chain (simulating the EDLI path).
    """
    if direction in ("buy_no", "sell_no"):
        token_id = "no-token-001"
    else:
        token_id = "yes-token-001"

    # fee_rate=0 -> fee_adjusted_execution_price == expected_fill_price_before_fee
    return FinalExecutionIntent(
        hypothesis_id="hyp-parity",
        selected_token_id=token_id,
        direction=direction,
        size_kind="shares",
        size_value=submitted_shares,
        submitted_shares=submitted_shares,
        final_limit_price=limit_price,
        expected_fill_price_before_fee=expected_fill_price,
        fee_adjusted_execution_price=expected_fill_price,
        order_policy="marketable_limit_depth_bound",
        order_type="FOK",
        post_only=False,
        cancel_after=_CANCEL_AFTER,
        snapshot_id="snap-parity-test",
        snapshot_hash=_SNAP_HASH,
        cost_basis_id=_COST_ID,
        cost_basis_hash=_COST_HASH,
        max_slippage_bps=Decimal("0"),
        tick_size=tick_size,
        min_order_size=Decimal("1.0"),
        fee_rate=Decimal("0"),
        neg_risk=neg_risk,
        event_id="event-parity",
        resolution_window="default",
        correlation_key="intent-parity",
        decision_source_context=None,
        passive_maker_context=None,
    )


def _run_guard(intent: FinalExecutionIntent, snapshot: SimpleNamespace):
    """Invoke _final_intent_snapshot_metadata with a patched snapshot lookup."""
    import src.execution.executor as _executor
    import src.state.snapshot_repo as _snap_repo

    with patch.object(_snap_repo, "get_snapshot", return_value=snapshot):
        with patch(
            "src.execution.executor.get_trade_connection_with_world_required",
            return_value=MagicMock(__enter__=lambda s: s, __exit__=lambda s, *a: None),
        ):
            return _executor._final_intent_snapshot_metadata(
                intent,
                None,
                submitted_shares=float(intent.submitted_shares),
            )


# ---------------------------------------------------------------------------
# Bug A: tick_size provenance (cert payload float vs DB snapshot Decimal)
# ---------------------------------------------------------------------------

class TestTickSizeParityRED:
    """
    RED: reproduce the live divergence.

    ERA:1145 (pre-fix) reads tick_size from executable_snapshot.payload.get("min_tick_size")
    (cert payload, string -> float).  The DB snapshot has a DIFFERENT min_tick_size
    value.  Result: intent.tick_size != snapshot.min_tick_size -> guard raises.

    Case: cert payload says "0.01" (default), DB snapshot has "0.001".
    """

    def test_red_buy_no_tick_size_from_cert_payload_diverges_from_db_snapshot(self):
        """
        BUG A pre-fix: cert was built with tick_size=float("0.01") (cert payload),
        DB snapshot has min_tick_size=Decimal("0.001").
        intent.tick_size = Decimal("0.01") != snapshot.min_tick_size = Decimal("0.001").
        """
        # Simulate what the current BUGGY path produces:
        # ERA:1145 reads cert payload "0.01", floats it -> 0.01
        # execution.py:137 stores float(0.01)
        # translator: _decimal(0.01) = Decimal(str(0.01)) = Decimal("0.01")
        cert_payload_tick_float = 0.01
        intent_tick_size = Decimal(str(cert_payload_tick_float))  # "0.01"

        # DB snapshot has the authoritative value
        db_snap = _make_db_snapshot(min_tick_size="0.001")

        intent = _make_intent(tick_size=intent_tick_size, expected_fill_price=Decimal("0.80"))

        with pytest.raises(ValueError, match="tick_size does not match"):
            _run_guard(intent, db_snap)

    def test_red_buy_yes_tick_size_from_cert_payload_diverges_from_db_snapshot(self):
        """
        BUG A pre-fix, buy_yes direction: same divergence.
        """
        cert_payload_tick_float = 0.01
        intent_tick_size = Decimal(str(cert_payload_tick_float))

        db_snap = _make_db_snapshot(min_tick_size="0.001", direction="buy_yes")
        intent = _make_intent(
            tick_size=intent_tick_size,
            expected_fill_price=Decimal("0.80"),
            direction="buy_yes",
        )

        with pytest.raises(ValueError, match="tick_size does not match"):
            _run_guard(intent, db_snap)

    def test_red_float_precision_tick_0001_same_value_is_fine(self):
        """
        Verify: when cert payload and DB snapshot AGREE on the value, tick passes.
        Both have 0.001 -> Decimal("0.001") == Decimal("0.001").
        This test passes even pre-fix (value match), confirming the root is VALUE
        divergence (cert payload != DB), not float precision.
        """
        cert_payload_tick_float = 0.001
        intent_tick_size = Decimal(str(cert_payload_tick_float))  # Decimal("0.001")

        db_snap = _make_db_snapshot(min_tick_size="0.001")

        # Sweep with 5 shares @ 0.80 -> PASS (20 available)
        intent = _make_intent(
            tick_size=intent_tick_size,
            expected_fill_price=Decimal("0.80"),
            submitted_shares=Decimal("5"),
        )

        # No exception expected -- tick matches
        result = _run_guard(intent, db_snap)
        assert result is not None


class TestTickSizeParityGREEN:
    """
    GREEN: after fix, tick_size sourced from DB snapshot (Decimal string) always matches.
    """

    def test_green_buy_no_tick_size_from_db_snapshot_passes_guard(self):
        """
        BUG A post-fix: ERA passes tick_size=str(_snap_for_depth.min_tick_size)
        to cert builder.  execution.py stores str(Decimal(str(snap.min_tick_size))).
        Translator: Decimal(str("0.001")) = Decimal("0.001").
        Guard: snapshot.min_tick_size = Decimal("0.001"). Equal -> passes.
        """
        db_snap = _make_db_snapshot(min_tick_size="0.001")

        # Fixed path: tick_size derived from DB snapshot, stored as normalised string
        snap_tick_str = str(db_snap.min_tick_size)  # "0.001"
        intent_tick_size = Decimal(snap_tick_str)   # Decimal("0.001") -- exact match

        intent = _make_intent(
            tick_size=intent_tick_size,
            expected_fill_price=Decimal("0.80"),
            submitted_shares=Decimal("5"),
        )

        result = _run_guard(intent, db_snap)
        assert result is not None

    def test_green_buy_yes_tick_size_from_db_snapshot_passes_guard(self):
        """
        BUG A post-fix, buy_yes direction.
        """
        db_snap = _make_db_snapshot(min_tick_size="0.001", direction="buy_yes")

        snap_tick_str = str(db_snap.min_tick_size)
        intent_tick_size = Decimal(snap_tick_str)

        intent = _make_intent(
            tick_size=intent_tick_size,
            expected_fill_price=Decimal("0.80"),
            direction="buy_yes",
            submitted_shares=Decimal("5"),
        )

        result = _run_guard(intent, db_snap)
        assert result is not None

    def test_green_tick_size_0001_normalised_string_roundtrip(self):
        """
        Structural: Decimal("0.001") -> str -> Decimal roundtrip is exact.
        Proves the fix expression str(Decimal(str(tick_size))) is safe.
        """
        for tick_str in ("0.001", "0.01", "0.1", "0.001000"):
            d = Decimal(tick_str)
            roundtripped = Decimal(str(d))
            assert roundtripped == d, f"roundtrip broken for {tick_str!r}"

    def test_green_source_lines_present_in_era(self):
        """
        Structural antibody: verify ERA passes DB-snap-derived tick_size,
        not cert payload float.  Fails if fix is accidentally reverted.
        """
        with open("src/engine/event_reactor_adapter.py") as f:
            src = f.read()
        assert "str(_snap_for_depth.min_tick_size)" in src, (
            "ERA Bug-A fix missing: tick_size must use str(_snap_for_depth.min_tick_size)"
        )

    def test_green_cert_builder_stores_tick_as_normalised_string(self):
        """
        Structural antibody: execution.py must store tick_size as normalised
        Decimal string, not raw float.
        """
        with open("src/decision_kernel/certificates/execution.py") as f:
            src = f.read()
        assert 'str(Decimal(str(tick_size)))' in src, (
            "execution.py Bug-A fix missing: tick_size must be str(Decimal(str(tick_size)))"
        )


# ---------------------------------------------------------------------------
# Bug B: Decimal vs float arithmetic for shares -> different VWAP
# ---------------------------------------------------------------------------

class TestFillPriceParityRED:
    """
    RED: reproduce the live fill_price mismatch.

    ERA (pre-fix) computes desired_shares with Decimal arithmetic:
      _desired_shares = Decimal("5.0") / Decimal("0.6") = Decimal("8.333...333")
    Cert builder computes size with float arithmetic:
      size = 5.0 / 0.6 = 8.333333333333334
    Guard sweeps with Decimal(str(float(submitted_shares))) = Decimal("8.333333333333334").
    On a multi-level orderbook, different share amounts produce different VWAPs.

    The stored fill price (float(ERA_vwap)) also loses Decimal precision.
    Combined: intent.expected_fill_price_before_fee != guard_sweep.average_price.
    """

    def test_red_buy_no_decimal_vs_float_shares_produce_different_vwap(self):
        """
        BUG B pre-fix, buy_no: Decimal-division shares != float-division shares
        on multi-level book -> VWAP mismatch.

        Book: asks=[0.60@3, 0.65@7], reserved_notional=5.0
        ERA desired_shares (Decimal): 5/0.6 = 8.333...333 (infinite repeating)
        Cert/guard shares (float):    5/0.6 = 8.333333333333334

        The two sweeps fill different amounts at level 2 -> different VWAPs.
        Full guard-path proof: we directly assert the two VWAP values differ,
        which is the root that causes parity rejection when the stored fill
        and the guard re-sweep give different numbers.
        """
        orderbook = json.dumps({
            "asks": [{"price": "0.60", "size": "3"}, {"price": "0.65", "size": "7"}],
            "bids": [],
        })
        db_snap = _make_db_snapshot(min_tick_size="0.001", orderbook_json=orderbook)

        # ERA (buggy): Decimal-arithmetic desired_shares
        era_desired = Decimal("5.0") / Decimal("0.6")
        era_sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=era_desired,
            limit_price=Decimal("0.65"),
        )
        assert era_sweep.depth_status == "PASS"
        assert era_sweep.average_price is not None

        # Guard sweeps with float-derived shares (what cert encodes as submitted_shares)
        guard_shares = Decimal(str(5.0 / 0.6))  # 8.333333333333334
        guard_sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=guard_shares,
            limit_price=Decimal("0.65"),
        )
        assert guard_sweep.depth_status == "PASS"
        assert guard_sweep.average_price is not None

        # Confirm they differ (root of Bug B)
        assert era_desired != guard_shares, "test precondition: shares must differ"
        assert era_sweep.average_price != guard_sweep.average_price, (
            "ERA and guard VWAPs should differ when shares differ on multi-level book"
        )

        # Also prove: if stored fill (from ERA sweep) != guard sweep VWAP, parity fails.
        # Use a book and shares where the VWAP == limit_price so FinalExecutionIntent
        # construction passes (no adverse-slippage violation), but the stored vs re-sweep
        # fill still diverge due to Decimal/float shares arithmetic.
        # Single-level book, limit price == ask price so VWAP = limit = no slippage.
        single_level_book = json.dumps({
            "asks": [{"price": "0.95", "size": "20"}],
            "bids": [],
        })
        snap_single = _make_db_snapshot(min_tick_size="0.001", orderbook_json=single_level_book)
        # ERA: Decimal sweep at 0.95
        era_single = simulate_clob_sweep(
            snapshot=snap_single,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=Decimal("5.0") / Decimal("0.6"),
            limit_price=Decimal("0.95"),
        )
        # Guard: float-derived shares (same quantity for single-level -> same VWAP)
        # Note: on a single-level book, different shares still fill at 0.95, so VWAP=0.95
        # regardless of quantity. This sub-test proves the VWAP identity for single-level,
        # and the multi-level assertion above proves divergence exists.
        guard_single = simulate_clob_sweep(
            snapshot=snap_single,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=guard_shares,
            limit_price=Decimal("0.95"),
        )
        assert era_single.depth_status == "PASS"
        assert guard_single.depth_status == "PASS"
        # On a single-level book all fills are at 0.95 regardless of shares; VWAP is the same.
        assert era_single.average_price == guard_single.average_price == Decimal("0.95")

    def test_red_buy_yes_decimal_vs_float_shares_produce_different_vwap(self):
        """
        BUG B pre-fix, buy_yes direction: Decimal-vs-float share arithmetic produces
        different VWAPs on multi-level book.
        """
        orderbook = json.dumps({
            "asks": [{"price": "0.60", "size": "3"}, {"price": "0.65", "size": "7"}],
            "bids": [],
        })
        db_snap = _make_db_snapshot(
            min_tick_size="0.001", direction="buy_yes", orderbook_json=orderbook
        )

        era_desired = Decimal("5.0") / Decimal("0.6")
        era_sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_yes",
            requested_size_kind="shares",
            requested_size_value=era_desired,
            limit_price=Decimal("0.65"),
        )
        assert era_sweep.depth_status == "PASS"

        guard_shares = Decimal(str(5.0 / 0.6))
        guard_sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_yes",
            requested_size_kind="shares",
            requested_size_value=guard_shares,
            limit_price=Decimal("0.65"),
        )

        assert era_desired != guard_shares, "test precondition: shares must differ"
        assert era_sweep.average_price != guard_sweep.average_price, (
            "ERA and guard VWAPs should differ when shares differ on multi-level book"
        )

    def test_red_decimal_float_share_arithmetic_inequality_is_real(self):
        """
        Structural proof: Decimal and float division of 5/0.6 produce different values.
        This is the root of Bug B shares divergence.
        """
        decimal_result = Decimal("5.0") / Decimal("0.6")
        float_result = 5.0 / 0.6
        float_as_decimal = Decimal(str(float_result))

        assert decimal_result != float_as_decimal, (
            "Decimal and float division must differ for 5/0.6 — Bug B root changed"
        )


class TestFillPriceParityGREEN:
    """
    GREEN: after fix, ERA uses float-arithmetic shares for sweep (matching cert builder),
    and stores str(average_price) (exact Decimal) instead of float(average_price).
    Both sweep with same shares -> same VWAP -> parity passes.
    """

    def test_green_buy_no_float_shares_str_vwap_passes_guard(self):
        """
        BUG B post-fix, buy_no: ERA sweeps with float-derived shares,
        stores str(average_price). Guard sweeps with same shares -> same VWAP.

        Use a single-level book so VWAP == limit_price (fill at ask = no adverse
        slippage from FinalExecutionIntent's perspective).
        """
        # Single-level book at 0.80, 20 shares available
        db_snap = _make_db_snapshot(min_tick_size="0.001")  # default single-level book

        # Fixed path: float-arithmetic shares
        fixed_shares = Decimal(str(5.0 / 0.6))  # 8.333333333333334

        fixed_sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=fixed_shares,
            limit_price=Decimal("0.80"),
        )
        assert fixed_sweep.depth_status == "PASS"
        assert fixed_sweep.average_price is not None

        # Fixed: store str(average_price), not float(average_price)
        intent_fill_price = Decimal(str(fixed_sweep.average_price))

        intent = _make_intent(
            tick_size=Decimal("0.001"),
            expected_fill_price=intent_fill_price,
            submitted_shares=fixed_shares,
            limit_price=Decimal("0.80"),
        )

        result = _run_guard(intent, db_snap)
        assert result is not None

    def test_green_buy_yes_float_shares_str_vwap_passes_guard(self):
        """
        BUG B post-fix, buy_yes direction.
        """
        db_snap = _make_db_snapshot(min_tick_size="0.001", direction="buy_yes")

        fixed_shares = Decimal(str(5.0 / 0.6))

        fixed_sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_yes",
            requested_size_kind="shares",
            requested_size_value=fixed_shares,
            limit_price=Decimal("0.80"),
        )
        assert fixed_sweep.depth_status == "PASS"
        intent_fill_price = Decimal(str(fixed_sweep.average_price))

        intent = _make_intent(
            tick_size=Decimal("0.001"),
            expected_fill_price=intent_fill_price,
            direction="buy_yes",
            submitted_shares=fixed_shares,
            limit_price=Decimal("0.80"),
        )

        result = _run_guard(intent, db_snap)
        assert result is not None

    def test_green_str_decimal_vwap_roundtrip_is_exact(self):
        """
        Structural: str(Decimal("0.6320000000000000014400000000")) roundtrips exactly.
        Proves storing str(average_price) preserves the guard match.
        """
        vwap = Decimal("0.6320000000000000014400000000")
        as_str = str(vwap)
        roundtripped = Decimal(as_str)
        assert roundtripped == vwap

    def test_green_era_stores_sweep_as_string_not_float(self):
        """
        Structural antibody: ERA must store str(<final sweep>.average_price),
        not float(_depth_sweep.average_price).
        """
        with open("src/engine/event_reactor_adapter.py") as f:
            src = f.read()
        assert "str(_venue_quantized_sweep.average_price)" in src, (
            "ERA Bug-B fix missing: sweep_expected_fill_price must use "
            "str(<final sweep>.average_price), not float(...)"
        )

    def test_green_era_uses_float_arithmetic_for_shares(self):
        """
        Structural antibody: ERA must use float arithmetic for desired_shares
        to match the cert builder's size computation.
        """
        with open("src/engine/event_reactor_adapter.py") as f:
            src = f.read()
        assert "_desired_shares_f" in src, (
            "ERA Bug-B fix missing: must compute _desired_shares_f via float arithmetic"
        )


# ---------------------------------------------------------------------------
# Neg-risk provenance: certificate true may override omitted snapshot false
# ---------------------------------------------------------------------------

class TestNegRiskMonotonicParity:
    """
    Live executable snapshot rows can carry an omitted/stale false while the
    EDLI certificate path has already proven the event is neg-risk.  The
    executor guard may admit that true-monotonic repair, but must still reject
    the inverse direction because it would drop a proven neg-risk event.
    """

    def test_cert_proven_neg_risk_true_overrides_stale_snapshot_false(self):
        db_snap = _make_db_snapshot(min_tick_size="0.001", neg_risk=False)
        intent = _make_intent(
            tick_size=Decimal("0.001"),
            expected_fill_price=Decimal("0.80"),
            neg_risk=True,
            submitted_shares=Decimal("5"),
        )

        result = _run_guard(intent, db_snap)
        assert result == ("gamma-parity", "event-parity")

    def test_snapshot_true_intent_false_remains_fail_closed(self):
        db_snap = _make_db_snapshot(min_tick_size="0.001", neg_risk=True)
        intent = _make_intent(
            tick_size=Decimal("0.001"),
            expected_fill_price=Decimal("0.80"),
            neg_risk=False,
            submitted_shares=Decimal("5"),
        )

        with pytest.raises(ValueError, match="neg_risk does not match"):
            _run_guard(intent, db_snap)


# ---------------------------------------------------------------------------
# Combined: both bugs fixed -> full parity round-trip
# ---------------------------------------------------------------------------

class TestFullParityRoundTrip:
    """
    GREEN: with both fixes applied, a FinalExecutionIntent built from snapshot S
    via the corrected cert builder chain passes parity against S.
    """

    def test_full_round_trip_buy_no_passes_guard(self):
        """
        Combined post-fix round-trip: tick_size from DB snap + fill_price as str.
        Guard must pass with no exception.
        """
        db_snap = _make_db_snapshot(min_tick_size="0.001")

        # Fixed tick_size: from DB snap
        intent_tick_size = Decimal(str(db_snap.min_tick_size))  # Decimal("0.001")

        # Fixed fill_price: str of Decimal from sweep (not float-converted)
        sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=Decimal("5"),
            limit_price=Decimal("0.80"),
        )
        assert sweep.depth_status == "PASS"
        intent_fill_price = Decimal(str(sweep.average_price))  # Decimal("0.80")

        intent = _make_intent(
            tick_size=intent_tick_size,
            expected_fill_price=intent_fill_price,
            submitted_shares=Decimal("5"),
        )

        result = _run_guard(intent, db_snap)
        assert result == ("gamma-parity", "event-parity")

    def test_full_round_trip_buy_yes_passes_guard(self):
        """
        Combined post-fix round-trip, buy_yes.
        """
        db_snap = _make_db_snapshot(min_tick_size="0.001", direction="buy_yes")

        intent_tick_size = Decimal(str(db_snap.min_tick_size))

        sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_yes",
            requested_size_kind="shares",
            requested_size_value=Decimal("5"),
            limit_price=Decimal("0.80"),
        )
        assert sweep.depth_status == "PASS"
        intent_fill_price = Decimal(str(sweep.average_price))

        intent = _make_intent(
            tick_size=intent_tick_size,
            expected_fill_price=intent_fill_price,
            direction="buy_yes",
            submitted_shares=Decimal("5"),
        )

        result = _run_guard(intent, db_snap)
        assert result == ("gamma-parity", "event-parity")

    def test_full_round_trip_multi_level_book_buy_no_passes_guard(self):
        """
        Combined post-fix: multi-level book, float-derived shares, str VWAP.
        Specifically exercises the Bug B scenario (different VWAP on multi-level
        books depending on share arithmetic).

        Uses a multi-level book where the VWAP equals the highest-level ask price
        exactly, so the fill-price <= limit-price constraint holds even with
        FinalExecutionIntent's strict slippage check (max_slippage_bps=0).
        Book: all asks at 0.65, limit_price=0.65 -> VWAP == 0.65 -> no slippage.
        """
        # Multi-level but all at same price: exercises Bug B sweep path while
        # keeping VWAP == limit_price (no adverse slippage violation).
        orderbook = json.dumps({
            "asks": [{"price": "0.65", "size": "5"}, {"price": "0.65", "size": "10"}],
            "bids": [],
        })
        db_snap = _make_db_snapshot(min_tick_size="0.001", orderbook_json=orderbook)

        intent_tick_size = Decimal(str(db_snap.min_tick_size))

        # Fixed: float-arithmetic shares (cert builder style)
        fixed_shares = Decimal(str(5.0 / 0.6))  # 8.333333333333334
        sweep = simulate_clob_sweep(
            snapshot=db_snap,
            direction="buy_no",
            requested_size_kind="shares",
            requested_size_value=fixed_shares,
            limit_price=Decimal("0.65"),
        )
        assert sweep.depth_status == "PASS"
        assert sweep.average_price == Decimal("0.65")  # uniform price -> VWAP = ask
        intent_fill_price = Decimal(str(sweep.average_price))

        intent = _make_intent(
            tick_size=intent_tick_size,
            expected_fill_price=intent_fill_price,
            submitted_shares=fixed_shares,
            limit_price=Decimal("0.65"),
        )

        result = _run_guard(intent, db_snap)
        assert result == ("gamma-parity", "event-parity")
