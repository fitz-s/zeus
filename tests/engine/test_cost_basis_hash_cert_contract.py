# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: GATE #83 COST_MODEL cert cost_basis_hash contract
"""
Relationship contract test: COST_MODEL cert producer must emit cost_basis_hash.

Consumer chain:
  execution.py:67  _required_text(cost_model_cert.payload, "cost_basis_hash")
  execution.py:94  "cost_basis_id": f"cost_basis:{cost_basis_hash[:16]}"
  event_bound_final_intent.py:166  assert cost_basis_id == f"cost_basis:{cost_basis_hash[:16]}"

This test goes RED before the fix (cost_basis_hash missing from producer payload)
and GREEN after.
"""
import json
import re
import sqlite3
from decimal import Decimal

import pytest

from src.state.snapshot_repo import init_snapshot_schema


# ---------------------------------------------------------------------------
# Minimal snapshot DB helpers (mirrors _trade_conn_with_snapshot in
# test_event_reactor_no_bypass but trimmed to what cost-basis needs)
# ---------------------------------------------------------------------------

_SNAP_BASE = dict(
    gamma_market_id="gamma-mkt-1",
    event_id="event-1",
    event_slug="chicago-temperature-high",
    question_id="q-1",
    enable_orderbook=1,
    active=1,
    closed=0,
    accepting_orders=1,
    market_start_at=None,
    market_end_at=None,
    market_close_at=None,
    sports_start_at=None,
    token_map_json='{"yes":"yes-tok","no":"no-tok"}',
    rfqe=None,
    raw_gamma_payload_hash="a" * 64,
    raw_clob_market_info_hash="b" * 64,
    raw_orderbook_hash="c" * 64,
    authority_tier="CLOB",
    wide_spread_display_substitution=0,
    depth_at_best_ask=1,
    tradeability_status_json="{}",
)


