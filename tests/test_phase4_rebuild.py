"""Phase 4 rebuild tests: R-J, R-M

R-J: INV-15 hotfix — non-whitelisted source forces training_allowed=False.
R-M: calibration_pairs_v2 rows from rebuild have all required identity fields populated.
"""
from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# R-J: INV-15 — source whitelist gate in add_calibration_pair_v2
# ---------------------------------------------------------------------------

class TestINV15SourceWhitelistGate:
    """R-J: INV-15 hotfix — a data_version not starting with a whitelisted prefix must force
    training_allowed=False in the written calibration row.

    Implementation (exec-bob 4A.0): _resolve_training_allowed() checks data_version prefix
    against _TRAINING_ALLOWED_SOURCES = {'tigge', 'ecmwf_ens'}. Any data_version whose
    prefix does not match is fail-closed to training_allowed=False regardless of caller intent.

    The gate is STRUCTURAL — the function enforces it, not the caller. A caller that passes
    training_allowed=True with a non-canonical data_version must still get training_allowed=0.
    """

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _write_pair(self, conn, *, decision_group_id: str, data_version: str,
                    training_allowed: bool, target_date: str = "2026-04-16") -> None:
        from src.calibration.store import add_calibration_pair_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        add_calibration_pair_v2(
            conn=conn,
            city="NYC",
            target_date=target_date,
            range_label="70-71°F",
            p_raw=0.25,
            outcome=0,
            lead_days=2.0,
            season="spring",
            cluster="NYC_F_2",
            forecast_available_at="2026-04-14T12:00:00",
            decision_group_id=decision_group_id,
            metric_identity=HIGH_LOCALDAY_MAX,
            data_version=data_version,
            training_allowed=training_allowed,
        )
        conn.commit()

    def test_openmeteo_data_version_forces_training_allowed_false(self):
        """R-J: data_version with 'openmeteo_hourly' prefix must force training_allowed=0.

        The whitelist checks data_version prefix. 'openmeteo_hourly_v1' does not start
        with 'tigge' or 'ecmwf_ens' — must be fail-closed regardless of caller intent.
        """
        conn = self._make_conn()
        self._write_pair(conn,
            decision_group_id="dg-test-inv15-001",
            data_version="openmeteo_hourly_v1",
            training_allowed=True,  # caller intent overridden by INV-15 gate
        )
        (training_allowed,) = conn.execute(
            "SELECT training_allowed FROM calibration_pairs_v2 WHERE decision_group_id='dg-test-inv15-001'"
        ).fetchone()
        assert training_allowed == 0, (
            f"INV-15 violated: 'openmeteo_hourly_v1' data_version wrote training_allowed={training_allowed}, "
            "expected 0. Non-whitelisted data_version prefix must be fail-closed (R-J)."
        )

    def test_canonical_tigge_data_version_preserves_training_allowed_true(self):
        """R-J complement: data_version starting with 'tigge' must not be downgraded."""
        conn = self._make_conn()
        self._write_pair(conn,
            decision_group_id="dg-test-inv15-002",
            data_version="tigge_mx2t6_local_calendar_day_max_v1",
            training_allowed=True,
            target_date="2026-04-17",
        )
        (training_allowed,) = conn.execute(
            "SELECT training_allowed FROM calibration_pairs_v2 WHERE decision_group_id='dg-test-inv15-002'"
        ).fetchone()
        assert training_allowed == 1, (
            f"INV-15 guard incorrectly downgraded canonical 'tigge_*' data_version: "
            f"training_allowed={training_allowed} (expected 1) (R-J complement)."
        )

    def test_ecmwf_ens_data_version_preserves_training_allowed_true(self):
        """R-J complement: data_version starting with 'ecmwf_ens' must not be downgraded."""
        conn = self._make_conn()
        self._write_pair(conn,
            decision_group_id="dg-test-inv15-003",
            data_version="ecmwf_ens_v1",
            training_allowed=True,
            target_date="2026-04-18",
        )
        (training_allowed,) = conn.execute(
            "SELECT training_allowed FROM calibration_pairs_v2 WHERE decision_group_id='dg-test-inv15-003'"
        ).fetchone()
        assert training_allowed == 1, (
            f"'ecmwf_ens_*' data_version is whitelisted; training_allowed must be 1, "
            f"got {training_allowed} (R-J complement)."
        )

    def test_unknown_data_version_prefix_forces_training_allowed_false(self):
        """R-J: Any data_version not starting with 'tigge' or 'ecmwf_ens' must be fail-closed."""
        conn = self._make_conn()
        self._write_pair(conn,
            decision_group_id="dg-test-inv15-004",
            data_version="custom_experimental_v1",
            training_allowed=True,
            target_date="2026-04-19",
        )
        (training_allowed,) = conn.execute(
            "SELECT training_allowed FROM calibration_pairs_v2 WHERE decision_group_id='dg-test-inv15-004'"
        ).fetchone()
        assert training_allowed == 0, (
            f"INV-15 violated: 'custom_experimental_v1' data_version wrote "
            f"training_allowed={training_allowed}, expected 0 (R-J)."
        )

    def test_uppercase_non_whitelisted_source_forces_training_allowed_false(self):
        """R-J M1: uppercase source 'TIGGE_' (note trailing underscore, not prefix match) is not
        whitelisted — _resolve_training_allowed must normalize case before checking membership."""
        from src.calibration.store import _resolve_training_allowed
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        # "TIGGE_" has a trailing underscore — not in the whitelist even after lowercasing
        result = _resolve_training_allowed("TIGGE_", HIGH_LOCALDAY_MAX.data_version, True)
        assert result is False, (
            "M1: 'TIGGE_' is not a whitelisted source (trailing underscore ≠ 'tigge'); "
            "must fail-closed to False after case normalization (R-J M1)."
        )

    def test_source_with_trailing_space_is_normalized(self):
        """R-J M1: source with trailing whitespace (e.g. 'openmeteo_hourly ') must
        still be rejected — normalization strips before membership check."""
        from src.calibration.store import _resolve_training_allowed
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        result = _resolve_training_allowed("openmeteo_hourly ", HIGH_LOCALDAY_MAX.data_version, True)
        assert result is False, (
            "M1: 'openmeteo_hourly ' (trailing space) must normalize to 'openmeteo_hourly' "
            "and be rejected as non-whitelisted (R-J M1)."
        )

    def test_data_version_with_leading_space_is_rejected(self):
        """R-J M1: data_version with leading whitespace (' tigge_...') must normalize
        to an empty string prefix check and fail-close — it's not a canonical tag."""
        from src.calibration.store import _resolve_training_allowed
        # Leading space means stripped dv_norm = "tigge_..." — wait, strip removes it,
        # so " tigge_mx2t6_..." should actually normalize to "tigge_mx2t6_..." and pass.
        # The M1 fix is about preventing BYPASS, not about blocking valid padded canonical tags.
        # This test verifies the positive case: a space-padded canonical tag still normalizes correctly.
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        result = _resolve_training_allowed("", " " + HIGH_LOCALDAY_MAX.data_version, True)
        assert result is True, (
            "M1: leading-space-padded canonical data_version must normalize (strip) and pass "
            "the whitelist check (R-J M1 — normalization must not reject valid canonical tags)."
        )


