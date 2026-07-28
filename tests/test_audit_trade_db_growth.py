# Created: 2026-07-28
# Last reused/audited: 2026-07-28
# Authority basis: operator-directed bounded trade DB growth audit.
"""Antibodies for the read-only, tail-bounded trade DB census."""

from __future__ import annotations

import sqlite3

from scripts.audit_trade_db_growth import audit


def test_audit_uses_bounded_rowid_tail_without_mutating_db(tmp_path):
    db_path = tmp_path / "trades.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            captured_at TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            fee_details_json TEXT NOT NULL,
            token_map_json TEXT NOT NULL,
            tradeability_status_json TEXT NOT NULL,
            capture_trigger TEXT
        );
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            snapshot_id TEXT
        );
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            decision_snapshot_id TEXT
        );
        """
    )
    for row_id in range(1, 31):
        conn.execute(
            "INSERT INTO decision_log VALUES (?, ?, ?, ?)",
            (
                row_id,
                "exit_monitor" if row_id % 2 else "global_auction",
                '{"payload":"' + ("x" * row_id) + '"}',
                f"2026-07-28T08:00:{row_id:02d}+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"snapshot-{row_id}",
                f"2026-07-28T08:00:{row_id:02d}+00:00",
                '{"bids":[],"asks":[]}',
                "{}",
                "{}",
                "{}",
                "PRIORITY_MARKER" if row_id % 2 else "DISCOVERY_SWEEP",
            ),
        )
    conn.executemany(
        "INSERT INTO venue_commands VALUES (?, ?)",
        [
            ("command-1", "snapshot-1"),
            ("command-2", "snapshot-2"),
            ("command-3", "snapshot-2"),
        ],
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?, ?)",
        [
            ("position-1", "snapshot-2"),
            ("position-2", "snapshot-3"),
        ],
    )
    conn.commit()
    before = conn.total_changes
    conn.close()

    report = audit(db_path, tail_rows=10)

    assert report["schema_version"] == 3
    assert report["method"] == "bounded_rowid_tail_v2"
    assert report["freelist_count"] == 0
    decision = report["tables"]["decision_log"]
    assert decision["rowid_high_watermark"] == 30
    assert decision["sample_rows"] == 10
    assert decision["sample_categories"] == {
        "exit_monitor": 5,
        "global_auction": 5,
    }
    assert decision["sample_category_payloads"]["global_auction"]["rows"] == 5
    assert (
        decision["sample_category_payloads"]["global_auction"]["mean_bytes"]
        > decision["sample_category_payloads"]["exit_monitor"]["mean_bytes"]
    )
    assert decision["sample_payload_columns"]["artifact_json"]["nonnull_fraction"] == 1.0
    snapshots = report["tables"]["executable_market_snapshots"]
    assert snapshots["sample_rows"] == 10
    assert snapshots["sample_categories"] == {
        "DISCOVERY_SWEEP": 5,
        "PRIORITY_MARKER": 5,
    }
    snapshot_dedup = snapshots["sample_payload_content_addressability"]
    assert snapshot_dedup["content_addresses"] == 1
    assert snapshot_dedup["repeated_rows"] == 9
    assert snapshot_dedup["repeated_row_fraction"] == 0.9
    assert snapshot_dedup["content_addressed_savings_bytes"] > 0
    decision_dedup = decision["sample_payload_content_addressability"]
    assert decision_dedup["content_addresses"] == 10
    assert decision_dedup["repeated_rows"] == 0
    retention = report["snapshot_retention_evidence"]
    assert retention["minimum_distinct_operational_snapshot_ids"] == 3
    assert retention["sources"]["venue_commands"] == {
        "cited_rows": 3,
        "distinct_snapshot_ids": 2,
    }
    assert retention["sources"]["position_current"] == {
        "cited_rows": 2,
        "distinct_snapshot_ids": 2,
    }
    assert retention["minimum_direct_ref_fraction_of_rowid_high_watermark"] == 0.1
    assert report["tables"]["position_events"] == {"present": False}

    check = sqlite3.connect(db_path)
    assert check.total_changes == 0
    assert check.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 30
    check.close()
    assert before == 65
