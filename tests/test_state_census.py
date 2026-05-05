# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1H/phase.json
"""Relationship tests for scripts/state_census.py — T1H invariants.

Four named invariant tests (per phase.json §asserted_invariants):
  1. test_census_read_only
  2. test_census_data_unavailable_on_empty_settlement_queue
  3. test_census_detects_placeholder_identity
  4. test_census_detects_corrected_row_without_fill_authority

Plus helper tests for argv parsing, JSON shape, and read-only-URI guard.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure scripts/ is importable as a module regardless of working dir.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.state_census import (  # noqa: E402
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    _classify_identity_truth,
    _classify_redeem_truth,
    _open_read_only,
    main,
    run_census,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_trade_db(path: Path) -> None:
    """Create a minimal read-write zeus_trades.db fixture with all census tables."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            trade_id TEXT PRIMARY KEY,
            condition_id TEXT,
            state TEXT DEFAULT 'holding',
            fill_authority TEXT DEFAULT 'none',
            corrected_executable_economics_eligible INTEGER DEFAULT 0,
            exit_state TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settlement_commands (
            command_id TEXT PRIMARY KEY,
            condition_id TEXT,
            state TEXT,
            requested_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS venue_commands (
            command_id TEXT PRIMARY KEY,
            trade_id TEXT,
            state TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS venue_submission_envelopes (
            envelope_id TEXT PRIMARY KEY,
            condition_id TEXT,
            question_id TEXT,
            trade_ids_json TEXT,
            captured_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Test 1 (T1H-CENSUS-READ-ONLY): read-only URI mode is enforced
# ---------------------------------------------------------------------------


class TestCensusReadOnly:
    """T1H-CENSUS-READ-ONLY: DB opened with file:PATH?mode=ro; writes refused."""

    def test_census_read_only(self, tmp_path: Path) -> None:
        """Assert _open_read_only returns a connection that refuses writes.

        Strategy: open the same DB file via the read-only helper, then attempt
        an INSERT. SQLite must raise OperationalError with "readonly" in the
        message because mode=ro is enforced at the driver layer.
        """
        db_file = tmp_path / "test_ro.db"
        # Create a valid DB with a table first (read-write).
        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute("CREATE TABLE t (x TEXT)")
        rw_conn.commit()
        rw_conn.close()

        # Open via read-only helper.
        ro_conn = _open_read_only(db_file)
        try:
            with pytest.raises(sqlite3.OperationalError) as exc_info:
                ro_conn.execute("INSERT INTO t VALUES ('bad')")
            # SQLite message contains "readonly" when mode=ro refused the write.
            assert "readonly" in str(exc_info.value).lower(), (
                f"Expected 'readonly' in error message, got: {exc_info.value}"
            )
        finally:
            ro_conn.close()

    def test_census_run_does_not_create_db(self, tmp_path: Path) -> None:
        """run_census on a non-existent DB returns empty census (no file created)."""
        missing_db = tmp_path / "nonexistent.db"
        result = run_census(missing_db)
        # File must NOT be created by the census (mode=ro refuses creation).
        assert not missing_db.exists(), "Census must not create a new DB file."
        assert "warning" in result
        assert result["positions"] == []
        assert result["summary"]["total_positions"] == 0

    def test_main_requires_read_only_flag(self, tmp_path: Path) -> None:
        """--read-only flag is required; argparse exits if absent."""
        out = tmp_path / "out.json"
        with pytest.raises(SystemExit) as exc_info:
            main(["--json-out", str(out)])
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Test 2 (T1H-DATA-UNAVAILABLE-DISTINCT-FROM-NO-REDEEM)
# ---------------------------------------------------------------------------


class TestCensusDataUnavailableOnEmptySettlementQueue:
    """T1H-DATA-UNAVAILABLE-DISTINCT-FROM-NO-REDEEM."""

    def test_census_data_unavailable_on_empty_settlement_queue(
        self, tmp_path: Path
    ) -> None:
        """Fixture: open position condition_id='abc', 0 settlement_commands rows.

        Assert redeem_truth == 'data_unavailable', NOT 'no_redeem_queued'.
        """
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions (trade_id, condition_id, state, fill_authority) "
            "VALUES ('trade-abc', 'abc', 'holding', 'none')"
        )
        # settlement_commands intentionally has 0 rows for condition_id='abc'
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)

        assert len(result["positions"]) == 1
        pos = result["positions"][0]
        assert pos["trade_id"] == "trade-abc"
        assert pos["condition_id"] == "abc"

        # T1H-DATA-UNAVAILABLE-DISTINCT-FROM-NO-REDEEM: MUST be 'data_unavailable'
        assert pos["redeem_truth"] == "data_unavailable", (
            f"Expected 'data_unavailable' but got {pos['redeem_truth']!r}. "
            "Per T1H invariant, 0 rows in settlement_commands MUST yield "
            "'data_unavailable', NOT 'no_redeem_queued'."
        )
        assert pos["redeem_truth"] != "no_redeem_queued", (
            "T1H invariant violated: 'no_redeem_queued' is explicitly forbidden "
            "when settlement_commands has 0 rows for the condition_id."
        )

    def test_census_redeem_truth_with_existing_row(self, tmp_path: Path) -> None:
        """When a settlement_commands row exists, redeem_truth reflects its state."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions (trade_id, condition_id, state) "
            "VALUES ('trade-def', 'def-cid', 'holding')"
        )
        rw_conn.execute(
            "INSERT INTO settlement_commands (command_id, condition_id, state, requested_at) "
            "VALUES ('cmd-1', 'def-cid', 'REDEEM_INTENT_CREATED', '2026-05-05T00:00:00Z')"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)
        pos = next(p for p in result["positions"] if p["trade_id"] == "trade-def")
        assert pos["redeem_truth"] == "redeem_intent_created"

    def test_classify_redeem_truth_no_table(self, tmp_path: Path) -> None:
        """When settlement_commands table is absent, returns 'data_unavailable'."""
        db_file = tmp_path / "minimal.db"
        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute("CREATE TABLE dummy (x TEXT)")
        rw_conn.commit()
        rw_conn.close()

        ro_conn = _open_read_only(db_file)
        try:
            result = _classify_redeem_truth(ro_conn, "any-condition-id")
        finally:
            ro_conn.close()
        assert result == "data_unavailable"


# ---------------------------------------------------------------------------
# Test 3 (T1H-DETECTS-PLACEHOLDER-IDENTITY)
# ---------------------------------------------------------------------------


class TestCensusDetectsPlaceholderIdentity:
    """T1H-DETECTS-PLACEHOLDER-IDENTITY."""

    def test_census_detects_placeholder_identity_via_condition_id(
        self, tmp_path: Path
    ) -> None:
        """Fixture: envelope with condition_id='legacy:0x123'.

        Assert identity_truth == 'placeholder' AND trade_id is in anomalies.
        """
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions (trade_id, condition_id, state) "
            "VALUES ('trade-legacy', 'legacy:0x123abc', 'holding')"
        )
        rw_conn.execute(
            "INSERT INTO venue_submission_envelopes "
            "(envelope_id, condition_id, question_id, trade_ids_json, captured_at) "
            "VALUES ('env-1', 'legacy:0x123abc', 'some-qid', '[\"trade-legacy\"]', "
            "'2026-05-05T00:00:00Z')"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)

        pos = next(
            (p for p in result["positions"] if p["trade_id"] == "trade-legacy"), None
        )
        assert pos is not None, "Position 'trade-legacy' not found in census output."
        assert pos["identity_truth"] == "placeholder", (
            f"Expected 'placeholder' but got {pos['identity_truth']!r}. "
            "T1H invariant: condition_id starting with 'legacy:' must yield "
            "identity_truth='placeholder'."
        )

        anomaly_trade_ids = [a["trade_id"] for a in result["anomalies"]]
        assert "trade-legacy" in anomaly_trade_ids, (
            f"Expected trade_id 'trade-legacy' in anomalies but got: {anomaly_trade_ids}. "
            "T1H invariant: placeholder identity MUST be reported in anomalies list."
        )

        identity_anomalies = [
            a for a in result["anomalies"]
            if a["trade_id"] == "trade-legacy" and a["axis"] == "identity_truth"
        ]
        assert identity_anomalies, "No identity_truth anomaly for 'trade-legacy'."
        assert identity_anomalies[0]["reason"] == "placeholder"

    def test_census_detects_placeholder_identity_via_question_id(
        self, tmp_path: Path
    ) -> None:
        """Fixture: envelope with question_id='legacy-compat'.

        Assert identity_truth == 'placeholder' AND trade_id is in anomalies.
        """
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions (trade_id, condition_id, state) "
            "VALUES ('trade-legacyq', 'some-cid-789', 'holding')"
        )
        rw_conn.execute(
            "INSERT INTO venue_submission_envelopes "
            "(envelope_id, condition_id, question_id, trade_ids_json, captured_at) "
            "VALUES ('env-2', 'some-cid-789', 'legacy-compat', '[\"trade-legacyq\"]', "
            "'2026-05-05T00:00:00Z')"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)

        pos = next(
            (p for p in result["positions"] if p["trade_id"] == "trade-legacyq"), None
        )
        assert pos is not None
        assert pos["identity_truth"] == "placeholder"
        assert any(
            a["trade_id"] == "trade-legacyq" and a["axis"] == "identity_truth"
            for a in result["anomalies"]
        )

    def test_census_live_bound_identity(self, tmp_path: Path) -> None:
        """Envelope without legacy markers → identity_truth == 'live_bound'."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions (trade_id, condition_id, state) "
            "VALUES ('trade-live', '0xrealconditionid', 'holding')"
        )
        rw_conn.execute(
            "INSERT INTO venue_submission_envelopes "
            "(envelope_id, condition_id, question_id, trade_ids_json, captured_at) "
            "VALUES ('env-3', '0xrealconditionid', 'real-question-id', "
            "'[\"trade-live\"]', '2026-05-05T00:00:00Z')"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)
        pos = next(p for p in result["positions"] if p["trade_id"] == "trade-live")
        assert pos["identity_truth"] == "live_bound"
        # No identity anomaly for live_bound position.
        identity_anomalies = [
            a for a in result["anomalies"]
            if a["trade_id"] == "trade-live" and a["axis"] == "identity_truth"
        ]
        assert not identity_anomalies

    def test_census_no_envelope_identity(self, tmp_path: Path) -> None:
        """No envelope row for trade_id → identity_truth == 'no_envelope'."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions (trade_id, condition_id, state) "
            "VALUES ('trade-noenv', '0xcondid', 'holding')"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)
        pos = next(p for p in result["positions"] if p["trade_id"] == "trade-noenv")
        assert pos["identity_truth"] == "no_envelope"


# ---------------------------------------------------------------------------
# Test 4 (T1H-DETECTS-CORRECTED-WITHOUT-FILL-AUTHORITY)
# ---------------------------------------------------------------------------


class TestCensusDetectsCorrectedRowWithoutFillAuthority:
    """T1H-DETECTS-CORRECTED-WITHOUT-FILL-AUTHORITY."""

    def test_census_detects_corrected_row_without_fill_authority(
        self, tmp_path: Path
    ) -> None:
        """Fixture: corrected_executable_economics_eligible=True, fill_authority='pending'.

        Assert position_truth == 'review_required' AND trade_id is in anomalies.
        """
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions "
            "(trade_id, condition_id, state, fill_authority, "
            "corrected_executable_economics_eligible) "
            "VALUES ('trade-corrected', 'cid-corrected', 'holding', 'pending', 1)"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)

        pos = next(
            (p for p in result["positions"] if p["trade_id"] == "trade-corrected"),
            None,
        )
        assert pos is not None, "Position 'trade-corrected' not found in census output."
        assert pos["position_truth"] == "review_required", (
            f"Expected 'review_required' but got {pos['position_truth']!r}. "
            "T1H invariant: corrected_executable_economics_eligible=True with "
            "non-locked fill_authority must yield position_truth='review_required'."
        )

        anomaly_trade_ids = [a["trade_id"] for a in result["anomalies"]]
        assert "trade-corrected" in anomaly_trade_ids, (
            f"Expected 'trade-corrected' in anomalies but got: {anomaly_trade_ids}. "
            "T1H invariant: corrected-without-fill-authority MUST be in anomalies."
        )

        pos_anomalies = [
            a for a in result["anomalies"]
            if a["trade_id"] == "trade-corrected" and a["axis"] == "position_truth"
        ]
        assert pos_anomalies
        assert pos_anomalies[0]["reason"] == "corrected_without_fill_authority"

    def test_census_corrected_with_locked_fill_authority_is_open(
        self, tmp_path: Path
    ) -> None:
        """corrected_eligible=True WITH fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL.

        Must yield position_truth='open' (not review_required).
        """
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions "
            "(trade_id, condition_id, state, fill_authority, "
            "corrected_executable_economics_eligible) "
            "VALUES ('trade-locked', 'cid-locked', 'holding', ?, 1)",
            (FILL_AUTHORITY_VENUE_CONFIRMED_FULL,),
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)

        pos = next(p for p in result["positions"] if p["trade_id"] == "trade-locked")
        assert pos["position_truth"] == "open", (
            f"Expected 'open' but got {pos['position_truth']!r}. "
            "corrected_eligible=True WITH locked fill_authority is healthy."
        )

        pos_anomalies = [
            a for a in result["anomalies"]
            if a["trade_id"] == "trade-locked" and a["axis"] == "position_truth"
        ]
        assert not pos_anomalies, "No anomaly expected for locked fill authority."

    def test_census_not_corrected_any_fill_authority_is_open(
        self, tmp_path: Path
    ) -> None:
        """corrected_eligible=False (default) — any fill_authority → position_truth='open'."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions "
            "(trade_id, condition_id, state, fill_authority, "
            "corrected_executable_economics_eligible) "
            "VALUES ('trade-normal', 'cid-normal', 'holding', 'none', 0)"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)
        pos = next(p for p in result["positions"] if p["trade_id"] == "trade-normal")
        assert pos["position_truth"] == "open"


