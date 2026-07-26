# Created: 2026-07-26
# Last reused/audited: 2026-07-26
# Authority basis: 2026-07-26 operator law "the learning-curve machinery is not
#   first-principles — decouple it, do not repair it" + LX-T3
#   (docs/rebuild/local_ledger_excision_2026-07-12.md round-2 delta) + AGENTS.md
#   INV-47 (a gate with no consumer and no reset path is presumed defective).
"""RED-on-revert antibodies for the EDLI learning-gate excision.

Each test fails if the excised shape is restored:
  1. no learning-admission column may exist on edli_live_profit_audit;
  2. the execution-quality metric keeps its honest name and its value;
  3. the writer may not name settlement_outcome/pnl_usd, so a re-projection
     can never NULL a historical settlement grade;
  4. kelly_size_usd is stamped from the certificate that authorized the order;
  5. columns no writer can fill do not exist.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.events.live_profit_audit import (
    LiveProfitAuditLedger,
    compute_fill_alpha_gap_from_authorities,
    record_edli_live_profit_audit_from_aggregate,
)
from src.state.schema.edli_live_profit_audit_schema import (
    _RETIRED_COLUMNS,
    ensure_table,
)
from src.state.table_registry import required_columns_for

CERT_HASH = "ab" * 32
COST_HASH = "cd" * 32
COND = "0xcondition"
TOKEN = "tok-1"


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {str(r[1]) for r in conn.execute("PRAGMA table_info(edli_live_profit_audit)")}


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_table(c)
    return c


def test_no_learning_admission_column_survives(conn: sqlite3.Connection) -> None:
    """A learning gate on this table graded a fill against our own belief and had
    no consumer. Learning admission belongs to SettlementResolution."""
    cols = _columns(conn)
    assert "learning_eligible" not in cols
    assert "promotion_eligible" not in cols
    indexes = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='edli_live_profit_audit'"
        )
    }
    assert not any("learning" in i or "promotion" in i for i in indexes)


def test_columns_no_writer_can_fill_do_not_exist(conn: sqlite3.Connection) -> None:
    """0/5368 live rows carried these; nothing produces them."""
    cols = _columns(conn)
    assert "live_cap_notional" not in cols
    assert "post_fill_mark" not in cols


def test_registry_does_not_require_retired_columns() -> None:
    """Boot registry must not demand columns that the schema owner retires."""
    required = required_columns_for("edli_live_profit_audit")
    assert required is not None
    declared = {column.name for column in required}
    assert declared.isdisjoint(_RETIRED_COLUMNS)


def test_execution_quality_metric_keeps_its_honest_name(conn: sqlite3.Connection) -> None:
    """The metric has no settlement term, so it may not be named for realization."""
    cols = _columns(conn)
    assert {"fill_alpha_gap", "fill_alpha_gap_usd"} <= cols
    assert "realized_edge" not in cols
    assert "edge_value_usd" not in cols


def test_legacy_rename_preserves_historical_values() -> None:
    """The pre-rename corpus survives the rename; a fresh column beside the old
    one would silently orphan 401 live rows of execution-quality history."""
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE edli_live_profit_audit ("
        "audit_id TEXT NOT NULL PRIMARY KEY, event_id TEXT NOT NULL,"
        "aggregate_id TEXT NOT NULL, condition_id TEXT NOT NULL,"
        "token_id TEXT NOT NULL, order_lifecycle_state TEXT NOT NULL,"
        "realized_edge REAL, edge_value_usd REAL,"
        "learning_eligible INTEGER NOT NULL DEFAULT 0,"
        "created_at TEXT NOT NULL, schema_version INTEGER NOT NULL)"
    )
    c.execute(
        "INSERT INTO edli_live_profit_audit VALUES "
        "('a','e','g','c','t','CONFIRMED',0.25,2.5,1,'2026-07-01T00:00:00Z',1)"
    )
    ensure_table(c)
    row = c.execute(
        "SELECT fill_alpha_gap, fill_alpha_gap_usd FROM edli_live_profit_audit"
    ).fetchone()
    assert row == (0.25, 2.5)
    assert "learning_eligible" not in _columns(c)
    ensure_table(c)  # idempotent


def test_writer_cannot_null_a_historical_settlement_grade(conn: sqlite3.Connection) -> None:
    """settlement_outcome/pnl_usd are frozen legacy. Naming them in the INSERT
    made every re-projection overwrite a real grade with NULL."""
    ledger = LiveProfitAuditLedger(conn)
    audit_id = ledger.insert_record(
        event_id="e1",
        aggregate_id="agg-1",
        condition_id=COND,
        token_id=TOKEN,
        order_lifecycle_state="CONFIRMED",
    )
    conn.execute(
        "UPDATE edli_live_profit_audit SET settlement_outcome='WON', pnl_usd=6.5 "
        "WHERE audit_id=?",
        (audit_id,),
    )
    ledger.insert_record(
        event_id="e1",
        aggregate_id="agg-1",
        condition_id=COND,
        token_id=TOKEN,
        order_lifecycle_state="CONFIRMED",
    )
    row = conn.execute(
        "SELECT settlement_outcome, pnl_usd FROM edli_live_profit_audit WHERE audit_id=?",
        (audit_id,),
    ).fetchone()
    assert (row[0], row[1]) == ("WON", 6.5)


def test_writer_rejects_a_field_it_cannot_persist(conn: sqlite3.Connection) -> None:
    """record.get(field) silently dropped unknown kwargs — that is how three
    columns stayed 0/5368 non-null while the schema advertised them."""
    with pytest.raises(ValueError, match="UNKNOWN_FIELDS:pnl_usd"):
        LiveProfitAuditLedger(conn).insert_record(
            event_id="e1",
            aggregate_id="agg-1",
            condition_id=COND,
            token_id=TOKEN,
            order_lifecycle_state="CONFIRMED",
            pnl_usd=1.0,
        )


def test_fill_alpha_gap_is_belief_vs_fill_not_settlement() -> None:
    """Explicit statement of what the number means: it rises when q is higher,
    which is exactly why it may not gate learning."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _seed_certificates(c, q_live=0.60)
    out = compute_fill_alpha_gap_from_authorities(
        conn=c,
        cost_model_cert_hash=COST_HASH,
        expected_edge_cert_hash=CERT_HASH,
        fill_event_hash="fh",
        pre_submit={"condition_id": COND, "token_id": TOKEN, "side": "BUY"},
        fill_payload={"avg_fill_price": 0.40, "filled_size": 10.0, "fees": 1.0},
    )
    assert out is not None
    # 0.60 - 0.40 - (1.0/10.0)
    assert out["fill_alpha_gap"] == pytest.approx(0.10)
    assert out["fill_alpha_gap_usd"] == pytest.approx(1.0)
    assert "realized_edge" not in out


