# Lifecycle: created=2026-04-19; last_reviewed=2026-04-19; last_reused=never
# Purpose: Phase 9C Gate F prep antibodies (R-BZ..R-CE). Dedicated test file
#          per critic-carol cycle-3 L2 observation — P8/9A/9B antibodies were
#          piled into test_phase8_shadow_code.py + test_dual_track_law_stubs.py;
#          P9C has its own home to reduce checkbox-antibody contamination risk
#          and make phase-boundary regression math clean.
# Reuse: Anchors on phase9c_contract.md (S1 L3 CRITICAL + S2 A3 + S3 A1 + S4 A4
#        + S5 B1 + S6 B3). All P9C antibodies here; DT#2 R-BY/R-BY.2 + R-BV/
#        R-BW/R-BX live in test_dual_track_law_stubs.py (law-stub convention).

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# R-BZ — L3 CRITICAL: get_calibrator is metric-aware
# ---------------------------------------------------------------------------


class TestRBZGetCalibratorMetricAware:
    """Phase 9C L3 CRITICAL fix: get_calibrator reads platt_models_v2 with
    metric discrimination. Pre-P9C the function read exclusively from legacy
    platt_models (no metric column) — a LOW candidate would silently receive
    a HIGH Platt model. This is the structural CRITICAL that blocked LOW
    deployment.

    Relationship antibody per critic-carol cycle-3 L9 runtime-probe pattern:
    the cross-module invariant is writer (save_platt_model_v2) ↔ reader
    (get_calibrator) symmetric on `temperature_metric` axis. Constructs a
    DB with both HIGH + LOW rows for same (cluster, season) and asserts
    get_calibrator returns the metric-matching row.
    """

    def _make_db_with_two_metrics(self) -> sqlite3.Connection:
        """Build minimal v2-schema DB with HIGH + LOW Platt rows for same bucket."""
        from src.state.schema.v2_schema import apply_v2_schema
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)

        # Insert HIGH + LOW Platt rows with DIFFERENT param_A so we can
        # disambiguate which was returned.
        now = "2026-04-18T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO platt_models_v2
                (model_key, temperature_metric, cluster, season, data_version,
                 input_space, param_A, param_B, param_C, bootstrap_params_json,
                 n_samples, brier_insample, fitted_at, is_active, authority)
            VALUES
                ('high:US-Northeast:JJA:v1:width_normalized_density',
                 'high', 'US-Northeast', 'JJA',
                 'tigge_mx2t6_local_calendar_day_max_v1',
                 'width_normalized_density',
                 1.23, 0.5, 0.0, '[]', 200, 0.10, ?, 1, 'VERIFIED')
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO platt_models_v2
                (model_key, temperature_metric, cluster, season, data_version,
                 input_space, param_A, param_B, param_C, bootstrap_params_json,
                 n_samples, brier_insample, fitted_at, is_active, authority)
            VALUES
                ('low:US-Northeast:JJA:v1:width_normalized_density',
                 'low', 'US-Northeast', 'JJA',
                 'tigge_mn2t6_local_calendar_day_min_v1',
                 'width_normalized_density',
                 4.56, 0.7, 0.0, '[]', 200, 0.15, ?, 1, 'VERIFIED')
            """,
            (now,),
        )
        conn.commit()
        return conn

    def test_get_calibrator_with_metric_low_returns_low_model(self, monkeypatch):
        """R-BZ.1: get_calibrator(temperature_metric='low') reads LOW row.

        Without this fix, LOW candidate would get HIGH Platt model.
        """
        from src.calibration.manager import get_calibrator
        from src.config import City

        conn = self._make_db_with_two_metrics()
        city = City(
            name="NYC", lat=40.7, lon=-74.0,
            timezone="America/New_York", settlement_unit="F",
            cluster="US-Northeast", wu_station="KNYC",
        )

        # LOW path — must return the row with param_A=4.56, NOT 1.23
        cal_low, level_low = get_calibrator(
            conn, city, "2026-07-15",  # July → JJA
            temperature_metric="low",
        )
        assert cal_low is not None, (
            "R-BZ.1: LOW calibrator lookup returned None despite a LOW row "
            "existing in platt_models_v2. Pre-P9C this was guaranteed None "
            "(legacy table has no metric). Post-P9C must find the row."
        )
        assert cal_low.A == pytest.approx(4.56), (
            f"R-BZ.1: LOW calibrator returned wrong param_A. "
            f"Got {cal_low.A}; expected 4.56 (LOW row). If this is 1.23, "
            f"the HIGH row was returned — L3 CRITICAL regressed."
        )

    def test_get_calibrator_with_metric_high_returns_high_model(self):
        """R-BZ.2: get_calibrator(temperature_metric='high') reads HIGH row.

        Paired-positive antibody per critic-carol cycle-1 L7 — both metrics
        must be exercised to prevent silent HIGH→LOW flip.
        """
        from src.calibration.manager import get_calibrator
        from src.config import City

        conn = self._make_db_with_two_metrics()
        city = City(
            name="NYC", lat=40.7, lon=-74.0,
            timezone="America/New_York", settlement_unit="F",
            cluster="US-Northeast", wu_station="KNYC",
        )

        cal_high, _ = get_calibrator(
            conn, city, "2026-07-15",
            temperature_metric="high",
        )
        assert cal_high is not None
        assert cal_high.A == pytest.approx(1.23), (
            f"R-BZ.2: HIGH calibrator returned wrong param_A. "
            f"Got {cal_high.A}; expected 1.23 (HIGH row)."
        )

    def test_get_calibrator_default_metric_is_high_backward_compat(self):
        """R-BZ.3: get_calibrator() with no temperature_metric param defaults
        to 'high' — backward compat for pre-P9C callers (if any missed).
        """
        from src.calibration.manager import get_calibrator
        from src.config import City

        conn = self._make_db_with_two_metrics()
        city = City(
            name="NYC", lat=40.7, lon=-74.0,
            timezone="America/New_York", settlement_unit="F",
            cluster="US-Northeast", wu_station="KNYC",
        )

        # No kwarg — should behave as 'high'
        cal, _ = get_calibrator(conn, city, "2026-07-15")
        assert cal is not None
        assert cal.A == pytest.approx(1.23), (
            f"R-BZ.3: default temperature_metric regressed; expected 'high' "
            f"behavior (param_A=1.23); got {cal.A}"
        )


# ---------------------------------------------------------------------------
# R-CA — A3: Day0LowNowcastSignal.p_vector
# ---------------------------------------------------------------------------


class TestRCADay0LowP_Vector:
    """Phase 9C A3: Day0LowNowcastSignal now exposes p_vector(bins, n_mc, rng)
    matching Day0HighSignal signature. Pre-P9C only p_bin(low, high) existed,
    so evaluator calls `signal.p_vector(bins)` on a LOW Day0 signal would
    AttributeError. Critical gate for Gate F (live LOW Day0 trading).
    """

    def test_p_vector_returns_per_bin_probabilities(self):
        """R-CA.1: p_vector(bins) returns np.ndarray with probability per bin."""
        import numpy as np
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal

        signal = Day0LowNowcastSignal(
            observed_low_so_far=38.0,
            member_mins_remaining=np.array([35.0, 36.0, 37.0, 38.5, 40.0]),
            current_temp=42.0,
            hours_remaining=6.0,
            unit="F",
        )

        class _Bin:
            def __init__(self, lo, hi):
                self.low = lo
                self.high = hi

        bins = [_Bin(30, 35), _Bin(35, 40), _Bin(40, 45)]

        probs = signal.p_vector(bins)

        assert isinstance(probs, np.ndarray)
        assert probs.shape == (3,)
        # Probabilities must be in [0, 1]
        assert (probs >= 0.0).all() and (probs <= 1.0).all(), (
            f"R-CA.1: p_vector returned out-of-range probabilities: {probs}"
        )
        # Each p must match p_bin individually (consistency check)
        for i, b in enumerate(bins):
            assert probs[i] == pytest.approx(signal.p_bin(b.low, b.high)), (
                f"R-CA.1: p_vector[{i}] != p_bin({b.low}, {b.high})"
            )

    def test_p_vector_does_not_delegate_to_high(self):
        """R-CA.2: p_vector MUST NOT import from day0_high_signal (R-BE
        invariant: no HIGH↔LOW cross-import for Day0 signals).
        Closes P6 handoff concern about lazy-HIGH-delegate anti-pattern.
        """
        import ast
        from pathlib import Path

        src = Path(__file__).parent.parent / "src" / "signal" / "day0_low_nowcast_signal.py"
        tree = ast.parse(src.read_text())
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        for imp in imports:
            if isinstance(imp, ast.ImportFrom):
                assert imp.module != "src.signal.day0_high_signal", (
                    f"R-CA.2: day0_low_nowcast_signal imports from "
                    f"day0_high_signal at L{imp.lineno} — R-BE invariant "
                    f"violated."
                )
                assert "day0_high_signal" not in (imp.module or ""), (
                    f"R-CA.2: day0_low_nowcast_signal imports day0_high_signal "
                    f"module at L{imp.lineno}"
                )


# ---------------------------------------------------------------------------
# R-CB — A1: _forecast_rows_for conditional v2 read
# ---------------------------------------------------------------------------


class TestRCBForecastRowsV2:
    """Phase 9C A1 (B093 half-2): _forecast_rows_for queries
    historical_forecasts_v2 WITH metric filter when v2 has data; else falls
    back to legacy `forecasts` table. Before P9C the function was
    legacy-only — any v2 data was unreachable even once Golden Window lifts.
    """

    def test_v2_populated_query_filters_by_metric(self):
        """R-CB.1: when historical_forecasts_v2 has rows for the city+date+metric,
        _forecast_rows_for returns ONLY the v2 rows with matching metric."""
        from src.engine.replay import ReplayContext
        from src.state.schema.v2_schema import apply_v2_schema
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)

        # v2 schema is per-row metric-partitioned (single forecast_value +
        # temperature_metric column). Seed HIGH and LOW rows for same
        # (city, target_date); expect translated downstream to (forecast_high=95.0)
        # / (forecast_low=32.0) via _forecast_rows_for's legacy-shape shim.
        now = "2026-07-10T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO historical_forecasts_v2
                (city, target_date, source, temperature_metric,
                 forecast_value, temp_unit, lead_days, available_at)
            VALUES
                ('NYC', '2026-07-15', 'TIGGE_ECMWF', 'high',
                 95.0, 'F', 5, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO historical_forecasts_v2
                (city, target_date, source, temperature_metric,
                 forecast_value, temp_unit, lead_days, available_at)
            VALUES
                ('NYC', '2026-07-15', 'TIGGE_ECMWF', 'low',
                 32.0, 'F', 5, ?)
            """,
            (now,),
        )
        conn.commit()

        ctx = ReplayContext(conn)
        low_rows = ctx._forecast_rows_for("NYC", "2026-07-15", temperature_metric="low")
        assert len(low_rows) == 1, (
            f"R-CB.1: expected 1 LOW row from v2; got {len(low_rows)}"
        )
        assert low_rows[0]["forecast_low"] == 32.0, (
            f"R-CB.1: v2 LOW row's forecast_low must be 32.0; got {low_rows[0]['forecast_low']}"
        )
        assert low_rows[0]["forecast_high"] is None, (
            f"R-CB.1: LOW row's forecast_high is NULL in v2 (metric-partitioned)"
        )

    def test_v2_empty_falls_back_to_legacy(self):
        """R-CB.2: when v2 is empty (Golden Window current state), legacy
        `forecasts` table is queried unchanged. Backward-compat preservation."""
        from src.engine.replay import ReplayContext
        from src.state.schema.v2_schema import apply_v2_schema
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)

        # Seed ONLY legacy forecasts (v2 is empty)
        conn.execute(
            """
            INSERT INTO forecasts
                (city, target_date, source, forecast_basis_date, forecast_issue_time,
                 lead_days, forecast_high, forecast_low, temp_unit)
            VALUES
                ('NYC', '2026-07-15', 'TIGGE_ECMWF', '2026-07-10',
                 '2026-07-10T00:00:00+00:00', 5.0, 95.0, 70.0, 'F')
            """
        )
        conn.commit()

        ctx = ReplayContext(conn)
        rows = ctx._forecast_rows_for("NYC", "2026-07-15", temperature_metric="high")
        assert len(rows) == 1, (
            f"R-CB.2: legacy fallback failed; expected 1 row; got {len(rows)}"
        )
        assert rows[0]["forecast_high"] == 95.0


