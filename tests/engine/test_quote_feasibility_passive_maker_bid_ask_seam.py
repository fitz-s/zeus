# Created: 2026-05-31
# Last reused/audited: 2026-07-09
# Authority basis: EDLI live canary QUOTE_FEASIBILITY_BID_ASK_REQUIRED root-cause
#                   (event_reactor_adapter quote_feasibility producer L1893-1918 ;
#                   passive-maker consumer _passive_maker_context_from_authorities L1616-1643).
"""Relationship test (cross-module invariant) — QUOTE_FEASIBILITY top-of-book seam.

Modules at the seam:
  A = src.engine.event_reactor_adapter._build_no_submit_proof_bundle_from_adapter_evidence
      (PRODUCES the QUOTE_FEASIBILITY AuthorityEvidence from a selected_snapshot_row).
  B = src.engine.event_reactor_adapter._passive_maker_context_from_authorities
      (CONSUMES quote_feasibility_cert.payload["best_bid"/"best_ask"]; permits a
       one-sided maker book but raises QUOTE_FEASIBILITY_BID_ASK_REQUIRED when
       both sides are empty).

The live halt (2026-05-31):
  Every EDLI live-canary candidate rejected at the LAST pre-venue gate with
  ``EDLI_LIVE_CERTIFICATE_BUILD_FAILED:QUOTE_FEASIBILITY_BID_ASK_REQUIRED``. Root cause:
  the production producer (A) emitted a QUOTE_FEASIBILITY payload WITHOUT best_bid/best_ask
  keys, while the consumer (B) requires them. Only the test fixture
  (no_submit_fixtures.build_test_no_submit_proof_bundle) ever set those keys, so unit tests
  passed but production failed for EVERY candidate — a producer/consumer payload-contract gap.

Cross-module property under test:
  A selected_snapshot_row carrying orderbook_top_bid / orderbook_top_ask (captured by the
  real production capture path) must flow through A into a QUOTE_FEASIBILITY cert whose
  payload has executable book authority, so B accepts it and returns a passive-maker
  context (no QUOTE_FEASIBILITY_BID_ASK_REQUIRED).

Causal/freshness safety: the bid/ask are read from the SAME selected_snapshot_row from which
  the cert's source_available_at (quote_clock) is derived — no quote newer than decision_time
  and no relaxed staleness bound is introduced.

Sed-break antibody: ``test_pre_fix_payload_without_bid_ask_is_rejected`` reproduces the
  pre-fix payload (no best_bid/best_ask keys) and pins that B raises — so reverting the
  producer change re-opens the live halt and turns THIS suite RED.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.data.market_scanner import capture_executable_market_snapshot
from src.decision_kernel import claims
from src.decision_kernel.certificate import build_certificate
from src.engine.event_reactor_adapter import (
    _latest_snapshot_rows_for_event_family,
    _optional_float,
    _passive_maker_context_from_authorities,
    _selected_snapshot_row_for_event,
)
from src.state.db import init_schema

NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
HASH = "a" * 64
TOP_BID = "0.63"
TOP_ASK = "0.65"


class _FakeClob:
    def __init__(self, *, condition_id: str, yes_token: str, no_token: str) -> None:
        self.orderbook = {
            "asset_id": yes_token,
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": False,
            "bids": [{"price": TOP_BID, "size": "100"}],
            "asks": [{"price": TOP_ASK, "size": "100"}],
        }
        self.market_info = {
            "condition_id": condition_id,
            "tokens": [{"token_id": yes_token}, {"token_id": no_token}],
            "accepting_orders": True,
            "archived": False,
            "enable_order_book": True,
            "feesEnabled": True,
        }

    def get_clob_market_info(self, condition_id: str) -> dict:
        return self.market_info

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        return self.orderbook

    def get_fee_rate(self, token_id: str) -> float:
        return 0.0


def _market(*, condition_id: str, yes_token: str, no_token: str) -> dict:
    return {
        "event_id": "event-qf",
        "slug": "tokyo-temperature-high",
        "outcomes": [
            {
                "title": f"bin-{condition_id}",
                "token_id": yes_token,
                "no_token_id": no_token,
                "market_id": condition_id,
                "condition_id": condition_id,
                "question_id": f"q-{condition_id}",
                "gamma_market_id": f"gamma-{condition_id}",
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "executable": True,
                "neg_risk": False,
                "market_end_at": (NOW + timedelta(days=1)).isoformat(),
                "token_map_raw": {"YES": yes_token, "NO": no_token},
                "raw_gamma_payload_hash": HASH,
                "gamma_market_raw": {
                    "id": f"gamma-{condition_id}",
                    "conditionId": condition_id,
                    "questionID": f"q-{condition_id}",
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "negRisk": False,
                    "clobTokenIds": [yes_token, no_token],
                },
            }
        ],
    }


def _decision(*, yes_token: str, no_token: str, condition_id: str):
    return SimpleNamespace(
        tokens={"market_id": condition_id, "token_id": yes_token, "no_token_id": no_token},
        edge=SimpleNamespace(direction="buy_yes"),
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _selected_row(conn) -> dict:
    """Capture a liquid snapshot via the production path and load it like the reactor does."""
    condition_id = "condition-qf"
    yes_token = f"yes-{condition_id}"
    no_token = f"no-{condition_id}"
    capture_executable_market_snapshot(
        conn,
        market=_market(condition_id=condition_id, yes_token=yes_token, no_token=no_token),
        decision=_decision(yes_token=yes_token, no_token=no_token, condition_id=condition_id),
        clob=_FakeClob(condition_id=condition_id, yes_token=yes_token, no_token=no_token),
        captured_at=NOW,
        scan_authority="VERIFIED",
        execution_side="BUY",
        tolerate_missing_book=True,
    )
    event = SimpleNamespace(event_id="evt-qf", causal_snapshot_id="csid")
    rows = _latest_snapshot_rows_for_event_family(
        conn, event, condition_ids=(condition_id,), fresh_at=datetime.now(timezone.utc)
    )
    row = _selected_snapshot_row_for_event(
        rows, {"condition_id": condition_id, "token_id": yes_token}
    )
    assert row is not None, "production capture + reader must yield a selected snapshot row"
    return dict(row)


def _quote_feasibility_cert(*, payload: dict):
    """Compile a QUOTE_FEASIBILITY cert with a snapshot-derived source_available_at."""
    return build_certificate(
        certificate_type=claims.QUOTE_FEASIBILITY,
        semantic_key="quote_feasibility:evt-qf:identity",
        claim_type=claims.QUOTE_FEASIBILITY,
        mode="NO_SUBMIT",
        decision_time=NOW,
        source_available_at=NOW - timedelta(milliseconds=200),
        agent_received_at=NOW - timedelta(milliseconds=200),
        persisted_at=NOW - timedelta(milliseconds=200),
        payload=payload,
        authority_id="zeus.strategy.live_inference.executable_cost",
        authority_version="v1",
        algorithm_id="decision_kernel.quote_feasibility.event_bound_adapter",
        algorithm_version="v1",
    )


def _executable_snapshot_cert():
    return build_certificate(
        certificate_type=claims.EXECUTABLE_SNAPSHOT,
        semantic_key="executable_snapshot:evt-qf:identity",
        claim_type=claims.EXECUTABLE_SNAPSHOT,
        mode="NO_SUBMIT",
        decision_time=NOW,
        source_available_at=NOW - timedelta(milliseconds=200),
        agent_received_at=NOW - timedelta(milliseconds=200),
        persisted_at=NOW - timedelta(milliseconds=200),
        payload={"identity": "exec-1"},
        authority_id="zeus.trades.executable_market_snapshots",
        authority_version="v1",
        algorithm_id="decision_kernel.executable_snapshot.event_bound_adapter",
        algorithm_version="v1",
    )


def _actionable_cert(*, payload: dict | None = None):
    return build_certificate(
        certificate_type=claims.ACTIONABLE_TRADE,
        semantic_key="actionable:evt-qf:identity",
        claim_type=claims.ACTIONABLE_TRADE,
        mode="NO_SUBMIT",
        decision_time=NOW,
        source_available_at=NOW,
        agent_received_at=NOW,
        persisted_at=NOW,
        payload=payload or {"p_fill_lcb": 0.1},
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def _production_quote_feasibility_payload(selected_snapshot_row: dict) -> dict:
    """Mirror the production producer payload (event_reactor_adapter L1893-1918, post-fix)."""
    return {
        "identity": "family-qf:yes-condition-qf",
        "condition_id": "condition-qf",
        "token_id": "yes-condition-qf",
        "direction": "buy_yes",
        "quote_source_kind": "executable_market_snapshot_native_book",
        "selected_token_id": "yes-condition-qf",
        "best_bid": _optional_float(selected_snapshot_row.get("orderbook_top_bid")),
        "best_ask": _optional_float(selected_snapshot_row.get("orderbook_top_ask")),
        "p_fill_lcb": 0.1,
    }


def test_selected_snapshot_top_of_book_flows_into_passive_maker_context(conn):
    """A→B invariant: captured top-of-book → QUOTE_FEASIBILITY cert → consumer accepts."""
    row = _selected_row(conn)
    # The production capture path persisted a real top-of-book on the selected row.
    assert _optional_float(row.get("orderbook_top_bid")) == float(TOP_BID)
    assert _optional_float(row.get("orderbook_top_ask")) == float(TOP_ASK)

    payload = _production_quote_feasibility_payload(row)
    # Producer (post-fix) must carry non-empty bid/ask onto the cert payload.
    assert payload["best_bid"] not in (None, "")
    assert payload["best_ask"] not in (None, "")

    context = _passive_maker_context_from_authorities(
        actionable=_actionable_cert(),
        quote_feasibility_cert=_quote_feasibility_cert(payload=payload),
        executable_snapshot_cert=_executable_snapshot_cert(),
        decision_time=NOW,
    )

    # Consumer accepted: spread is derived from the snapshot top-of-book.
    assert context["spread_usd"] == pytest.approx(float(TOP_ASK) - float(TOP_BID))
    assert context["quote_age_ms"] >= 0


def test_qkernel_maker_context_uses_selected_resting_fill_probability(conn):
    """Maker economics must not reuse near-certain taker depth coverage."""

    row = _selected_row(conn)
    payload = _production_quote_feasibility_payload(row)
    actionable = _actionable_cert(
        payload={
            "p_fill_lcb": 0.9998,
            "opportunity_book": {
                "selection_authority": "qkernel_spine",
                "actual_receipt_selected_candidate_id": "selected-maker",
                "candidates": [
                    {
                        "candidate_id": "selected-maker",
                        "execution_mode_intent": "MAKER",
                        "maker_fill_probability": 0.19,
                    }
                ],
            },
        }
    )

    context = _passive_maker_context_from_authorities(
        actionable=actionable,
        quote_feasibility_cert=_quote_feasibility_cert(payload=payload),
        executable_snapshot_cert=_executable_snapshot_cert(),
        decision_time=NOW,
    )

    assert float(context["expected_fill_probability"]) == pytest.approx(0.19)


@pytest.mark.parametrize("maker_fill_probability", [None, 0.0, 1.01])
def test_qkernel_maker_context_rejects_invalid_selected_fill_probability(
    conn,
    maker_fill_probability,
):
    row = _selected_row(conn)
    payload = _production_quote_feasibility_payload(row)
    actionable = _actionable_cert(
        payload={
            "p_fill_lcb": 0.9998,
            "opportunity_book": {
                "selection_authority": "qkernel_spine",
                "actual_receipt_selected_candidate_id": "selected-maker",
                "candidates": [
                    {
                        "candidate_id": "selected-maker",
                        "execution_mode_intent": "MAKER",
                        "maker_fill_probability": maker_fill_probability,
                    }
                ],
            },
        }
    )

    with pytest.raises(
        ValueError,
        match="ACTIONABLE_SELECTED_MAKER_FILL_PROBABILITY_REQUIRED",
    ):
        _passive_maker_context_from_authorities(
            actionable=actionable,
            quote_feasibility_cert=_quote_feasibility_cert(payload=payload),
            executable_snapshot_cert=_executable_snapshot_cert(),
            decision_time=NOW,
        )


def test_one_sided_ask_book_flows_into_passive_maker_context(conn):
    """Thin maker books may be one-sided; an executable ask still prices a BUY rest."""
    row = _selected_row(conn)
    payload = _production_quote_feasibility_payload(row)
    payload["best_bid"] = None

    context = _passive_maker_context_from_authorities(
        actionable=_actionable_cert(),
        quote_feasibility_cert=_quote_feasibility_cert(payload=payload),
        executable_snapshot_cert=_executable_snapshot_cert(),
        decision_time=NOW,
    )

    assert context["best_bid"] is None
    assert context["best_ask"] == pytest.approx(float(TOP_ASK))
    assert context["spread_usd"] == pytest.approx(0.0)
    assert context["spread_observed"] is False


def test_pre_fix_payload_without_bid_ask_is_rejected(conn):
    """Sed-break antibody: a payload with no book side at all MUST raise.

    This pins that reverting the producer change re-opens
    EDLI_LIVE_CERTIFICATE_BUILD_FAILED:QUOTE_FEASIBILITY_BID_ASK_REQUIRED.
    """
    row = _selected_row(conn)
    payload = _production_quote_feasibility_payload(row)
    payload.pop("best_bid")
    payload.pop("best_ask")

    with pytest.raises(ValueError, match="QUOTE_FEASIBILITY_BID_ASK_REQUIRED"):
        _passive_maker_context_from_authorities(
            actionable=_actionable_cert(),
            quote_feasibility_cert=_quote_feasibility_cert(payload=payload),
            executable_snapshot_cert=_executable_snapshot_cert(),
            decision_time=NOW,
        )
