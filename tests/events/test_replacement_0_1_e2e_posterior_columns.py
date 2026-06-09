# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: REAUDIT_0_1.md §2 H2 + §4 (every replacement_0_1 order SQL-reconstructable, no JSON_EXTRACT).
"""H2_E2E antibody — every replacement_0_1 order is SQL-reconstructable.

The goal's e2e law: forecast(posterior_id) -> q -> q_lcb(+calibration_source) ->
edge -> size -> cert -> submit -> FILL -> settlement, all reconstructable in SQL
WITHOUT JSON_EXTRACT. Today the only durable link between a live order and the
posterior that drove it is the receipt_json blob (queryable only via
JSON_EXTRACT). These tests pin the typed columns + carry path.

Observability ONLY: these columns must NOT change any trading decision. They are
nullable (NULL on every legacy/canonical row) and omitted-when-None from
receipt_json so existing-row hashes stay byte-stable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
from src.events.reactor import EventSubmissionReceipt
from src.state.schema.edli_no_submit_receipts_schema import ensure_table


UTC = timezone.utc


def _make_receipt(
    *,
    event_id: str = "evt-e2e-001",
    final_intent_id: str = "intent-e2e-001",
    posterior_id: int | None = 4242,
    probability_authority: str | None = "replacement_0_1",
    q_lcb_calibration_source: str | None = "FORECAST_BOOTSTRAP",
) -> EventSubmissionReceipt:
    return EventSubmissionReceipt(
        submitted=False,
        event_id=event_id,
        causal_snapshot_id="snap-001",
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        condition_id="cond-001",
        token_id="tok-yes-001",
        outcome_label="YES",
        candidate_id="cand-001",
        executable_snapshot_id="es-001",
        family_id="fam-001",
        bin_label="20-21C",
        direction="buy_yes",
        q_live=0.65,
        q_lcb_5pct=0.55,
        c_fee_adjusted=0.52,
        c_cost_95pct=0.58,
        p_fill_lcb=0.70,
        trade_score=0.05,
        native_quote_available=True,
        source_status="MATCH",
        family_complete=True,
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="fdr-fam-001",
        fdr_hypothesis_count=5,
        kelly_pass=True,
        kelly_size_usd=1.50,
        kelly_cost_basis_id="cost-001",
        kelly_decision_id="kdec-001",
        risk_decision_id="rdec-001",
        final_intent_id=final_intent_id,
        side_effect_status="NO_SUBMIT",
        reason="event_bound_final_intent_no_submit",
        proof_accepted=True,
        q_source="replacement_0_1",
        q_lcb_calibration_source=q_lcb_calibration_source,
        posterior_id=posterior_id,
        probability_authority=probability_authority,
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    return conn


# --------------------------------------------------------------------------
# 1. Migration: typed columns + partial index exist after ensure_table
# --------------------------------------------------------------------------

def test_migration_adds_typed_columns() -> None:
    conn = _conn()
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(edli_no_submit_receipts)").fetchall()}
    assert "posterior_id" in cols
    assert "probability_authority" in cols
    assert "q_lcb_calibration_source" in cols


def test_migration_adds_partial_index_on_probability_authority() -> None:
    conn = _conn()
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(edli_no_submit_receipts)").fetchall()}
    assert "idx_edli_no_submit_receipts_probability_authority" in indexes


def test_migration_is_idempotent() -> None:
    conn = _conn()
    # Second ensure_table call must not raise (idempotent / duplicate-column swallowed).
    ensure_table(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(edli_no_submit_receipts)").fetchall()}
    assert "posterior_id" in cols


# --------------------------------------------------------------------------
# 2. EventSubmissionReceipt + _CandidateProof carry the fields
# --------------------------------------------------------------------------

def test_receipt_dataclass_carries_posterior_fields() -> None:
    receipt = _make_receipt()
    assert receipt.posterior_id == 4242
    assert receipt.probability_authority == "replacement_0_1"


def test_candidate_proof_carries_posterior_fields() -> None:
    from src.engine.event_reactor_adapter import _CandidateProof

    fields = _CandidateProof.__dataclass_fields__
    assert "posterior_id" in fields
    assert "probability_authority" in fields


# --------------------------------------------------------------------------
# 3. Ledger persists to typed columns — queryable WITHOUT JSON_EXTRACT
# --------------------------------------------------------------------------

def test_replacement_0_1_order_sql_reconstructable_no_json_extract() -> None:
    conn = _conn()
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(posterior_id=777, probability_authority="replacement_0_1")
    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 6, 7, 12, 0, tzinfo=UTC))

    # The §2/§4 antibody: SELECT WHERE probability_authority='replacement_0_1'
    # returns rows with posterior_id NOT NULL — NO JSON_EXTRACT.
    rows = conn.execute(
        """
        SELECT posterior_id, probability_authority, q_lcb_calibration_source, final_intent_id
        FROM edli_no_submit_receipts
        WHERE probability_authority = 'replacement_0_1'
          AND posterior_id IS NOT NULL
        """
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["posterior_id"] == 777
    assert rows[0]["probability_authority"] == "replacement_0_1"
    assert rows[0]["q_lcb_calibration_source"] == "FORECAST_BOOTSTRAP"


def test_null_posterior_id_for_canonical_rows() -> None:
    # Observability only: a canonical (non-replacement) receipt has NULL
    # posterior_id/probability_authority — the typed columns never change a
    # canonical decision, and the WHERE filter excludes them.
    conn = _conn()
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(
        event_id="evt-canon",
        final_intent_id="intent-canon",
        posterior_id=None,
        probability_authority=None,
        q_lcb_calibration_source=None,
    )
    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    row = conn.execute(
        "SELECT posterior_id, probability_authority FROM edli_no_submit_receipts WHERE final_intent_id = 'intent-canon'"
    ).fetchone()
    assert row["posterior_id"] is None
    assert row["probability_authority"] is None
    # And it is excluded from the replacement_0_1 query.
    rows = conn.execute(
        "SELECT * FROM edli_no_submit_receipts WHERE probability_authority = 'replacement_0_1'"
    ).fetchall()
    assert rows == []


# --------------------------------------------------------------------------
# 4. Hash stability: NULL posterior fields omitted from receipt_json
# --------------------------------------------------------------------------

def test_receipt_json_omits_null_posterior_fields_for_hash_stability() -> None:
    conn = _conn()
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(
        event_id="evt-canon-hash",
        final_intent_id="intent-canon-hash",
        posterior_id=None,
        probability_authority=None,
        q_lcb_calibration_source=None,
    )
    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    row = conn.execute(
        "SELECT receipt_json FROM edli_no_submit_receipts WHERE final_intent_id = 'intent-canon-hash'"
    ).fetchone()
    payload = json.loads(row["receipt_json"])
    # Omit-when-None: legacy/canonical rows keep byte-identical hashes.
    assert "posterior_id" not in payload
    assert "probability_authority" not in payload


def test_receipt_json_includes_posterior_fields_when_set() -> None:
    conn = _conn()
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(event_id="evt-repl-json", final_intent_id="intent-repl-json", posterior_id=999)
    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    row = conn.execute(
        "SELECT receipt_json FROM edli_no_submit_receipts WHERE final_intent_id = 'intent-repl-json'"
    ).fetchone()
    payload = json.loads(row["receipt_json"])
    assert payload["posterior_id"] == 999
    assert payload["probability_authority"] == "replacement_0_1"


# --------------------------------------------------------------------------
# 5. E2E: fill -> posterior link via execution_fact (no JSON_EXTRACT)
# --------------------------------------------------------------------------

def test_execution_fact_carries_posterior_id_for_fill_link() -> None:
    from src.state import db as state_db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(state_db._TRADE_CLASS_DDL)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(execution_fact)").fetchall()}
    assert "posterior_id" in cols


def _seed_confirmed_edli_fill_aggregate(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    final_intent_id: str,
    event_id: str,
) -> None:
    """Seed a realistic CONFIRMED EDLI fill aggregate the PRODUCTION reconcile reads.

    Mirrors the real producer's edli_live_order_events rows (the bridge reads
    event_type + payload_json only). This is the SAME shape the live submit
    pipeline writes; the bridge's production reconcile then joins
    edli_no_submit_receipts on final_intent_id to recover posterior_id.
    """
    rows = [
        (
            1,
            "PreSubmitRevalidated",
            "engine_adapter",
            {
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "strategy_key": "opening_inertia",
                "condition_id": "cond-001",
                "token_id": "tok-yes-001",
                "side": "BUY",
                "direction": "buy_yes",
                "native_token_side": "YES",
                "outcome_label": "YES",
                "city": "Shanghai",
                "target_date": "2026-06-07",
                "bin_label": "20-21C",
                "metric": "high",
                "unit": "C",
                "market_id": "cond-001",
                "q_live": 0.65,
                "executable_snapshot_id": "es-001",
            },
        ),
        (
            2,
            "ExecutionCommandCreated",
            "engine_adapter",
            {
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "execution_command_id": "execcmd-e2e-1",
            },
        ),
        (
            3,
            "UserTradeObserved",
            "user_channel",
            {
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "trade_status": "CONFIRMED",
                "fill_authority_state": "FILL_CONFIRMED",
                "venue_order_id": "venue-order-e2e-1",
                "filled_size": 3.0,
                "avg_fill_price": 0.53,
                "fees": 0.01,
            },
        ),
    ]
    for sequence, event_type, source_authority, payload in rows:
        event_hash = f"{aggregate_id}:{sequence}:{event_type}"
        conn.execute(
            """
            INSERT INTO edli_live_order_events (
                aggregate_event_id, aggregate_id, event_sequence, event_type,
                parent_event_hash, event_hash, payload_json, payload_hash,
                source_authority, occurred_at, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                f"edli_evt:{event_hash}",
                aggregate_id,
                sequence,
                event_type,
                None if sequence == 1 else f"{aggregate_id}:{sequence-1}",
                event_hash,
                json.dumps(payload, sort_keys=True, default=str),
                f"ph:{event_hash}",
                source_authority,
                "2026-06-07T12:00:00+00:00",
                "2026-06-07T12:00:01+00:00",
            ),
        )


