# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: operator LANDMINE #1 (the_path PR review 2026-06-08) — in
#   src/data/bayes_precision_fusion_download._detect_request_conflict, a STORED row whose
#   request_url_hash IS NULL (a pre-identity / legacy-backfill row) must be treated as
#   ENRICHABLE (update-in-place / not a conflict), NOT raised as a same-key-different-request
#   conflict; otherwise the live download after ANY legacy backfill falsely conflicts. The genuine
#   populated-vs-DIFFERENT-populated conflict must STILL be detected. BAYES_PRECISION_FUSION_SPEC §6 F1, BLOCKER 4.
"""Relationship test (legacy NULL-identity stored row -> live download conflict boundary).

Two cases across the boundary:
  (a) STORED row has request_url_hash NULL (legacy backfill before product identity existed):
      a live populated-identity insert on the SAME logical key is ENRICHABLE, NOT a conflict.
  (b) STORED row has a POPULATED request_url_hash and the incoming one DIFFERS: that is a
      genuine corrected-request conflict and MUST still be detected.
"""
from __future__ import annotations

import sqlite3

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema
from src.data.bayes_precision_fusion_download import _detect_request_conflict


def _conn():
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_live_schema(conn)
    return conn


_LOGICAL = dict(
    model="ecmwf_ifs", city="Paris", target_date="2026-06-09", metric="high",
    source_cycle_time="2026-06-08T00:00:00+00:00", endpoint="previous_runs",
)


def _insert_stored(conn, *, product_id, request_url_hash):
    conn.execute(
        """INSERT INTO raw_model_forecasts
           (model, city, target_date, metric, source_cycle_time, source_available_at,
            captured_at, lead_days, forecast_value_c, endpoint, product_id, request_url_hash)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _LOGICAL["model"], _LOGICAL["city"], _LOGICAL["target_date"], _LOGICAL["metric"],
            _LOGICAL["source_cycle_time"], _LOGICAL["source_cycle_time"], "cap", 1, 20.0,
            _LOGICAL["endpoint"], product_id, request_url_hash,
        ),
    )
    conn.commit()


def test_null_stored_request_hash_is_enrichable_not_conflict() -> None:
    conn = _conn()
    # (a) legacy backfill row: NULL identity.
    _insert_stored(conn, product_id=None, request_url_hash=None)
    incoming = dict(
        **_LOGICAL,
        product_id="ecmwf_ifs025::previous_runs",
        request_url_hash="populated_live_hash_abc",
    )
    assert _detect_request_conflict(conn, incoming) is None, (
        "a stored NULL request_url_hash (legacy/pre-identity row) must be ENRICHABLE, not a conflict"
    )


def test_populated_stored_hash_different_incoming_is_still_a_conflict() -> None:
    conn = _conn()
    # (b) genuine corrected-request conflict: stored populated hash, incoming DIFFERENT.
    _insert_stored(
        conn, product_id="ecmwf_ifs025::previous_runs", request_url_hash="stored_hash_OLD",
    )
    incoming = dict(
        **_LOGICAL,
        product_id="ecmwf_ifs025::previous_runs",
        request_url_hash="incoming_hash_NEW_DIFFERENT",
    )
    conflict = _detect_request_conflict(conn, incoming)
    assert conflict is not None, (
        "a populated-vs-different-populated request_url_hash must STILL be a conflict"
    )
    assert conflict["existing_request_url_hash"] == "stored_hash_OLD"


def test_same_populated_hash_is_not_a_conflict() -> None:
    conn = _conn()
    # The normal idempotent re-run: stored populated hash == incoming hash -> not a conflict.
    _insert_stored(
        conn, product_id="ecmwf_ifs025::previous_runs", request_url_hash="same_hash",
    )
    incoming = dict(
        **_LOGICAL,
        product_id="ecmwf_ifs025::previous_runs",
        request_url_hash="same_hash",
    )
    assert _detect_request_conflict(conn, incoming) is None
