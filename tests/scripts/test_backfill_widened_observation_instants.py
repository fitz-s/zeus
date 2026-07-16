# Created: 2026-07-16
# Authority basis: defect-2 fix (f1d135901) — one-shot backfill of the pre-fix
#                  observation_instants revisions quarantine.
"""Tests for scripts/backfill_widened_observation_instants.py.

Simulates the pre-fix frozen state directly: seeds a main row via
insert_rows, then quarantines a second reading via the writer's own
_insert_revision helper WITHOUT going through insert_rows (which would now
auto-widen) — this is exactly what the old writer did before f1d135901.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.backfill_widened_observation_instants import (
    BACKFILL_REASON,
    apply_backfill,
    find_widening_backfill_candidates,
)
from src.data.observation_instants_writer import (
    ObsV2Row,
    _fetch_existing,
    _insert_revision,
    _payload_hash_from_provenance,
    _row_to_dict,
    insert_rows,
)
from src.state.schema.v2_schema import apply_canonical_schema


def _valid_provenance(**overrides) -> str:
    data = {
        "tier": "WU_ICAO",
        "station_id": "KORD",
        "payload_hash": "sha256:" + "a" * 64,
        "source_url": "https://api.weather.com/v1/location/KORD:9:US/observations/historical.json?apiKey=REDACTED",
        "parser_version": "test_backfill_widened_observation_instants_v1",
    }
    data.update(overrides)
    return json.dumps(data, sort_keys=True)


def _minimal_valid_kwargs(**overrides) -> dict:
    base = dict(
        city="Chicago",
        target_date="2024-01-15",
        source="wu_icao_history",
        timezone_name="America/Chicago",
        local_hour=8.0,
        local_timestamp="2024-01-15T08:00:00-06:00",
        utc_timestamp="2024-01-15T14:00:00+00:00",
        utc_offset_minutes=-360,
        time_basis="utc_hour_aligned",
        temp_unit="F",
        imported_at="2026-04-21T23:30:00+00:00",
        authority="VERIFIED",
        data_version="v1.wu-native.pilot",
        provenance_json=_valid_provenance(),
        temp_current=32.0,
        running_max=34.0,
        running_min=10.0,
        station_id="KORD",
    )
    base.update(overrides)
    return base


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_canonical_schema(conn)
    yield conn
    conn.close()


def _seed_frozen_cell(conn: sqlite3.Connection, **overrides) -> dict:
    kwargs = _minimal_valid_kwargs(**overrides)
    row = ObsV2Row(**kwargs)
    insert_rows(conn, [row])
    conn.commit()
    return kwargs


def _seed_quarantined_revision(conn: sqlite3.Connection, base_kwargs: dict, *, payload_hash: str, **incoming_overrides) -> dict:
    """Record a pre-fix quarantine: revision row written, main row left alone."""
    existing = _fetch_existing(
        conn,
        {
            "city": base_kwargs["city"],
            "source": base_kwargs["source"],
            "utc_timestamp": base_kwargs["utc_timestamp"],
        },
    )
    incoming_kwargs = dict(base_kwargs)
    incoming_kwargs.update(incoming_overrides)
    incoming_kwargs["provenance_json"] = _valid_provenance(payload_hash=payload_hash)
    incoming_dict = _row_to_dict(ObsV2Row(**incoming_kwargs))
    _insert_revision(
        conn,
        existing=existing,
        incoming=incoming_dict,
        existing_payload_hash=_payload_hash_from_provenance(existing["provenance_json"]),
        incoming_payload_hash=payload_hash,
        reason="payload_hash_mismatch",
    )
    conn.commit()
    return incoming_dict


def _main_row(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT running_max, running_min, observation_count FROM observation_instants WHERE city='Chicago'"
    ).fetchone()
    return {"running_max": row[0], "running_min": row[1], "observation_count": row[2]}


class TestDryRun:
    def test_reports_widening_candidate_without_writing(self, mem_db):
        base = _seed_frozen_cell(mem_db)
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )

        candidates = find_widening_backfill_candidates(mem_db)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["city"] == "Chicago"
        assert candidate["before"] == {"running_max": 34.0, "running_min": 10.0, "observation_count": None}
        assert candidate["after"]["running_max"] == 36.0
        assert candidate["after"]["running_min"] == 8.0
        # Dry-run: scan must not mutate the main row.
        assert _main_row(mem_db) == {"running_max": 34.0, "running_min": 10.0, "observation_count": None}

    def test_no_quarantine_means_no_candidates(self, mem_db):
        _seed_frozen_cell(mem_db)

        assert find_widening_backfill_candidates(mem_db) == []


class TestApply:
    def test_widens_main_row_and_writes_audit_revision(self, mem_db):
        base = _seed_frozen_cell(mem_db)
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )
        candidates = find_widening_backfill_candidates(mem_db)

        updated = apply_backfill(mem_db, candidates)
        mem_db.commit()

        assert updated == 1
        assert _main_row(mem_db)["running_max"] == 36.0
        assert _main_row(mem_db)["running_min"] == 8.0
        audit_row = mem_db.execute(
            "SELECT reason FROM observation_revisions WHERE city='Chicago' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit_row[0] == BACKFILL_REASON

    def test_rerun_after_apply_is_noop(self, mem_db):
        base = _seed_frozen_cell(mem_db)
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )
        apply_backfill(mem_db, find_widening_backfill_candidates(mem_db))
        mem_db.commit()

        assert find_widening_backfill_candidates(mem_db) == []

    def test_multiple_revisions_fold_to_the_widest_seen(self, mem_db):
        base = _seed_frozen_cell(mem_db)
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "b" * 64, running_max=35.0, running_min=9.0
        )
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "c" * 64, running_max=36.0, running_min=8.0
        )

        candidates = find_widening_backfill_candidates(mem_db)

        assert len(candidates) == 1
        assert candidates[0]["n_revisions_applied"] == 2
        assert candidates[0]["after"]["running_max"] == 36.0
        assert candidates[0]["after"]["running_min"] == 8.0


class TestNonWideningRevisionsAreNeverApplied:
    def test_narrower_revision_never_touches_main_row(self, mem_db):
        base = _seed_frozen_cell(mem_db)
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "b" * 64, running_max=30.0, running_min=15.0
        )

        assert find_widening_backfill_candidates(mem_db) == []
        assert _main_row(mem_db)["running_max"] == 34.0

    def test_different_identity_revision_never_touches_main_row(self, mem_db):
        base = _seed_frozen_cell(mem_db)
        # Wider values, but a DIFFERENT station — not the same bucket's
        # accumulating set, must not be folded in even though the numbers
        # would otherwise pass the widening check.
        _seed_quarantined_revision(
            mem_db,
            base,
            payload_hash="sha256:" + "b" * 64,
            running_max=40.0,
            running_min=5.0,
            station_id="KMDW",
            provenance_json=_valid_provenance(payload_hash="sha256:" + "b" * 64, station_id="KMDW"),
        )

        assert find_widening_backfill_candidates(mem_db) == []
        assert _main_row(mem_db)["running_max"] == 34.0

    def test_mixed_applicable_and_non_applicable_revisions_only_folds_applicable(self, mem_db):
        base = _seed_frozen_cell(mem_db)
        # Applies: wider, same identity.
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )
        # Does not apply: narrower than the (already-wider) fold in progress.
        _seed_quarantined_revision(
            mem_db, base, payload_hash="sha256:" + "c" * 64, running_max=35.0, running_min=9.0
        )

        candidates = find_widening_backfill_candidates(mem_db)

        assert len(candidates) == 1
        assert candidates[0]["n_revisions_examined"] == 2
        assert candidates[0]["n_revisions_applied"] == 1
        assert candidates[0]["after"]["running_max"] == 36.0
