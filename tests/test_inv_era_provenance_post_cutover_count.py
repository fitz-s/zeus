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


@pytest.mark.skip(
    reason=(
        "SCAFFOLD: skip until PR 1 is merged and backfill is complete. "
        "Remove skip when verified COUNT = 0 post-backfill. "
        "Pre-merge expected count: 2829 (per preflight/migration_dry_runs.json)."
    )
)
def test_antibody_zero_bleeding_rows_post_cutover_in_live_db():
    """CI antibody (live DB): after PR 1 merge and backfill, the query
    SELECT COUNT(*) FROM settlements_v2
    WHERE provenance_json LIKE '%harvester_live_uma_vote%'
    AND settled_at >= '2026-02-21'
    MUST return 0.

    This test is the final verification gate before Phase 0 is declared complete.
    """
    ...


@pytest.mark.skip(reason="SCAFFOLD: backfill not yet executed against live DB; 2829 expected. Fixture DB path pending separate operator-coordinated backfill step. Remove skip when backfill is verified complete (COUNT = 0).")
def test_antibody_zero_bleeding_rows_post_cutover_in_fixture_db():
    """CI antibody (fixture DB): using a minimal in-memory fixture DB with known
    BLEEDING rows, after running write_settlement_v2_with_era_provenance() on each,
    the antibody query must return 0 for post-cutover rows.

    BLEEDING rows with settled_at < 2026-02-21 are NOT in scope for this antibody;
    they are handled by the pre-cutover backfill path.
    """
    ...


def test_antibody_query_string_is_correct():
    """Verify the antibody SQL string is syntactically what we expect.
    This is a static assertion — it documents the exact query form.
    """
    assert "harvester_live_uma_vote" in _ANTIBODY_QUERY
    assert "settlements_v2" in _ANTIBODY_QUERY
    assert _PR1_MERGE_DATE in _ANTIBODY_QUERY
    assert "COUNT(*)" in _ANTIBODY_QUERY
