# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
#                  + critic finding #6A/E (2026-04-28 adversarial review).
"""Relapse antibody — block re-introduction of synthetic provenance markers.

If anyone re-runs `enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN`
or writes equivalent fabricated provenance, this test fires.

Specifically detects the two synth signatures used this session:
  - observation_instants_v2.provenance_json contains
        parser_version="legacy:enrich_2026-04-28"
    or  source_url LIKE 'legacy://obs_v2/%'
    or  source_file LIKE 'legacy://obs_v2/%'
  - observations.provenance_metadata.synthesized_by="legacy:backfill_obs_prov_2026-04-28"

CI-safe: skip if no live DB is available.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


CANDIDATE_DB_PATHS = (
    Path("/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db"),
    Path(__file__).resolve().parents[1] / "state" / "zeus-world.db",
)


def _find_live_db() -> Path | None:
    """Return the first candidate db that exists, has size > 1 MB, and has the
    settlements + observation_instants_v2 tables."""
    for p in CANDIDATE_DB_PATHS:
        if not p.exists():
            continue
        try:
            if p.stat().st_size < 1_000_000:
                continue
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if {"observation_instants_v2", "observations"}.issubset(tables):
                    return p
            finally:
                conn.close()
        except sqlite3.Error:
            continue
    return None


@pytest.fixture(scope="module")
def live_db_path() -> Path:
    p = _find_live_db()
    if p is None:
        pytest.skip("no live zeus-world.db with required tables (CI-safe)")
    return p


def test_no_legacy_enrich_marker_in_obs_v2(live_db_path: Path) -> None:
    """observation_instants_v2.provenance_json must not carry the synth marker."""
    conn = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM observation_instants_v2
            WHERE provenance_json IS NOT NULL
              AND json_valid(provenance_json) = 1
              AND json_extract(provenance_json, '$.parser_version') = ?
            """,
            ("legacy:enrich_2026-04-28",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 0, (
        f"{row[0]} observation_instants_v2 rows carry the synthetic "
        f"parser_version='legacy:enrich_2026-04-28' marker. "
        f"Either enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN "
        f"was re-run, or another writer is fabricating provenance. "
        f"See docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/README_BROKEN.md."
    )


def test_no_legacy_source_url_in_obs_v2(live_db_path: Path) -> None:
    """observation_instants_v2.provenance_json must not carry legacy:// source URLs."""
    conn = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM observation_instants_v2
            WHERE provenance_json IS NOT NULL
              AND json_valid(provenance_json) = 1
              AND (
                json_extract(provenance_json, '$.source_url') LIKE 'legacy://obs_v2/%'
                OR json_extract(provenance_json, '$.source_file') LIKE 'legacy://obs_v2/%'
              )
            """
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 0, (
        f"{row[0]} observation_instants_v2 rows carry synthetic "
        f"'legacy://obs_v2/...' source_url or source_file. Synthesis is forbidden."
    )


def test_no_synthetic_provenance_metadata_in_observations(live_db_path: Path) -> None:
    """observations.provenance_metadata must not carry the synth backfill tag."""
    conn = sqlite3.connect(f"file:{live_db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM observations
            WHERE provenance_metadata IS NOT NULL
              AND TRIM(provenance_metadata) != ''
              AND TRIM(provenance_metadata) != '{}'
              AND json_valid(provenance_metadata) = 1
              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
            """,
            ("legacy:backfill_obs_prov_2026-04-28",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 0, (
        f"{row[0]} observations rows carry the synthetic 'legacy:backfill_obs_prov_2026-04-28' "
        f"synthesized_by tag. Synthesis is forbidden."
    )
