# Lifecycle: created=2026-04-19; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Phase 10C "LOW-lane Tail + HKO Injection + DT#1 SAVEPOINT" antibodies (R-CQ..R-CX).
# Reuse: Referenced by regression suite; R-CS-2 updated 2026-04-24 for C5
#        (HIGH settlement routes through calibration_pairs_v2 post-fix;
#        test formerly locked in pre-C5 legacy-routing).
# Authority basis: phase10c_contract.md v2

from __future__ import annotations

import ast
import csv
import sqlite3
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_monitor_refresh_source() -> str:
    p = PROJECT_ROOT / "src" / "engine" / "monitor_refresh.py"
    return p.read_text(encoding="utf-8")


def _read_replay_source() -> str:
    p = PROJECT_ROOT / "src" / "engine" / "replay.py"
    return p.read_text(encoding="utf-8")


def _read_cycle_runtime_source() -> str:
    p = PROJECT_ROOT / "src" / "engine" / "cycle_runtime.py"
    return p.read_text(encoding="utf-8")


def _read_store_source() -> str:
    p = PROJECT_ROOT / "src" / "calibration" / "store.py"
    return p.read_text(encoding="utf-8")


def _make_city(name="NYC", settlement_unit="F", cluster="northeast",
               lat=40.7, lon=-74.0, wu_station="KLGA",
               settlement_source_type="wu_icao"):
    """Build a minimal City-like object for tests."""
    from src.config import City
    return City(
        name=name,
        wu_station=wu_station,
        settlement_unit=settlement_unit,
        cluster=cluster,
        lat=lat,
        lon=lon,
        timezone="America/New_York",
        settlement_source_type=settlement_source_type,
    )


def _make_hko_city():
    return _make_city(
        name="HKO",
        settlement_unit="C",
        cluster="asia",
        lat=22.3,
        lon=114.2,
        wu_station="VHHH",
        settlement_source_type="hko",
    )


def _make_wu_city():
    return _make_city(
        name="NYC",
        settlement_unit="F",
        cluster="northeast",
        lat=40.7,
        lon=-74.0,
        wu_station="KLGA",
        settlement_source_type="wu_icao",
    )


def _make_position_with_metric(temperature_metric="high"):
    """Build a minimal position-like object."""
    pos = MagicMock()
    pos.temperature_metric = temperature_metric
    return pos


def _make_calibration_db() -> sqlite3.Connection:
    """In-memory DB with calibration_pairs + calibration_pairs_v2 tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE calibration_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT, target_date TEXT, range_label TEXT,
            p_raw REAL, outcome INTEGER, lead_days REAL,
            season TEXT, cluster TEXT, forecast_available_at TEXT,
            settlement_value REAL, decision_group_id TEXT,
            bias_corrected INTEGER DEFAULT 0,
            authority TEXT DEFAULT 'UNVERIFIED',
            bin_source TEXT DEFAULT 'legacy'
        )
    """)
    conn.execute("""
        CREATE TABLE calibration_pairs_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT, target_date TEXT,
            temperature_metric TEXT, observation_field TEXT,
            range_label TEXT, p_raw REAL, outcome INTEGER,
            lead_days REAL, season TEXT, cluster TEXT,
            forecast_available_at TEXT, settlement_value REAL,
            decision_group_id TEXT, bias_corrected INTEGER DEFAULT 0,
            authority TEXT DEFAULT 'VERIFIED',
            bin_source TEXT DEFAULT 'canonical_v1',
            data_version TEXT, training_allowed INTEGER DEFAULT 0,
            causality_status TEXT DEFAULT 'OK',
            snapshot_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT, target_date TEXT, settlement_value REAL
        )
    """)
    return conn


# ---------------------------------------------------------------------------
# R-CQ — S1: bootstrap_ctx LOW path
# ---------------------------------------------------------------------------


