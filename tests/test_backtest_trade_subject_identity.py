from __future__ import annotations

from src.engine.replay import (
    TradeSubjectCandidate,
    resolve_trade_history_subject,
    select_canonical_trade_subject,
)
from src.state.db import get_connection, init_schema


def _insert_decision(conn, *, runtime_trade_id: str | None = None):
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status,
         edge_source, runtime_trade_id, env)
        VALUES ('mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-03-31T12:00:00+00:00', NULL,
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered',
                'center_buy', ?, 'live')
        """,
        (runtime_trade_id,),
    )


def test_trade_decisions_row_id_is_not_subject_id(tmp_path):
    db_path = tmp_path / "trade.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _insert_decision(conn)
    row_id = conn.execute("SELECT trade_id FROM trade_decisions").fetchone()["trade_id"]

    subject = resolve_trade_history_subject(conn, str(row_id))
    conn.close()

    assert subject.position_id is None
    assert subject.missing_reason == "decision_row_id_not_subject"


def test_runtime_trade_id_maps_to_position_id_alias(tmp_path):
    db_path = tmp_path / "trade.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _insert_decision(conn, runtime_trade_id="pos-1")
    conn.execute(
        """
        INSERT INTO position_current
        (position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
         direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
         last_monitor_prob, last_monitor_edge, last_monitor_market_price,
         decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
         chain_state, order_id, order_status, updated_at, temperature_metric)
        VALUES ('pos-1', 'active', 'pos-1', 'mkt', 'NYC', 'US-Northeast', '2026-04-03',
                '39-40°F', 'buy_yes', 'F', 5.0, 10.0, 5.0, 0.5, 0.6,
                0.6, 0.1, 0.5, 'snap-1', 'entry', 'center_buy', 'edge',
                'opening_hunt', 'on_chain', 'ord-1', 'filled', '2026-04-02T00:00:00Z', 'high')
        """
    )

    subject = resolve_trade_history_subject(conn, "pos-1")
    conn.close()

    assert subject.position_id == "pos-1"
    assert subject.source == "position_current"


def test_ambiguous_trade_subject_is_unresolved_not_guessed():
    subject = select_canonical_trade_subject(
        [
            TradeSubjectCandidate("pos-a", "outcome_fact", 1),
            TradeSubjectCandidate("pos-b", "outcome_fact", 1),
        ]
    )

    assert subject.position_id is None
    assert subject.missing_reason == "ambiguous_trade_subject"


def test_orphan_trade_decision_is_reported_not_replayed(tmp_path):
    db_path = tmp_path / "trade.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _insert_decision(conn, runtime_trade_id="orphan-pos")

    subject = resolve_trade_history_subject(conn, "orphan-pos")
    conn.close()

    assert subject.position_id is None
    assert subject.missing_reason == "orphan_trade_decision"
