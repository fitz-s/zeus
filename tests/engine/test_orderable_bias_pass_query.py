# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: Operator directive 2026-06-04 #3 — the candidate LIST the operator
#   reviews (the arm review set) = ONLY order-able ∩ bias-PASS. Order-able = a no-submit
#   receipt EXISTS (it cleared all gates -> would submit if armed). bias-PASS =
#   mainstream_agreement_pass = 1 (forecast point within tolerance of the traded bin).
#   This is an OBSERVABILITY filter for the arm decision, NOT a trade gate.
"""Function test: order-able ∩ bias-pass query over edli_no_submit_receipts.

The query returns ONLY receipts whose mainstream_agreement_pass = 1, ordered by
created_at DESC, with city/direction/q/cost/trade_score/kelly_size/mainstream_delta
per candidate. None / 0 (unknown / fail) verdicts are EXCLUDED (unknown != pass).
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts.ops.orderable_bias_pass_candidates import query_orderable_bias_pass


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from src.state.schema.edli_no_submit_receipts_schema import ensure_table

    ensure_table(conn)
    return conn


def _insert(conn, *, receipt_id, city, direction, mainstream_agreement_pass,
            mainstream_delta, created_at, trade_score=0.05, kelly_size_usd=3.0,
            q_live=0.4, c_fee_adjusted=0.3):
    import json

    receipt_json = json.dumps({
        "city": city, "target_date": "2026-06-04", "metric": "high",
        "direction": direction, "q_live": q_live, "trade_score": trade_score,
    })
    conn.execute(
        """
        INSERT INTO edli_no_submit_receipts (
            receipt_id, event_id, decision_time, side_effect_status,
            direction, q_live, c_fee_adjusted, trade_score, kelly_size_usd,
            projection_hash, receipt_json, receipt_hash, created_at, schema_version,
            mainstream_agreement_pass, mainstream_delta
        ) VALUES (
            :receipt_id, :event_id, :decision_time, 'NO_SUBMIT',
            :direction, :q_live, :c_fee_adjusted, :trade_score, :kelly_size_usd,
            'ph', :receipt_json, :receipt_hash, :created_at, 1,
            :mainstream_agreement_pass, :mainstream_delta
        )
        """,
        {
            "receipt_id": receipt_id, "event_id": f"evt-{receipt_id}",
            "decision_time": created_at, "direction": direction,
            "q_live": q_live, "c_fee_adjusted": c_fee_adjusted,
            "trade_score": trade_score, "kelly_size_usd": kelly_size_usd,
            "receipt_json": receipt_json, "receipt_hash": f"h-{receipt_id}",
            "created_at": created_at,
            "mainstream_agreement_pass": mainstream_agreement_pass,
            "mainstream_delta": mainstream_delta,
        },
    )
    conn.commit()


def test_query_returns_only_bias_pass_receipts():
    conn = _make_db()
    # PASS (1) — should be returned.
    _insert(conn, receipt_id="r-pass", city="Wellington", direction="buy_yes",
            mainstream_agreement_pass=1, mainstream_delta=-0.6,
            created_at="2026-06-04T10:00:00+00:00")
    # FAIL (0) — excluded.
    _insert(conn, receipt_id="r-fail", city="San Francisco", direction="buy_no",
            mainstream_agreement_pass=0, mainstream_delta=-4.5,
            created_at="2026-06-04T11:00:00+00:00")
    # UNKNOWN (None) — excluded (unknown != pass).
    _insert(conn, receipt_id="r-unknown", city="Tokyo", direction="buy_no",
            mainstream_agreement_pass=None, mainstream_delta=None,
            created_at="2026-06-04T12:00:00+00:00")

    rows = query_orderable_bias_pass(conn)

    cities = {r["city"] for r in rows}
    assert cities == {"Wellington"}, (
        f"only the bias-PASS receipt must be returned, got cities={cities}"
    )
    assert len(rows) == 1


def test_query_orders_by_created_at_desc():
    conn = _make_db()
    _insert(conn, receipt_id="r-old", city="Wellington", direction="buy_yes",
            mainstream_agreement_pass=1, mainstream_delta=-0.6,
            created_at="2026-06-04T08:00:00+00:00")
    _insert(conn, receipt_id="r-new", city="Panama City", direction="buy_no",
            mainstream_agreement_pass=1, mainstream_delta=0.4,
            created_at="2026-06-04T14:00:00+00:00")

    rows = query_orderable_bias_pass(conn)

    assert [r["city"] for r in rows] == ["Panama City", "Wellington"], (
        "rows must be ordered by created_at DESC (newest first)"
    )


def test_query_exposes_review_fields():
    conn = _make_db()
    _insert(conn, receipt_id="r-1", city="Wellington", direction="buy_yes",
            mainstream_agreement_pass=1, mainstream_delta=-0.6,
            created_at="2026-06-04T10:00:00+00:00",
            trade_score=0.12, kelly_size_usd=4.5, q_live=0.55, c_fee_adjusted=0.33)

    rows = query_orderable_bias_pass(conn)
    assert len(rows) == 1
    row = rows[0]
    # The operator review set must expose city/direction/q/cost/trade_score/kelly/delta.
    for field in ("city", "direction", "q_live", "c_fee_adjusted", "trade_score",
                  "kelly_size_usd", "mainstream_delta"):
        assert field in row.keys(), f"review field {field!r} missing from query result"
    assert row["direction"] == "buy_yes"
    assert row["trade_score"] == pytest.approx(0.12)
    assert row["kelly_size_usd"] == pytest.approx(4.5)
    assert row["mainstream_delta"] == pytest.approx(-0.6)