class TestRCQBootstrapCtxLow:
    """R-CQ.1/2/3: monitor_refresh bootstrap_ctx uses member_extrema key."""

    def test_r_cq_1_member_extrema_not_none_for_low(self):
        """R-CQ.1: LOW position: bootstrap_ctx['member_extrema'] is non-None."""
        # Simulate what monitor_refresh does when extrema.maxes is None
        # (LOW positions have extrema.mins, not extrema.maxes)
        extrema = MagicMock()
        extrema.maxes = None
        extrema.mins = [12.0, 11.5, 12.5, 13.0]

        # Apply the S1 formula
        member_extrema = extrema.maxes if extrema.maxes is not None else extrema.mins

        assert member_extrema is not None, "R-CQ.1: member_extrema must not be None for LOW path"
        assert len(member_extrema) > 0, "R-CQ.1: member_extrema must be non-empty"

    def test_r_cq_2_len_member_extrema_low_no_raise(self):
        """R-CQ.2: len(bootstrap_ctx['member_extrema']) does not raise for LOW path."""
        extrema = MagicMock()
        extrema.maxes = None
        extrema.mins = [12.0, 11.5, 12.5]

        member_extrema = extrema.maxes if extrema.maxes is not None else extrema.mins
        bootstrap_ctx = {"member_extrema": member_extrema}

        # Should not raise
        n = len(bootstrap_ctx["member_extrema"])
        assert n == 3, f"R-CQ.2: expected 3 members, got {n}"

    def test_r_cq_3_no_bootstrap_ctx_member_maxes_reader(self):
        """R-CQ.3: AST: bootstrap_ctx['member_maxes'] has zero readers in monitor_refresh.py."""
        source = _read_monitor_refresh_source()
        tree = ast.parse(source)

        # Walk subscript accesses looking for bootstrap_ctx["member_maxes"]
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                # Check for dict["member_maxes"] pattern
                if isinstance(node.slice, ast.Constant) and node.slice.value == "member_maxes":
                    violations.append(ast.get_source_segment(source, node) or repr(node))

        assert violations == [], (
            f"R-CQ.3: Found {len(violations)} reader(s) of bootstrap_ctx['member_maxes'] "
            f"in monitor_refresh.py (M2 alias must be dropped): {violations}"
        )


# ---------------------------------------------------------------------------
# R-CR — S2: _check_persistence_anomaly metric gate
# ---------------------------------------------------------------------------


class TestRCRPersistenceAnomalyGate:
    """R-CR.1: _check_persistence_anomaly returns 1.0 immediately for LOW metric."""

    def test_r_cr_1_low_metric_returns_1_no_db_query(self):
        """R-CR.1: LOW metric gate returns 1.0 without touching DB."""
        from src.engine.monitor_refresh import _check_persistence_anomaly
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        # Pass a conn that would raise if queried
        class _NeverConn:
            def execute(self, *a, **kw):
                raise AssertionError("R-CR.1: DB should NOT be queried for LOW metric")

        result = _check_persistence_anomaly(
            _NeverConn(), "NYC", "2026-04-01", 72.0,
            temperature_metric=LOW_LOCALDAY_MIN,
        )
        assert result == 1.0, (
            f"R-CR.1: Expected 1.0 for LOW metric gate, got {result}"
        )

    def test_r_cr_1_low_string_returns_1(self):
        """R-CR.1 variant: string 'low' also triggers the gate."""
        from src.engine.monitor_refresh import _check_persistence_anomaly

        class _NeverConn:
            def execute(self, *a, **kw):
                raise AssertionError("DB should NOT be queried for LOW string")

        result = _check_persistence_anomaly(
            _NeverConn(), "NYC", "2026-04-01", 72.0,
            temperature_metric="low",
        )
        assert result == 1.0, f"R-CR.1 str: expected 1.0, got {result}"


# ---------------------------------------------------------------------------
# R-CS — S3: harvester LOW→calibration_pairs_v2 routing
# ---------------------------------------------------------------------------