# ---------------------------------------------------------------------------
# R-CC — A4: DT#7 evaluator wire (boundary_ambiguous refusal)
# ---------------------------------------------------------------------------


class TestRCCBoundaryGateWired:
    """Phase 9C A4: evaluator's candidate decision flow reads
    boundary_ambiguous from ensemble_snapshots_v2 and refuses the candidate
    when the flag is True. Pre-P9C the contract function
    boundary_ambiguous_refuses_signal existed as ORPHAN code (no caller).
    This antibody locks the wire.
    """

    def test_read_v2_snapshot_metadata_empty_table_returns_empty_dict(self):
        """R-CC.1: helper returns {} when v2 is empty — permissive default
        that keeps the gate dormant until data flows (current Golden Window
        state)."""
        from src.engine.evaluator import _read_v2_snapshot_metadata
        from src.state.schema.v2_schema import apply_v2_schema
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)

        meta = _read_v2_snapshot_metadata(conn, "NYC", "2026-07-15", "low")
        assert meta == {}, (
            f"R-CC.1: empty v2 must yield empty dict for permissive gate; "
            f"got {meta!r}"
        )

    def test_read_v2_snapshot_metadata_with_flagged_row_returns_true(self):
        """R-CC.2: helper returns {"boundary_ambiguous": True} when v2 has a
        boundary_ambiguous=1 row for (city, target_date, metric). Combined
        with boundary_ambiguous_refuses_signal, this drives the evaluator
        refusal at the candidate gate.
        """
        from src.engine.evaluator import _read_v2_snapshot_metadata
        from src.contracts.boundary_policy import boundary_ambiguous_refuses_signal
        from src.state.schema.v2_schema import apply_v2_schema
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)

        # Insert a v2 snapshot row with boundary_ambiguous=1. Schema has
        # many NOT NULL columns (see v2_schema.py ensemble_snapshots_v2).
        now = "2026-07-10T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2
                (city, target_date, temperature_metric,
                 physical_quantity, observation_field,
                 available_at, fetch_time, lead_hours,
                 members_json, model_version, data_version,
                 boundary_ambiguous, authority)
            VALUES
                ('NYC', '2026-07-15', 'low',
                 'mn2t6_local_calendar_day_min_v1', 'low_temp',
                 ?, ?, 120.0, '[]',
                 'v1', 'tigge_mn2t6_local_calendar_day_min_v1',
                 1, 'VERIFIED')
            """,
            (now, now),
        )
        conn.commit()

        meta = _read_v2_snapshot_metadata(conn, "NYC", "2026-07-15", "low")
        assert meta.get("boundary_ambiguous") is True, (
            f"R-CC.2: v2 row with boundary_ambiguous=1 must yield True in "
            f"helper output; got meta={meta!r}"
        )
        # End-to-end relationship: function consumed by the gate
        assert boundary_ambiguous_refuses_signal(meta) is True, (
            f"R-CC.2: the complete chain helper→gate must return True for "
            f"a flagged snapshot; got False"
        )


# ---------------------------------------------------------------------------
# R-CD — B1: --temperature-metric CLI flag on run_replay.py
# ---------------------------------------------------------------------------


class TestRCDRunReplayCLIFlag:
    """Phase 9C B1: scripts/run_replay.py exposes --temperature-metric flag
    so operators can select the LOW audit lane from shell. Pre-P9C the
    kwarg was Python-API only (critic-carol cycle-2 MINOR-1 forward-log).
    """

    def test_run_replay_argparser_accepts_temperature_metric_low(self):
        """R-CD.1: argparser shape includes --temperature-metric with
        choices=[high, low] and parses 'low' correctly."""
        import argparse
        import importlib.util

        script_path = Path(__file__).parent.parent / "scripts" / "run_replay.py"
        # Cannot easily import the script (it has top-level side effects);
        # parse its source for the argparse wiring.
        source = script_path.read_text()
        assert '--temperature-metric' in source, (
            "R-CD.1: scripts/run_replay.py missing --temperature-metric flag"
        )
        assert 'choices=["high", "low"]' in source, (
            "R-CD.1: --temperature-metric must restrict to high/low"
        )
        assert 'temperature_metric=args.temperature_metric' in source, (
            "R-CD.1: CLI arg not threaded into run_replay() call"
        )


# ---------------------------------------------------------------------------
# R-CE — B3: save_portfolio source param + JSON audit
# ---------------------------------------------------------------------------


class TestRCESavePortfolioSource:
    """Phase 9C B3: save_portfolio accepts `source` kwarg logged into JSON
    audit trail. Caller-side discipline per DT#6 §B Interpretation B.
    No runtime enforcement — observability only.
    """

    def test_save_portfolio_records_source_tag(self, tmp_path):
        """R-CE.1: save_portfolio(source='test_origin') → JSON has
        `save_source` key with the tag value."""
        from src.state.portfolio import PortfolioState, save_portfolio

        state = PortfolioState(positions=[], bankroll=100.0)
        save_path = tmp_path / "positions-test-source.json"
        save_portfolio(state, path=save_path, source="test_origin")

        data = json.loads(save_path.read_text())
        assert data.get("save_source") == "test_origin", (
            f"R-CE.1: save_source tag missing or wrong; got "
            f"{data.get('save_source')!r}, expected 'test_origin'. "
            f"All keys: {list(data.keys())!r}"
        )

    def test_save_portfolio_default_source_is_internal(self, tmp_path):
        """R-CE.2: save_portfolio without source kwarg defaults to 'internal'
        (backward-compat)."""
        from src.state.portfolio import PortfolioState, save_portfolio

        state = PortfolioState(positions=[], bankroll=100.0)
        save_path = tmp_path / "positions-test-default-source.json"
        save_portfolio(state, path=save_path)

        data = json.loads(save_path.read_text())
        assert data.get("save_source") == "internal"
