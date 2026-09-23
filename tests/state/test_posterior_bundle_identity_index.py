# Created: 2026-09-19
# Last reused/audited: 2026-09-23
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


def test_center_debias_keys_are_generated_columns() -> None:
    """The center-debias fit must not json_extract a 93 KB blob per row.

    `_RESIDUAL_SQL` filters on `$.q_shape` and projects `$.anchor_value_c`. Measured
    cold on the live database: **361.8 ms/row** — the same class of cost that made an
    un-deduped fitter join take 546.37 s (see src/ingest_main.py's 600 s bound note).
    On a 6,000-row replica the filter went 0.681 s -> 0.000 s with identical matches.
    """
    conn = _forecasts_conn()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(forecast_posteriors)")}
        assert {"q_shape", "anchor_value_c"} <= columns, (
            "center-debias still reads its keys out of the blob"
        )
        rows = (
            ("direct", '{"q_shape": "fused_normal_direct", "anchor_value_c": "21.729351"}'),
            ("numeric", '{"q_shape": "fused_normal_direct", "anchor_value_c": 21.729351}'),
            ("other-shape", '{"q_shape": "fused_day0_fast_residual_likelihood"}'),
            ("absent", "{}"),
        )
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
                 "2026-09-19T00:00:00+00:00", "{}", "m", provenance, identity_hash),
            )

        # A JSON string and a JSON number must both read back as the same number:
        # REAL affinity converts the string, and the consumer calls float() anyway.
        for identity_hash in ("direct", "numeric"):
            assert conn.execute(
                "SELECT anchor_value_c FROM forecast_posteriors WHERE posterior_identity_hash = ?",
                (identity_hash,),
            ).fetchone()[0] == 21.729351

        # Absent keys stay NULL rather than becoming 0.0, which the fit skips.
        assert conn.execute(
            "SELECT anchor_value_c FROM forecast_posteriors WHERE posterior_identity_hash = 'absent'"
        ).fetchone()[0] is None

        selected = {
            row[0]
            for row in conn.execute(
                "SELECT posterior_identity_hash FROM forecast_posteriors "
                "WHERE q_shape = 'fused_normal_direct' AND anchor_value_c IS NOT NULL"
            )
        }
        assert selected == {"direct", "numeric"}, selected
    finally:
        conn.close()


def test_center_debias_sql_does_not_json_extract_the_blob() -> None:
    """Rank candidates from the index; read generated values only for winners."""
    from src.calibration.center_debias_live_fit import _LATE_FALLBACK_SQL, _RESIDUAL_SQL

    for sql in (_RESIDUAL_SQL, _LATE_FALLBACK_SQL):
        assert "provenance_json" not in sql
        assert "json_extract" not in sql
    assert "q_shape" not in _RESIDUAL_SQL and "anchor_value_c" not in _RESIDUAL_SQL
    assert "q_shape" in _LATE_FALLBACK_SQL and "anchor_value_c" in _LATE_FALLBACK_SQL


def test_every_generated_column_the_shipped_sql_reads_exists_in_production() -> None:
    """A query may only read columns the production schema actually creates.

    The center-debias fixture built `forecast_posteriors` with its own DDL, so when
    the shipped SQL moved off the blob the fixture kept passing while production
    would have raised `no such column`. Bind the two together: every bare `p.<col>`
    the query selects or filters on must exist on a real `init_schema_forecasts`
    table.
    """
    import re

    from src.calibration.center_debias_live_fit import _LATE_FALLBACK_SQL, _RESIDUAL_SQL

    conn = _forecasts_conn()
    try:
        available = {row[1] for row in conn.execute("PRAGMA table_xinfo(forecast_posteriors)")}
        # Compile the actual candidate and fallback queries against canonical
        # DDL, including unqualified columns the p.<col> scan cannot inspect.
        start, end, cutoff = (
            "2026-09-21T00:00:00+00:00",
            "2026-09-22T00:00:00+00:00",
            "2026-09-23T00:00:00+00:00",
        )
        conn.execute(
            _RESIDUAL_SQL.format(windows="(?, ?, ?, ?, ?)"),
            ("London", "2026-09-22", 1, start, end, "high", cutoff),
        ).fetchall()
        conn.execute(
            _LATE_FALLBACK_SQL,
            ("London", "2026-09-22", "high", start, end, cutoff, cutoff),
        ).fetchall()
    finally:
        conn.close()
    referenced = set(re.findall(r"\bp\.([a-z_][a-z0-9_]*)", _RESIDUAL_SQL))
    missing = referenced - available
    assert not missing, (
        f"center-debias SQL reads {sorted(missing)} which init_schema_forecasts "
        "does not create; the query would raise 'no such column' in production"
    )


def test_the_generated_column_migration_is_idempotent() -> None:
    """Running the migration twice must not raise.

    `_table_columns` uses `PRAGMA table_info`, which omits VIRTUAL generated columns
    entirely — so on an already-migrated database every ALTER re-fired and raised
    `duplicate column name: bundle_identity`. Caught by running it against the live
    database, not by any test, because a fresh in-memory schema only ever runs it once.
    """
    from src.state.schema.v2_schema import _ensure_forecast_posteriors_bundle_identity

    conn = _forecasts_conn()
    try:
        # The schema build already ran it once; a second and third call are no-ops.
        _ensure_forecast_posteriors_bundle_identity(conn)
        _ensure_forecast_posteriors_bundle_identity(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(forecast_posteriors)")}
        assert {"bundle_identity", "q_shape", "anchor_value_c"} <= columns
    finally:
        conn.close()