def test_kelly_size_is_stamped_from_the_authorizing_certificate() -> None:
    """Sizing in the ledger is the prerequisite for 'was the size right?'."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_table(c)
    _seed_certificates(c, q_live=0.60, kelly_size_usd=4.72164)
    _seed_aggregate(c)
    audit_id = record_edli_live_profit_audit_from_aggregate(c, "agg-1")
    assert audit_id is not None
    row = c.execute(
        "SELECT kelly_size_usd, q_live, fill_alpha_gap FROM edli_live_profit_audit "
        "WHERE audit_id=?",
        (audit_id,),
    ).fetchone()
    assert row["kelly_size_usd"] == pytest.approx(4.72164)
    assert row["q_live"] == pytest.approx(0.60)
    assert row["fill_alpha_gap"] is not None


def _seed_certificates(
    conn: sqlite3.Connection, *, q_live: float, kelly_size_usd: float | None = None
) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decision_certificates ("
        "certificate_hash TEXT, certificate_type TEXT, verifier_status TEXT,"
        "payload_json TEXT)"
    )
    edge = {
        "condition_id": COND,
        "token_id": TOKEN,
        "q_live": q_live,
        "q_lcb_5pct": q_live - 0.05,
        "qkernel_execution_economics": {"edge_lcb": 0.07},
    }
    if kelly_size_usd is not None:
        edge["kelly_size_usd"] = kelly_size_usd
    conn.execute(
        "INSERT INTO decision_certificates VALUES (?,?,?,?)",
        (CERT_HASH, "ActionableTradeCertificate", "VERIFIED", json.dumps(edge)),
    )
    conn.execute(
        "INSERT INTO decision_certificates VALUES (?,?,?,?)",
        (
            COST_HASH,
            "CostModelCertificate",
            "VERIFIED",
            json.dumps({"condition_id": COND, "token_id": TOKEN, "c_fee_adjusted": 0.42}),
        ),
    )
    conn.commit()


def _seed_aggregate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS edli_live_order_events ("
        "aggregate_id TEXT, event_sequence INTEGER, event_type TEXT,"
        "payload_json TEXT, event_hash TEXT)"
    )
    conn.execute(
        "INSERT INTO edli_live_order_events VALUES (?,?,?,?,?)",
        (
            "agg-1",
            1,
            "PreSubmitRevalidated",
            json.dumps(
                {
                    "event_id": "e1",
                    "condition_id": COND,
                    "token_id": TOKEN,
                    "side": "BUY",
                    "expected_edge_source_certificate_hash": CERT_HASH,
                    "cost_basis_source_certificate_hash": COST_HASH,
                }
            ),
            "h1",
        ),
    )
    conn.execute(
        "INSERT INTO edli_live_order_events VALUES (?,?,?,?,?)",
        (
            "agg-1",
            2,
            "UserTradeObserved",
            json.dumps(
                {
                    "fill_authority_state": "FILL_CONFIRMED",
                    "avg_fill_price": 0.40,
                    "filled_size": 10.0,
                    "fees": 1.0,
                }
            ),
            "h2",
        ),
    )
    conn.commit()