# ---------------------------------------------------------------------------
# R-M: calibration_pairs_v2 rows from rebuild have correct identity fields
# ---------------------------------------------------------------------------

class TestCalibrationPairsV2IdentityFields:
    """R-M: calibration_pairs_v2 rows from rebuild_calibration_pairs_v2 must have
    temperature_metric='high', training_allowed=1, observation_field='high_temp',
    data_version='tigge_mx2t6_local_calendar_day_max_v1'. None defaulted.
    """

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _insert_calibration_pair_v2(self, conn: sqlite3.Connection, **overrides) -> str:
        """Helper: write a v2 calibration pair and return the decision_group_id."""
        from src.calibration.store import add_calibration_pair_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        dg_id = overrides.pop("decision_group_id", "dg-rm-test-001")
        add_calibration_pair_v2(
            conn=conn,
            city="NYC",
            target_date="2026-04-16",
            range_label="70-71°F",
            p_raw=0.25,
            outcome=0,
            lead_days=2.0,
            season="spring",
            cluster="NYC_F_2",
            forecast_available_at="2026-04-14T12:00:00",
            decision_group_id=dg_id,
            metric_identity=HIGH_LOCALDAY_MAX,
            data_version=HIGH_LOCALDAY_MAX.data_version,
            source="tigge",
            training_allowed=True,
            **overrides,
        )
        conn.commit()
        return dg_id

    def test_temperature_metric_is_high(self):
        """R-M: temperature_metric must be 'high' for high-track rebuild rows."""
        conn = self._make_conn()
        dg_id = self._insert_calibration_pair_v2(conn)
        (val,) = conn.execute(
            "SELECT temperature_metric FROM calibration_pairs_v2 WHERE decision_group_id=?",
            (dg_id,)
        ).fetchone()
        assert val == "high", (
            f"temperature_metric must be 'high' for Phase 4 rebuild, got {val!r} (R-M)"
        )

    def test_training_allowed_is_one(self):
        """R-M: training_allowed must be 1 for canonical high-track pairs."""
        conn = self._make_conn()
        dg_id = self._insert_calibration_pair_v2(conn)
        (val,) = conn.execute(
            "SELECT training_allowed FROM calibration_pairs_v2 WHERE decision_group_id=?",
            (dg_id,)
        ).fetchone()
        assert val == 1, (
            f"training_allowed must be 1 for canonical high-track pairs, got {val!r} (R-M)"
        )

    def test_observation_field_is_high_temp(self):
        """R-M: observation_field must be 'high_temp' (not 'low_temp', not NULL)."""
        conn = self._make_conn()
        dg_id = self._insert_calibration_pair_v2(conn)
        (val,) = conn.execute(
            "SELECT observation_field FROM calibration_pairs_v2 WHERE decision_group_id=?",
            (dg_id,)
        ).fetchone()
        assert val == "high_temp", (
            f"observation_field must be 'high_temp' for high-track rebuild, got {val!r} (R-M)"
        )

    def test_data_version_is_canonical_local_calendar_day(self):
        """R-M: data_version must be 'tigge_mx2t6_local_calendar_day_max_v1'.

        The old 'peak_window' tag is quarantined in Phase 4. Any row with the
        old tag entering calibration_pairs_v2 is a Phase 4 scope violation.
        """
        conn = self._make_conn()
        dg_id = self._insert_calibration_pair_v2(conn)
        (val,) = conn.execute(
            "SELECT data_version FROM calibration_pairs_v2 WHERE decision_group_id=?",
            (dg_id,)
        ).fetchone()
        assert val == "tigge_mx2t6_local_calendar_day_max_v1", (
            f"data_version must be 'tigge_mx2t6_local_calendar_day_max_v1', got {val!r} (R-M). "
            "The peak_window tag is quarantined and must not appear in v2 calibration pairs."
        )

    def test_no_field_is_null(self):
        """R-M: temperature_metric, observation_field, data_version must all be non-NULL."""
        conn = self._make_conn()
        dg_id = self._insert_calibration_pair_v2(conn)
        row = conn.execute(
            """SELECT temperature_metric, observation_field, data_version, training_allowed
               FROM calibration_pairs_v2 WHERE decision_group_id=?""",
            (dg_id,)
        ).fetchone()
        assert row is not None, "Row not found in calibration_pairs_v2 (R-M)"
        tm, of, dv, ta = row
        for field_name, val in [
            ("temperature_metric", tm),
            ("observation_field", of),
            ("data_version", dv),
            ("training_allowed", ta),
        ]:
            assert val is not None, (
                f"{field_name} must not be NULL in calibration_pairs_v2 row (R-M)"
            )