# ---------------------------------------------------------------------------
# Helper tests: JSON output shape and argv parsing
# ---------------------------------------------------------------------------


class TestCensusJSONShape:
    """Verify JSON output schema compliance."""

    def test_census_output_has_required_top_level_keys(self, tmp_path: Path) -> None:
        """Empty DB produces a valid schema with all required top-level keys."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        result = run_census(db_file)

        required_keys = {
            "generated_at",
            "census_version",
            "db_path",
            "positions",
            "anomalies",
            "summary",
        }
        assert required_keys.issubset(set(result.keys())), (
            f"Missing keys: {required_keys - set(result.keys())}"
        )
        assert result["census_version"] == "T1H/v1"
        assert isinstance(result["positions"], list)
        assert isinstance(result["anomalies"], list)
        assert isinstance(result["summary"], dict)
        assert "total_positions" in result["summary"]
        assert "anomaly_count" in result["summary"]

    def test_census_position_row_has_all_six_axes(self, tmp_path: Path) -> None:
        """Each position row has all six classification axes."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        rw_conn = sqlite3.connect(str(db_file))
        rw_conn.execute(
            "INSERT INTO positions (trade_id, condition_id, state) "
            "VALUES ('trade-shape', 'cid-shape', 'holding')"
        )
        rw_conn.commit()
        rw_conn.close()

        result = run_census(db_file)
        assert len(result["positions"]) == 1
        pos = result["positions"][0]

        required_axes = {
            "trade_id",
            "condition_id",
            "position_truth",
            "redeem_truth",
            "command_truth",
            "fill_truth",
            "quote_or_exit_truth",
            "identity_truth",
        }
        assert required_axes.issubset(set(pos.keys())), (
            f"Missing axes in position row: {required_axes - set(pos.keys())}"
        )

    def test_census_json_is_serializable(self, tmp_path: Path) -> None:
        """Census output round-trips through json.dumps/loads cleanly."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)

        result = run_census(db_file)
        serialized = json.dumps(result)
        parsed = json.loads(serialized)
        assert parsed["census_version"] == "T1H/v1"

    def test_main_writes_json_file(self, tmp_path: Path) -> None:
        """main() writes a valid JSON file to --json-out path."""
        db_file = tmp_path / "zeus_trades.db"
        _make_trade_db(db_file)
        out_file = tmp_path / "census_out.json"

        ret = main([
            "--read-only",
            "--json-out", str(out_file),
            "--db", str(db_file),
        ])
        assert ret == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["census_version"] == "T1H/v1"

    def test_main_missing_db_writes_warning_json(self, tmp_path: Path) -> None:
        """main() on missing DB writes warning JSON and exits 0 (graceful)."""
        missing = tmp_path / "no_such.db"
        out_file = tmp_path / "warning_out.json"

        ret = main([
            "--read-only",
            "--json-out", str(out_file),
            "--db", str(missing),
        ])
        assert ret == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "warning" in data
        assert data["positions"] == []