def test_e2e_chain_posterior_to_fill_reconstructable() -> None:
    """Full e2e via the PRODUCTION reconcile path — NO manual posterior injection.

    Drives ``materialize_position_current_from_edli_fill`` (the primary entry-fill
    reconcile). Production resolves the fill's final_intent_id, joins the
    actually-written ``edli_no_submit_receipts.posterior_id`` (written here by the
    real ``EdliNoSubmitReceiptLedger``), and persists it onto
    ``execution_fact.posterior_id`` via ``log_execution_fact``. The test asserts
    the ACTUAL value production wrote and reconstructs the chain via the
    receipt-join — proving the column is populated by the real path, not by a
    test-only value the production code never produces.
    """
    from src.events.edli_position_bridge import materialize_position_current_from_edli_fill
    from src.state.db import init_schema, init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # init_schema creates the full single-DB unit graph: edli_live_order_events,
    # edli_no_submit_receipts, position_current/_events. init_schema_trade_only runs
    # the real trade-class DDL + the production execution_fact.posterior_id migration
    # (db.py:init_schema_trade_only) — so this exercises the actual schema path, not
    # a hand-rolled column. The bridge resolves tables unqualified on this conn (no
    # ATTACHed world), so the receipt-join runs locally exactly as in
    # production-on-attached-world.
    init_schema(conn)
    init_schema_trade_only(conn)

    final_intent_id = "intent-e2e-chain"
    event_id = "evt-e2e-chain"

    # 1. The REAL receipt writer persists the typed posterior_id (the source of
    #    truth the production reconcile joins). No execution_fact write here.
    ledger = EdliNoSubmitReceiptLedger(conn)
    receipt = _make_receipt(
        event_id=event_id,
        final_intent_id=final_intent_id,
        posterior_id=12321,
    )
    ledger.insert_idempotent(receipt, decision_time=datetime(2026, 6, 7, 12, 0, tzinfo=UTC))

    # 2. Seed the CONFIRMED fill aggregate sharing the same final_intent_id.
    _seed_confirmed_edli_fill_aggregate(
        conn,
        aggregate_id="agg-e2e-chain-1",
        final_intent_id=final_intent_id,
        event_id=event_id,
    )

    # 3. PRODUCTION reconcile: this is the only call that writes execution_fact.
    #    It internally looks up the receipt's posterior_id and passes it through.
    result = materialize_position_current_from_edli_fill(conn, "agg-e2e-chain-1")
    assert result is not None

    # 4. The fill row's posterior_id was populated BY PRODUCTION (not injected).
    fill = conn.execute(
        "SELECT posterior_id, fill_price FROM execution_fact WHERE intent_id = ?",
        (final_intent_id,),
    ).fetchone()
    assert fill is not None
    assert fill["posterior_id"] == 12321, (
        "execution_fact.posterior_id must be populated by the production reconcile "
        "(receipt join), not left NULL — this is the dead-column antibody"
    )

    # 5. Reconstruct the full chain via the receipt-join — NO JSON_EXTRACT.
    chain = conn.execute(
        """
        SELECT r.posterior_id AS receipt_posterior_id,
               r.probability_authority,
               r.q_lcb_calibration_source,
               f.posterior_id AS fill_posterior_id,
               f.fill_price
        FROM edli_no_submit_receipts r
        JOIN execution_fact f ON f.intent_id = r.final_intent_id
        WHERE r.probability_authority = 'replacement_0_1'
        """
    ).fetchall()
    assert len(chain) == 1
    assert chain[0]["receipt_posterior_id"] == 12321
    assert chain[0]["fill_posterior_id"] == 12321
    assert chain[0]["fill_posterior_id"] == chain[0]["receipt_posterior_id"]
    assert chain[0]["fill_price"] == pytest.approx(0.53)