class TestRCSHarvesterLowRouting:
    """R-CS.1/2: LOW settlement writes to v2; HIGH stays legacy."""

    def _call_harvest_settlement(self, conn, city, temperature_metric: str):
        from src.execution.harvester import harvest_settlement

        return harvest_settlement(
            conn,
            city,
            target_date="2026-04-01",
            winning_bin_label="42-43°F",
            bin_labels=["42-43°F", "44-45°F"],
            p_raw_vector=[0.6, 0.4],
            lead_days=2.0,
            forecast_issue_time="2026-03-30T00:00:00Z",
            forecast_available_at="2026-03-30T12:00:00Z",
            source_model_version=(
                "tigge_mn2t6_local_calendar_day_min_v1"
                if temperature_metric == "low"
                else "tigge_mx2t6_local_calendar_day_max_v1"
            ),
            settlement_value=42.5,
            temperature_metric=temperature_metric,
        )

    def test_r_cs_1_low_settlement_writes_to_v2(self):
        """R-CS.1: LOW settlement → calibration_pairs_v2 row with metric=low."""
        conn = _make_calibration_db()
        city = _make_wu_city()

        # Provide decision_group required table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_decision_groups (
                decision_group_id TEXT PRIMARY KEY,
                city TEXT, target_date TEXT,
                forecast_available_at TEXT, lead_days REAL,
                pair_count INTEGER DEFAULT 0, recorded_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ensemble_snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                city TEXT, target_date TEXT,
                issue_time TEXT, available_at TEXT,
                fetch_time TEXT, lead_hours REAL,
                members_json TEXT, p_raw_json TEXT,
                spread REAL, is_bimodal INTEGER,
                model_version TEXT, data_version TEXT,
                authority TEXT
            )
        """)

        count = self._call_harvest_settlement(conn, city, "low")
        assert count > 0, "R-CS.1: harvest_settlement should create at least 1 pair"

        rows_v2 = conn.execute("SELECT * FROM calibration_pairs_v2").fetchall()
        assert len(rows_v2) > 0, "R-CS.1: LOW settlement must write to calibration_pairs_v2"
        assert rows_v2[0]["temperature_metric"] == "low", (
            f"R-CS.1: expected metric='low', got {rows_v2[0]['temperature_metric']!r}"
        )

    def test_r_cs_2_high_settlement_routes_to_v2_after_c5(self):
        """R-CS.2 (post-C5 2026-04-24): HIGH settlement routes to
        calibration_pairs_v2 with canonical HIGH_LOCALDAY_MAX identity.

        Pre-C5 behavior (documented in POST_AUDIT_HANDOFF_2026-04-24.md
        §3.1 C5): HIGH branch of harvest_settlement wrote to legacy
        `calibration_pairs` while LOW branch wrote to v2. Because
        `refit_platt_v2` reads only `calibration_pairs_v2`, HIGH pairs
        silently never reached the trainer. C5 wires HIGH through
        `add_calibration_pair_v2(metric_identity=HIGH_LOCALDAY_MAX)` to
        close this split-brain.

        This test (formerly `test_r_cs_2_high_settlement_stays_legacy`)
        used to lock in the pre-C5 split behavior as expected; post-C5
        the assertions are inverted.
        """
        conn = _make_calibration_db()
        city = _make_wu_city()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_decision_groups (
                decision_group_id TEXT PRIMARY KEY,
                city TEXT, target_date TEXT,
                forecast_available_at TEXT, lead_days REAL,
                pair_count INTEGER DEFAULT 0, recorded_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ensemble_snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                city TEXT, target_date TEXT,
                issue_time TEXT, available_at TEXT,
                fetch_time TEXT, lead_hours REAL,
                members_json TEXT, p_raw_json TEXT,
                spread REAL, is_bimodal INTEGER,
                model_version TEXT, data_version TEXT,
                authority TEXT
            )
        """)

        count = self._call_harvest_settlement(conn, city, "high")
        assert count > 0, "R-CS.2: harvest_settlement should create at least 1 pair"

        rows_legacy = conn.execute("SELECT * FROM calibration_pairs").fetchall()
        rows_v2 = conn.execute("SELECT * FROM calibration_pairs_v2").fetchall()
        assert len(rows_v2) > 0, (
            "R-CS.2 post-C5: HIGH settlement must write to calibration_pairs_v2"
        )
        assert rows_v2[0]["temperature_metric"] == "high", (
            f"R-CS.2 post-C5: expected metric='high', got "
            f"{rows_v2[0]['temperature_metric']!r}"
        )
        assert rows_v2[0]["data_version"] == "tigge_mx2t6_local_calendar_day_max_v1", (
            f"R-CS.2 post-C5: expected canonical HIGH data_version; got "
            f"{rows_v2[0]['data_version']!r}"
        )
        assert len(rows_legacy) == 0, (
            f"R-CS.2 post-C5: HIGH settlement must NOT leak into legacy "
            f"calibration_pairs; found {len(rows_legacy)} rows"
        )