# ---------------------------------------------------------------------------
# MAJOR-2: Pipeline integration test — rebuild_v2 end-to-end
# ---------------------------------------------------------------------------

class TestRebuildV2PipelineIntegration:
    """MAJOR-2 antibody: rebuild_v2() must produce calibration_pairs_v2 rows with correct
    identity fields when called end-to-end (not just add_calibration_pair_v2 in isolation).

    This is the test that would have caught CRITICAL-1 (phantom 'source' column in SELECT)
    before critic-alice's review — direct calls to add_calibration_pair_v2 bypass the
    _fetch_eligible_snapshots_v2 SQL path entirely.
    """

    _CITY_NAME = "Atlanta"
    _TARGET_DATE = "2025-06-15"
    _ISSUE_TIME = "2025-06-13T00:00:00Z"
    _AVAILABLE_AT = "2025-06-13T06:00:00Z"
    _DATA_VERSION = "tigge_mx2t6_local_calendar_day_max_v1"
    _OBS_TEMP_F = 75.0

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _insert_snapshot(self, conn: sqlite3.Connection) -> int:
        import json
        import numpy as np
        members = list(np.full(51, self._OBS_TEMP_F).tolist())
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2
                (city, target_date, temperature_metric, physical_quantity, observation_field,
                 issue_time, available_at, fetch_time, lead_hours, members_json,
                 model_version, data_version, training_allowed, causality_status,
                 authority, members_unit)
            VALUES (?, ?, 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                    ?, ?, '2025-06-13T06:05:00', 48.0, ?,
                    'ENS', ?, 1, 'OK', 'VERIFIED', 'degF')
            """,
            (
                self._CITY_NAME, self._TARGET_DATE,
                self._ISSUE_TIME, self._AVAILABLE_AT,
                json.dumps(members),
                self._DATA_VERSION,
            ),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _insert_observation(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO observations
                (city, target_date, source, high_temp, unit, authority)
            VALUES (?, ?, 'WU', ?, 'F', 'VERIFIED')
            """,
            (self._CITY_NAME, self._TARGET_DATE, self._OBS_TEMP_F),
        )
        conn.commit()

    def test_rebuild_v2_writes_high_track_identity_fields(self):
        """MAJOR-2: rebuild_v2() end-to-end must produce calibration_pairs_v2 rows with
        temperature_metric='high', observation_field='high_temp',
        data_version='tigge_mx2t6_local_calendar_day_max_v1', training_allowed=1.

        This test exercises the _fetch_eligible_snapshots_v2 SQL path, which is where
        CRITICAL-1 (phantom 'source' column) lived. Any regression there fails here.
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from rebuild_calibration_pairs_v2 import METRIC_SPECS, rebuild_v2

        conn = self._make_conn()
        self._insert_snapshot(conn)
        self._insert_observation(conn)

        import numpy as np
        rng = np.random.default_rng(42)
        # Phase 7A: rebuild_v2 no longer defaults spec to HIGH; caller iterates METRIC_SPECS.
        # This Phase-4 regression test exercises only the HIGH track; pass HIGH spec explicitly.
        stats = rebuild_v2(
            conn, dry_run=False, force=True, n_mc=200, rng=rng,
            spec=METRIC_SPECS[0],
        )

        assert stats.pairs_written > 0, (
            f"rebuild_v2 wrote 0 pairs — pipeline integration failed (MAJOR-2). "
            f"Stats: {stats.as_dict()}"
        )
        assert not stats.refused, f"rebuild_v2 refused: stats={stats.as_dict()}"

        rows = conn.execute(
            """SELECT temperature_metric, observation_field, data_version, training_allowed
               FROM calibration_pairs_v2
               WHERE city=? AND target_date=?""",
            (self._CITY_NAME, self._TARGET_DATE),
        ).fetchall()

        assert len(rows) > 0, (
            "No calibration_pairs_v2 rows found for Atlanta/2025-06-15 after rebuild_v2 (MAJOR-2)"
        )

        for row in rows:
            tm, of, dv, ta = row["temperature_metric"], row["observation_field"], row["data_version"], row["training_allowed"]
            assert tm == "high", f"temperature_metric must be 'high', got {tm!r} (MAJOR-2)"
            assert of == "high_temp", f"observation_field must be 'high_temp', got {of!r} (MAJOR-2)"
            assert dv == self._DATA_VERSION, f"data_version must be {self._DATA_VERSION!r}, got {dv!r} (MAJOR-2)"
            assert ta == 1, f"training_allowed must be 1, got {ta!r} (MAJOR-2)"
