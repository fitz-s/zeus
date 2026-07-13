# Created: 2026-07-13
# Authority basis: LX-E packet (docs/rebuild/local_ledger_excision_2026-07-12.md
#   Round-2 delta adjudication) — world_grade_pnl_usd column addition +
#   settlement_attribution_supersessions.
"""Tests for src/state/schema/settlement_attribution_schema.py.

Covers the interaction between the pre-existing category-CHECK guarded rebuild
(``_rebuild_stale_category_check``) and the LX-E ``world_grade_pnl_usd`` column
addition: a legacy table missing BOTH must be upgraded to the current shape
without positionally misaligning columns (a ``SELECT *`` copy would silently
shift values once a new column lands at a different physical position on an
ALTER-upgraded old table than in a freshly CREATEd table).
"""
from __future__ import annotations

import sqlite3

from src.state.schema.settlement_attribution_schema import ensure_table


def _create_legacy_5value_table_without_new_columns(conn: sqlite3.Connection) -> None:
    """A table shaped like settlement_attribution BEFORE the 2026-06-21
    UNATTRIBUTABLE_Q_MISSING category addition AND before the LX-E
    world_grade_pnl_usd column — the worst-case legacy shape."""
    conn.execute(
        """
        CREATE TABLE settlement_attribution (
            attribution_id TEXT NOT NULL PRIMARY KEY,
            position_id TEXT NOT NULL,
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            direction TEXT,
            traded_bin_label TEXT,
            category TEXT NOT NULL CHECK (category IN (
                'SKILL_WIN', 'LUCKY_WIN', 'SKILL_LOSS', 'MISCALIBRATED_LOSS',
                'STALE_DECISION'
            )),
            won INTEGER NOT NULL CHECK (won IN (0, 1)),
            counts_as_skill_win INTEGER NOT NULL CHECK (counts_as_skill_win IN (0, 1)),
            avg_fill_price REAL,
            q_live REAL,
            q_lcb_5pct REAL,
            q_in_bin REAL,
            market_in_bin_prob REAL,
            market_q_ratio REAL,
            decision_posterior_id TEXT,
            decision_posterior_computed_at TEXT,
            decision_posterior_age_hours REAL,
            fresh_posterior_id TEXT,
            fresh_posterior_computed_at TEXT,
            fresh_q_supports_position INTEGER CHECK (fresh_q_supports_position IN (0, 1)),
            fresh_q_in_bin REAL,
            fresh_input_identity TEXT,
            fresh_input_age_hours REAL,
            settled_value REAL,
            settlement_unit TEXT,
            settled_in_bin INTEGER CHECK (settled_in_bin IN (0, 1)),
            settled_at TEXT,
            freshness_budget_hours REAL,
            fresher_cycle_existed_at_decision INTEGER CHECK (fresher_cycle_existed_at_decision IN (0, 1)),
            large_factor_threshold REAL,
            derivation_note TEXT,
            rationale TEXT,
            graded_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
            UNIQUE(position_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO settlement_attribution (
            attribution_id, position_id, condition_id, city, target_date,
            temperature_metric, direction, traded_bin_label, category, won,
            counts_as_skill_win, avg_fill_price, q_live, q_lcb_5pct,
            settled_value, settlement_unit, settled_in_bin, settled_at,
            freshness_budget_hours, large_factor_threshold, derivation_note,
            rationale, graded_at, schema_version
        ) VALUES (
            'attr-legacy-1', 'pos-legacy-1', 'cond-1', 'Denver', '2026-06-01',
            'high', 'buy_no', '50-51F', 'SKILL_WIN', 1,
            1, 0.35, 0.80, 0.70,
            47.0, 'F', 0, '2026-06-01T20:00:00Z',
            6.0, 2.0, 'note',
            'rationale text', '2026-06-01T20:05:00Z', 1
        )
        """
    )
    conn.commit()


def test_legacy_table_missing_check_and_column_upgrades_without_misaligning_values():
    """A legacy table (5-value CHECK, no world_grade_pnl_usd) is upgraded to the
    current shape: category CHECK accepts UNATTRIBUTABLE_Q_MISSING, and every
    pre-existing column value survives at its ORIGINAL semantic position (not
    shifted by the column-order mismatch a positional SELECT * would cause)."""
    conn = sqlite3.connect(":memory:")
    _create_legacy_5value_table_without_new_columns(conn)

    ensure_table(conn)

    row = conn.execute(
        """
        SELECT position_id, condition_id, city, category, won, avg_fill_price,
               q_live, q_lcb_5pct, settled_value, settlement_unit, settled_in_bin,
               rationale, world_grade_pnl_usd
        FROM settlement_attribution WHERE attribution_id = 'attr-legacy-1'
        """
    ).fetchone()
    assert row[0] == "pos-legacy-1"
    assert row[1] == "cond-1"
    assert row[2] == "Denver"
    assert row[3] == "SKILL_WIN"
    assert row[4] == 1
    assert row[5] == 0.35
    assert row[6] == 0.80
    assert row[7] == 0.70
    assert row[8] == 47.0
    assert row[9] == "F"
    assert row[10] == 0
    assert row[11] == "rationale text"
    assert row[12] is None  # world_grade_pnl_usd: ALTER-added, no data yet — never fabricated

    # The CHECK is upgraded: a row using the new category is now accepted.
    conn.execute(
        """
        INSERT INTO settlement_attribution (
            attribution_id, position_id, category, won, counts_as_skill_win,
            settled_value, settlement_unit, settled_in_bin, graded_at,
            schema_version
        ) VALUES (
            'attr-legacy-2', 'pos-legacy-2', 'UNATTRIBUTABLE_Q_MISSING', 0, 0,
            47.0, 'F', 0, '2026-06-01T20:05:00Z', 1
        )
        """
    )
    n = conn.execute("SELECT COUNT(*) FROM settlement_attribution").fetchone()[0]
    assert n == 2


def test_fresh_table_has_world_grade_pnl_usd_and_supersessions_table():
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(settlement_attribution)").fetchall()}
    assert "world_grade_pnl_usd" in cols
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "settlement_attribution_supersessions" in tables