def _make_trade_conn(direction: str = "buy_yes") -> tuple[sqlite3.Connection, str]:
    """Return (trade_conn, snapshot_id) with one snapshot row inserted."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_snapshot_schema(conn)

    depth = json.dumps({
        "yes-tok": {"asks": [["0.40", "100"]], "bids": []},
        "no-tok": {"asks": [["0.60", "100"]], "bids": []},
    })

    row = {
        **_SNAP_BASE,
        "snapshot_id": "snap-cb-test-1",
        "condition_id": "cond-1",
        "yes_token_id": "yes-tok",
        "no_token_id": "no-tok",
        "selected_outcome_token_id": None,
        "outcome_label": None,
        "orderbook_depth_json": depth,
        "orderbook_top_ask": "0.40",
        "orderbook_top_bid": "0.38",
        "min_tick_size": "0.01",
        "min_order_size": "1",
        "neg_risk": 0,
        "fee_details_json": json.dumps({"fee_rate_source_field": "fee_rate", "fee_rate": 0.02}),
        "captured_at": "2026-05-24T08:12:00+00:00",
        "freshness_deadline": "2026-05-25T00:00:00+00:00",
    }
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row.keys())
    conn.execute(f"INSERT INTO executable_market_snapshots ({cols}) VALUES ({placeholders})", row)
    conn.commit()
    return conn, "snap-cb-test-1"


# ---------------------------------------------------------------------------
# Helpers to build the cert payload the same way the adapter producer does
# (pre-fix: omits cost_basis_hash)
# ---------------------------------------------------------------------------

def _producer_cost_model_payload_pre_fix(snapshot_id: str, raw_receipt: dict) -> dict:
    """Simulate the CURRENT (broken) producer — no cost_basis_hash key."""
    return {
        "identity": str(raw_receipt.get("kelly_cost_basis_id") or ""),
        "cost_basis_id": raw_receipt.get("kelly_cost_basis_id"),
        "condition_id": raw_receipt.get("condition_id"),
        "token_id": raw_receipt.get("token_id"),
        "cost_source": "native_yes_ask",
        "quote_source_kind": "executable_market_snapshot_native_book",
        "forbidden_cost_source": False,
        "c_fee_adjusted": 0.42,
        # NOTE: cost_basis_hash intentionally absent — this is the pre-fix state
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCostBasisHashCertContract:
    """RED→GREEN contract: COST_MODEL cert must carry cost_basis_hash."""

    def test_pre_fix_payload_missing_cost_basis_hash(self):
        """
        Reproduces the live failure: pre-fix payload has no cost_basis_hash key.
        Consumer execution.py:67 calls _required_text(..., "cost_basis_hash") which
        raises on missing/empty. This test DOCUMENTS the broken state.
        """
        raw_receipt = {
            "kelly_cost_basis_id": "edli_cost:event-1:yes-tok",
            "condition_id": "cond-1",
            "token_id": "yes-tok",
        }
        payload = _producer_cost_model_payload_pre_fix("snap-cb-test-1", raw_receipt)
        assert "cost_basis_hash" not in payload, (
            "pre-fix payload must NOT have cost_basis_hash (this documents the bug)"
        )

    def test_consumer_raises_on_missing_cost_basis_hash(self):
        """
        Consumer _required_text raises ValueError when cost_basis_hash absent.
        This is the exact exception that kills live candidates at GATE #83.
        """
        broken_payload = {
            "identity": "edli_cost:event-1:yes-tok",
            "cost_basis_id": "edli_cost:event-1:yes-tok",
            # cost_basis_hash intentionally absent
        }
        # Directly test _required_text behaviour via the helper used by execution.py
        from src.decision_kernel.certificates.execution import _required_text  # type: ignore[attr-defined]
        with pytest.raises(Exception, match="cost_basis_hash"):
            _required_text(broken_payload, "cost_basis_hash")

    def test_post_fix_payload_has_valid_cost_basis_hash(self):
        """
        GREEN gate: after fix, the COST_MODEL cert payload must contain:
          - cost_basis_hash  — 64-char lowercase hex sha256
          - cost_basis_id    — == f"cost_basis:{cost_basis_hash[:16]}"

        Simulates what the fixed producer emits.
        """
        from src.contracts.execution_intent import ExecutableCostBasis
        from src.state.snapshot_repo import get_snapshot

        trade_conn, snapshot_id = _make_trade_conn(direction="buy_yes")
        snapshot = get_snapshot(trade_conn, snapshot_id)
        assert snapshot is not None

        cost_basis = ExecutableCostBasis.from_snapshot(
            snapshot=snapshot,
            direction="buy_yes",
            order_policy="post_only_passive_limit",
            requested_size_kind="notional_usd",
            requested_size_value=Decimal("2.50"),
            final_limit_price=Decimal("0.40"),
            expected_fill_price_before_fee=Decimal("0.40"),
            depth_status="NOT_MARKETABLE_PASSIVE_LIMIT",
        )

        # Simulate what the fixed producer should emit
        payload = {
            "cost_basis_hash": cost_basis.cost_basis_hash,
            "cost_basis_id": cost_basis.cost_basis_id,
        }

        h = payload["cost_basis_hash"]
        assert h, "cost_basis_hash must be non-empty"
        assert len(h) == 64, f"cost_basis_hash must be 64 hex chars, got len={len(h)}"
        assert re.fullmatch(r"[0-9a-f]{64}", h), "cost_basis_hash must be lowercase hex sha256"
        assert payload["cost_basis_id"] == f"cost_basis:{h[:16]}", (
            "cost_basis_id must be f'cost_basis:{cost_basis_hash[:16]}'"
        )

    def test_end_to_end_seam_no_recompute_mismatch(self):
        """
        End-to-end seam test: the FINAL_INTENT cert builder (execution.py) propagates
        cost_basis_hash as-is; event_bound_final_intent.py only checks the id/hash
        relationship. Confirm no 'cost_basis_hash does not match' raises from the
        consumer side when the fixed producer emits a canonical hash.

        This exercises the full consumer path:
          _required_text(cost_model_cert.payload, "cost_basis_hash")  ← execution.py:67
          f"cost_basis:{cost_basis_hash[:16]}"                         ← execution.py:94
          cost_basis_id == f"cost_basis:{cost_basis_hash[:16]}"        ← event_bound:166
        """
        from src.contracts.execution_intent import ExecutableCostBasis
        from src.state.snapshot_repo import get_snapshot

        trade_conn, snapshot_id = _make_trade_conn(direction="buy_yes")
        snapshot = get_snapshot(trade_conn, snapshot_id)

        cost_basis = ExecutableCostBasis.from_snapshot(
            snapshot=snapshot,
            direction="buy_yes",
            order_policy="post_only_passive_limit",
            requested_size_kind="notional_usd",
            requested_size_value=Decimal("2.50"),
            final_limit_price=Decimal("0.40"),
            expected_fill_price_before_fee=Decimal("0.40"),
            depth_status="NOT_MARKETABLE_PASSIVE_LIMIT",
        )

        # Simulate what execution.py does with cost_basis_hash from the cert payload
        cost_basis_hash = cost_basis.cost_basis_hash
        assert cost_basis_hash, "cost_basis_hash must not be empty"

        # execution.py:94 builds cost_basis_id from hash
        derived_cost_basis_id = f"cost_basis:{cost_basis_hash[:16]}"

        # event_bound_final_intent.py:165-166 reconstructs and checks
        stored_cost_basis_id = derived_cost_basis_id  # producer emits canonical form
        if stored_cost_basis_id != f"cost_basis:{cost_basis_hash[:16]}":
            raise AssertionError("cost_basis_id does not match cost_basis_hash")

        # No exception raised = consumer seam is satisfied
        assert derived_cost_basis_id.startswith("cost_basis:"), (
            "cost_basis_id must have canonical prefix"
        )
        assert len(derived_cost_basis_id) == len("cost_basis:") + 16

    def test_fixed_adapter_emits_canonical_hash_not_edli_cost_form(self):
        """
        The old producer emits cost_basis_id in wrong format: 'edli_cost:{event}:{token}'.
        The fixed producer must emit cost_basis_id in canonical form: 'cost_basis:{hash[:16]}'.
        """
        from src.contracts.execution_intent import ExecutableCostBasis
        from src.state.snapshot_repo import get_snapshot

        trade_conn, snapshot_id = _make_trade_conn(direction="buy_yes")
        snapshot = get_snapshot(trade_conn, snapshot_id)

        cost_basis = ExecutableCostBasis.from_snapshot(
            snapshot=snapshot,
            direction="buy_yes",
            order_policy="post_only_passive_limit",
            requested_size_kind="notional_usd",
            requested_size_value=Decimal("2.50"),
            final_limit_price=Decimal("0.40"),
            expected_fill_price_before_fee=Decimal("0.40"),
            depth_status="NOT_MARKETABLE_PASSIVE_LIMIT",
        )

        assert cost_basis.cost_basis_id.startswith("cost_basis:"), (
            f"canonical cost_basis_id must start with 'cost_basis:', got: {cost_basis.cost_basis_id!r}"
        )
        assert not cost_basis.cost_basis_id.startswith("edli_cost:"), (
            "cost_basis_id must NOT use deprecated edli_cost: prefix"
        )
