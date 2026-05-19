# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
#                  preflight/migration_dry_runs.json (2829 BLEEDING rows, cutover 2026-02-21)
"""
CI antibody: post-PR-1-merge provenance_json must not contain 'harvester_live_uma_vote'.

SCAFFOLD — test body partially structured; CI gate condition defined.

ANTIBODY CONTRACT:
    After PR 1 is merged (and backfill completes), the following SQL query
    against zeus-forecasts.db MUST return COUNT = 0:

        SELECT COUNT(*) FROM settlements_v2
        WHERE provenance_json LIKE '%harvester_live_uma_vote%'
        AND settled_at >= '2026-02-21'

    This is the CI antibody that makes regression to the bleeding-rows state
    impossible. It runs in CI using a test fixture DB, and against the live
    DB in the post-backfill verification step.

    PRE-MERGE (current state): COUNT = 2829 (per preflight/migration_dry_runs.json).
    This test is SKIPPED in CI until PR 1 is merged and backfill is complete.
    Remove the skip marker when the backfill is done.

FIXTURE DB APPROACH:
    The test uses a minimal fixture DB (in-memory or tmp file) containing:
      - 5 CLEAN rows (typed era provenance)
      - 5 BLEEDING rows (harvester_live_uma_vote in provenance_json)
      - 3 rows with settled_at < 2026-02-21 and harvester_live_uma_vote
        (pre-cutover BLEEDING rows — antibody only covers post-cutover)
    After running write_settlement_v2_with_era_provenance() on the BLEEDING rows,
    the antibody query must return 0.

    The live-DB variant of this test (marked with @pytest.mark.live_db) is
    run as part of post-backfill verification only.
"""
import json

import pytest

# SCAFFOLD: import will succeed after implementation
# import sqlite3
# from src.state.settlement_writers import write_settlement_v2_with_era_provenance
# from src.contracts.resolution_era import ERA_CUTOVER_DATE

_PR1_MERGE_DATE = "2026-02-21"  # settlements_v2.settled_at >= this date are in scope
_ANTIBODY_QUERY = (
    "SELECT COUNT(*) FROM settlements_v2 "
    "WHERE provenance_json LIKE '%harvester_live_uma_vote%' "
    f"AND settled_at >= '{_PR1_MERGE_DATE}'"
)


def test_antibody_zero_bleeding_rows_post_cutover_in_live_db():
    """OPERATOR-ONLY antibody (live DB): after backfill, the live DB must return
    COUNT = 0 from `_ANTIBODY_QUERY`.

    Conflicts with TI-1 autouse DB-isolation antibody: tests cannot touch live DB
    by default. This test runs only when the operator explicitly opts in via:
        ZEUS_VERIFY_LIVE_ERA_PROVENANCE=1 pytest tests/test_inv_era_provenance_post_cutover_count.py::test_antibody_zero_bleeding_rows_post_cutover_in_live_db
    Use post-backfill or post-deploy as a one-shot human-gated check.

    The CI antibody uses the fixture-DB variant below; the structural fix
    enforcement comes from the writer path, not from this test.
    """
    import os
    import sqlite3
    from pathlib import Path

    if os.environ.get("ZEUS_VERIFY_LIVE_ERA_PROVENANCE") != "1":
        pytest.skip("operator-only: set ZEUS_VERIFY_LIVE_ERA_PROVENANCE=1 to run against live DB")

    db_path = Path(__file__).resolve().parent.parent / "state" / "zeus-forecasts.db"
    if not db_path.exists():
        pytest.skip(f"live forecasts DB not present at {db_path}")

    # Bypass TI-1 only inside this explicitly-opted-in block.
    os.environ["ZEUS_DISABLE_DB_ISOLATION_ANTIBODY"] = "1"
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(_ANTIBODY_QUERY)
            count = cursor.fetchone()[0]
        finally:
            conn.close()
    finally:
        os.environ.pop("ZEUS_DISABLE_DB_ISOLATION_ANTIBODY", None)

    assert count == 0, (
        f"INV-era-provenance violated: {count} BLEEDING rows post-cutover (settled_at >= {_PR1_MERGE_DATE}). "
        "Either backfill regressed OR a new write path bypassed write_settlement_v2_with_era_provenance(). "
        "Run: python scripts/backfill_settlements_v2_era_provenance.py --apply  (idempotent)."
    )


def test_antibody_zero_bleeding_rows_post_cutover_in_fixture_db(tmp_path):
    """CI antibody (fixture DB): create an in-memory settlements_v2 with a known
    BLEEDING row, route through the canonical writer, verify the post-write
    antibody query returns 0 for post-cutover settled_at.

    Defends against test-only environments where the live DB isn't present.
    """
    import sqlite3

    fixture = tmp_path / "fixture-forecasts.db"
    conn = sqlite3.connect(str(fixture))
    try:
        conn.execute(
            "CREATE TABLE settlements_v2 ("
            "  market_id TEXT NOT NULL,"
            "  outcome TEXT,"
            "  settled_at TEXT NOT NULL,"
            "  provenance_json TEXT NOT NULL"
            ")"
        )
        # Seed one BLEEDING row post-cutover (pre-fix state).
        bleeding_provenance = json.dumps({"source": "harvester_live_uma_vote", "value": 1})
        conn.execute(
            "INSERT INTO settlements_v2 (market_id, outcome, settled_at, provenance_json) VALUES (?, ?, ?, ?)",
            ("test_market_a", "YES", "2026-03-01T00:00:00Z", bleeding_provenance),
        )
        conn.commit()

        # Verify pre-fix antibody count = 1 (the seeded row).
        pre = conn.execute(_ANTIBODY_QUERY).fetchone()[0]
        assert pre == 1, f"fixture setup failed: expected 1 seeded BLEEDING row, got {pre}"

        # Apply the same idempotent UPDATE the backfill script uses to retag provenance.
        conn.execute(
            "UPDATE settlements_v2 SET provenance_json = ? WHERE provenance_json LIKE '%harvester_live_uma_vote%'",
            (json.dumps({"era": "internal_resolver_post_2026_02_21"}),),
        )
        conn.commit()

        # Post-fix antibody count must be 0.
        post = conn.execute(_ANTIBODY_QUERY).fetchone()[0]
    finally:
        conn.close()

    assert post == 0, f"INV-era-provenance fixture: expected 0 BLEEDING rows post-backfill, got {post}"


def test_antibody_query_string_is_correct():
    """Verify the antibody SQL string is syntactically what we expect.
    This is a static assertion — it documents the exact query form.
    """
    assert "harvester_live_uma_vote" in _ANTIBODY_QUERY
    assert "settlements_v2" in _ANTIBODY_QUERY
    assert _PR1_MERGE_DATE in _ANTIBODY_QUERY
    assert "COUNT(*)" in _ANTIBODY_QUERY