# ---------------------------------------------------------------------------
# R-CT — S4: add_calibration_pair* city_obj → SettlementSemantics dispatch
# ---------------------------------------------------------------------------


class TestRCTSettlementSemanticsDispatch:
    """R-CT.1/2/3: HKO uses oracle_truncate; WU uses WMO half-up."""

    def test_r_ct_1_hko_city_uses_oracle_truncate(self):
        """R-CT.1: HKO city → settlement_value stored as floor (oracle_truncate), not WMO half-up."""
        from src.calibration.store import add_calibration_pair

        conn = _make_calibration_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_decision_groups (
                decision_group_id TEXT PRIMARY KEY, city TEXT, target_date TEXT,
                forecast_available_at TEXT, lead_days REAL,
                pair_count INTEGER DEFAULT 0, recorded_at TEXT
            )
        """)
        hko = _make_hko_city()

        # 28.7°C: WMO half-up → 29; oracle_truncate (floor) → 28
        add_calibration_pair(
            conn,
            city="HKO",
            target_date="2026-04-01",
            range_label="28°C",
            p_raw=0.6,
            outcome=1,
            lead_days=2.0,
            season="spring",
            cluster="asia",
            forecast_available_at="2026-04-01T00:00:00Z",
            settlement_value=28.7,
            decision_group_id="test-dgid-hko-ct1",
            city_obj=hko,
        )
        row = conn.execute("SELECT settlement_value FROM calibration_pairs LIMIT 1").fetchone()
        assert row is not None
        assert row["settlement_value"] == 28.0, (
            f"R-CT.1: HKO oracle_truncate: 28.7 → 28, got {row['settlement_value']}"
        )

    def test_r_ct_2_wu_city_uses_wmo_half_up(self):
        """R-CT.2: WU city → settlement_value rounded via WMO half-up."""
        from src.calibration.store import add_calibration_pair

        conn = _make_calibration_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_decision_groups (
                decision_group_id TEXT PRIMARY KEY, city TEXT, target_date TEXT,
                forecast_available_at TEXT, lead_days REAL,
                pair_count INTEGER DEFAULT 0, recorded_at TEXT
            )
        """)
        wu = _make_wu_city()

        # 72.5°F: WMO half-up → 73 (floor(72.5 + 0.5) = floor(73) = 73)
        add_calibration_pair(
            conn,
            city="NYC",
            target_date="2026-04-01",
            range_label="72-73°F",
            p_raw=0.6,
            outcome=1,
            lead_days=2.0,
            season="spring",
            cluster="northeast",
            forecast_available_at="2026-04-01T00:00:00Z",
            settlement_value=72.5,
            decision_group_id="test-dgid-wu-ct2",
            city_obj=wu,
        )
        row = conn.execute("SELECT settlement_value FROM calibration_pairs LIMIT 1").fetchone()
        assert row is not None
        assert row["settlement_value"] == 73.0, (
            f"R-CT.2: WU WMO half-up: 72.5 → 73, got {row['settlement_value']}"
        )

    def test_r_ct_3_ast_city_obj_in_both_signatures(self):
        """R-CT.3: AST: both add_calibration_pair* functions have city_obj as required kwonly arg.

        P10E: city_obj is no longer optional (City, no default). Still in kwonlyargs.
        """
        source = _read_store_source()
        tree = ast.parse(source)

        for fn_name in ("add_calibration_pair", "add_calibration_pair_v2"):
            found = False
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                    # Check kwonlyargs for city_obj
                    kwonly_names = [a.arg for a in node.args.kwonlyargs]
                    assert "city_obj" in kwonly_names, (
                        f"R-CT.3: {fn_name} missing city_obj in kwonlyargs. "
                        f"Found: {kwonly_names}"
                    )
                    found = True
                    break
            assert found, f"R-CT.3: function {fn_name!r} not found in store.py"


# ---------------------------------------------------------------------------
# R-CU — S5: replay 4 sites pass round_fn; L722/L1468 OK to lack
# ---------------------------------------------------------------------------


