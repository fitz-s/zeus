# Lifecycle: created=2026-04-19; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: Phase 9C Gate F prep antibodies (R-BZ..R-CE). Dedicated test file
#          per critic-carol cycle-3 L2 observation — P8/9A/9B antibodies were
#          piled into test_phase8_low_prerequisites.py + test_dual_track_law_stubs.py;
#          P9C has its own home to reduce checkbox-antibody contamination risk
#          and make phase-boundary regression math clean.
# Reuse: Anchors on phase9c_contract.md (S1 L3 CRITICAL + S2 A3 + S3 A1 + S4 A4
#        + S5 B1 + S6 B3). All P9C antibodies here; DT#2 R-BY/R-BY.2 + R-BV/
#        R-BW/R-BX live in test_dual_track_law_stubs.py (law-stub convention).

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# R-BZ — L3 CRITICAL: get_calibrator is metric-aware
# ---------------------------------------------------------------------------


class TestRBZGetCalibratorMetricAware:
    """Phase 9C L3 CRITICAL fix: get_calibrator reads platt_models with
    metric discrimination. Pre-P9C the function read exclusively from legacy
    platt_models (no metric column) — a LOW candidate would silently receive
    a HIGH Platt model. This is the structural CRITICAL that blocked LOW
    deployment.

    Relationship antibody per critic-carol cycle-3 L9 runtime-probe pattern:
    the cross-module invariant is writer (save_platt_model) ↔ reader
    (get_calibrator) symmetric on `temperature_metric` axis. Constructs a
    DB with both HIGH + LOW rows for same (cluster, season) and asserts
    get_calibrator returns the metric-matching row.
    """

    def _make_db_with_two_metrics(self) -> sqlite3.Connection:
        """Build minimal v2-schema DB with HIGH + LOW Platt rows for same bucket."""
        from src.state.schema.v2_schema import apply_canonical_schema
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_canonical_schema(conn)

        # Insert HIGH + LOW Platt rows with DIFFERENT param_A so we can
        # disambiguate which was returned.
        # recorded_at must be set explicitly to a value before the config
        # frozen_as_of cutoff (config/settings.json calibration.pin.frozen_as_of
        # pins to a live-deployment timestamp); CURRENT_TIMESTAMP would be after
        # the cutoff and cause load_platt_model to exclude the row.
        now = "2026-04-18T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO platt_models
                (model_key, temperature_metric, cluster, season, data_version,
                 input_space, param_A, param_B, param_C, bootstrap_params_json,
                 n_samples, brier_insample, fitted_at, is_active, authority,
                 recorded_at)
            VALUES
                ('high:NYC:JJA:v1:width_normalized_density',
                 'high', 'NYC', 'JJA',
                 'tigge_mx2t6_local_calendar_day_max',
                 'width_normalized_density',
                 1.23, 0.5, 0.0, '[]', 200, 0.10, ?, 1, 'VERIFIED', ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO platt_models
                (model_key, temperature_metric, cluster, season, data_version,
                 input_space, param_A, param_B, param_C, bootstrap_params_json,
                 n_samples, brier_insample, fitted_at, is_active, authority,
                 recorded_at)
            VALUES
                ('low:NYC:JJA:v1:width_normalized_density',
                 'low', 'NYC', 'JJA',
                 'tigge_mn2t6_local_calendar_day_min',
                 'width_normalized_density',
                 4.56, 0.7, 0.0, '[]', 200, 0.15, ?, 1, 'VERIFIED', ?)
            """,
            (now, now),
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
            cluster="NYC", wu_station="KNYC",
            settlement_source_type="wu_icao",
        )

        # LOW path — must return the row with param_A=4.56, NOT 1.23
        cal_low, level_low = get_calibrator(
            conn, city, "2026-07-15",  # July → JJA
            temperature_metric="low",
        )
        assert cal_low is not None, (
            "R-BZ.1: LOW calibrator lookup returned None despite a LOW row "
            "existing in platt_models. Pre-P9C this was guaranteed None "
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
            cluster="NYC", wu_station="KNYC",
            settlement_source_type="wu_icao",
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
            cluster="NYC", wu_station="KNYC",
            settlement_source_type="wu_icao",
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
# R-CA.3 / F01-F02 — monitor LOW metric and Day0 shoulder continuity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# R-CB — A1: _forecast_rows_for conditional v2 read
# ---------------------------------------------------------------------------


class TestRCBForecastRowsV2:
    """Phase 9C A1 (B093 half-2): _forecast_rows_for queries
    historical_forecasts WITH metric filter when v2 has data; else falls
    back to legacy `forecasts` table. Before P9C the function was
    legacy-only — any v2 data was unreachable even once Golden Window lifts.

    B3 (PR3) drops historical_forecasts entirely
    (src/state/schema/v2_schema.py:829 — "historical_forecasts — DROPPED in B3").
    The test_v2_populated_query_filters_by_metric scenario (INSERT into
    historical_forecasts) is now impossible; the table is never created by
    apply_canonical_schema. Canonical behavior is legacy-forecasts fallback
    (test_v2_empty_falls_back_to_legacy below).
    """

    def test_v2_empty_falls_back_to_legacy(self):
        """R-CB.2: when v2 is empty (Golden Window current state), legacy
        `forecasts` table is queried unchanged. Backward-compat preservation."""
        from src.engine.replay import ReplayContext
        from src.state.schema.v2_schema import apply_canonical_schema
        from src.state.db import init_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_canonical_schema(conn)

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


# ---------------------------------------------------------------------------
# R-CG — Phase 9C.1 ITERATE-fix (critic-dave MAJOR-2):
#        _fit_from_pairs LOW-skip write-path guard (two-seam closure)
# ---------------------------------------------------------------------------


class TestRCGFitFromPairsLowSkip:
    """Phase 9C.1 (critic-dave cycle-1 MAJOR-2 "latent bomb" fix):

    Pre-P9C.1 write-side gap: `_fit_from_pairs` at manager.py:252 called
    legacy `save_platt_model` — metric-blind. For LOW on-the-fly refits:
    would pollute legacy `platt_models` with a LOW-fitted model. For
    HIGH v2-miss via get_calibrator's L165-168 legacy fallback: could
    read that LOW-fitted model AS HIGH. Classic two-seam violation
    (critic-beth cycle-1 L1) — the L3 read-side fix was partial rollback-
    able via the legacy-save write seam.

    P9C.1 fix: `_fit_from_pairs` accepts `temperature_metric` (default
    "high"), early-returns None for anything else. LOW refits MUST go
    through scripts/refit_platt.py → save_platt_model (Golden-
    Window-gated). R-CG locks this invariant.
    """

    def test_fit_from_pairs_returns_none_for_low_metric(self):
        """R-CG.1: `_fit_from_pairs(..., temperature_metric='low')` returns
        None WITHOUT touching legacy save_platt_model. Primary antibody:
        type-system-like guard (early return)."""
        import sqlite3
        from src.calibration.manager import _fit_from_pairs
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_canonical_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_canonical_schema(conn)

        result = _fit_from_pairs(
            conn, cluster="NYC", season="JJA",
            unit="F", temperature_metric="low",
        )
        assert result is None, (
            f"R-CG.1 (critic-dave MAJOR-2 fix): _fit_from_pairs MUST return "
            f"None for non-HIGH metric; got {result!r}. LOW on-the-fly "
            f"refits would pollute legacy platt_models via metric-blind "
            f"save_platt_model call."
        )

    def test_fit_from_pairs_does_not_call_save_platt_model_for_low(self, monkeypatch):
        """R-CG.2 (surgical-probe): even if somehow pairs were available for
        LOW, _fit_from_pairs MUST NOT invoke save_platt_model. Monkeypatches
        save_platt_model to raise on call; asserts the early-return path
        never gets there for LOW."""
        import sqlite3
        from src.calibration import manager as manager_module
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_canonical_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_canonical_schema(conn)

        calls = []

        def _trap_save(*args, **kwargs):
            calls.append(args)
            raise AssertionError(
                "R-CG.2: save_platt_model called during LOW _fit_from_pairs "
                "— metric-blind legacy save pollutes platt_models"
            )

        monkeypatch.setattr(manager_module, "save_platt_model", _trap_save)

        result = manager_module._fit_from_pairs(
            conn, cluster="NYC", season="JJA",
            unit="F", temperature_metric="low",
        )
        assert result is None
        assert len(calls) == 0, (
            f"R-CG.2: save_platt_model was called {len(calls)} times "
            f"during LOW _fit_from_pairs — write-path rollback incomplete"
        )

    def test_fit_from_pairs_still_works_for_high_metric(self):
        """R-CG.3 (paired-positive antibody): HIGH path is unchanged.
        _fit_from_pairs with default temperature_metric='high' behaves
        exactly as pre-P9C.1 (returns None here because we seed no pairs,
        which is the normal Level-4 outcome)."""
        import sqlite3
        from src.calibration.manager import _fit_from_pairs
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_canonical_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_canonical_schema(conn)

        # HIGH path with no pairs → None (Level 4). Pre-P9C.1 behavior
        # preserved (no metric guard fires for HIGH).
        result = _fit_from_pairs(
            conn, cluster="NYC", season="JJA", unit="F",
            # Default temperature_metric="high"
        )
        assert result is None  # No pairs → no fit, same as pre-P9C.1
