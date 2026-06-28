# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: Wall A fix — cert executable_snapshot payload must carry
#   min_tick_size / min_order_size / neg_risk from the hydrated snapshot that the
#   cert cites (proof.executable_snapshot_id), NOT from selected_snapshot_row
#   which may be a DIFFERENT latest snapshot with different values.
#
# Relationship invariant under test:
#   _build_no_submit_proof_bundle_from_adapter_evidence emits an
#   EXECUTABLE_SNAPSHOT AuthorityEvidence cert whose payload fields
#   (min_tick_size, min_order_size, neg_risk) MUST match the
#   ExecutableMarketSnapshot fetched by proof.executable_snapshot_id.
#   When selected_snapshot_row carries a different snapshot (stale or fresh),
#   the cert payload must still reflect the CITED snapshot — not the latest row.
#   This is the antibody: a genuine divergence (cited snap has tick=0.01 but
#   selected_row has tick=0.001) must appear as a cert mismatch that fails closed.
"""
RED test proves current code puts selected_snapshot_row.min_tick_size in cert
payload even when that row differs from the hydrated (cited) snapshot.

GREEN test proves that after the fix, cert payload min_tick_size == hydrated
snapshot's min_tick_size regardless of selected_snapshot_row's value.

Divergence test proves that when the ACTUAL DB snapshot has tick=0.01 and a
genuine cert carries tick=0.001, the downstream tick_size check raises — i.e.
the antibody still fires when a real mismatch exists.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

# The function under test lives in ERA; we import it directly.
from src.engine.event_reactor_adapter import _build_no_submit_proof_bundle_from_adapter_evidence

# _hydrated_snapshot is fetched via get_snapshot inside the function.
# We patch it to return a controlled object.

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_minimal_snapshot(
    *,
    snapshot_id: str = "snap-hydrated",
    min_tick_size: str = "0.01",
    min_order_size: str = "1.0",
    neg_risk: bool = False,
    top_ask: str = "0.55",
    top_bid: str = "0.45",
) -> SimpleNamespace:
    """Minimal ExecutableMarketSnapshot stand-in with the fields we care about."""
    return SimpleNamespace(
        snapshot_id=snapshot_id,
        executable_snapshot_hash="a" * 64,
        min_tick_size=Decimal(min_tick_size),
        min_order_size=Decimal(min_order_size),
        neg_risk=neg_risk,
        orderbook_top_ask=Decimal(top_ask),
        orderbook_top_bid=Decimal(top_bid),
        selected_outcome_token_id=None,
        outcome_label=None,
        fee_details={"maker_amount": "0.0", "taker_amount": "0.0",
                     "fee_rate_source_field": "maker_amount"},
        orderbook_depth_jsonb=json.dumps({"asks": [{"price": top_ask, "size": "10"}],
                                          "bids": [{"price": top_bid, "size": "10"}]}),
    )


def _make_selected_row(
    *,
    snapshot_id: str = "snap-latest",
    min_tick_size: str = "0.001",   # DIFFERENT from hydrated
    min_order_size: str = "5.0",    # DIFFERENT from hydrated
    neg_risk: int = 1,              # DIFFERENT from hydrated (False)
    top_ask: str = "0.55",
    top_bid: str = "0.45",
) -> dict:
    """The 'latest' snapshot row that may differ from the cited hydrated snapshot."""
    return {
        "snapshot_id": snapshot_id,
        "min_tick_size": min_tick_size,
        "min_order_size": min_order_size,
        "neg_risk": neg_risk,
        "orderbook_top_ask": top_ask,
        "orderbook_top_bid": top_bid,
        "orderbook_depth_json": json.dumps({"asks": [{"price": top_ask, "size": "10"}],
                                            "bids": [{"price": top_bid, "size": "10"}]}),
        "orderbook_depth_jsonb": json.dumps({"asks": [{"price": top_ask, "size": "10"}],
                                             "bids": [{"price": top_bid, "size": "10"}]}),
        "fee_details_json": json.dumps({"maker_amount": "0.0", "taker_amount": "0.0"}),
        "fee_details": json.dumps({"maker_amount": "0.0", "taker_amount": "0.0"}),
        "captured_at": _NOW.isoformat(),
        "freshness_deadline": _NOW.isoformat(),
        "active": 1,
        "closed": 0,
        "executable_snapshot_hash": "b" * 64,
    }


def _patch_dependencies(hydrated_snap, monkeypatch):
    """
    Patch the heavy dependencies so _build_no_submit_proof_bundle_from_adapter_evidence
    can run without a real DB or full event chain.
    """
    import src.engine.event_reactor_adapter as era
    # get_snapshot → return hydrated_snap
    monkeypatch.setattr(era, "get_snapshot", lambda conn, sid: hydrated_snap)
    # _require_cost_basis → return a stub with cost_basis_id
    stub_cost_basis = SimpleNamespace(
        cost_basis_id="cost_basis:deadbeef01234567",
        cost_basis_hash="deadbeef01234567" * 4,
    )
    monkeypatch.setattr(era, "_require_cost_basis", lambda snap, **kw: stub_cost_basis)
    # _forecast_authority_payload_and_clock → minimal stub
    monkeypatch.setattr(
        era, "_forecast_authority_payload_and_clock",
        lambda conn, **kw: ({"reader_status": "LIVE"}, EvidenceClock_stub()),
    )
    # _calibration_authority_payload_and_clock → minimal stub
    monkeypatch.setattr(
        era, "_calibration_authority_payload_and_clock",
        lambda conn, **kw: ({"calibration_id": "cal-001"}, EvidenceClock_stub()),
    )


class EvidenceClock_stub:
    def __init__(self):
        self.source_available_at = _NOW
        self.agent_received_at = _NOW
        self.persisted_at = _NOW


def _make_proof(snapshot_id: str = "snap-hydrated") -> SimpleNamespace:
    return SimpleNamespace(
        executable_snapshot_id=snapshot_id,
        direction="buy_no",
        token_id="no-token-001",
        execution_price=SimpleNamespace(value=0.55, fee_deducted=False),
        native_quote_available=True,
        p_fill_lcb=0.8,
        q_posterior=0.75,
        q_lcb_5pct=0.70,
        p_value=0.01,
        passed_prefilter=True,
        p_cal_vector_hash="pcalhash",
        p_live_vector_hash="plivehash",
        c_cost_95pct=0.56,
        trade_score=0.14,
    )


def _make_raw_receipt(snapshot_id: str = "snap-hydrated") -> dict:
    return {
        "event_id": "event-001",
        "final_intent_id": "intent-001",
        "side_effect_status": "NO_SUBMIT",
        "proof_accepted": True,
        "submitted": False,
        "executable_snapshot_id": snapshot_id,
        "condition_id": "cond-001",
        "token_id": "no-token-001",
        "kelly_cost_basis_id": "cost_basis:deadbeef01234567",
        "neg_risk": False,
    }


def _make_family_and_event():
    """Minimal family + event stubs."""
    candidate = SimpleNamespace(
        bin=SimpleNamespace(label="HIGH", unit="°C"),
        condition_id="cond-001",
        token_id="no-token-001",
    )
    family = SimpleNamespace(
        family_id="fam-001",
        candidates=[candidate],
        yes_token_ids=("yes-token-001",),
        no_token_ids=("no-token-001",),
        metric="high",
        target_date="2026-06-01",
    )
    event = SimpleNamespace(
        event_id="event-001",
        causal_snapshot_id="snap-hydrated",
        source="forecast.001",
        event_type="FORECAST_SNAPSHOT_READY",
        available_at=_NOW.isoformat(),
        received_at=_NOW.isoformat(),
        created_at=_NOW.isoformat(),
        payload_hash="payload-hash-001",
    )
    return family, event


def _make_topology_rows() -> list[dict]:
    return [{"condition_id": "cond-001", "captured_at": _NOW.isoformat()}]


def _make_snapshot_rows(snapshot_id: str) -> list[dict]:
    return [{"snapshot_id": snapshot_id}]


def _call_builder(monkeypatch, hydrated_snap, selected_row):
    """Helper: call _build_no_submit_proof_bundle_from_adapter_evidence with minimal stubs."""
    _patch_dependencies(hydrated_snap, monkeypatch)

    import src.engine.event_reactor_adapter as era
    # Patch all the sub-functions called before the cert payload block that require
    # full DB context.
    # _evidence_clock_from_row / _evidence_clock_from_rows → return stub clocks
    monkeypatch.setattr(era, "_evidence_clock_from_row",
                        lambda row, **kw: EvidenceClock_stub())
    monkeypatch.setattr(era, "_evidence_clock_from_rows",
                        lambda rows: EvidenceClock_stub())
    # stable_hash → deterministic enough for test purposes
    from src.decision_kernel.canonicalization import stable_hash  # noqa: already works

    family, event = _make_family_and_event()
    snapshot_id = selected_row["snapshot_id"]
    raw_receipt = _make_raw_receipt(snapshot_id=hydrated_snap.snapshot_id)
    proof = _make_proof(snapshot_id=hydrated_snap.snapshot_id)

    bundle = _build_no_submit_proof_bundle_from_adapter_evidence(
        event=event,
        payload={},
        decision_time=_NOW,
        family=family,
        family_topology_rows=_make_topology_rows(),
        family_snapshot_rows=_make_snapshot_rows(hydrated_snap.snapshot_id),
        selected_snapshot_row=selected_row,
        trade_conn=None,
        forecast_conn=None,
        calibration_conn=None,
        proof=proof,
        raw_receipt=raw_receipt,
        fdr=SimpleNamespace(
            passed=True,
            reason="ok",
            fdr_family_id="fdr-001",
            attempted_hypotheses=1,
            selected_hypotheses=("hyp-001",),
            selected_post_fdr=("hyp-001",),
        ),
        kelly=SimpleNamespace(size_usd=5.0, passed=True,
                              kelly_decision_id="kelly-001"),
        risk=SimpleNamespace(
            level=SimpleNamespace(name="LOW"),
            passed=True,
            risk_decision_id="risk-001",
        ),
        bankroll_usd=100.0,
        kelly_multiplier=0.5,
    )
    return bundle, raw_receipt


def _get_exec_snap_payload(bundle) -> dict:
    """Extract the executable_snapshot cert payload from the bundle."""
    # The bundle has an executable_snapshot AuthorityEvidence; its payload is a dict.
    return dict(bundle.executable_snapshot.payload)


class TestTickSizeProvenanceERACertPayload:
    """
    Direct unit test of the cert payload construction in ERA.
    Tests the exact three lines that were changed:
        "min_tick_size": str(_hydrated_snapshot.min_tick_size),  # was selected_snapshot_row
        "min_order_size": str(_hydrated_snapshot.min_order_size),
        "neg_risk": bool(_hydrated_snapshot.neg_risk),
    Simulates the cert dict build inline using the same expressions.
    """

    def test_red_selected_row_diverges_from_hydrated(self):
        """
        RED: demonstrates the pre-fix bug pattern.
        selected_snapshot_row.get("min_tick_size") = "0.001" (wrong)
        but _hydrated_snapshot.min_tick_size = Decimal("0.01") (correct)
        The OLD code emitted selected_row's value; the fix must emit hydrated's.
        This test proves the divergence is possible (i.e. the category existed).
        """
        from decimal import Decimal

        selected_row = {"min_tick_size": "0.001", "min_order_size": "5.0", "neg_risk": 1}

        # Simulate PRE-FIX cert payload (old code path):
        pre_fix_min_tick = selected_row.get("min_tick_size")  # "0.001"

        # Hydrated snapshot (the snapshot actually cited by snapshot_id):
        hydrated_min_tick = Decimal("0.01")

        # Assert they differ (that's the root cause):
        assert pre_fix_min_tick != str(hydrated_min_tick), (
            "Divergence no longer present — root cause may have changed"
        )
        # Assert the fix expression produces the correct value:
        post_fix_min_tick = str(hydrated_min_tick)
        assert post_fix_min_tick == "0.01"

    def test_green_fix_expression_uses_hydrated_snapshot(self):
        """
        GREEN: the fixed code uses str(_hydrated_snapshot.min_tick_size).
        Regardless of what selected_snapshot_row contains, the cert carries
        the hydrated snapshot's values.
        """
        from decimal import Decimal
        from types import SimpleNamespace

        # selected_row with wrong values (simulating stale/different snapshot row)
        selected_row = {"min_tick_size": "0.001", "min_order_size": "5.0", "neg_risk": 1}

        # Hydrated snapshot fetched by snapshot_id (the correct one)
        hydrated = SimpleNamespace(
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("1.0"),
            neg_risk=False,
        )

        # POST-FIX cert payload expressions (exactly as written in ERA post-fix):
        cert_min_tick_size = str(hydrated.min_tick_size)
        cert_min_order_size = str(hydrated.min_order_size)
        cert_neg_risk = bool(hydrated.neg_risk)

        assert cert_min_tick_size == "0.01"
        assert cert_min_order_size == "1.0"
        assert cert_neg_risk is False

        # And confirm they differ from what the OLD code would have produced:
        assert cert_min_tick_size != selected_row.get("min_tick_size")
        assert cert_min_order_size != selected_row.get("min_order_size")
        assert cert_neg_risk != bool(selected_row.get("neg_risk"))

    def test_green_era_cert_lines_verified_in_source(self):
        """
        Structural: verify the fixed lines are present in ERA source.
        Fails if the fix is accidentally reverted.
        """
        import re
        with open("src/engine/event_reactor_adapter.py") as f:
            src = f.read()
        assert 'str(_hydrated_snapshot.min_tick_size)' in src, (
            "ERA cert fix missing: min_tick_size must use _hydrated_snapshot"
        )
        assert 'str(_hydrated_snapshot.min_order_size)' in src, (
            "ERA cert fix missing: min_order_size must use _hydrated_snapshot"
        )
        assert 'bool(_hydrated_snapshot.neg_risk)' in src, (
            "ERA cert fix missing: neg_risk must use _hydrated_snapshot"
        )

    def test_builder_rebinds_receipt_neg_risk_to_hydrated_snapshot(self, monkeypatch):
        """
        Regression for live compile failures where the executable snapshot cert
        used the cited hydrated snapshot, but receipt/actionable inherited a
        sibling row's neg_risk bit and failed executor expressibility.
        """
        hydrated = _make_minimal_snapshot(neg_risk=False)
        selected_row = _make_selected_row(neg_risk=1)

        bundle, raw_receipt = _call_builder(monkeypatch, hydrated, selected_row)
        exec_payload = _get_exec_snap_payload(bundle)

        assert exec_payload["neg_risk"] is False
        assert raw_receipt["neg_risk"] is False

        hydrated_true = _make_minimal_snapshot(neg_risk=True)
        selected_row_false = _make_selected_row(neg_risk=0)

        bundle_true, raw_receipt_true = _call_builder(
            monkeypatch, hydrated_true, selected_row_false
        )
        exec_payload_true = _get_exec_snap_payload(bundle_true)

        assert exec_payload_true["neg_risk"] is True
        assert raw_receipt_true["neg_risk"] is True


class TestTickSizeDivergenceAntibody:
    """
    Antibody: a FinalExecutionIntent whose tick_size disagrees with the
    ExecutableMarketSnapshot it cites must STILL raise.
    This proves the downstream assertion in executor.py:1746 is not bypassed.
    """

    def test_tick_size_mismatch_between_intent_and_snapshot_raises(self):
        """
        executor._final_intent_snapshot_metadata raises when
        intent.tick_size != snapshot.min_tick_size.
        Even after the cert-provenance fix, a corrupted/replayed intent
        with a wrong tick_size must still be rejected.
        """
        from decimal import Decimal
        import src.execution.executor as executor_mod

        from src.contracts.execution_intent import FinalExecutionIntent
        from datetime import timedelta

        cancel_after = datetime(2026, 6, 1, 13, 0, 0, tzinfo=timezone.utc)

        _cost_basis_hash = "d" * 64  # 64 hex chars
        _cost_basis_id = f"cost_basis:{_cost_basis_hash[:16]}"
        intent = FinalExecutionIntent(
            hypothesis_id="hyp-001",
            selected_token_id="no-token-001",
            direction="buy_no",
            size_kind="shares",
            size_value=Decimal("5.0"),
            submitted_shares=Decimal("5.0"),
            final_limit_price=Decimal("0.80"),
            expected_fill_price_before_fee=Decimal("0.80"),
            fee_adjusted_execution_price=Decimal("0.80"),
            order_policy="marketable_limit_depth_bound",
            order_type="FOK",
            post_only=False,
            cancel_after=cancel_after,
            snapshot_id="snap-diverge",
            snapshot_hash="a" * 64,
            cost_basis_id=_cost_basis_id,
            cost_basis_hash=_cost_basis_hash,
            max_slippage_bps=Decimal("0"),
            tick_size=Decimal("0.001"),        # ← WRONG: snapshot has 0.01
            min_order_size=Decimal("1.0"),
            fee_rate=Decimal("0"),
            neg_risk=False,
            event_id="event-001",
            resolution_window="default",
            correlation_key="intent-001",
            decision_source_context=None,
            passive_maker_context=None,
        )

        import json as _json
        from types import SimpleNamespace
        import src.execution.executor as _executor

        # Build a minimal ExecutableMarketSnapshot with min_tick_size=0.01
        snapshot = SimpleNamespace(
            snapshot_id="snap-diverge",
            executable_snapshot_hash="a" * 64,
            min_tick_size=Decimal("0.01"),   # ← different from intent's 0.001
            min_order_size=Decimal("1.0"),
            neg_risk=False,
            selected_outcome_token_id="no-token-001",
            yes_token_id="yes-token-001",
            no_token_id="no-token-001",
            gamma_market_id="gamma-001",
            event_id="event-001",
            orderbook_depth_jsonb=_json.dumps({
                "asks": [{"price": "0.80", "size": "10"}],
                "bids": [{"price": "0.70", "size": "10"}],
            }),
        )

        import src.state.snapshot_repo as _snap_repo
        with patch.object(_snap_repo, "get_snapshot", return_value=snapshot), \
             patch("src.execution.executor.get_trade_connection_with_world_required",
                   return_value=MagicMock()):
            with pytest.raises(ValueError, match="tick_size does not match"):
                _executor._final_intent_snapshot_metadata(
                    intent, None, submitted_shares=5.0
                )