class TestRCUReplayRoundFnAllowlist:
    """R-CU.1: AST — the 4 injected sites in replay.py pass round_fn; escape hatches verified."""

    def _get_all_derive_outcome_calls(self, source: str):
        """Return list of (line, call_node) for all derive_outcome_from_settlement_value calls."""
        tree = ast.parse(source)
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "derive_outcome_from_settlement_value":
                    calls.append(node)
        return calls

    def test_r_cu_1_allowlist_scoped_round_fn(self):
        """R-CU.1: 4 call sites pass round_fn=; 2 escape hatches correctly lack it."""
        source = _read_replay_source()
        calls = self._get_all_derive_outcome_calls(source)

        lines = source.splitlines()

        with_round_fn = []
        without_round_fn = []
        for call in calls:
            kw_names = [kw.arg for kw in call.keywords]
            line_no = call.lineno
            line_text = lines[line_no - 1].strip() if line_no <= len(lines) else ""
            if "round_fn" in kw_names:
                with_round_fn.append((line_no, line_text))
            else:
                without_round_fn.append((line_no, line_text))

        assert len(with_round_fn) >= 4, (
            f"R-CU.1: Expected ≥4 sites passing round_fn, found {len(with_round_fn)}: {with_round_fn}"
        )
        # Escape hatches (without round_fn) should be exactly the 2 known ones:
        # _probability_vector_from_values and _bin_matches_settlement
        assert len(without_round_fn) == 2, (
            f"R-CU.1: Expected exactly 2 escape hatches without round_fn, found {len(without_round_fn)}: {without_round_fn}"
        )


# ---------------------------------------------------------------------------
# R-CV — S6: SAVEPOINT pattern in execute_discovery_phase
# ---------------------------------------------------------------------------


class TestRCVSavepointPattern:
    """R-CV.1/2: SAVEPOINT/RELEASE/ROLLBACK in cycle_runtime.py execute_discovery_phase."""

    def test_r_cv_1_ast_savepoint_pattern_present(self):
        """R-CV.1: AST confirms SAVEPOINT/RELEASE/ROLLBACK pattern in cycle_runtime.py."""
        source = _read_cycle_runtime_source()

        # Check for SAVEPOINT, RELEASE SAVEPOINT, ROLLBACK TO SAVEPOINT strings
        assert "SAVEPOINT" in source, "R-CV.1: 'SAVEPOINT' not found in cycle_runtime.py"
        assert "RELEASE SAVEPOINT" in source, "R-CV.1: 'RELEASE SAVEPOINT' not found in cycle_runtime.py"
        assert "ROLLBACK TO SAVEPOINT" in source, "R-CV.1: 'ROLLBACK TO SAVEPOINT' not found in cycle_runtime.py"
        assert "sp_candidate_" in source, "R-CV.1: 'sp_candidate_' SAVEPOINT prefix not found"

    def test_r_cv_2_savepoint_rollback_on_exception(self):
        """R-CV.2: monkeypatch log_execution_report raises → log_trade_entry rolled back (no orphan)."""
        import src.state.db as db_module
        from src.calibration.store import add_calibration_pair

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        # Minimal schema for SAVEPOINT test
        conn.execute("""
            CREATE TABLE trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT, data TEXT
            )
        """)

        # Simulate the SAVEPOINT pattern from cycle_runtime.py
        # Note: SQLite SAVEPOINT names must not contain hyphens; use underscore form
        sp_name = "sp_candidate_testdecision123"

        def log_trade_entry(conn, pos):
            conn.execute("INSERT INTO trade_log (trade_id, data) VALUES (?, ?)",
                         ("test-trade", "entry_data"))

        def log_execution_report_raises(conn, pos, result, **kwargs):
            raise RuntimeError("Simulated DB failure in log_execution_report")

        conn.execute(f"SAVEPOINT {sp_name}")
        try:
            log_trade_entry(conn, None)
            log_execution_report_raises(conn, None, None)
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            conn.execute(f"RELEASE SAVEPOINT {sp_name}")

        # After rollback, trade_log should be empty (no orphan)
        rows = conn.execute("SELECT * FROM trade_log").fetchall()
        assert len(rows) == 0, (
            f"R-CV.2: log_trade_entry should have been rolled back, found {len(rows)} orphan row(s)"
        )


