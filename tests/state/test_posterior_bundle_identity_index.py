# Created: 2026-09-19
# Authority basis: docs/operations/current/storage_and_latency_next_levers_2026-09-19.md
#   — the live bundle lookup in replacement_forecast_bundle_reader filters on a
#   json_extract over a ~93 KB provenance_json blob.
"""The live bundle lookup must not walk a 93 KB blob to read one small key.

`_replacement_bundle_identity_is_live` (src/data/replacement_forecast_bundle_reader.py)
filters `forecast_posteriors` on
`json_extract(provenance_json, '$.day0_causal_evidence_bundle.bundle_identity')`.
`provenance_json` averages 93,275 B — about 23 pages of overflow chain per row — so
every candidate row is faulted in whole to read one identifier.

Measured on the live database: 1.427 ms/call against 0.014 ms for the same query with
the blob untouched, and on a 20,000-row replica 192.3 ms/call collapsing to 0.012 ms
once the key is a VIRTUAL generated column with an index over it. A VIRTUAL column
stores no bytes, so the file grew 2 MB (the index) rather than duplicating the key.
"""
from __future__ import annotations

import sqlite3

from src.state.db import init_schema_forecasts


BUNDLE_IDENTITY_PATH = "$.day0_causal_evidence_bundle.bundle_identity"


def _forecasts_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    return conn


def test_bundle_identity_is_a_generated_column_over_the_provenance_key() -> None:
    conn = _forecasts_conn()
    try:
        # table_xinfo, not table_info: the latter omits VIRTUAL generated columns
        # entirely, so it reports absent for a column that is present and selectable.
        columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(forecast_posteriors)")}
        assert "bundle_identity" in columns, (
            "forecast_posteriors has no bundle_identity column, so the live bundle "
            "lookup still json_extracts a ~93 KB blob per candidate row"
        )
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='forecast_posteriors'"
        ).fetchone()[0]
        assert BUNDLE_IDENTITY_PATH in sql, "bundle_identity must derive from the provenance key"
        assert "VIRTUAL" in sql.upper(), (
            "the column must be VIRTUAL: a STORED column would duplicate the key on disk "
            "in a table that is already the largest object in the database"
        )
    finally:
        conn.close()


def test_bundle_identity_matches_json_extract_row_for_row() -> None:
    conn = _forecasts_conn()
    try:
        rows = [
            ("with-bundle", '{"day0_causal_evidence_bundle": {"bundle_identity": "abc123"}}'),
            ("no-bundle", '{"q_shape": "fused_normal_direct"}'),
            ("empty-bundle", '{"day0_causal_evidence_bundle": {}}'),
            ("null-identity", '{"day0_causal_evidence_bundle": {"bundle_identity": null}}'),
        ]
        for identity_hash, provenance in rows:
            conn.execute(
                """
                INSERT INTO forecast_posteriors
                    (source_id, product_id, data_version, city, target_date,
                     temperature_metric, source_cycle_time, source_available_at,
                     computed_at, q_json, posterior_method, provenance_json,
                     posterior_identity_hash, runtime_layer, training_allowed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 0)
                """,
                ("s", "p", "v", "London", "2026-09-19", "high",
                 "2026-09-19T00:00:00+00:00", "2026-09-19T00:00:00+00:00",
                 "2026-09-19T00:00:00+00:00", "{}", "test_method",
                 provenance, identity_hash),
            )
        mismatched = conn.execute(
            f"""
            SELECT COUNT(*) FROM forecast_posteriors
             WHERE COALESCE(bundle_identity, '~')
                <> COALESCE(json_extract(provenance_json, '{BUNDLE_IDENTITY_PATH}'), '~')
            """
        ).fetchone()[0]
        assert mismatched == 0, "the generated column disagrees with json_extract"
        assert conn.execute(
            "SELECT bundle_identity FROM forecast_posteriors WHERE posterior_identity_hash='with-bundle'"
        ).fetchone()[0] == "abc123"
        assert conn.execute(
            "SELECT bundle_identity FROM forecast_posteriors WHERE posterior_identity_hash='no-bundle'"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_the_live_lookup_plan_uses_an_index_not_a_blob_scan() -> None:
    conn = _forecasts_conn()
    try:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT posterior_identity_hash FROM forecast_posteriors
             WHERE city = ? AND target_date = ? AND temperature_metric = ?
               AND training_allowed = 0 AND runtime_layer = ?
               AND bundle_identity = ?
             ORDER BY computed_at DESC, posterior_id DESC LIMIT 1
            """,
            ("London", "2026-09-19", "high", "live", "abc123"),
        ).fetchall()
        detail = " ".join(str(step[-1]) for step in plan)
        assert "SCAN" not in detail.upper() or "USING INDEX" in detail.upper(), (
            f"the live bundle lookup still scans: {detail}"
        )
        assert "bundle_identity" in detail or "idx_forecast_posteriors_bundle_identity" in detail, (
            f"no index covers bundle_identity: {detail}"
        )
    finally:
        conn.close()
