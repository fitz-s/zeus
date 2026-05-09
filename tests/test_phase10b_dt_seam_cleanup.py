# Created: 2026-04-19
# Last reused/audited: 2026-05-05
# Authority basis: Phase 10B DT-Seam Cleanup, 2026-04-29 design simplification audit F4, and 2026-05-01 stale live-state artifact tracking.
# Lifecycle: created=2026-04-19; last_reviewed=2026-05-05; last_reused=2026-05-05
# Purpose: Phase 10B "DT-Seam Cleanup" antibodies (R-CL..R-CP).
#          Dedicated test file per critic-carol cycle-3 L2 convention.
#          Do NOT co-locate with test_phase10a_hygiene.py.
# Reuse: Run after status_summary, v2 row-count, bankroll semantics, or execution capability status changes.

from __future__ import annotations

import ast
import json
import sqlite3
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# R-CL — S1 R3: replay legacy WHERE metric-aware
# ---------------------------------------------------------------------------


class TestRCLReplayLegacyWhereMetricAware:
    """R-CL.1/2: _forecast_rows_for uses metric-aware WHERE clause.

    R-CL.1: LOW replay with v2 empty + legacy row with forecast_low=X,
            forecast_high=NULL → returns the LOW row.
    R-CL.2: HIGH replay unchanged behavior — pair-negative surgical-revert probe.
    """

    def _make_test_db(self) -> sqlite3.Connection:
        """Create minimal in-memory DB with legacy forecasts + ensemble_snapshots tables."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # ReplayContext.__init__ checks for ensemble_snapshots
        conn.execute("CREATE TABLE ensemble_snapshots (id INTEGER PRIMARY KEY)")
        conn.execute("""
            CREATE TABLE forecasts (
                city TEXT,
                target_date TEXT,
                source TEXT,
                forecast_basis_date TEXT,
                forecast_issue_time TEXT,
                lead_days REAL,
                forecast_high REAL,
                forecast_low REAL,
                temp_unit TEXT
            )
        """)
        # historical_forecasts_v2 must exist but be empty (Golden Window)
        conn.execute("""
            CREATE TABLE historical_forecasts_v2 (
                id INTEGER PRIMARY KEY,
                temperature_metric TEXT
            )
        """)
        # Row where forecast_high IS NULL but forecast_low IS NOT NULL
        conn.execute("""
            INSERT INTO forecasts VALUES
            ('NYC', '2026-04-01', 'ecmwf', '2026-03-31', NULL, 1.0, NULL, 12.5, 'F')
        """)
        # Row where forecast_high IS NOT NULL (normal HIGH row)
        conn.execute("""
            INSERT INTO forecasts VALUES
            ('NYC', '2026-04-01', 'ecmwf', '2026-03-30', NULL, 2.0, 85.0, NULL, 'F')
        """)
        conn.commit()
        return conn

    def test_r_cl_1_low_replay_returns_low_row(self):
        """R-CL.1: LOW replay with forecast_low-only row returns that row."""
        from src.engine.replay import ReplayContext

        conn = self._make_test_db()
        ctx = ReplayContext.__new__(ReplayContext)
        ctx.conn = conn
        ctx._sp = ""

        rows = ctx._forecast_rows_for("NYC", "2026-04-01", temperature_metric="low")
        assert len(rows) >= 1, "LOW replay must return the forecast_low-only row"
        assert rows[0]["forecast_low"] == 12.5

    def test_r_cl_2_high_replay_unchanged(self):
        """R-CL.2: HIGH replay returns only forecast_high-not-null rows (pair-negative)."""
        from src.engine.replay import ReplayContext

        conn = self._make_test_db()
        ctx = ReplayContext.__new__(ReplayContext)
        ctx.conn = conn
        ctx._sp = ""

        rows = ctx._forecast_rows_for("NYC", "2026-04-01", temperature_metric="high")
        # Should NOT return the forecast_high=NULL row
        for row in rows:
            assert row["forecast_high"] is not None, (
                "HIGH replay must filter to forecast_high IS NOT NULL"
            )


# ---------------------------------------------------------------------------
# R-CM — S2 R4: oracle_penalty (city, metric) keying
# ---------------------------------------------------------------------------


class TestRCMOraclePenaltyCityMetricKeying:
    """R-CM.1/2/3: oracle_penalty cache keyed by (city, metric).

    R-CM.1: seeding (chicago, high) penalty → get_oracle_info(chicago, low)
            returns a separate, uncontaminated OracleInfo.
    R-CM.2: cache invalidation per (city, metric) — invalidating HIGH does not
            evict LOW.
    R-CM.3: legacy flat JSON {city: {oracle_error_rate: N}} loads as (city, "high")
            entries only (backward-compat migration).
    """

    def _reset_cache(self):
        """Force oracle_penalty module to reload its cache on next call."""
        import src.strategy.oracle_penalty as op
        op._cache = None

    def test_r_cm_1_high_seed_does_not_contaminate_low(self, tmp_path, monkeypatch):
        """R-CM.1: HIGH penalty entry is isolated from LOW.

        Post-A3 (PLAN.md §A3 + Bug review Finding C): LOW always returns
        METRIC_UNSUPPORTED until a LOW snapshot bridge ships. The seam-
        isolation property still holds — reading HIGH does not affect
        what LOW reports — but the LOW status is METRIC_UNSUPPORTED, not
        the legacy default-OK.
        """
        import src.strategy.oracle_penalty as op
        op._reset_for_test()

        # Post-A3 schema: bridge writes n + mismatches. n=25, m=10 →
        # posterior_upper_95 ≈ 0.564 > 0.10 → BLACKLIST.
        json_path = tmp_path / "data" / "oracle_error_rates.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps({
            "chicago": {
                "high": {"n": 25, "mismatches": 10},  # BLACKLIST tier
            }
        }))
        monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

        info_high = op.get_oracle_info("chicago", "high")
        info_low = op.get_oracle_info("chicago", "low")

        assert info_high.status.value == "BLACKLIST", (
            "chicago HIGH should be BLACKLIST (n=25 m=10 → posterior_upper_95 > 0.10)"
        )
        # Post-A3 (PLAN.md §A3 + Bug review Finding C): LOW always returns
        # METRIC_UNSUPPORTED until a LOW oracle bridge ships. Seam isolation
        # is preserved (HIGH=BLACKLIST does not flip LOW), but the LOW
        # status reflects the missing-bridge reality, not silent OK.
        assert info_low.status.value == "METRIC_UNSUPPORTED", (
            "chicago LOW must be METRIC_UNSUPPORTED until LOW oracle bridge ships"
        )
        assert info_low.penalty_multiplier == 0.0

    def test_r_cm_2_invalidating_high_does_not_evict_low(self, tmp_path, monkeypatch):
        """R-CM.2: (city, 'high') and (city, 'low') are independent cache keys.

        Post-A3: LOW is METRIC_UNSUPPORTED regardless of cache contents
        (computed at get_oracle_info time, not at load). The seam-
        isolation property is now stronger — even if the cache is
        fully populated for both metrics, LOW never inherits HIGH's
        status.
        """
        import src.strategy.oracle_penalty as op
        op._reset_for_test()

        json_path = tmp_path / "data" / "oracle_error_rates.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        # Post-A3 schema: include n + mismatches.
        json_path.write_text(json.dumps({
            "london": {
                "high": {"n": 100, "mismatches": 4},  # CAUTION (p95 ~ 0.090)
                "low": {"n": 100, "mismatches": 0},   # would be OK if not METRIC_UNSUPPORTED
            }
        }))
        monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

        # Load both. LOW gets METRIC_UNSUPPORTED short-circuit.
        info_high = op.get_oracle_info("london", "high")
        info_low_first = op.get_oracle_info("london", "low")

        assert info_high.status.value == "CAUTION"
        assert info_low_first.status.value == "METRIC_UNSUPPORTED"

        # Simulate "invalidating" HIGH by deleting from raw cache.
        if op._cache is not None:
            op._cache.pop(("london", "high"), None)

        # LOW must still report METRIC_UNSUPPORTED — independent of HIGH cache.
        info_low_after = op.get_oracle_info("london", "low")
        assert info_low_after.status.value == "METRIC_UNSUPPORTED", (
            "Evicting (london, high) must not affect what LOW reports"
        )

    def test_r_cm_3_legacy_flat_json_loads_as_high_only(self, tmp_path, monkeypatch):
        """R-CM.3: Legacy flat {city: {oracle_error_rate: N}} treated as (city, 'high').

        Post-A3 the loader returns ``(records, mtime)``; legacy flat records
        carry only ``oracle_error_rate`` (no n / mismatches), so the reader
        treats them as MISSING (mult 0.5) until the next bridge run writes
        the new schema. The "loaded as (city, 'high') key only" cache-shape
        property is what this test pins; the resulting STATUS now degrades
        to MISSING per PLAN.md §A3 (the legacy point estimate alone cannot
        bound the posterior).
        """
        import src.strategy.oracle_penalty as op
        op._reset_for_test()

        json_path = tmp_path / "data" / "oracle_error_rates.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        # Legacy flat shape (no 'high'/'low' sub-keys, no n/m fields).
        json_path.write_text(json.dumps({
            "tokyo": {"oracle_error_rate": 0.08, "status": "CAUTION"}
        }))
        monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

        # Post-A3 _load() returns (records, mtime).
        loaded, _mtime = op._load()

        assert ("tokyo", "high") in loaded, (
            "Legacy flat JSON must create (city, 'high') cache key"
        )
        assert ("tokyo", "low") not in loaded, (
            "Legacy flat JSON must NOT create (city, 'low') cache key"
        )
        # Status degrades to MISSING because n/mismatches absent.
        info = op.get_oracle_info("tokyo", "high")
        assert info.status.value == "MISSING", (
            "Legacy flat record without n/m → MISSING (rate alone cannot bound posterior)"
        )


# ---------------------------------------------------------------------------
# R-CN — S3 R5: Literal annotations at 9 runtime seams
# ---------------------------------------------------------------------------


class TestRCNLiteralAnnotations:
    """R-CN.1/2: Literal["high", "low"] annotation at each of the 9 allowlist seams.

    R-CN.1: AST probe — each seam has Literal annotation on temperature_metric.
    R-CN.2: Allowlist-scoped gate — the 9 seams carry Literal; probe confirms
            the annotation exists in the known good locations.
    """

    _SEAMS = [
        ("src/state/portfolio.py", "Position", "temperature_metric"),
        ("src/calibration/manager.py", "get_calibrator", "temperature_metric"),
        ("src/calibration/manager.py", "_fit_from_pairs", "temperature_metric"),
        ("src/engine/replay.py", "_forecast_rows_for", "temperature_metric"),
        ("src/engine/replay.py", "_forecast_reference_for", "temperature_metric"),
        ("src/engine/replay.py", "_forecast_snapshot_for", "temperature_metric"),
        ("src/engine/replay.py", "get_decision_reference_for", "temperature_metric"),
        ("src/engine/replay.py", "_replay_one_settlement", "temperature_metric"),
        ("src/engine/replay.py", "run_replay", "temperature_metric"),
    ]

    def _has_literal_annotation(self, source: str, param_name: str) -> bool:
        """Check that any function/class in source has a Literal annotation for param."""
        return "Literal[" in source and param_name in source

    def test_r_cn_1_all_9_seams_have_literal_annotation(self):
        """R-CN.1: Each of the 9 allowlist seams has Literal annotation."""
        missing = []
        for rel_path, scope_name, param_name in self._SEAMS:
            src_path = PROJECT_ROOT / rel_path
            assert src_path.exists(), f"File not found: {rel_path}"
            source = src_path.read_text()
            if "Literal[" not in source:
                missing.append(f"{rel_path} (no Literal import/usage)")
            elif "Literal" not in source or "temperature_metric" not in source:
                missing.append(f"{rel_path}:{scope_name} missing Literal on {param_name}")

        assert not missing, (
            f"Seams missing Literal annotation: {missing}\n"
            f"S3 R5 P10B requires Literal[\"high\", \"low\"] on temperature_metric."
        )

    def test_r_cn_2_literal_import_present_in_each_seam_file(self):
        """R-CN.2: Each seam file imports Literal from typing."""
        files_needing_literal = {rel_path for rel_path, _, _ in self._SEAMS}
        missing_import = []
        for rel_path in sorted(files_needing_literal):
            src_path = PROJECT_ROOT / rel_path
            source = src_path.read_text()
            if "from typing import" not in source or "Literal" not in source:
                missing_import.append(rel_path)

        assert not missing_import, (
            f"Files missing `from typing import Literal`: {missing_import}"
        )


# ---------------------------------------------------------------------------
# R-CO — S4 R9: FDR family_id metric-aware EXTEND
# ---------------------------------------------------------------------------


class TestRCOFDRFamilyIdMetricAware:
    """R-CO.1/2: FDR family_id discriminates by temperature_metric.

    R-CO.1: EXTEND — metric-discriminating assertion (HIGH != LOW).
    R-CO.2: Evaluator AST probe — caller sites pass temperature_metric kwarg.
    """

    def test_r_co_1_family_id_discriminates_by_metric(self):
        """R-CO.1: make_hypothesis_family_id with HIGH != LOW for same other args."""
        from src.strategy.selection_family import make_hypothesis_family_id

        base_args = dict(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        )
        h_id_high = make_hypothesis_family_id(**base_args, temperature_metric="high")
        h_id_low = make_hypothesis_family_id(**base_args, temperature_metric="low")

        assert h_id_high != h_id_low, (
            "family_id must discriminate by metric: "
            "HIGH and LOW candidates must have separate BH discovery budgets"
        )

    def test_r_co_2_evaluator_callers_pass_temperature_metric(self):
        """R-CO.2: AST probe — evaluator.py make_*_family_id callers pass temperature_metric."""
        src_path = PROJECT_ROOT / "src" / "engine" / "evaluator.py"
        source = src_path.read_text()
        tree = ast.parse(source)

        call_sites_with_metric: list[int] = []
        call_sites_without_metric: list[int] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = None
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr

            if func_name not in ("make_hypothesis_family_id", "make_edge_family_id"):
                continue

            kwarg_names = [kw.arg for kw in node.keywords]
            if "temperature_metric" in kwarg_names:
                call_sites_with_metric.append(node.lineno)
            else:
                call_sites_without_metric.append(node.lineno)

        assert call_sites_with_metric, (
            "No evaluator.py calls to make_*_family_id found with temperature_metric kwarg"
        )
        assert not call_sites_without_metric, (
            f"evaluator.py call sites missing temperature_metric kwarg at lines: "
            f"{call_sites_without_metric}"
        )


# ---------------------------------------------------------------------------
# R-CP — S5 R11: v2 row-count sensor + discrepancy flag
# ---------------------------------------------------------------------------


class TestRCPV2RowCountSensor:
    """R-CP.1/2: status_summary v2 row-count sensor and discrepancy flag.

    R-CP.1: v2_row_counts dict is populated from live sqlite table metadata
            (not hardcoded and not unqualified) for 5 v2 tables.
    R-CP.2: discrepancy flag fires when dual_track_scaffold_claimed=True AND
            any v2 table has 0 rows.
    """

    def _make_empty_v2_conn(self):
        """In-memory DB with 5 v2 tables, all empty."""
        conn = sqlite3.connect(":memory:")
        for table in (
            "platt_models_v2",
            "calibration_pairs_v2",
            "ensemble_snapshots_v2",
            "historical_forecasts_v2",
            "settlements_v2",
        ):
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
        return conn

    def _make_populated_v2_conn(self):
        """In-memory DB with all v2 tables having 1 row."""
        conn = self._make_empty_v2_conn()
        for table in (
            "platt_models_v2",
            "calibration_pairs_v2",
            "ensemble_snapshots_v2",
            "historical_forecasts_v2",
            "settlements_v2",
        ):
            conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
        conn.commit()
        return conn

    def _empty_lifecycle_funnel(self):
        return {
            "status": "certified_empty",
            "authority": "derived_operator_visibility",
            "counts": {
                "evaluated": 0,
                "selected": 0,
                "rejected": 0,
                "submitted": 0,
                "filled": 0,
                "learned": 0,
            },
            "source_errors": [],
        }

    def _empty_calibration_serving(self):
        return {
            "schema_version": 1,
            "status": "certified_empty",
            "authority": "derived_operator_visibility",
            "buckets": [],
            "source_errors": [],
        }

    def test_r_cp_1_v2_row_counts_queries_actual_tables(self):
        """R-CP.1: _get_v2_row_counts returns real table row signals."""
        from src.observability.status_summary import _get_v2_row_counts

        empty_conn = self._make_empty_v2_conn()
        counts_empty = _get_v2_row_counts(empty_conn)

        assert set(counts_empty.keys()) == {
            "platt_models_v2",
            "calibration_pairs_v2",
            "ensemble_snapshots_v2",
            "historical_forecasts_v2",
            "settlements_v2",
        }, "v2_row_counts must cover all 5 v2 tables"

        assert all(v == 0 for v in counts_empty.values()), (
            "Empty v2 tables must return 0 counts (not hardcoded)"
        )

        # Verify it actually queries — insert 1 row to platt_models_v2
        populated_conn = self._make_populated_v2_conn()
        counts_populated = _get_v2_row_counts(populated_conn)
        assert counts_populated["platt_models_v2"] == 1, (
            "_get_v2_row_counts must return actual row count, not hardcoded 0"
        )

    def test_r_cp_1b_prefers_attached_world_over_empty_trade_shadow(self, tmp_path):
        """F4: attached world data tables outrank empty trade shadow tables."""
        from src.observability.status_summary import _V2_TABLES, _get_v2_row_counts

        trade_conn = self._make_empty_v2_conn()
        world_path = tmp_path / "world.db"
        world_conn = sqlite3.connect(str(world_path))
        expected_counts: dict[str, int] = {}
        for index, table in enumerate(_V2_TABLES, start=1):
            world_conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
            for _ in range(index):
                world_conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
            expected_counts[table] = index
        world_conn.commit()
        world_conn.close()

        trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
        counts = _get_v2_row_counts(trade_conn)

        assert counts == expected_counts

    def test_r_cp_1d_v2_row_counts_avoid_full_table_count_scans(self):
        """F4 latency guard: status writes must not run COUNT(*) over large v2 tables."""
        from src.observability.status_summary import _get_v2_row_counts

        conn = self._make_populated_v2_conn()
        statements: list[str] = []
        conn.set_trace_callback(statements.append)

        counts = _get_v2_row_counts(conn)

        assert counts["platt_models_v2"] == 1
        assert not any("COUNT(*)" in statement.upper() for statement in statements), (
            "v2 status row-count telemetry must not full-scan large tables every cycle"
        )

    def test_r_cp_1c_existing_empty_world_table_does_not_fallback_to_trade_shadow(self, tmp_path):
        """F4 pair-negative: an existing world table is the authority even if empty."""
        from src.observability.status_summary import _V2_TABLES, _get_v2_row_counts

        trade_conn = self._make_populated_v2_conn()
        world_path = tmp_path / "world.db"
        world_conn = sqlite3.connect(str(world_path))
        for table in _V2_TABLES:
            world_conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        world_conn.commit()
        world_conn.close()

        trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
        counts = _get_v2_row_counts(trade_conn)

        assert all(count == 0 for count in counts.values()), (
            "Present world v2 tables must be reported as world truth, not "
            "replaced by populated trade shadow rows"
        )

    def test_r_cp_2_discrepancy_flag_fires_when_claim_true_and_zero_rows(self):
        """R-CP.2: discrepancy flag 'v2_empty_despite_closure_claim' fires when
        dual_track_scaffold_claimed=True AND any v2 table has 0 rows.
        """
        from src.observability.status_summary import _get_v2_row_counts

        empty_conn = self._make_empty_v2_conn()
        v2_counts = _get_v2_row_counts(empty_conn)

        # Simulate the discrepancy flag logic directly
        dual_track_scaffold_claimed = True
        discrepancy_flags: list[str] = []

        if dual_track_scaffold_claimed and v2_counts:
            empty_v2 = [t for t, c in v2_counts.items() if c == 0]
            if empty_v2:
                discrepancy_flags.append("v2_empty_despite_closure_claim")

        assert "v2_empty_despite_closure_claim" in discrepancy_flags, (
            "Discrepancy flag must fire: claim=True AND v2 tables all empty"
        )

    def test_r_cp_2b_discrepancy_flag_absent_when_v2_populated(self):
        """R-CP.2 pair-negative: flag absent when v2 tables are populated."""
        from src.observability.status_summary import _get_v2_row_counts

        populated_conn = self._make_populated_v2_conn()
        v2_counts = _get_v2_row_counts(populated_conn)

        dual_track_scaffold_claimed = True
        discrepancy_flags: list[str] = []

        if dual_track_scaffold_claimed and v2_counts:
            empty_v2 = [t for t, c in v2_counts.items() if c == 0]
            if empty_v2:
                discrepancy_flags.append("v2_empty_despite_closure_claim")

        assert "v2_empty_despite_closure_claim" not in discrepancy_flags, (
            "Flag must be absent when all v2 tables have rows"
        )

    def test_r_cp_1_missing_v2_table_returns_zero_not_error(self):
        """R-CP.1 resilience: missing v2 table returns 0, not an exception."""
        from src.observability.status_summary import _get_v2_row_counts

        # DB with NO v2 tables at all
        empty_conn = sqlite3.connect(":memory:")
        counts = _get_v2_row_counts(empty_conn)

        assert all(v == 0 for v in counts.values()), (
            "Missing v2 table must return 0 count, not raise exception"
        )

    def test_legacy_positions_artifact_flags_canonical_empty_conflict(self, tmp_path, monkeypatch):
        """Legacy positions.json is telemetry only, but nonterminal rows must be visible."""
        from src.observability import status_summary as status_summary_module

        positions_path = tmp_path / "positions.json"
        positions_path.write_text(json.dumps({
            "updated_at": "2026-04-12T19:59:43+00:00",
            "positions": [
                {
                    "trade_id": "stale-json-live",
                    "market_id": "m1",
                    "city": "Seattle",
                    "target_date": "2026-04-14",
                    "bin_label": "Will the highest temperature in Seattle be between 50-51F on April 14?",
                    "direction": "buy_no",
                    "state": "entered",
                    "strategy_key": "opening_inertia",
                    "chain_state": "synced",
                    "cost_basis_usd": 4.9047,
                }
            ],
        }))
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)

        summary = status_summary_module._legacy_positions_artifact_summary({
            "status": "empty",
            "open_positions": 0,
        })

        assert summary["authority"] == "legacy_json_derived_observability_only"
        assert summary["canonical_truth_source"] == "position_current"
        assert summary["status"] == "conflict"
        assert summary["active_positions"] == 1
        assert summary["active_cost_basis_usd"] == pytest.approx(4.9047)
        assert summary["target_dates"] == ["2026-04-14"]
        assert summary["chain_state_counts"] == {"synced": 1}
        assert summary["sample_positions"][0]["trade_id"] == "stale-json-live"
        assert summary["conflicts"] == ["canonical_empty_legacy_active_positions"]

    def test_status_summary_escalates_legacy_positions_conflict_to_infrastructure_red(self, tmp_path, monkeypatch):
        """Relationship: status remains DB-empty while surfacing stale legacy state as RED telemetry."""
        from src.observability import status_summary as status_summary_module

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"
        positions_path.write_text(json.dumps({
            "updated_at": "2026-04-12T19:59:43+00:00",
            "positions": [
                {
                    "trade_id": "stale-json-live",
                    "market_id": "m1",
                    "city": "Seattle",
                    "target_date": "2026-04-14",
                    "bin_label": "Will the highest temperature in Seattle be between 50-51F on April 14?",
                    "direction": "buy_no",
                    "state": "entered",
                    "strategy_key": "opening_inertia",
                    "chain_state": "synced",
                    "cost_basis_usd": 4.9047,
                }
            ],
        }))

        class DummyConn:
            def close(self):
                return None

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "empty",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 0.0,
                "unrealized_pnl": 0.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {"status": "fresh", "by_strategy": {}, "stale_strategy_keys": []},
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: self._empty_lifecycle_funnel())
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "GREEN"})
        status = json.loads(status_path.read_text())

        assert status["portfolio"]["open_positions"] == 0
        assert status["portfolio"]["legacy_artifact"]["active_positions"] == 1
        assert status["portfolio"]["legacy_artifact"]["conflicts"] == [
            "canonical_empty_legacy_active_positions"
        ]
        assert status["risk"]["infrastructure_level"] == "RED"
        assert "legacy_positions_json_conflicts_with_canonical_empty" in status["risk"]["infrastructure_issues"]

    def test_status_summary_exposes_s2_lifecycle_funnel(self, tmp_path, monkeypatch):
        """S2 visibility stays a derived status block, not a cycle authority."""
        from src.observability import status_summary as status_summary_module

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"
        positions_path.write_text(json.dumps({"updated_at": "2026-05-08T00:00:00+00:00", "positions": []}))
        lifecycle_funnel = {
            "status": "observed",
            "authority": "derived_operator_visibility",
            "counts": {
                "evaluated": 4,
                "selected": 3,
                "rejected": 2,
                "submitted": 2,
                "filled": 1,
                "learned": 1,
            },
        }

        class DummyConn:
            def close(self):
                return None

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "empty",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 0.0,
                "unrealized_pnl": 0.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {"status": "fresh", "by_strategy": {}, "stale_strategy_keys": []},
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: lifecycle_funnel)
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "GREEN"})
        status = json.loads(status_path.read_text())

        assert status["lifecycle_funnel"] == lifecycle_funnel
        assert status["lifecycle_funnel"]["authority"] == "derived_operator_visibility"
        assert "lifecycle_funnel" not in status["cycle"]

    def test_status_summary_exposes_s3_calibration_serving(self, tmp_path, monkeypatch):
        """S3 calibration serving visibility stays top-level derived telemetry."""
        from src.observability import status_summary as status_summary_module

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"
        positions_path.write_text(json.dumps({"updated_at": "2026-05-09T00:00:00+00:00", "positions": []}))
        calibration_serving = {
            "schema_version": 1,
            "status": "observed",
            "authority": "derived_operator_visibility",
            "buckets": [
                {
                    "bucket_key": "high:UK:MAM:ecmwf:v1",
                    "forecast_ready": True,
                    "calibration_ready": False,
                    "trade_ready": False,
                }
            ],
            "source_errors": [],
        }

        class DummyConn:
            def close(self):
                return None

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "empty",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 0.0,
                "unrealized_pnl": 0.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {"status": "fresh", "by_strategy": {}, "stale_strategy_keys": []},
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: self._empty_lifecycle_funnel())
        monkeypatch.setattr(status_summary_module, "build_calibration_serving_status", lambda conn: calibration_serving)
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "GREEN", "wallet_balance_usd": 211.37})
        status = json.loads(status_path.read_text())

        assert status["calibration_serving"] == calibration_serving
        assert status["calibration_serving"]["authority"] == "derived_operator_visibility"
        assert "calibration_serving" not in status["cycle"]

    @pytest.mark.parametrize(
        ("serving_status", "expected_issue"),
        [
            ("query_error", "calibration_serving_summary_unavailable"),
            ("partial", "calibration_serving_summary_partial"),
        ],
    )
    def test_status_summary_surfaces_calibration_serving_degradation(
        self,
        tmp_path,
        monkeypatch,
        serving_status,
        expected_issue,
    ):
        """S3 status degradation is visible as infrastructure telemetry."""
        from src.observability import status_summary as status_summary_module

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"
        positions_path.write_text(json.dumps({"updated_at": "2026-05-09T00:00:00+00:00", "positions": []}))
        calibration_serving = {
            "schema_version": 1,
            "status": serving_status,
            "authority": "derived_operator_visibility",
            "buckets": [],
            "source_errors": [{"source": "readiness_state", "error": "table_missing"}],
        }

        class DummyConn:
            def close(self):
                return None

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "ok",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 0.0,
                "unrealized_pnl": 0.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {"status": "fresh", "by_strategy": {}, "stale_strategy_keys": []},
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: self._empty_lifecycle_funnel())
        monkeypatch.setattr(status_summary_module, "build_calibration_serving_status", lambda conn: calibration_serving)
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "GREEN", "wallet_balance_usd": 211.37})
        status = json.loads(status_path.read_text())

        assert status["calibration_serving"] == calibration_serving
        assert status["risk"]["infrastructure_level"] == "YELLOW"
        assert expected_issue in status["risk"]["infrastructure_issues"]

    @pytest.mark.parametrize(
        ("funnel_status", "expected_issue"),
        [
            ("query_error", "lifecycle_funnel_summary_unavailable"),
            ("partial", "lifecycle_funnel_summary_partial"),
        ],
    )
    def test_status_summary_surfaces_lifecycle_funnel_degradation(
        self,
        tmp_path,
        monkeypatch,
        funnel_status,
        expected_issue,
    ):
        """S2 status degradation is visible as infrastructure telemetry."""
        from src.observability import status_summary as status_summary_module

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"
        positions_path.write_text(json.dumps({"updated_at": "2026-05-08T00:00:00+00:00", "positions": []}))
        lifecycle_funnel = {
            "status": funnel_status,
            "authority": "derived_operator_visibility",
            "source_errors": [{"source": "position_events", "error_type": "OperationalError"}],
        }

        class DummyConn:
            def close(self):
                return None

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "ok",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 0.0,
                "unrealized_pnl": 0.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {"status": "fresh", "by_strategy": {}, "stale_strategy_keys": []},
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: lifecycle_funnel)
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "GREEN", "wallet_balance_usd": 211.37})
        status = json.loads(status_path.read_text())

        assert status["lifecycle_funnel"] == lifecycle_funnel
        assert status["risk"]["infrastructure_level"] == "YELLOW"
        assert expected_issue in status["risk"]["infrastructure_issues"]

    def test_bankroll_semantics_status_keeps_pnl_out_of_effective_bankroll(self, tmp_path, monkeypatch):
        """Relationship: wallet-equity bankroll is not recomputed from analytics PnL."""
        from src.observability import status_summary as status_summary_module
        from src.runtime import bankroll_provider

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"

        class DummyConn:
            def close(self):
                return None

        record = bankroll_provider.BankrollOfRecord(
            value_usd=211.37,
            fetched_at="2026-05-05T00:00:00+00:00",
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        )

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(bankroll_provider, "current", lambda: record)
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "ok",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 15.0,
                "unrealized_pnl": 3.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {
                "status": "fresh",
                "by_strategy": {
                    "center_buy": {
                        "open_exposure_usd": 15.0,
                        "realized_pnl_30d": 4.0,
                        "unrealized_pnl": 3.0,
                    },
                },
                "stale_strategy_keys": [],
            },
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: self._empty_lifecycle_funnel())
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "GREEN"})
        status = json.loads(status_path.read_text())

        assert status["portfolio"]["initial_bankroll"] == pytest.approx(211.37)
        assert status["portfolio"]["total_pnl"] == pytest.approx(7.0)
        assert status["portfolio"]["effective_bankroll"] == pytest.approx(211.37)
        assert status["portfolio"]["bankroll"] == pytest.approx(211.37)
        assert status["portfolio"]["bankroll_object_identity"] == "wallet_equity"
        assert status["portfolio"]["effective_bankroll_derivation"] == "wallet_equity_no_pnl"
        assert status["truth"]["compatibility_inputs"]["bankroll_fallback_source"] == "bankroll_provider"

    def test_bankroll_semantics_rejects_unproven_riskguard_bankroll(self, tmp_path, monkeypatch):
        """Relationship: legacy risk rows cannot promote wallet+PnL into status bankroll."""
        from src.observability import status_summary as status_summary_module
        from src.runtime import bankroll_provider

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"

        class DummyConn:
            def close(self):
                return None

        record = bankroll_provider.BankrollOfRecord(
            value_usd=211.37,
            fetched_at="2026-05-05T00:00:00+00:00",
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        )

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "GREEN")
        monkeypatch.setattr(
            status_summary_module,
            "_get_risk_details",
            lambda: {
                "initial_bankroll": 211.37,
                "effective_bankroll": 157.0,
                "realized_pnl": 4.0,
                "unrealized_pnl": 3.0,
                "total_pnl": 7.0,
            },
        )
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(bankroll_provider, "current", lambda: record)
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "ok",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 15.0,
                "unrealized_pnl": 3.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {"status": "fresh", "by_strategy": {}, "stale_strategy_keys": []},
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: self._empty_lifecycle_funnel())
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "GREEN"})
        status = json.loads(status_path.read_text())

        assert status["portfolio"]["total_pnl"] == pytest.approx(7.0)
        assert status["portfolio"]["initial_bankroll"] == pytest.approx(211.37)
        assert status["portfolio"]["effective_bankroll"] == pytest.approx(211.37)
        assert status["portfolio"]["effective_bankroll"] != pytest.approx(157.0)
        assert status["portfolio"]["bankroll_rejected_source"] == "riskguard_unproven"
        assert status["truth"]["compatibility_inputs"]["bankroll_rejected_source"] == "riskguard_unproven"
        assert "bankroll_rejected_riskguard_unproven" in status["risk"]["infrastructure_issues"]

    def test_bankroll_semantics_status_degrades_when_wallet_truth_missing(self, tmp_path, monkeypatch):
        """Relationship: missing wallet truth does not become 0+PnL synthetic bankroll."""
        from src.observability import status_summary as status_summary_module
        from src.runtime import bankroll_provider

        status_path = tmp_path / "status_summary.json"
        positions_path = tmp_path / "positions.json"

        class DummyConn:
            def close(self):
                return None

        monkeypatch.setattr(status_summary_module, "STATUS_PATH", status_path)
        monkeypatch.setattr(status_summary_module, "LEGACY_POSITIONS_PATH", positions_path)
        monkeypatch.setattr(status_summary_module, "_get_risk_level", lambda: "DATA_DEGRADED")
        monkeypatch.setattr(status_summary_module, "_get_risk_details", lambda: {})
        monkeypatch.setattr(status_summary_module, "get_trade_connection_with_world", lambda: DummyConn())
        monkeypatch.setattr(bankroll_provider, "current", lambda: None)
        monkeypatch.setattr(
            status_summary_module,
            "query_position_current_status_view",
            lambda conn: {
                "status": "ok",
                "positions": [],
                "open_positions": 0,
                "total_exposure_usd": 15.0,
                "unrealized_pnl": 3.0,
                "strategy_open_counts": {},
                "chain_state_counts": {},
                "exit_state_counts": {},
                "unverified_entries": 0,
                "day0_positions": 0,
            },
        )
        monkeypatch.setattr(
            status_summary_module,
            "query_strategy_health_snapshot",
            lambda conn, now=None: {
                "status": "fresh",
                "by_strategy": {
                    "center_buy": {
                        "open_exposure_usd": 15.0,
                        "realized_pnl_30d": 4.0,
                        "unrealized_pnl": 3.0,
                    },
                },
                "stale_strategy_keys": [],
            },
        )
        monkeypatch.setattr(status_summary_module, "query_execution_event_summary", lambda conn, not_before=None: {"overall": {}})
        monkeypatch.setattr(status_summary_module, "query_learning_surface_summary", lambda conn, not_before=None: {"by_strategy": {}})
        monkeypatch.setattr(status_summary_module, "query_no_trade_cases", lambda conn, hours=24: [])
        monkeypatch.setattr(status_summary_module, "query_lifecycle_funnel_report", lambda conn, not_before=None: self._empty_lifecycle_funnel())
        monkeypatch.setattr(status_summary_module, "_get_execution_capability_status", lambda: {})
        monkeypatch.setattr(status_summary_module, "is_entries_paused", lambda: False)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_source", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_entries_pause_reason", lambda: None)
        monkeypatch.setattr(status_summary_module, "get_edge_threshold_multiplier", lambda: 1.0)
        monkeypatch.setattr(status_summary_module, "strategy_gates", lambda: {})
        monkeypatch.setattr(status_summary_module, "recommended_autosafe_commands_from_status", lambda status: [])
        monkeypatch.setattr(status_summary_module, "recommended_commands_from_status", lambda status, include_review_required=True: [])
        monkeypatch.setattr(status_summary_module, "review_required_commands_from_status", lambda status: [])

        status_summary_module.write_status({"mode": "opening_hunt", "risk_level": "DATA_DEGRADED"})
        status = json.loads(status_path.read_text())

        assert status["portfolio"]["total_pnl"] == pytest.approx(7.0)
        assert status["portfolio"]["initial_bankroll"] is None
        assert status["portfolio"]["effective_bankroll"] is None
        assert status["portfolio"]["bankroll"] is None
        assert status["portfolio"]["bankroll_truth_status"] == "missing"
        assert status["portfolio"]["effective_bankroll_derivation"] == "missing_wallet_truth"
        assert status["truth"]["compatibility_inputs"]["bankroll_fallback_source"] == "bankroll_provider_unavailable"
        assert "bankroll_truth_missing" in status["risk"]["infrastructure_issues"]


# ---------------------------------------------------------------------------
# Phase 2D — DSA-16 derived execution capability status matrix
# ---------------------------------------------------------------------------


class TestPhase2DExecutionCapabilityStatus:
    """Derived operator visibility for composed execution gates.

    This status output must not become a live-action authority surface. It
    summarizes global blockers and leaves per-intent gates unresolved.
    """

    def _allowing_cutover(self):
        return {
            "state": "LIVE_ENABLED",
            "entry": {"allow_submit": True, "block_reason": None},
            "exit": {"allow_submit": True, "block_reason": None},
            "cancel": {"allow_cancel": True, "block_reason": None},
            "redemption": {"allow_redemption": True, "block_reason": None},
        }

    def _blocking_cutover(self):
        return {
            "state": "NORMAL",
            "entry": {"allow_submit": False, "block_reason": "NORMAL:ENTRY"},
            "exit": {"allow_submit": False, "block_reason": "NORMAL:EXIT"},
            "cancel": {"allow_cancel": False, "block_reason": "NORMAL:CANCEL"},
            "redemption": {"allow_redemption": False, "block_reason": "NORMAL"},
        }

    def _allowing_heartbeat(self):
        return {
            "health": "HEALTHY",
            "last_error": None,
            "entry": {"allow_submit": True, "required_order_types": ["GTC", "GTD"]},
        }

    def _allowing_ws_gap(self):
        return {
            "connected": True,
            "subscription_state": "SUBSCRIBED",
            "gap_reason": "message_received",
            "m5_reconcile_required": False,
            "entry": {"allow_submit": True},
        }

    def _allowing_risk_allocator(self):
        return {
            "configured": True,
            "kill_switch_reason": None,
            "reduce_only": False,
            "entry": {"allow_submit": True, "reason": "ok"},
        }

    def _allowing_collateral(self):
        return {
            "configured": True,
            "authority_tier": "CHAIN",
            "captured_at": "2026-04-29T00:00:00+00:00",
            "reason": "ok",
        }

    def test_phase2d_matrix_blocks_global_actions_on_cutover(self, monkeypatch):
        from src.observability import status_summary as status_summary_module

        monkeypatch.setattr(status_summary_module, "_cutover_summary", self._blocking_cutover)
        monkeypatch.setattr(status_summary_module, "_heartbeat_summary", self._allowing_heartbeat)
        monkeypatch.setattr(status_summary_module, "_ws_gap_summary", self._allowing_ws_gap)
        monkeypatch.setattr(
            status_summary_module,
            "_risk_allocator_summary",
            self._allowing_risk_allocator,
        )
        monkeypatch.setattr(status_summary_module, "_collateral_summary", self._allowing_collateral)

        matrix = status_summary_module._get_execution_capability_status()

        assert matrix["derived_only"] is True
        assert matrix["live_action_authorized"] is False
        for action, allow_key in (
            ("entry", "global_allow_submit"),
            ("exit", "global_allow_submit"),
            ("cancel", "global_allow_cancel"),
            ("redeem", "global_allow_redeem"),
        ):
            assert matrix[action]["status"] == "blocked"
            assert matrix[action][allow_key] is False
            assert matrix[action]["live_action_authorized"] is False
            assert "cutover_guard" in matrix[action]["blocked_components"]

    def test_phase2d_matrix_keeps_per_intent_gates_unresolved(self, monkeypatch):
        from src.observability import status_summary as status_summary_module

        monkeypatch.setattr(status_summary_module, "_cutover_summary", self._allowing_cutover)
        monkeypatch.setattr(status_summary_module, "_heartbeat_summary", self._allowing_heartbeat)
        monkeypatch.setattr(status_summary_module, "_ws_gap_summary", self._allowing_ws_gap)
        monkeypatch.setattr(
            status_summary_module,
            "_risk_allocator_summary",
            self._allowing_risk_allocator,
        )
        monkeypatch.setattr(status_summary_module, "_collateral_summary", self._allowing_collateral)

        matrix = status_summary_module._get_execution_capability_status()

        assert matrix["entry"]["status"] == "requires_intent"
        assert matrix["entry"]["global_allow_submit"] is True
        assert matrix["exit"]["status"] == "requires_intent"
        assert matrix["exit"]["global_allow_submit"] is True
        assert matrix["cancel"]["status"] == "requires_intent"
        assert matrix["cancel"]["global_allow_cancel"] is True
        assert matrix["redeem"]["status"] == "requires_intent"
        assert matrix["redeem"]["global_allow_redeem"] is True

        entry_unresolved = {
            component["component"]
            for component in matrix["entry"]["required_intent_components"]
        }
        exit_unresolved = {
            component["component"]
            for component in matrix["exit"]["required_intent_components"]
        }
        cancel_unresolved = {
            component["component"]
            for component in matrix["cancel"]["required_intent_components"]
        }
        assert "executable_snapshot_gate" in entry_unresolved
        assert "risk_allocator_capacity" in entry_unresolved
        assert "collateral_buy_amount" in entry_unresolved
        assert "executable_snapshot_gate" in exit_unresolved
        assert "replacement_sell_guard" in exit_unresolved
        assert "collateral_sell_inventory" in exit_unresolved
        assert "cancel_command_identity" in cancel_unresolved
        assert "venue_order_cancelability" in cancel_unresolved

    def test_phase2d_status_summary_does_not_import_executor_authority(self):
        source = Path("src/observability/status_summary.py").read_text()

        assert "src.execution.executor" not in source
        assert "execute_intent" not in source