def test_e2e_fill_posterior_id_null_when_no_receipt_failsoft() -> None:
    """Fail-soft proof: a CONFIRMED fill with NO matching receipt reconciles
    normally and leaves execution_fact.posterior_id NULL — the receipt-miss never
    blocks or alters the fill.
    """
    from src.events.edli_position_bridge import materialize_position_current_from_edli_fill
    from src.state.db import init_schema, init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)

    final_intent_id = "intent-e2e-noreceipt"
    # NO receipt inserted for this final_intent_id.
    _seed_confirmed_edli_fill_aggregate(
        conn,
        aggregate_id="agg-e2e-noreceipt-1",
        final_intent_id=final_intent_id,
        event_id="evt-e2e-noreceipt",
    )

    result = materialize_position_current_from_edli_fill(conn, "agg-e2e-noreceipt-1")
    # The fill is reconciled normally (not blocked by the receipt miss).
    assert result is not None
    assert result["shares"] == pytest.approx(3.0)
    assert result["avg_fill_price"] == pytest.approx(0.53)

    fill = conn.execute(
        "SELECT posterior_id, fill_price, shares FROM execution_fact WHERE intent_id = ?",
        (final_intent_id,),
    ).fetchone()
    assert fill is not None
    # Receipt-miss → posterior_id stays NULL (observability only, fail-soft).
    assert fill["posterior_id"] is None
    # The fill economics are untouched by the posterior lookup.
    assert fill["fill_price"] == pytest.approx(0.53)
    assert fill["shares"] == pytest.approx(3.0)