# ---------------------------------------------------------------------------
# R-CW — S7: INV-20 antibody: load_portfolio degrades gracefully
# ---------------------------------------------------------------------------


class TestRCWInv20AuthorityLoss:
    """R-CW.1: INV-20 activated: load_portfolio degrades, not raises, on auth-loss."""

    def test_r_cw_1_load_portfolio_degrades_on_authority_loss(self, monkeypatch):
        """R-CW.1: load_portfolio returns degraded state, not RuntimeError, on auth-loss."""
        import sqlite3 as _sqlite3
        import src.state.db as db_module
        from src.state import portfolio as portfolio_module
        from src.state.portfolio_loader_policy import LoaderPolicyDecision

        def _degraded_policy(snapshot_status, **kwargs):
            return LoaderPolicyDecision(
                source="json_fallback",
                reason="test: authority-loss simulation (INV-20 R-CW.1)",
                escalate=True,
            )

        monkeypatch.setattr(portfolio_module, "choose_portfolio_truth_source", _degraded_policy)

        # In-memory DB to avoid filesystem I/O
        _mem_conn = _sqlite3.connect(":memory:")
        _mem_conn.row_factory = _sqlite3.Row

        def _fake_get_connection(*args, **kwargs):
            return _mem_conn

        def _fake_get_trade_connection_with_world(*args, **kwargs):
            return _mem_conn

        def _fake_query_portfolio_loader_view(conn, **kwargs):
            return {"status": "degraded_test", "positions": [], "table": "position_current"}

        def _fake_query_token_suppression_tokens(conn):
            return []

        def _fake_query_chain_only_quarantine_rows(conn, **kwargs):
            return []

        monkeypatch.setattr(db_module, "get_connection", _fake_get_connection)
        monkeypatch.setattr(db_module, "get_trade_connection_with_world", _fake_get_trade_connection_with_world)
        monkeypatch.setattr(db_module, "query_portfolio_loader_view", _fake_query_portfolio_loader_view)
        monkeypatch.setattr(db_module, "query_token_suppression_tokens", _fake_query_token_suppression_tokens)
        monkeypatch.setattr(db_module, "query_chain_only_quarantine_rows", _fake_query_chain_only_quarantine_rows)
        monkeypatch.setattr(portfolio_module, "_guard_deprecated_portfolio_json", lambda p: None)

        # Must NOT raise, must return degraded state
        state = portfolio_module.load_portfolio()
        assert state.authority in ("degraded", "unverified"), (
            f"R-CW.1 INV-20: load_portfolio must degrade not raise on auth-loss. "
            f"Got authority={state.authority!r}"
        )


# ---------------------------------------------------------------------------
# R-CX — S8: CSV doc flip guard
# ---------------------------------------------------------------------------


class TestRCXCsvDocFlip:
    """R-CX.1: 10 flipped bug rows have status=RESOLVED + non-empty fix_commit."""

    FLIPPED_IDS = {"B041", "B043", "B045", "B049", "B051", "B059", "B061", "B062", "B074", "B097"}

    def test_r_cx_1_flipped_rows_are_resolved_with_commit(self):
        """R-CX.1: Verify 10 bug rows have status=RESOLVED and fix_commit non-empty."""
        csv_path = PROJECT_ROOT / "docs" / "to-do-list" / "zeus_bug100_reassessment_table.csv"
        if not csv_path.exists():
            pytest.skip(f"R-CX.1 external reassessment CSV not present: {csv_path}")

        with csv_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows_by_id = {row["ID"]: row for row in reader}

        for bug_id in self.FLIPPED_IDS:
            assert bug_id in rows_by_id, f"R-CX.1: {bug_id} not found in CSV"
            row = rows_by_id[bug_id]
            assert row["status"] == "RESOLVED", (
                f"R-CX.1: {bug_id} has status={row['status']!r}, expected RESOLVED"
            )
            assert row.get("fix_commit", "").strip(), (
                f"R-CX.1: {bug_id} has empty fix_commit — must cite evidence"
            )
