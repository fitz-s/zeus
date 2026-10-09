# Created: 2026-07-16
# Lifecycle: created=2026-07-16; last_reviewed=2026-10-09; last_reused=2026-10-09
# Last reused/audited: 2026-10-09 (isolated canonical fixtures, offline guard)
# Purpose: Pin current-writer field custody and blocked operational repair entry points.
# Reuse: Inspect historical snapshot completeness and the repair's disabled apply boundary.
# Authority basis: defect-2 fix (f1d135901) — one-shot backfill of the pre-fix
#                  observation_instants revisions quarantine.
#                  PLAN.md, finite_evidence_probability_symmetry, 2026-10-09 consumer repair.
"""Tests for scripts/backfill_widened_observation_instants.py.

Simulates the pre-fix frozen state directly: seeds a main row via
insert_rows, then quarantines a second reading via the writer's own
_insert_revision helper WITHOUT going through insert_rows (which would now
auto-widen) — this is exactly what the old writer did before f1d135901.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace

import pytest

import scripts.backfill_widened_observation_instants as backfill
from scripts.backfill_widened_observation_instants import (
    BACKFILL_REASON,
    _apply_backfill_transaction,
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
def fixture_db_path(tmp_path):
    return tmp_path / "fixture-world.db"


@pytest.fixture
def fixture_db(fixture_db_path) -> sqlite3.Connection:
    """Private file-backed canonical schema so the guarded transaction is real."""
    conn = sqlite3.connect(fixture_db_path)
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
    provenance = json.loads(incoming_kwargs["provenance_json"])
    provenance["payload_hash"] = payload_hash
    incoming_kwargs["provenance_json"] = json.dumps(provenance, sort_keys=True)
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
    def test_reports_widening_candidate_without_writing(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )

        candidates = find_widening_backfill_candidates(fixture_db)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["city"] == "Chicago"
        assert candidate["before"] == {"running_max": 34.0, "running_min": 10.0, "observation_count": None}
        assert candidate["after"]["running_max"] == 36.0
        assert candidate["after"]["running_min"] == 8.0
        # Dry-run: scan must not mutate the main row.
        assert _main_row(fixture_db) == {"running_max": 34.0, "running_min": 10.0, "observation_count": None}

    def test_no_quarantine_means_no_candidates(self, fixture_db):
        _seed_frozen_cell(fixture_db)

        assert find_widening_backfill_candidates(fixture_db) == []


class TestApply:
    def test_widens_main_row_and_writes_audit_revision(self, fixture_db, fixture_db_path):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )
        candidates = find_widening_backfill_candidates(fixture_db)

        updated = _apply_backfill_transaction(fixture_db_path, candidates)

        assert updated == 1
        assert _main_row(fixture_db)["running_max"] == 36.0
        assert _main_row(fixture_db)["running_min"] == 8.0
        audit_row = fixture_db.execute(
            "SELECT reason FROM observation_revisions WHERE city='Chicago' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit_row[0] == BACKFILL_REASON

    def test_rerun_after_apply_is_noop(self, fixture_db, fixture_db_path):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )
        _apply_backfill_transaction(fixture_db_path, find_widening_backfill_candidates(fixture_db))

        assert find_widening_backfill_candidates(fixture_db) == []

    def test_multiple_revisions_fold_to_the_widest_seen(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=35.0, running_min=9.0
        )
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "c" * 64, running_max=36.0, running_min=8.0
        )

        candidates = find_widening_backfill_candidates(fixture_db)

        assert len(candidates) == 1
        assert candidates[0]["n_revisions_applied"] == 2
        assert candidates[0]["after"]["running_max"] == 36.0
        assert candidates[0]["after"]["running_min"] == 8.0


class TestNonWideningRevisionsAreNeverApplied:
    def test_narrower_revision_never_touches_main_row(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=30.0, running_min=15.0
        )

        assert find_widening_backfill_candidates(fixture_db) == []
        assert _main_row(fixture_db)["running_max"] == 34.0

    def test_different_identity_revision_never_touches_main_row(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        # Wider values, but a DIFFERENT station — not the same bucket's
        # accumulating set, must not be folded in even though the numbers
        # would otherwise pass the widening check.
        _seed_quarantined_revision(
            fixture_db,
            base,
            payload_hash="sha256:" + "b" * 64,
            running_max=40.0,
            running_min=5.0,
            station_id="KMDW",
            provenance_json=_valid_provenance(payload_hash="sha256:" + "b" * 64, station_id="KMDW"),
        )

        assert find_widening_backfill_candidates(fixture_db) == []
        assert _main_row(fixture_db)["running_max"] == 34.0

    def test_mixed_applicable_and_non_applicable_revisions_only_folds_applicable(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        # Applies: wider, same identity.
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, running_min=8.0
        )
        # Does not apply: narrower than the (already-wider) fold in progress.
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "c" * 64, running_max=35.0, running_min=9.0
        )

        candidates = find_widening_backfill_candidates(fixture_db)

        assert len(candidates) == 1
        assert candidates[0]["n_revisions_examined"] == 2
        assert candidates[0]["n_revisions_applied"] == 1
        assert candidates[0]["after"]["running_max"] == 36.0


def _snapshot(conn):
    return list(conn.iterdump())


def _captured_kwargs(tmp_path, **overrides):
    """A synthetic, typed source capture wholly owned by this test."""
    kwargs = _minimal_valid_kwargs(**overrides)
    row = ObsV2Row(**kwargs)
    body = b"synthetic source capture for historical backfill fixture\r\n"
    sha = hashlib.sha256(body).hexdigest()
    path = tmp_path / "observation_raw" / "sha256" / f"{sha}.body"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    provenance = json.loads(row.provenance_json)
    reports = sorted({provenance[key] for key in
                      ("hour_max_raw_ts", "hour_min_raw_ts", "latest_raw_ts")
                      if provenance.get(key) is not None})
    provenance["captured_entity_custody_v1"] = {
        "status": "OBSERVED", "reason": None,
        "city": row.city, "source": row.source, "station_id": row.station_id,
        "target_date": row.target_date, "utc_timestamp": row.utc_timestamp,
        "temp_unit": row.temp_unit, "payload_hash": provenance["payload_hash"],
        "source_issued_at_utc": None, "completeness": "UNPROVEN",
        "settlement_equivalence": "UNPROVEN", "absorbing_authority": False,
        "captures": [{
            "sha256": sha, "byte_count": len(body), "source_file": str(path),
            "started_at": "2026-04-21T23:28:00+00:00",
            "finished_at": "2026-04-21T23:29:00+00:00",
            "request_url": "https://api.weather.com/v1/location/KORD:9:US/observations/historical.json",
            "request_params": {"units": "e", "startDate": "20240115", "endDate": "20240115"},
            "native_unit": "F", "headers": {}, "report_timestamps": reports,
        }],
    }
    return vars(replace(row, source_file=str(path), provenance_json=json.dumps(provenance)))


def _rewrite_revision(conn, mutate):
    revision_id, payload = conn.execute(
        "SELECT id,incoming_row_json FROM observation_revisions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    row = json.loads(payload)
    mutate(row)
    conn.execute("UPDATE observation_revisions SET incoming_row_json=? WHERE id=?",
                 (json.dumps(row), revision_id))
    conn.commit()


class TestCurrentWriterFieldSemantics:
    @pytest.mark.parametrize("metric", ["HIGH", "LOW"])
    def test_widening_carries_exact_current_and_capture_and_matches_writer(
        self, fixture_db, fixture_db_path, tmp_path, metric
    ):
        base = _seed_frozen_cell(fixture_db)
        incoming = _captured_kwargs(
            tmp_path, temp_current=33.0, observation_count=3,
            running_max=36.0 if metric == "HIGH" else 34.0,
            running_min=8.0 if metric == "LOW" else 10.0,
            imported_at="2026-04-21T23:31:00+00:00",
            provenance_json=_valid_provenance(
                payload_hash="sha256:" + "b" * 64,
                latest_raw_ts="2024-01-15T14:35:00+00:00", latest_temp=33.0,
                raw_obs_count=3, hour_max_raw_ts="2024-01-15T14:35:00+00:00",
                hour_min_raw_ts="2024-01-15T14:05:00+00:00",
            ),
        )
        snapshot = _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, **incoming
        )
        candidates = find_widening_backfill_candidates(fixture_db)
        assert len(candidates) == 1
        assert candidates[0]["_folded_row"]["temp_current"] == 33.0
        assert candidates[0]["_folded_row"]["source_file"] == incoming["source_file"]

        reference = sqlite3.connect(":memory:")
        try:
            apply_canonical_schema(reference)
            insert_rows(reference, [ObsV2Row(**base), ObsV2Row(**incoming)])
            expected = _fetch_existing(reference, base)
        finally:
            reference.close()

        assert _apply_backfill_transaction(fixture_db_path, candidates) == 1
        assert _fetch_existing(fixture_db, base) == expected
        receipt = fixture_db.execute(
            "SELECT existing_row_json,incoming_row_json FROM observation_revisions WHERE reason=?",
            (BACKFILL_REASON,),
        ).fetchone()
        assert json.loads(receipt[0]) == candidates[0]["_current_row"]
        assert json.loads(receipt[1]) == candidates[0]["_folded_row"]
        provenance = json.loads(expected["provenance_json"])
        assert provenance["captured_entity_custody_v1"] == json.loads(snapshot["provenance_json"])["captured_entity_custody_v1"]
        assert provenance["widened_from"]["temp_current"] == 32.0
        before = _snapshot(fixture_db)
        assert find_widening_backfill_candidates(fixture_db) == []
        assert _snapshot(fixture_db) == before

    def test_explicit_nullable_current_and_source_file_remain_null(self, fixture_db, fixture_db_path):
        base = _seed_frozen_cell(fixture_db, temp_current=None)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        candidates = find_widening_backfill_candidates(fixture_db)
        assert _apply_backfill_transaction(fixture_db_path, candidates) == 1
        row = _fetch_existing(fixture_db, base)
        assert row["temp_current"] is None
        assert row["source_file"] is None

    @pytest.mark.parametrize("later_clock", [None, "2024-01-15T14:20:00+00:00", "2024-01-15T14:35:00+00:00"])
    def test_folded_current_clock_blocks_missing_older_or_same_clock_temperature_change(self, fixture_db, later_clock):
        base = _seed_frozen_cell(fixture_db, provenance_json=_valid_provenance(latest_raw_ts="2024-01-15T14:05:00+00:00"))
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0, temp_current=33.0,
            provenance_json=_valid_provenance(latest_raw_ts="2024-01-15T14:35:00+00:00", latest_temp=33.0),
        )
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "c" * 64, running_max=40.0, temp_current=32.0,
            provenance_json=_valid_provenance(latest_raw_ts=later_clock, latest_temp=32.0),
        )
        candidate, = find_widening_backfill_candidates(fixture_db)
        assert candidate["n_revisions_applied"] == 1
        assert candidate["after"]["running_max"] == 36.0
        assert candidate["_folded_row"]["temp_current"] == 33.0

    def test_current_only_advance_does_not_expand_historical_candidate_universe(self, fixture_db):
        base = _seed_frozen_cell(fixture_db, provenance_json=_valid_provenance(latest_raw_ts="2024-01-15T14:05:00+00:00"))
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, temp_current=33.0,
            provenance_json=_valid_provenance(latest_raw_ts="2024-01-15T14:35:00+00:00", latest_temp=33.0),
        )
        before = _snapshot(fixture_db)
        assert find_widening_backfill_candidates(fixture_db) == []
        assert _snapshot(fixture_db) == before

    @pytest.mark.parametrize("source_file", ["unproved-file", None])
    def test_unowned_or_removed_capture_is_not_accepted(self, fixture_db, tmp_path, source_file):
        if source_file is None:
            base = _seed_frozen_cell(fixture_db, **_captured_kwargs(tmp_path))
        else:
            base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(
            fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0,
            source_file=source_file, provenance_json=_valid_provenance(),
        )
        before = _snapshot(fixture_db)
        assert find_widening_backfill_candidates(fixture_db) == []
        assert _snapshot(fixture_db) == before


class TestHistoricalMetadataRefusal:
    @pytest.mark.parametrize("column", ["temp_current", "source_file", "imported_at", "provenance_json", "source_role"])
    def test_missing_snapshot_field_is_not_reconstructed(self, fixture_db, column):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        _rewrite_revision(fixture_db, lambda row: row.pop(column))
        before = _snapshot(fixture_db)
        with pytest.raises(ValueError, match=f"Missing historical metadata: {column}"):
            find_widening_backfill_candidates(fixture_db)
        assert _snapshot(fixture_db) == before

    @pytest.mark.parametrize("column,value", [("temp_current", True), ("temp_current", float("nan")), ("temp_current", "33"), ("source_file", 12)])
    def test_invalid_current_or_source_metadata_is_not_coerced(self, fixture_db, column, value):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        _rewrite_revision(fixture_db, lambda row: row.update({column: value}))
        before = _snapshot(fixture_db)
        with pytest.raises(ValueError, match="Invalid historical metadata"):
            find_widening_backfill_candidates(fixture_db)
        assert _snapshot(fixture_db) == before

    @pytest.mark.parametrize("where", ["snapshot", "provenance"])
    def test_duplicate_json_keys_are_ambiguous(self, fixture_db, where):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        revision_id, payload = fixture_db.execute("SELECT id,incoming_row_json FROM observation_revisions").fetchone()
        if where == "snapshot":
            payload = payload[:-1] + ', "temp_current": 999}'
        else:
            row = json.loads(payload)
            row["provenance_json"] = row["provenance_json"][:-1] + ', "payload_hash": "conflicting-hash"}'
            payload = json.dumps(row)
        fixture_db.execute("UPDATE observation_revisions SET incoming_row_json=? WHERE id=?", (payload, revision_id))
        fixture_db.commit()
        before = _snapshot(fixture_db)
        with pytest.raises(ValueError, match="duplicate JSON key"):
            find_widening_backfill_candidates(fixture_db)
        assert _snapshot(fixture_db) == before

    @pytest.mark.parametrize("field,value", [("latest_temp", 99.0), ("raw_obs_count", 99)])
    def test_contradictory_provenance_is_not_used_to_guess_values(self, fixture_db, field, value):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        def corrupt(row):
            provenance = json.loads(row["provenance_json"])
            provenance[field] = value
            row["provenance_json"] = json.dumps(provenance)
        _rewrite_revision(fixture_db, corrupt)
        with pytest.raises(ValueError, match="Ambiguous historical metadata"):
            find_widening_backfill_candidates(fixture_db)

    def test_invalid_owned_custody_is_refused(self, fixture_db, tmp_path):
        base = _seed_frozen_cell(fixture_db)
        incoming = _captured_kwargs(tmp_path, running_max=36.0, provenance_json=_valid_provenance(payload_hash="sha256:" + "b" * 64))
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, **incoming)
        _rewrite_revision(fixture_db, lambda row: row.update(source_file="wrong-capture"))
        before = _snapshot(fixture_db)
        with pytest.raises(ValueError, match="custody"):
            find_widening_backfill_candidates(fixture_db)
        assert _snapshot(fixture_db) == before

    def test_reused_payload_hash_cannot_license_changed_values(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "a" * 64, running_max=36.0)
        with pytest.raises(ValueError, match="payload_hash reused"):
            find_widening_backfill_candidates(fixture_db)

    def test_revision_hash_must_match_its_snapshot(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        fixture_db.execute("UPDATE observation_revisions SET incoming_payload_hash=?", ("sha256:" + "c" * 64,))
        fixture_db.commit()
        before = _snapshot(fixture_db)
        with pytest.raises(ValueError, match="revision payload hash disagrees with snapshot"):
            find_widening_backfill_candidates(fixture_db)
        assert _snapshot(fixture_db) == before


class TestOperationalApplyIsBlocked:
    def test_public_api_refuses_before_reading_or_writing(self, fixture_db):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        candidates = find_widening_backfill_candidates(fixture_db)
        before = _snapshot(fixture_db)
        with pytest.raises(RuntimeError, match="settled-only scope and zero live P&L overlap"):
            apply_backfill(fixture_db, candidates)
        assert _snapshot(fixture_db) == before
        with pytest.raises(RuntimeError, match="Operational backfill apply is disabled"):
            apply_backfill(None, [])

    def test_cli_refuses_before_opening_a_database_or_lock(self, monkeypatch, tmp_path, capsys):
        db = tmp_path / "must-not-be-created.db"
        monkeypatch.setattr(backfill.sys, "argv", ["backfill", "--db", str(db), "--apply"])
        def forbidden_connect(*args, **kwargs):
            pytest.fail("Blocked CLI must not open any SQLite connection")
        monkeypatch.setattr(backfill.sqlite3, "connect", forbidden_connect)
        with pytest.raises(SystemExit) as exc:
            backfill.main()
        assert exc.value.code == 2
        assert "settled-only scope and zero live P&L overlap" in capsys.readouterr().err
        assert not db.exists()
        assert not list(tmp_path.glob("*.writer-lock.*"))

    def test_default_cli_remains_read_only(self, fixture_db, fixture_db_path, monkeypatch, capsys):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        before = _snapshot(fixture_db)
        real_connect = sqlite3.connect
        calls = []
        def traced_connect(*args, **kwargs):
            calls.append((args, kwargs))
            return real_connect(*args, **kwargs)
        monkeypatch.setattr(backfill.sqlite3, "connect", traced_connect)
        monkeypatch.setattr(backfill.sys, "argv", ["backfill", "--db", str(fixture_db_path)])
        assert backfill.main() == 0
        result = json.loads(capsys.readouterr().out)
        assert result["dry_run"] is True
        assert result["rows_updated"] == 0
        assert result["stats"]["cells"] == 1
        assert calls[0][0] == (f"file:{fixture_db_path}?mode=ro",)
        assert calls[0][1]["uri"] is True
        assert _snapshot(fixture_db) == before


class TestPrivateTransaction:
    def test_stale_candidate_refuses_without_mutation(self, fixture_db, fixture_db_path):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        candidates = find_widening_backfill_candidates(fixture_db)
        fixture_db.execute("UPDATE observation_instants SET running_max=40.0")
        fixture_db.commit()
        before = _snapshot(fixture_db)
        with pytest.raises(ValueError, match="changed since scan"):
            _apply_backfill_transaction(fixture_db_path, candidates)
        assert _snapshot(fixture_db) == before

    def test_audit_collision_rolls_back_current_update(self, fixture_db, fixture_db_path):
        base = _seed_frozen_cell(fixture_db)
        _seed_quarantined_revision(fixture_db, base, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        candidates = find_widening_backfill_candidates(fixture_db)
        current, folded = candidates[0]["_current_row"], candidates[0]["_folded_row"]
        _insert_revision(fixture_db, existing=current, incoming=folded,
                         existing_payload_hash=_payload_hash_from_provenance(current["provenance_json"]),
                         incoming_payload_hash=_payload_hash_from_provenance(folded["provenance_json"]),
                         reason=BACKFILL_REASON)
        fixture_db.commit()
        before = _snapshot(fixture_db)
        with pytest.raises(ValueError, match="audit revision already exists"):
            _apply_backfill_transaction(fixture_db_path, candidates)
        assert _snapshot(fixture_db) == before

    def test_audit_failure_rolls_back_every_row_and_owns_bulk_and_one_savepoint(
        self, fixture_db, fixture_db_path, monkeypatch
    ):
        from src.state import db_writer_lock as lock_module
        base = _seed_frozen_cell(fixture_db)
        second = _seed_frozen_cell(fixture_db, local_hour=9.0, local_timestamp="2024-01-15T09:00:00-06:00", utc_timestamp="2024-01-15T15:00:00+00:00")
        for kwargs in (base, second):
            _seed_quarantined_revision(fixture_db, kwargs, payload_hash="sha256:" + "b" * 64, running_max=36.0)
        candidates = find_widening_backfill_candidates(fixture_db)
        assert len(candidates) == 2
        fixture_db.execute("""CREATE TRIGGER fail_second_backfill_audit BEFORE INSERT ON observation_revisions
            WHEN NEW.reason='backfill_monotone_widening_2026-07-16'
              AND NEW.utc_timestamp='2024-01-15T15:00:00+00:00'
            BEGIN SELECT RAISE(ABORT, 'fixture audit failure'); END""")
        fixture_db.commit()
        before = _snapshot(fixture_db)
        real_connect, real_lock = sqlite3.connect, lock_module.db_writer_lock
        trace, events = [], []
        held = False
        @contextmanager
        def traced_lock(path, write_class):
            nonlocal held
            assert path == fixture_db_path
            assert write_class is lock_module.WriteClass.BULK
            with real_lock(path, write_class):
                held = True
                events.append("lock")
                try:
                    yield
                finally:
                    held = False
        def traced_connect(*args, **kwargs):
            assert held
            events.append("connect")
            conn = real_connect(*args, **kwargs)
            def record(statement):
                assert held
                trace.append(statement)
            conn.set_trace_callback(record)
            return conn
        monkeypatch.setattr(lock_module, "db_writer_lock", traced_lock)
        monkeypatch.setattr(backfill.sqlite3, "connect", traced_connect)
        with pytest.raises(sqlite3.IntegrityError, match="fixture audit failure"):
            _apply_backfill_transaction(fixture_db_path, candidates)
        assert events == ["lock", "connect"]
        assert not held
        assert sum(s.startswith("SAVEPOINT ") for s in trace) == 1
        assert sum(s.startswith("ROLLBACK TO SAVEPOINT ") for s in trace) == 1
        assert sum(s.startswith("RELEASE SAVEPOINT ") for s in trace) == 1
        assert _snapshot(fixture_db) == before
